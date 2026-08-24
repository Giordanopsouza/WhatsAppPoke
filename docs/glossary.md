# Glossary

Domain terms for humans and agents. Prefer this file over inventing synonyms.

| **Term** | Definition | Distinctions / exclusions |
|---|---|---|
| **contact** | A WhatsApp user row; the tenant boundary for almost every table (`contact_id`). | Same as **tenant** in product language. Not a Google account, Twilio AccountSid, or WaId. Identity is digits-only `phone` from Twilio `From`. |
| **tenant** | Isolation unit of the product: one WhatsApp user. | Implemented as **contact**; not an org, workspace, or multi-user account. |
| **message** | One persisted inbound or outbound chat row (`direction` in/out). | Not a **job**. Empty-body / media-only inbound may be stored without an Interaction event. |
| **job** | Durable work unit in the Postgres queue (`kind`, `run_at`, `status`, attempts). | Not a user **task** (todo). Background kinds are `reminder_due`, `automation_due`, and `integration_notify`; ordinary inbound conversation work never enters it. `contact_id` is required. |
| **Interaction Agent** | Sole WhatsApp speaker in the target runtime. Handles every inbound and completed internal event; owns orchestration tools but no SaaS business tools. | Visible text leaves only through `send_message_to_user`. Pydantic output is internal control state. ADR 0014. |
| **Execution Agent** | Reusable non-speaking Pydantic agent that runs one isolated goal with owned tools and returns an internal event. | Not a named roster agent, not a worker process, and never calls Twilio. |
| **dispatch execution** | Deterministic service/tool that creates and starts an **execution run**. | The OpenPoke-like “spawn” role. Not an LLM or a third agent. On internal result re-entry, the Interaction tool is unavailable until a new user inbound (ADR 0019). |
| **execution run** | Persisted detached Execution lifecycle (`pending` through terminal status), goal, dedupe key, and compact result. | Audit/dedupe/active context, not a durable conversation queue. Api restart may mark it `abandoned`. |
| **execution event** | Internal result/failure/timeout record that re-enters Interaction against latest history. | Not a user-visible **message**; only Interaction decides what becomes outbound. Re-entry is non-delegating: it cannot call `dispatch_execution` (ADR 0019). |
| **execution outcome** | Agent-classified terminal result of one Execution run: `succeeded`, `failed`, or `needs_input` (stored as `result.outcome`). `needs_input` and other failures both persist as `execution_run.status = failed`; the distinction lives in the event payload. | Not `execution_run.status` itself; the outcome drives that status (ADR 0022). |
| **api** | FastAPI process: Twilio webhook and Composio connect HTTP. Target: returns webhook 200, then runs Interaction and detached conversation Execution tasks in process. | Conversation work after 200 is best-effort. Does not own durable scheduling. |
| **worker** | Process that claims durable background jobs: reminders, automations, and integration notifications. | Does not run ordinary inbound conversation jobs. Must not parse Twilio wire format. |
| **analytics** | Read-only Streamlit dashboard over Postgres aggregates; third Railway service. | Not the **api** or **worker**. Own settings (`analytics/settings.py`), never `app.core.config`. Never selects message bodies. |
| **advisory lock** | Postgres lock keyed on `contact_id` so one contact never runs concurrent turns. | Not a row lock on `job`. Different contacts stay parallel across worker replicas. |
| **debounce** | Artificial silence window before starting a turn (rejected for MVP). | Do **not** implement. Interaction starts immediately after the webhook 200; scheduled jobs use their explicit `run_at`. |
| **dead letter** | Terminal job status after retries are exhausted (`status = dead`). | Not a separate queue table. Failed-but-retryable jobs stay `pending`/`running` until max attempts. |
| **SKIP LOCKED** | Claim pattern: `FOR UPDATE SKIP LOCKED` so workers take distinct runnable jobs without blocking each other. | Queue mechanics only; does not replace the per-contact **advisory lock**. |
| **integration** | Per-contact SaaS connection pointer (`provider` toolkit slug, `external_account_id` = Composio `ca_…`, status). Credentials live at Composio. | One active account per contact+toolkit in the first owned-tool release. Connection alone does not imply an owned tool exists. |
| **Composio authenticated proxy** | Backend-only request through Composio with the selected connected account; our wrapper fixes toolkit endpoint and method. | Auth transport, not an agent tool. No generic URL/method/body surface is exposed to a model. ADR 0015. |
| **owned tool** | Product-defined typed business operation implemented in Python, with fixed proxy call, validation, compact output, telemetry, and confirmation policy. | Not a remote Composio schema, MCP tool, or generic HTTP escape hatch. |
| **connect link** | One-time, short-lived nonce + `provider` (toolkit slug) + signed URL that starts managed-auth for exactly one contact. | Not an **integration**. URL `provider` must match the row; forwarding must not attach another person’s account. Not Google-specific. |
| **pending action** | Exact contact-scoped sensitive write staged with payload hash, source Interaction/Execution id, expiry, and lifecycle status. | A later explicit WhatsApp inbound must claim it. Prompt text and same-turn “send it” cannot bypass the state machine; terminal rows distinguish executed, cancelled, expired, and failed outcomes. |
| **task** | User todo checklist item (title, status, due). | Not a queue **job**. Silent until the user asks; distinct from **reminder**. |
| **reminder** | Timed WhatsApp ping scheduled via a delayed **job** (`run_at`). Row is source of truth; job is wake-up only. | Not a **task**. Edit = cancel + set again. Fire sends `reminder.body` as-is (no LLM); outside the 24h WhatsApp window uses a Utility Content Template. |
| **automation** | Contact-scoped RRULE + timezone + goal that wakes Execution through a durable `automation_due` job and returns through Interaction. | Not a **reminder**: it may use tools/LLM. At most one catch-up after downtime; sensitive writes still stage confirmation. Pause skips fire; resume recomputes the next future occurrence. Not a platform-seeded morning briefing (ADR 0017). |
| **outbound_sweep** | Retired tenant-less `job` kind that fanned out `outbound_due` briefing knocks, then rescheduled itself ~15 minutes later. Removed in ADR 0017 / task 048. | Not a current kind. Do not seed on worker boot. `job.contact_id` is always required. |
| **memory** | Long-term facts retained across sessions (harness `Memory` + store, namespaced by contact). | Not chat **message** history (short horizon). Not a custom vector subsystem in MVP. |
| **usage counter** | Per-contact daily caps on LLM tokens / tool calls. | Not billing. Quota exceed → polite cap, not a charge. |
| **transport** | Channel that carries WhatsApp bytes in/out. | Twilio WhatsApp only (ADR 0007). No Z-API, no dual-transport. |
| **provider message id** | Twilio (or prior) message id stored for idempotency / correlation. | Not `contact.id`. Used in Sentry tags; never log message bodies (PII). |

<!-- Template for new rows:
| **Term** | One-sentence definition. | Distinctions from adjacent terms; deliberate exclusions. |
-->
