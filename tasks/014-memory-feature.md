---
id: 014-memory-feature
feature: agent
status: pending
---

# Long-term memory (Pydantic AI Harness)

## Migration preflight

Before implementation, inspect the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Per-contact persistent memory via the Harness `Memory` capability on
`PostgresMemoryStore` — a notebook the **Interaction Agent** updates and
searches across sessions after the new runtime stabilizes. No custom
table or tool: the framework provides bounded injection,
CAS/idempotent writes, and tenant scoping. Tier-1 visible history remains
the `message` table; `execution_event` is never injected as chat history.

## Acceptance criteria
- [ ] `uv add pydantic-ai-harness` (version pinned in `pyproject.toml`;
      harness is 0.x — upgrades go through the eval harness, task 015)
- [ ] `Memory(PostgresMemoryStore(pool), namespace=lambda ctx:
      str(ctx.deps.contact_id))` registered on the Interaction Agent only;
      asyncpg pool is owned by the process that runs Interaction and
      closes cleanly at shutdown
- [ ] Namespace comes only from deps — the model can never address
      another contact's memory (framework guarantee, verified in a
      test)
- [ ] Injection defaults kept (`inject_memory=True`, `max_tokens`
      ~2,000) — bounded snapshot per request, no history bloat
- [ ] System prompt updated to tell the agent what's worth remembering
      (preferences, names, routines — not one-off facts) and to update
      stale entries instead of duplicating
- [ ] Retry safety: a retried turn does not double-apply a memory write
      (including the pre-first-outbound retry from task 040)
- [ ] Eval cases: states a preference → `write_memory` called; memory
      present → next-session reply reflects it; prompt-injection
      attempt inside memory content does not override instructions
- [ ] Manual: "remember I prefer Portuguese" → new session, agent
      answers in Portuguese without being told again

## Out of scope
- pgvector / semantic search (Harness search is lexical — revisit only
  if recall quality complains)
- Multiple notebooks per contact (personal + shared org)
- Custom MemoryStore implementation
- Execution-run journals or named-agent memory

## Depends on
- 047 (new runtime fully cut over).

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from Phase E. Depends on 004.
### [PA] 2026-08-06 11:00 — Injection mechanism pinned
Pydantic AI has no persistent-memory feature by design; tier-1 history
goes through `message_history` (task 004), tier-2 facts through a
dynamic `@agent.system_prompt` with RunContext deps.
### [PA] 2026-08-06 11:10 — Superseded: adopt Harness Memory
The `pydantic_ai_harness.memory.Memory` capability (released, 0.x
harness) replaces the custom `memory` table + `save_memory` tool +
injection code: `PostgresMemoryStore` gives transactional CAS +
idempotency in our existing Supabase Postgres, namespaces resolve from
deps (model cannot cross tenants), injection is budget-bounded and
user-role delimited, and `search_memory` adds recall without vectors.
Task rewritten accordingly.
### [PA] 2026-08-15 15:22 — Rebase on Interaction runtime
Memory belongs to the sole WhatsApp speaker after 047. Detached
Execution receives explicit goals and owned tools, not a persistent
agent notebook.
