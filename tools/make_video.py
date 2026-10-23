"""Builds the <3 minute English demo video (screen recording of the running app + narration).

    python tools/make_video.py <output_dir> [voice_check_report.txt]

Needs: the server running on :8780 (python -m carecircle), Playwright + Edge, ffmpeg on PATH, and a Gemini API key in
GEMINI_API_KEY for the two voices (narrator and the Alexa stand-in voice). The optional report file is the real
output of `python -m mcp_voice_check http://127.0.0.1:8780/mcp`, shown on one card.
Everything else shown is the real app: the simulator sends each utterance through an MCP client session to the /mcp
endpoint and the dashboard polls the same database.
"""
import base64
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
REPORT = Path(sys.argv[2]) if len(sys.argv) > 2 else None
BASE = "http://127.0.0.1:8780"
W, H = 1920, 1080
TTS_MODEL = "gemini-3.8-flash-tts"
TTS_FALLBACK = "gemini-3.8-flash-lite-tts"
NARRATOR = ("Charon", "A calm, warm, clear male product-demo narrator in his thirties. Natural conversational American "
                      "English, unhurried, friendly and sincere; no announcer or commercial tone.")
ALEXA = ("Kore", "A gentle, clear female voice assistant speaking to an older woman: kind, slow and reassuring, "
                 "natural American English.")

N_COLD = "She already took this pill a few minutes ago. CareCircle just stopped a double dose."
N_TITLE = "This is CareCircle: Alexa Plus, for families who care from far away."
N_ARCH = ("A self-hosted MCP server with seven typed tools. Here a simulator plays Alexa's part; every call is real MCP.")
N_CHECK = ("We also built mcp voice check, an open-source tool that audits any MCP server the way a voice agent sees it. "
           "CareCircle passes, with zero failures.")
N_CLOSE = "CareCircle is open source and covered by end-to-end tests. Care from far away, one conversation at a time."

COLD = [dict(cap="\"I took my blood pressure pill again\"  →  confirm_medication refuses", say=N_COLD,
             act=("say", "I took my blood pressure pill again"), voice=True)]
STEPS = [
    dict(cap="A fresh morning, 8:05 AM  ·  Mrs. Han's Echo Show  ·  live MCP traffic  ·  Jisoo's phone",
         say="Now a fresh morning. On the left, Mrs. Han's Echo Show. In the middle, every MCP request, live. On the right, her daughter "
             "Jisoo's phone.", act=None),
    dict(cap="\"What do I need to do today?\"  →  get_daily_plan",
         say="She asks what she needs to do today. Get daily plan answers: three medicines are due this morning.",
         act=("say", "What do I need to do today?")),
    dict(cap="\"I took my sugar pill\"  →  confirm_medication",
         say="She takes her morning metformin and says so. The dose is logged, and Jisoo's dashboard updates at once.",
         act=("say", "I took my sugar pill")),
    dict(cap="10:15 AM, nobody says anything  →  the server's watcher alerts Jisoo",
         say="Now the quiet failure. By ten fifteen she still hasn't mentioned her blood pressure pill or her vitamin, "
             "and nobody asked. The server notices on its own, and Jisoo gets one alert per missed dose.",
         act=("sweep", "2026-10-23T10:15:00")),
    dict(cap="3 PM: \"My doctor told me to take my evening metformin now\"  →  doctor's override, and the family is told",
         say="Sometimes a rule has to bend. Her doctor moved her evening dose. The agent marks it as a doctor's override, the dose is "
             "logged, and the server always tells the family. A safety rule can bend, but never silently.",
         act=("clock+say", "15:00", "My doctor told me to take my evening metformin now"), voice=True),
    dict(cap="Evening: \"I feel lonely and tired today\"  →  check_in  →  important alert",
         say="That evening she says she feels lonely. It's her third low check-in in a row, so Jisoo gets an "
             "important alert and can call her tonight.",
         act=("clock+say", "19:30", "I feel lonely and tired today")),
    dict(cap="\"I fell in the kitchen\"  →  notify_family escalates to URGENT",
         say="Then the worst moment. She says she has fallen. Notify family escalates to urgent on its own and alerts "
             "both children, while Alexa tells her to call emergency services.",
         act=("say", "I fell in the kitchen, I need help"), voice=True),
    dict(cap="\"How have I been this week?\"  →  get_family_digest",
         say="And the weekly digest, the same one Jisoo reads, sums up adherence, missed doses and the mood trend in "
             "one sentence.", act=("say", "How have I been this week?")),
]

FONT = lambda size, bold=False: ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf", size)
MONO = lambda size: ImageFont.truetype("C:/Windows/Fonts/consola.ttf", size)


# --- voices ----------------------------------------------------------------------------------------------------

_client = None


def tts(text: str, path: Path, who=NARRATOR) -> Path:
    """Gemini TTS -> loudness-normalised wav. Cached by file name."""
    global _client
    if path.exists():
        return path
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options={"timeout": 40000})
    voice, style = who
    last = None
    import concurrent.futures as cf

    def call(model):
        return _client.interactions.create(
            model=model,
            input=[{"type": "user_input", "content": [{"type": "text", "text": text,
                                                       "annotations": [{"type": "speech_metadata", "style": style}]}]}],
            response_format={"type": "audio"},
            generation_config={"speech_config": [{"voice": voice}]},
        )
    for attempt in range(4):
        model = TTS_MODEL if attempt % 2 == 0 else TTS_FALLBACK   # same voice on both models
        ex = cf.ThreadPoolExecutor(max_workers=1)
        try:
            it = ex.submit(call, model).result(timeout=90)   # hard timeout: some calls hang past the HTTP timeout
            raw = path.with_suffix(".raw.wav")
            raw.write_bytes(base64.b64decode(it.output_audio.data))
            print(f"  voice {path.name}: {model}/{voice}", flush=True)
            break
        except Exception as exc:  # noqa: BLE001 - rate limits / hangs: wait and retry
            last = exc
            print(f"  voice {path.name}: retry after {type(exc).__name__}", flush=True)
            time.sleep(8 * (attempt + 1))
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
    else:
        raise SystemExit(f"TTS failed for {path.name}: {last}")
    subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af",
                           "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse,"
                           "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,areverse,"
                           "highpass=f=80,acompressor=threshold=-20dB:ratio=3:attack=5:release=90:makeup=2,"
                           "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "24000", "-ac", "1", str(path)])
    raw.unlink()
    return path


def dur(path: Path) -> float:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(out.strip())


# --- cards -----------------------------------------------------------------------------------------------------

def card(path: Path, title: str, sub: str, body):
    img = Image.new("RGB", (W, H), "#0b1220")
    d = ImageDraw.Draw(img)
    for y in range(H):  # soft vertical glow
        c = int(18 + 26 * (1 - y / H))
        d.line([(0, y), (W, y)], fill=(11, c, 40 + c))
    d.ellipse([120, 110, 190, 180], outline="#2dd4bf", width=10)
    d.text((215, 108), "CareCircle", font=FONT(64, True), fill="#e8eefc")
    d.text((120, 250), title, font=FONT(76, True), fill="#ffffff")
    d.text((120, 350), sub, font=FONT(40), fill="#8ea0c4")
    body(d)
    img.save(path)


def body_title(d):
    items = [("A pill taken twice", "#fbbf24"), ("A pill never taken", "#fbbf24"),
             ("A lonely week nobody hears about", "#f87171")]
    for i, (t, col) in enumerate(items):
        y = 500 + i * 110
        d.rounded_rectangle([120, y, 1100, y + 84], 20, fill="#111a2e", outline="#243252", width=2)
        d.ellipse([150, y + 28, 178, y + 56], fill=col)
        d.text((205, y + 14), t, font=FONT(40), fill="#e8eefc")
    d.text((1180, 560), "Alexa+ is already\nin the room.", font=FONT(70, True), fill="#2dd4bf", spacing=12)


def body_arch(d):
    def box(x, y, w, h, title, lines, col):
        d.rounded_rectangle([x, y, x + w, y + h], 24, fill="#111a2e", outline=col, width=4)
        d.text((x + 28, y + 22), title, font=FONT(40, True), fill=col)
        for i, ln in enumerate(lines):
            d.text((x + 28, y + 86 + i * 44), ln, font=FONT(30), fill="#c9d6f2")

    def arrow(x1, x2, y, label):
        d.line([(x1, y), (x2, y)], fill="#2dd4bf", width=6)
        d.polygon([(x2, y), (x2 - 22, y - 14), (x2 - 22, y + 14)], fill="#2dd4bf")
        d.text(((x1 + x2) / 2 - 150, y - 62), label, font=FONT(28), fill="#9ff3e6")
    box(100, 480, 420, 300, "Echo + Alexa+", ["Mrs. Han speaks", "the agent picks a tool", "and reads `spoken`",
                                              "(demo: simulator)"], "#60a5fa")
    arrow(530, 770, 620, "MCP over\nStreamable HTTP")
    box(780, 440, 620, 380, "CareCircle MCP server", ["7 tools · typed output schemas", "spec 2025-11-25 · /mcp",
                                                         "double-dose guard · override alert", "alert ladder · SQLite log"], "#2dd4bf")
    arrow(1410, 1590, 620, "alerts")
    box(1600, 480, 260, 300, "Family", ["Jisoo's phone", "SNS: SMS · push"], "#fbbf24")


def body_check(lines):
    def body(d):
        d.rounded_rectangle([120, 450, 1800, 1000], 20, fill="#060a14", outline="#243252", width=2)
        d.text((150, 470), "$ python -m mcp_voice_check http://127.0.0.1:8780/mcp", font=MONO(30), fill="#9ff3e6")
        for i, ln in enumerate(lines[:11]):
            col = "#4ade80" if ln.startswith("PASS") else "#fbbf24" if ln.startswith("WARN") else "#f87171" if ln.startswith("FAIL") else "#e8eefc"
            d.text((150, 525 + i * 40), ln[:96], font=MONO(27), fill=col)
    return body


def body_close(d):
    rows = ["Alexa+ track  ·  self-hosted MCP server (2025-11-25, Streamable HTTP)",
            "Safety in the server: double-dose guard, missed-dose watcher, overrides never silent",
            "34 end-to-end checks with the official MCP client  ·  MIT licensed",
            "Companion OSS: mcp-voice-check  ·  runs locally: python -m carecircle"]
    for i, t in enumerate(rows):
        y = 470 + i * 120
        d.rounded_rectangle([120, y, 1800, y + 90], 20, fill="#111a2e", outline="#243252", width=2)
        d.ellipse([150, y + 30, 180, y + 60], fill="#2dd4bf")
        d.text((215, y + 18), t, font=FONT(40), fill="#e8eefc")


# --- recording -------------------------------------------------------------------------------------------------

def play(pg, left, steps, prefix, demo_t0, marks, replies, d):
    for i, s in enumerate(steps):
        key = f"{prefix}{i}"
        t_start = time.time()
        marks.append(t_start - demo_t0)
        pg.evaluate("t=>window.cap(t)", s["cap"])
        time.sleep(1.0)
        act = s["act"]
        if act:
            if act[0] == "sweep":   # no utterance: move the demo clock and let the server's watcher run
                httpx.post(f"{BASE}/api/clock", json={"set": act[1]}, timeout=10)
                httpx.post(f"{BASE}/api/sweep", timeout=10)
                txt = None
            elif act[0] == "clock+say":
                left.locator(f'[data-set="{act[1]}"]').click()
                time.sleep(1.2)
                txt = act[2]
            else:
                txt = act[1]
        if act and txt:
            inp = left.locator("#text")
            inp.click()
            inp.press_sequentially(txt, delay=50)
            time.sleep(0.4)
            inp.press("Enter")
        end = t_start + d[key] + 0.8
        if s.get("voice"):
            time.sleep(2.6)  # let the reply render, then read it back in the Alexa stand-in voice
            reply = left.locator(".a").last.evaluate("e => e.childNodes[0].textContent")
            path = OUT / f"r_{key}.wav"
            th = threading.Thread(target=tts, args=(reply, path, ALEXA))
            th.start()
            th.join()
            rd = dur(path)
            start = max(time.time(), t_start + d[key] + 0.4)
            replies.append((path, start - demo_t0))
            end = max(end, start + rd + 0.9)
        time.sleep(max(0, end - time.time()))


def main():
    texts = {"cold": N_COLD, "title": N_TITLE, "arch": N_ARCH, "check": N_CHECK, "close": N_CLOSE,
             **{f"s{i}": s["say"] for i, s in enumerate(STEPS)}}
    d = {k: dur(tts(t, OUT / f"n_{k}.wav")) for k, t in texts.items()}
    d["c0"] = d["cold"]
    report = REPORT.read_text(encoding="utf-8").splitlines() if REPORT and REPORT.exists() else []
    card(OUT / "title.png", "Alexa+ for families who care from far away", "Keeps medicines safe and the family in the loop", body_title)
    card(OUT / "arch.png", "How it works", "A real MCP server; a simulator stands in for Alexa+", body_arch)
    card(OUT / "check.png", "Checked like a voice agent sees it", "mcp-voice-check — our open-source companion tool", body_check(report))
    card(OUT / "close.png", "Care from far away", "Open source · tested · built for Alexa+", body_close)

    # cold open state: the blood pressure pill was confirmed at 08:05, a few minutes ago
    httpx.post(f"{BASE}/api/reset", json={"set": "2026-10-23T08:05:00"}, timeout=10)
    httpx.post(f"{BASE}/api/utterance", json={"text": "I took my blood pressure pill"}, timeout=20)
    httpx.post(f"{BASE}/api/clock", json={"advance_minutes": 6}, timeout=10)
    cold_marks, cold_replies, marks, replies = [], [], [], []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge")
        ctx = b.new_context(viewport={"width": W, "height": H}, record_video_dir=str(OUT / "rec"), record_video_size={"width": W, "height": H})
        rec_t0 = time.time()
        pg = ctx.new_page()
        pg.goto(f"{BASE}/demo")
        pg.wait_for_timeout(2500)
        left = pg.frame_locator("#a")
        cold_t0 = time.time()
        play(pg, left, COLD, "c", cold_t0, cold_marks, cold_replies, d)
        cold_len = time.time() - cold_t0
        pg.evaluate("t=>window.cap(t)", "")
        # main demo from a fresh day (the gap is cut out of the video)
        httpx.post(f"{BASE}/api/reset", json={"set": "2026-10-23T08:05:00"}, timeout=10)
        pg.reload()
        pg.wait_for_timeout(2500)
        left = pg.frame_locator("#a")
        demo_t0 = time.time()
        play(pg, left, STEPS, "s", demo_t0, marks, replies, d)
        demo_len = time.time() - demo_t0
        pg.evaluate("t=>window.cap(t)", "")
        ctx.close()
        video = Path(pg.video.path())
        b.close()
    cold_ss, demo_ss = (cold_t0 - rec_t0) + 0.25, (demo_t0 - rec_t0) + 0.25

    # --- assemble: cold open | title | arch | demo | check | close -------------------------------------------
    title_len, arch_len = d["title"] + 0.8, d["arch"] + 0.9
    check_len, close_len = d["check"] + 1.0, d["close"] + 1.8
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-ss", f"{cold_ss:.2f}", "-t", f"{cold_len:.2f}", "-i", str(video),
           "-loop", "1", "-t", f"{title_len:.2f}", "-i", str(OUT / "title.png"),
           "-loop", "1", "-t", f"{arch_len:.2f}", "-i", str(OUT / "arch.png"),
           "-ss", f"{demo_ss:.2f}", "-t", f"{demo_len:.2f}", "-i", str(video),
           "-loop", "1", "-t", f"{check_len:.2f}", "-i", str(OUT / "check.png"),
           "-loop", "1", "-t", f"{close_len:.2f}", "-i", str(OUT / "close.png")]
    t_title = cold_len
    t_arch = t_title + title_len
    t_demo = t_arch + arch_len
    t_check = t_demo + demo_len
    t_close = t_check + check_len
    audio = [(OUT / "n_cold.wav", cold_marks[0] + 0.1)]
    audio += [(pth, off + 0.1) for pth, off in cold_replies]
    audio += [(OUT / "n_title.wav", t_title + 0.3), (OUT / "n_arch.wav", t_arch + 0.4)]
    audio += [(OUT / f"n_s{i}.wav", t_demo + m + 0.1) for i, m in enumerate(marks)]
    audio += [(pth, t_demo + off + 0.1) for pth, off in replies]
    audio += [(OUT / "n_check.wav", t_check + 0.4), (OUT / "n_close.wav", t_close + 0.5)]
    for pth, _ in audio:
        cmd += ["-i", str(pth)]
    n_v = 6
    vf = "".join(f"[{i}:v]scale={W}:{H},setsar=1,fps=30,format=yuv420p[v{i}];" for i in range(n_v))
    vf += "".join(f"[v{i}]" for i in range(n_v)) + f"concat=n={n_v}:v=1:a=0[v];"
    af = ""
    for j, (_, at) in enumerate(audio):
        ms = int(at * 1000)
        af += f"[{n_v + j}:a]adelay={ms}|{ms}[a{j}];"
    af += "".join(f"[a{j}]" for j in range(len(audio))) + f"amix=inputs={len(audio)}:normalize=0[a]"
    cmd += ["-filter_complex", vf + af, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "20", "-preset", "medium",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(OUT / "CareCircle_demo.mp4")]
    total = t_close + close_len
    if total > 178:   # Devpost limit is 3 minutes; keep a margin
        raise SystemExit(f"video would be {total:.1f}s (> 178s): shorten the narration")
    subprocess.check_call(cmd)
    (OUT / "timeline.json").write_text(json.dumps({"total_seconds": round(total, 1), "cold": cold_len, "demo_marks": marks,
                                                   "voices": {"narrator": NARRATOR[0], "alexa_stand_in": ALEXA[0], "model": TTS_MODEL}}, indent=1))
    print(f"video: {OUT / 'CareCircle_demo.mp4'}  total {total:.1f}s")


if __name__ == "__main__":
    main()
