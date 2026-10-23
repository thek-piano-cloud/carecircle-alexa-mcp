"""End-to-end test: start the real server, talk to it with the official MCP client over Streamable HTTP.

Run:  python tests/test_mcp_e2e.py      (exit code 0 = all checks passed)
"""
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parent.parent
PORT = 8791
URL = f"http://127.0.0.1:{PORT}"


def start_server() -> subprocess.Popen:
    env = dict(os.environ, CARECIRCLE_PORT=str(PORT), CARECIRCLE_DB=":memory:", CARECIRCLE_DEMO_NOW="2026-10-23T08:05",
               CARECIRCLE_WATCH_SECONDS="0", PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "carecircle"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            if httpx.get(f"{URL}/healthz", timeout=1).status_code == 200:
                return proc
        except Exception:
            time.sleep(0.25)
    proc.kill()
    raise SystemExit("server did not start")


async def run() -> list[str]:
    log: list[str] = []

    def check(name: str, ok: bool, extra: str = "") -> None:
        log.append(f"{'PASS' if ok else 'FAIL'}  {name} {extra}".rstrip())
        if not ok:
            raise AssertionError(name)

    async with streamablehttp_client(f"{URL}/mcp") as (read, write, _):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            check("negotiates MCP 2025-11-25", init.protocolVersion == "2025-11-25", init.protocolVersion)
            tools = {t.name: t for t in (await s.list_tools()).tools}
            check("exposes 7 tools", len(tools) == 7, ", ".join(sorted(tools)))
            check("read-only hints set", tools["get_daily_plan"].annotations.readOnlyHint is True)
            check("tools declare output schemas", all(t.outputSchema for t in tools.values()))
            res = (await s.list_resources()).resources
            check("exposes resources", {str(r.uri) for r in res} >= {"carecircle://care-plan", "carecircle://alerts"})
            prompts = (await s.list_prompts()).prompts
            check("exposes evening prompt", any(p.name == "evening_check_in" for p in prompts))

            plan = (await s.call_tool("get_daily_plan", {})).structuredContent
            due = [m["medication"] for m in plan["medications"] if m["state"] == "due"]
            check("08:05 plan shows 3 due morning medicines", len(due) == 3, str(due))

            r1 = (await s.call_tool("confirm_medication", {"medication": "my blood pressure pill"})).structuredContent
            check("logs Amlodipine", r1["status"] == "logged" and r1["medication"] == "Amlodipine")
            r2 = (await s.call_tool("confirm_medication", {"medication": "amlodipine again"})).structuredContent
            check("double-dose guard refuses", r2["status"] == "already_taken" and r2["logged"] is False)
            r3 = (await s.call_tool("confirm_medication", {"medication": "statin"})).structuredContent
            check("dose taken within its gap is not logged again", r3["logged"] is False, r3["status"])
            r4 = (await s.call_tool("confirm_medication", {"medication": "aspirin"})).structuredContent
            check("unknown medicine handled", r4["status"] == "unknown_medication")

            sk = (await s.call_tool("skip_medication", {"medication": "vitamin", "reason": "feeling sick"})).structuredContent
            check("skip is recorded", sk["status"] == "skipped" and sk["logged"] is True)
            sk2 = (await s.call_tool("skip_medication", {"medication": "metformin", "reason": "nausea"})).structuredContent
            check("repeated skip/miss alerts the family", sk2["family_alert"] is not None and sk2["family_alert"]["urgency"] == "important")
            c1 = (await s.call_tool("check_in", {"mood": 4, "pain": 0, "note": "fine"})).structuredContent
            check("good mood: no alert", c1["alerts"] == [])
            c2 = (await s.call_tool("check_in", {"mood": 2, "pain": 8, "note": "my knee"})).structuredContent
            check("strong pain: urgent family alert", c2["alerts"][0]["urgency"] == "urgent")

            n = (await s.call_tool("notify_family", {"message": "I fell in the kitchen", "urgency": "info"})).structuredContent
            check("fall wording escalates to urgent", n["notification"]["urgency"] == "urgent")

            rem = (await s.call_tool("add_reminder", {"text": "water the plants", "when": "15:00"})).structuredContent
            check("reminder saved", rem["status"] == "saved")

            dg = (await s.call_tool("get_family_digest", {"days": 7})).structuredContent
            check("digest has adherence and mood trend", 0 < dg["adherence_percent"] <= 100 and dg["mood_trend"] in ("declining", "steady", "improving"),
                  f"{dg['adherence_percent']}% / {dg['mood_trend']}")

            async with httpx.AsyncClient() as h:
                await h.post(f"{URL}/api/clock", json={"advance_minutes": 420})   # 15:05 — evening Metformin not due yet
            early = (await s.call_tool("confirm_medication", {"medication": "metformin"})).structuredContent
            check("not-yet-due dose is refused (too_early)", early["status"] == "too_early" and early["logged"] is False)
            forced = (await s.call_tool("confirm_medication", {"medication": "metformin", "force": True})).structuredContent
            check("force overrides timing guard for doctor-directed doses", forced["logged"] is True)
            tools = {t.name: t for t in (await s.list_tools()).tools}
            check("every tool publishes a real output schema with a required `spoken` field",
                  all("spoken" in (t.outputSchema or {}).get("properties", {}) and "spoken" in t.outputSchema.get("required", [])
                      for t in tools.values()), f"{len(tools)} tools")
            dose_status = tools["confirm_medication"].outputSchema["properties"]["status"]
            check("confirm_medication schema enumerates its refusal statuses",
                  {"already_taken", "too_early"} <= set(dose_status.get("enum", [])))
            check("a forced dose is never silent: family gets an important alert",
                  forced["override"] is True and forced["family_alert"]["urgency"] == "important")

            content = await s.read_resource("carecircle://care-plan")
            check("resource readable", "Amlodipine" in content.contents[0].text)
            p = await s.get_prompt("evening_check_in")
            check("prompt readable", "check_in" in p.messages[0].content.text)

    # the simulator path: utterance -> router -> MCP client -> tool
    async with httpx.AsyncClient(base_url=URL, timeout=20) as h:
        r = (await h.post("/api/utterance", json={"text": "I took my vitamin"})).json()
        check("simulator routes through MCP", r["tool"] == "confirm_medication" and r["trace"]["protocolVersion"] == "2025-11-25", r["reply"])
        r = (await h.post("/api/utterance", json={"text": "Remind me to call the pharmacy at 3 pm"})).json()
        check("simulator parses reminder", r["tool"] == "add_reminder" and r["arguments"]["when"] == "15:00", str(r["arguments"]))

        # the proactive watcher: nobody speaks, a dose goes two hours late, the family hears about it exactly once
        await h.post("/api/reset", json={"set": "2026-10-23T08:05:00"})
        await h.post("/api/utterance", json={"text": "I took my blood pressure pill"})
        await h.post("/api/clock", json={"advance_minutes": 130})            # 10:15, Metformin + Vitamin D unlogged
        first = (await h.post("/api/sweep")).json()["alerts"]
        check("watcher alerts the family about doses two hours late, unprompted",
              {a["source"] for a in first} == {"watcher"} and len(first) == 2 and all(a["urgency"] == "important" for a in first),
              "; ".join(a["message"][:40] for a in first))
        check("watcher never repeats an alert for the same dose", (await h.post("/api/sweep")).json()["alerts"] == [])
        late = (await h.post("/api/utterance", json={"text": "I took my vitamin"})).json()
        check("a missed dose can still be logged late", late["result"]["status"] == "logged" and late["result"]["late"] is True, late["reply"])

        meta = (await h.get("/.well-known/oauth-protected-resource")).json()
        check("publishes RFC 9728 protected-resource metadata", meta["resource"].endswith("/mcp") and meta["bearer_methods_supported"] == ["header"])
    return log


def auth_check() -> list[str]:
    """Second server instance with CARECIRCLE_TOKEN set: no token -> 401, wrong token -> 401, right token -> works."""
    port = PORT + 1
    env = dict(os.environ, CARECIRCLE_PORT=str(port), CARECIRCLE_DB=":memory:", CARECIRCLE_DEMO_NOW="2026-10-23T08:05",
               CARECIRCLE_TOKEN="s3cret-token", CARECIRCLE_WATCH_SECONDS="0", CARECIRCLE_DEMO="0",
               CARECIRCLE_ALLOWED_HOSTS=f"127.0.0.1:{port}", PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "carecircle"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out = []
    try:
        for _ in range(60):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(0.25)
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                                                                              "clientInfo": {"name": "t", "version": "1"}}}
        hdr = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
        u = f"http://127.0.0.1:{port}/mcp"
        denied = httpx.post(u, json=body, headers=hdr)
        assert denied.status_code == 401 and "resource_metadata=" in denied.headers["www-authenticate"]
        assert httpx.post(u, json=body, headers={**hdr, "authorization": "Bearer nope"}).status_code == 401
        ok = httpx.post(u, json=body, headers={**hdr, "authorization": "Bearer s3cret-token"})
        assert ok.status_code == 200 and ok.json()["result"]["protocolVersion"] == "2025-11-25"
        out.append("PASS  bearer token: missing/wrong -> 401 with resource_metadata challenge, correct -> MCP initialize works")
        assert httpx.get(f"http://127.0.0.1:{port}/api/state").status_code == 404
        assert httpx.get(f"http://127.0.0.1:{port}/").status_code == 404
        assert httpx.get(f"http://127.0.0.1:{port}/.well-known/oauth-protected-resource").status_code == 200
        out.append("PASS  public mode (CARECIRCLE_DEMO=0): demo pages and /api are not served, MCP + auth metadata are")
        bad = httpx.post(u, json=body, headers={**hdr, "authorization": "Bearer s3cret-token", "host": "evil.example"})
        assert bad.status_code in (400, 403, 421), bad.status_code
        out.append("PASS  Host header outside CARECIRCLE_ALLOWED_HOSTS is rejected (DNS rebinding protection)")
    finally:
        proc.kill()
    return out


def main() -> int:
    proc = start_server()
    try:
        log = asyncio.run(run()) + auth_check()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc!r}")
        return 1
    finally:
        proc.kill()
    print("\n".join(log))
    print(f"\n{len(log)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
