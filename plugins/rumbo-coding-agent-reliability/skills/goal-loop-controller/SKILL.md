---
name: goal-loop-controller
description: Keep agent work aligned to one measurable goal with explicit gates and stop conditions.
---

# Goal Loop Controller

Run: GOAL -> STATE -> OPTIONS -> NEXT ACTION -> VERIFY -> RECONCILE -> GATE.

For each iteration:
1. State one concrete goal.
2. Read current state before acting.
3. Pick the smallest action that can materially advance the goal.
4. Stay inside authorized scope.
5. Verify independently when possible.
6. Update state and evidence.
7. Stop when the next action needs missing authority, credentials, external approval, or unproven evidence.

Do not repeat unchanged diagnostics, create parallel systems, reopen closed paths without new evidence, or expand scope merely because a path is blocked.

Stop on missing authority, ambiguous state, destructive remediation without explicit approval, missing receipt, or unsatisfied production gate.
