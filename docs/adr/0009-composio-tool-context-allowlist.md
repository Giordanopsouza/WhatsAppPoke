0009. Shrink Composio tool context (enable allowlist now; agent/subagent later)
Status: Superseded by ADR 0015
Date: 2026-08-12

The later chat/exec split shipped in ADR 0012. The enable allowlist
here remains the exec agent's tool surface.

Context
ADR 0008 / task 024 attach a Composio MCP toolset on every agent turn
when the contact has active integrations. The session uses
`SESSION_PRESET_DIRECT_TOOLS` plus a **disable** list (send/reply and
destructive deletes). Direct Tools without an **enable** list still
exposes the rest of each toolkit catalog as native function
declarations.

A live turn (Logfire `019ff89394019fab06c0fae3e8c417f3`, contact 3,
`gemini-3.5-flash-lite`) billed **157,193 input tokens** to answer
`"hey"` with `"e aí"`. Breakdown:

- Chat history: 21 messages, ~9 KB (already capped by
  `HISTORY_LIMIT` / `HISTORY_MAX_CHARS` — not the problem).
- Function tools: **191 schemas, ~690 KB**. Eight local tools plus
  ~183 Composio tools from Gmail, Calendar, Sheets, and Notion.

Task 024 chose Direct Tools so “meta-tool search does not dump hundreds
of schemas.” That is only true when the session **enables** a small
set. A denylist on a full catalog is still a gigantic context.

WhatsApp turns are short. Paying ~150k tokens of tool JSON on every
connected-contact message is the wrong default. History and the system
prompt are already small; the lever is which Composio tools enter the
model request.

Options (main, currently)
Three remedies are in play. They compose; they are not mutually
exclusive forever.

1. **Enable allowlist (now).** Keep Direct Tools, MCP, tenancy, and
   `manage_connections=False`. Change the session filter from
   `tools={slug: {disable: […]}}` to `tools={slug: {enable: […]}}` —
   only the slugs WhatsApp actually needs (fetch/search, draft, list
   events, create event, Notion search/fetch, …). The 024 **disable
   list is not sent** — under Direct Tools, `enable` is exclusive, so
   send/delete never reach the model unless we put them on the list.
   Same single-agent turn, one Gemini call. Exact slugs live in
   `app/integrations/composio_policy.py` with a unit-test tripwire
   (enabled slugs must not match the old send/delete matcher).
   Expected drop for a contact like #3: ~191 tools / 157k tokens →
   tens of tools / low tens of thousands.

2. **Deferred loading / tool search.** Pydantic AI
   `MCPToolset.defer_loading()` (or Composio search/execute meta-tools)
   hides schemas until the model searches. `"hey"` stays cheap; “o que
   tem no inbox?” pays an extra round-trip. Fallback if the allowlist
   becomes too rigid. Not the first cut: 024 already preferred Direct
   Tools for a specialized agent, and meta-tools must not turn
   `manage_connections` back on (ADR 0008).

3. **Chat agent + execution subagent (later).** The WhatsApp-facing
   agent keeps local tools and history only — it never attaches
   Composio MCP. When the user actually needs Gmail/Agenda/Notion, it
   delegates to an execution subagent that runs with the allowlisted
   MCP toolset and returns facts, not the user-facing reply. Small talk
   never pays the toolkit tax. This matches the Poke-style split in
   `docs/poke_system_prompt_inspiration.md`. It is a real architecture
   change (two model runs, handoff, usage limits, how errors surface on
   WhatsApp). Do it when the allowlist is in and we still hate attaching
   MCP on every connected turn, or when we want the personality /
   execution split for product reasons. The subagent still uses the
   enable allowlist — splitting agents does not replace a tight tool
   surface.

Not chosen now: skip-MCP heuristics on greetings (brittle); truncating
Composio descriptions (still 191 tools); Gemini prompt cache of the fat
prefix (cost hedge, not a context fix); reverting Composio (0008
stands).

Decision
1. **Immediate:** session creation **enables** an explicit per-toolkit
   allowlist. Direct Tools stays. **Do not pass a `disable` list** —
   one source of truth. Send/reply and destructive deletes stay out
   because they are not enabled, not because they are disabled. Keep
   `is_denied_composio_tool` only as a test assertion on the enable
   list (catch a mistaken `GMAIL_SEND_EMAIL` in review — `GMAIL_SEND_DRAFT`
   is allowed). Drop `DENIED_TOOLS_BY_TOOLKIT` / `tools_disable_config`
   when the allowlist ships. Every provider people can connect gets a
   list; a toolkit without one is **omitted** from `toolkits=` /
   `tools=` — an empty `enable` ships the whole catalog. Local tools
   (tasks, reminders, Tavily,
   `request_integration`) are unchanged. Locked slugs: task `029`.
2. **Later, accepted direction:** split the WhatsApp chat agent from an
   execution subagent that owns Composio MCP. The allowlist remains the
   subagent’s tool surface. Do not build the split in the same change
   as the allowlist.
3. ADR 0008 session rules still hold: `user_id=str(contact_id)`,
   `mcp=True`, `manage_connections=False`, degrade to local tools on
   attach failure, policy at session creation not `before_execute`.

Consequences
Positive:
- Connected-contact turns stop shipping unused Gmail/Calendar/Sheets/
  Notion catalogs into Gemini.
- Allowlist is stricter than the old deny-list: a Composio rename of a
  dangerous sibling does nothing unless we add the new slug. Email
  **send** is `GMAIL_SEND_DRAFT` only (draft first, ask on WhatsApp,
  then send). `GMAIL_SEND_EMAIL` / `GMAIL_REPLY_*` stay off.
- Leaves a clean door to the agent/subagent split without throwing away
  the tool-surface work.

Negative / tradeoffs:
- We own a slug list. New user-visible capabilities are an allowlist
  edit + test, not “connect the toolkit and get everything.” The test
  matcher yells if `GMAIL_SEND_EMAIL` (direct send, no draft) lands on
  the enable list.
- Composio renames still need the tripwire; an enabled slug that
  disappears breaks that action until we update the list.
- A provider registered without an allowlist would be inert — connect
  works, the agent gets no tools. A unit test asserts `PROVIDERS` and
  the allowlist cover the same slugs.
- Toolkits with deep hierarchies (ClickUp: workspace → space → folder →
  list → task) get a read-first cut instead of the full ID walk: the
  turn's `request_limit` is the binding constraint, not schema bytes.
- `"hey"` from a connected contact used to still pay allowlisted
  schemas. ADR 0012 moved MCP to the exec agent — persona never
  attaches it.

See also: ADR 0008 (Composio-first; this ADR replaces the disable-list
session filter with enable-only), ADR 0003 (Gemini Flash), ADR 0012
(persona/exec split), `app/integrations/composio_policy.py`,
`app/integrations/composio.py` (`create_mcp_session`),
`docs/poke_system_prompt_inspiration.md`, tasks
`024-composio-mcp-agent`, `029-composio-tool-allowlist`,
`034-persona-exec-agents`.
