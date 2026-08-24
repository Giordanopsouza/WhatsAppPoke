---
id: 010-gmail-read-tools
feature: integrations
status: done
---

# Gmail read tools

## Scope
Read-only Gmail tools on the shared Google client: unread listing,
search, and message reading with aggressive truncation — email bodies
are huge and WhatsApp replies must stay short.

## Acceptance criteria
- [x] Tools: `list_unread`, `search_email(query)`, `read_email(id)`
- [x] `list_unread`: Primary inbox only (`category:primary is:unread`);
      fetch at least 10 unread (cap ≥ 10). Social / Promotions / other
      tabs are out of this tool — use `search_email` if needed
- [x] Body parse from Gmail `Message.payload`: walk `parts` (and nested
      multipart); decode `body.data` as base64url. Prefer `text/plain`;
      if absent, use `text/html` with tags stripped. Truncate ~1k chars.
      Listings keep only from/subject/date/snippet (no body)
- [x] Tool results sized so a full turn stays within the existing
      history/token budget
- [x] Agent summarizes rather than pasting — system prompt guidance for
      email answers (short, offer to read the full one)
- [x] Fixture-based tests with mock Gmail API responses covering
      plain-only, HTML-only, and multipart (`text/plain` + `text/html`)

## Out of scope
- `send_email` (task 011)
- Attachment handling
- Label/folder management
- Listing Social / Promotions / Updates as part of `list_unread`

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from `docs/plan.md` Phase C. Depends on 009.
### [PA] 2026-08-07 21:17 — Grooming
`list_unread` scoped to Primary only; unread fetch cap ≥ 10.
### [PA] 2026-08-07 21:20 — Grooming
Body parse rules: base64url parts, prefer plain over HTML strip;
fixtures for plain / HTML / multipart.
### [SWE] 2026-08-07 21:25 — Start
Implementing Gmail read tools on branch `010-gmail-read-tools`.
### [SWE] 2026-08-07 21:40 — Done
Extended `app/integrations/google.py` with Gmail list/search/read,
body parse + ~1k truncate; tools `list_unread` / `search_email` /
`read_email`; system prompt email guidance; fixture tests for plain /
HTML / multipart. `pytest` passed.
