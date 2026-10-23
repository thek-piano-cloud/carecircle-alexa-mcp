# Product feedback and friction log

Every tool, API or SDK used for CareCircle, in the order we met them. Honest scope note: we had **no access to
an Alexa+ device or the Alexa+ Preview**, so the Alexa+ side was built against the public MCP specification and
verified with the official MCP client; the simulator stands in for the Alexa+ agent.

## 1. MCP specification 2025-11-25 and the Streamable HTTP transport
* **Used for:** the whole server surface — tools with output schemas and annotations, resources, a prompt, server instructions.
* **Worked well:** the transport page is short and unambiguous; stateless JSON responses are enough for a voice
  product (no long-lived SSE stream to babysit), and `MCP-Protocol-Version` negotiation was easy to test.
  Tool annotations (`readOnlyHint`, `openWorldHint`) give an agent a cheap way to know which calls are safe to repeat.
* **Needs work:** nothing says how a *voice* agent should use structured output. We added our own `spoken`
  field to every result so the agent can read safety-critical wording verbatim; a standard "speakable summary"
  convention (or guidance for Alexa+) would remove that guesswork.
* **Onboarding:** quick (an afternoon). **Build again?** Yes.

## 2. MCP Python SDK
* **Used for:** `FastMCP` server, official client session in tests and in the simulator.
* **Worked well:** `@mcp.tool` with `Annotated[..., Field(...)]` produced input JSON Schemas from plain type hints, and
  Pydantic return models produce real output schemas with no extra code; `custom_route` let one process serve the MCP endpoint and the demo UI; the client's `streamablehttp_client` made the end-to-end test ~20 lines.
* **Needs work:** see F1 — a bare `pip install mcp` now installs a new major version with a renamed server API — and F5:
  a `-> dict[str, Any]` return type silently publishes an empty output schema.
* **Build again?** Yes.

## 3. Amazon SNS (via boto3) — AWS Builder mini challenge
* **Used for:** optional real delivery of family alerts: each alert is published to a topic with an `urgency`
  message attribute so subscribers (SMS, email, mobile push, a phone-call Lambda) can use filter policies.
* **Worked well:** message attributes + subscription filter policies map exactly onto our info / important / urgent ladder
  with no extra code; `botocore.stub.Stubber` made the publish path and the failure path testable without an account.
* **Not verified:** SNS was exercised against the stub only, **not against a live AWS account**, so we cannot comment on
  onboarding, SMS delivery to Korean numbers or phone-call Lambdas from experience.
* **Build again?** Yes.

## 4. Voice-readiness auditing — our own open-source tool
* **Used for:** checking CareCircle the way a voice agent sees it (`mcp-voice-check`, released as a separate MIT project).
* **Why:** none of the tools we found flag an empty output schema or a missing speakable field; both matter more for voice than for chat.
* **Build again?** Yes — it now runs in our test routine.

## 5. Alexa+ (MCP integrations)
* **Used for:** target platform. Not accessible to us; simulated.
* **Needs work:** the hackathon Resources tab links the MCP spec and the transport page but has **no Alexa+ sample
  server or repo**, and says the developer-credit codes were already used up (7 Oct update). A minimal "hello MCP for Alexa+"
  repository and a local test harness would make the track far easier to enter on day one.
* **Build again?** Yes, once Preview access exists.

## Friction log

| # | Task attempted | Steps | Expected | Actual | Severity | Workaround | Suggestion |
|---|---|---|---|---|---|---|---|
| F1 | Install the MCP Python SDK and write a server | `pip install mcp`, then `from mcp.server.fastmcp import FastMCP` as in most tutorials | Tutorials work as written | pip installed a new **major version (2.x)** where `FastMCP` was renamed `MCPServer` and its newest protocol version is a later date; the import raised `ModuleNotFoundError` | Medium (10 min lost, confusing for newcomers) | Pin `mcp>=1.23,<2` (1.30 negotiates 2025-11-25) | Hackathon docs should state the SDK version the 2025-11-25 requirement was written against |
| F2 | Find an Alexa+ starting point | Read the hackathon Resources tab | A sample MCP server for Alexa+ | Only the spec and transport links; Fire TV and Ring have sample code, Alexa+ has none | Medium | Built from the spec | Add an Alexa+ sample repo and a local test harness |
| F3 | Run two services locally | Chose port 8765 | Free port | Already in use on the developer machine; the server logged a bind error and exited | Low | Chose 8780 | Document the port and make it configurable (we did: `CARECIRCLE_PORT`) |
| F4 | Record the demo video | Playwright `record_video` | Works with the installed browser | Needed a separate `playwright install ffmpeg` download; the error text was clear | Low | Ran the install command | Mention in docs |
| F5 | Publish output schemas for every tool | Tools returned `dict[str, Any]`; all end-to-end tests passed | `outputSchema` describes the result fields | `tools/list` showed only `{"type": "object", "additionalProperties": true}` for all 7 tools — no warning at registration, and our tests (which read `structuredContent`) could not notice | Medium (a voice agent cannot know which field to read aloud) | Switched to Pydantic result models with a required `spoken` field; wrote **mcp-voice-check** to catch this automatically | Warn at registration when a tool's output schema has no properties, or document the `dict` behavior next to structured output |

## Feature requests

| Request | Why it matters | Urgency |
|---|---|---|
| Standard "speakable summary" field/convention for MCP tool results used by voice agents | Safety wording (e.g. "do not take another pill") must reach the user verbatim | important |
| Alexa+ local MCP test harness / simulator from Amazon | Lets developers without Preview access validate tools | important |
| Proactive-notification path from an MCP server to an Alexa device (e.g. "your evening pill is due") | Today the resident has to ask; reminders should come to her | important |
| Guidance on authentication and consent for health-adjacent MCP servers | Medicine data is sensitive; needs a recommended pattern | nice-to-have |
