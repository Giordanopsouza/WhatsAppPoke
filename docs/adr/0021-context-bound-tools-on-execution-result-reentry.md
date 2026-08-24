0021. Context-bound tools on Execution result re-entry
Status: Accepted
Date: 2026-08-19

Context
An Execution result re-enters Interaction as an internal event, not as a new
user message. Runtime guards currently block dispatch and confirmation, but
those tools remain visible to the model; repeated unavailable results can
consume the request limit without producing the required user-facing reply.
Tool failures also need a terminal conversational outcome without allowing
Interaction to restart work autonomously.

Decision
Interaction receives an explicit event kind and exposes tools by context.

- User inbound may dispatch Execution and confirm pending actions.
- Execution result re-entry may send a message, wait, or request an integration
  link; dispatch and confirmation tools are absent from the model schema.
- Existing runtime guards remain as fail-closed protection.
- While an Execution is active, model-correctable arguments may use bounded
  `ModelRetry`, and explicitly safe reads may retry at their request boundary.
  A write with an unknown outcome is never retried automatically.
- Once Execution emits its terminal event, Interaction only communicates the
  result or asks for missing input. A later user inbound is required to start
  new work or confirm a pending action.

Consequences
The model cannot loop on tools that are invalid for the event, while code guards
still prevent external effects if tool filtering regresses. Failed Executions
remain visible to the person without creating recursive runs. This adds no new
agent, queue, or long-lived Interaction process.

Task 051 implements this decision. Whole-Execution retry chains remain separate
work under ADR 0018 and task 050.
