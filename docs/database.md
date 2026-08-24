# Database

Tenant model: every WhatsApp user is a `contact`. Almost every table
has `contact_id` → `contact.id` (`ON DELETE RESTRICT`). App tables use
RLS + revoked PostgREST grants (API/worker use a privileged role).

**Live after task 048:** `contact` (incl. `tz`), `message` (including
Interaction outbound reservations), `job` (background kinds only:
`reminder_due`, `automation_due`, `integration_notify`),
`integration`, `connect_link`, `pending_action`, `task`, `reminder`,
`execution_run`, `execution_event`, and `automation`.
`briefing_state`, `outbound_sweep`, and `outbound_due` were retired.

## ER diagram (product target)

```mermaid
erDiagram
    contact ||--o{ message : has
    contact ||--o{ job : queues
    contact ||--o{ integration : connects
    contact ||--o{ connect_link : mints
    contact ||--o{ task : owns
    contact ||--o{ reminder : schedules
    contact ||--o{ automation : schedules
    contact ||--o{ pending_action : proposes
    contact ||--o{ execution_run : executes
    execution_run ||--o{ execution_event : emits
    contact ||--o{ usage_counter : tracks

    contact {
        bigint id PK
        text phone UK
        text name
        text tz "default America/Sao_Paulo"
        timestamptz last_seen_at
    }

    message {
        bigint id PK
        bigint contact_id FK
        text direction "in | out"
        text body
        text provider_message_id UK
        text account_sid
        text from_address
        text to_address
        int num_media
        text media_url
        text media_content_type
        text wa_id
        text sms_status
        text api_version
        int num_segments
        text profile_name
        uuid interaction_run_id "nullable; outbound idempotency"
        int outbound_sequence "nullable"
        text delivery_state "nullable; reserved | sent | failed"
        timestamptz created_at
    }

    job {
        uuid id PK
        bigint contact_id FK
        text kind "reminder_due | integration_notify | automation_due"
        jsonb payload
        timestamptz run_at
        text status "pending | running | done | dead"
        int attempts
        int max_attempts
        timestamptz locked_at
        timestamptz created_at
    }

    integration {
        uuid id PK
        bigint contact_id FK
        text provider "Composio toolkit slug"
        text external_account_id "Composio ca_… nullable"
        text status "active | revoked"
        timestamptz created_at
        timestamptz updated_at
    }

    connect_link {
        uuid id PK
        bigint contact_id FK
        text provider "toolkit slug (must match URL)"
        text nonce UK
        timestamptz expires_at
        timestamptz used_at
        timestamptz created_at
        timestamptz updated_at
    }

    task {
        uuid id PK
        bigint contact_id FK
        text title
        text status "open | done"
        timestamptz due_at
        timestamptz created_at
        timestamptz updated_at
    }

    reminder {
        uuid id PK
        bigint contact_id FK
        text body
        timestamptz due_at
        timestamptz sent_at
        text status "active | sent | cancelled"
        timestamptz created_at
        timestamptz updated_at
    }

    automation {
        uuid id PK
        bigint contact_id FK
        text name
        text goal
        text rrule
        text timezone
        jsonb required_toolkits
        text status "active | paused | cancelled"
        timestamptz next_run_at
        timestamptz last_run_at
        text last_run_status "succeeded | failed | timed_out | cancelled | skipped"
        timestamptz last_occurrence_at
        uuid last_execution_run_id
        boolean last_run_was_catch_up
        timestamptz created_at
        timestamptz updated_at
    }

    execution_run {
        uuid id PK
        bigint contact_id FK
        text goal
        jsonb toolkit_scope
        text dedupe_key
        text status
        jsonb result
        text error
        boolean cancel_requested
        timestamptz started_at
        timestamptz finished_at
        timestamptz created_at
        timestamptz updated_at
    }

    execution_event {
        uuid id PK
        bigint contact_id FK
        uuid execution_run_id FK
        text kind
        jsonb payload
        timestamptz processed_at
        timestamptz created_at
    }

    pending_action {
        uuid id PK
        bigint contact_id FK
        text kind "send_email | create_event"
        jsonb payload
        text payload_hash
        text status "pending | claimed | executed | cancelled | expired | failed"
        text created_turn_id "nullable"
        uuid source_interaction_run_id "nullable"
        uuid source_execution_run_id "nullable FK"
        timestamptz expires_at
        timestamptz created_at
        timestamptz updated_at
    }

    usage_counter {
        bigint contact_id PK_FK
        date date PK
        int llm_tokens
        int tool_calls
    }
```

## Quick reference

| Table | Purpose |
|---|---|
| `contact` | Tenant (WhatsApp user) |
| `message` | Chat history (in/out) |
| `job` | Durable worker queue (not user todos). Kinds: `reminder_due`, `integration_notify`, `automation_due`. Ordinary inbound conversation work never enters it. Every row has `contact_id`. `automation_due` has at most one pending row per automation (`uq_job_pending_automation_due`). |
| `integration` | Composio connected-account pointer per provider (`external_account_id`) |
| `connect_link` | One-time signed connect links (`provider` + nonce) |
| `task` | User todo checklist (silent until asked) |
| `reminder` | Timed WhatsApp ping |
| `automation` | RRULE recurring goal that may run owned tools; catch-up is at most one missed occurrence |
| `pending_action` | Exact confirm-before-send/create staging and terminal audit |
| `execution_run` | Detached Execution lifecycle/audit; not a queue |
| `execution_event` | Internal Execution result events; not chat messages |
| `usage_counter` | Per-contact daily LLM/tool caps |
| *(harness)* | Long-term memory, namespaced by `contact_id` |

**`task` vs `reminder` vs `automation`:** task = written down on a
list; reminder = system interrupts with stored text; automation =
scheduled goal that may use tools and re-enter Interaction. They are
separate domain objects.
