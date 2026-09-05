---
name: execute-verify-close
description: Execute an authorized bounded action, verify the actual effect, and close only with evidence.
---

# Execute Verify Close

Use only when exact target, intended effect, authorization, rollback/no-effect path, and verification criterion are known.

State machine:
READY -> AUTHORIZED -> EXECUTED -> VERIFIED -> CLOSED

Failure path:
EXECUTED -> FAILED -> RECONCILE

Never infer success from process exit, queue acceptance, UI feedback, model output, or a prepared artifact.

Verify:
- exact target
- expected postcondition
- unintended changes
- execution/effect receipt
- final authoritative state
- rollback/no-effect status

Use NOT_PROVEN when the effect cannot be independently evidenced. Only mark CLOSED when required evidence exists.
