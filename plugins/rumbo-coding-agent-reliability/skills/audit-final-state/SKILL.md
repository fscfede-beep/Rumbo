---
name: audit-final-state
description: Independently audit an agent task before accepting its final state.
---

# Audit Final State

Audit:
1. Workspace identity.
2. Canonical authority.
3. Exact current state/head.
4. Scope and permissions.
5. Tool, skill, and dependency provenance.
6. Human approval for consequential actions.
7. Execution receipt.
8. Expected postcondition.
9. Unexpected side effects.
10. Rollback or recovery state.
11. Cross-agent handoff.
12. Drift since start.

Verdict:
- GO: all required controls evidenced.
- CONDITIONAL: blocker has owner and next action/date.
- NO-GO: acceptance would exceed evidence or authorization.

A passing process is not proof of a passing outcome.
