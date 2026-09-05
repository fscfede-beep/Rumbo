---
name: canonical-state-recovery
description: Recover authoritative current state before an agent changes code, data, configuration, or external state.
---

# Canonical State Recovery

Use before consequential work.

1. Identify the authoritative system and exact scope.
2. Record repository/project, branch, exact head/version, files, and environment.
3. Separate CURRENT, HISTORICAL, CANDIDATE, and ARCHIVED state.
4. Record decisions, blockers, negative memory, and next gate.
5. Prefer fresh direct receipts over narratives or stale documents.
6. Label missing evidence NOT_PROVEN or UNRESOLVED.
7. Stop when authority, scope, or current state is ambiguous.

Required output: Objective, Authority, Current state, Decisions, Blockers, Next gate, Unresolved.

Never promote prepared, historical, or candidate material to CURRENT without evidence.

Truth rules:
prepared != executed
executed != verified
candidate != production
historical != current
