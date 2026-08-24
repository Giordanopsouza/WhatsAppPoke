---
id: 007-integration-table
feature: integrations
status: done
---

# Integration table + token encryption

## Scope
Alembic migration for the `integration` table (OAuth credentials per
contact per provider) and the Fernet encrypt/decrypt helpers used
before any token touches the database.

## Acceptance criteria
- [x] Migration: `integration` (`id` pk, `contact_id` FK following the
      existing `ON DELETE` convention, `provider` text,
      `refresh_token_enc` text, `scopes` text[], `status` text check in
      (`active`, `revoked`), timestamps, unique(`contact_id`,
      `provider`))
- [x] RLS enabled + Data API grants revoked, mirroring `28b0ac108edc`
- [x] `app/crypto.py`: `encrypt_token` / `decrypt_token` via Fernet;
      `FERNET_KEY` validated in `app/config.py` at boot
- [x] Round-trip check: encrypt → store → read → decrypt returns the
      original string; a wrong key fails loudly
- [x] No code path stores a plaintext token (grep-able invariant)

## Out of scope
- OAuth endpoints and the connect flow (task 008)
- Google API client (task 009)
- Supabase Vault / KMS (Fernet is the MVP choice, see plan.md)

## Log
### [PA] 2026-08-05 15:45 — Grooming
Created from `docs/plan.md` Phase B.
### [SWE] 2026-08-07 13:50 — Start
Implementing Alembic migration `3d6698e2f6bd_create_integration_table`
plus Fernet helpers and `FERNET_KEY` boot validation.
### [SWE] 2026-08-07 14:00 — Complete
Migration applied (`183da715dd33` → `3d6698e2f6bd`). Verified on
Supabase: table shape, `ON DELETE RESTRICT`, unique(contact_id,
provider), RLS on, anon/authenticated grants empty. Crypto unit tests
+ DB encrypt→store→read→decrypt round-trip pass.
