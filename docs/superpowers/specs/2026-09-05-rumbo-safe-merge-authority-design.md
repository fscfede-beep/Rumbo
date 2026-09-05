# RUMBO Safe Merge Authority — Design

Date: 2026-09-05
Status: Approved architecture, implementation not started
Repository: `RUMBO-IA/Rumbo`

## Objective

Create a host-local, fail-closed integration authority that can promote an already-reviewed pull-request head into an approved target branch without asking GitHub to synthesize a new commit.

The authority must preserve the exact audited commit SHA and must never use rebase merge, squash merge, merge-commit synthesis, force-push, tag creation, deployment promotion, or direct GitHub connector commit creation.

## Success condition

Given a PR number, expected head SHA, and target branch, the authority either:

1. proves all gates and performs one fast-forward-only branch update to that exact SHA; or
2. performs no write and emits a structured `SAFE_STOP` receipt explaining the failed gate.

A successful run is complete only after separate post-write verification confirms the remote target SHA, required GitHub checks, privacy ancestry, Vercel production separation, and absence of metadata violations.

## Non-goals

- No production deployment or traffic promotion.
- No GitHub ruleset, branch-protection, or Vercel configuration changes during a merge run.
- No bypass actor, admin override, force push, history rewrite, tag, release, or generated merge commit.
- No attempt to purge unreachable Git objects from GitHub storage.
- No automatic approval of business/product changes.

## Authority boundary

The only write authority is the authorized Windows host Git client using the repository-scoped public identity and pre-push guard. GitHub connector operations remain read/status/comment/PR-control tools; commit-producing connector operations are not used.

The authority operates only on `RUMBO-IA/Rumbo`. Repository identity and origin URL are fixed policy. Phase 3 initially permits only `main`; any additional target branch requires an explicit policy change and new acceptance evidence.

## Inputs

Required inputs:

- PR number.
- Expected PR head SHA.
- Expected base branch.
- Repository full name fixed to `RUMBO-IA/Rumbo`.

Optional execution metadata may include operator note and receipt destination, but cannot change safety policy.

## Gate chain

### G1 — PR_IDENTITY

Verify the PR exists, is open, targets the expected base branch, has the exact expected head SHA, and originates from the expected repository. Reject stale, closed, merged, retargeted, or drifted PR state.

### G2 — REQUIRED_CHECKS

Resolve the exact head SHA and require all configured mandatory contexts for the target branch to be successful. For current `main`, this includes `privacy` and `Vercel`. Pending, missing, neutral, skipped where success is required, or failed checks produce `SAFE_STOP`.

### G3 — PRIVACY_ANCESTRY

Fetch current remote refs, verify the candidate SHA metadata against the repository privacy allowlist, and execute the repository privacy verifier in all-refs mode. The candidate must be a descendant of the current target tip; otherwise stop.

### G4 — PRODUCTION_NO_GO

Read Vercel project state and current live-domain deployment. Require production auto-assignment to be disabled, the ignore-build override to be unset, and production branch configuration to remain `main`. Record the live deployment ID before the Git write. The authority never invokes a Vercel promotion action. Any unexpected production configuration drift produces `SAFE_STOP`.

### G5 — FAST_FORWARD_ONLY

Immediately before the write, re-fetch the target branch and re-check that its tip equals the value used by prior gates. Then update only `refs/heads/<target>` to the exact candidate SHA using a normal fast-forward push. No `--force`, no GitHub merge API, and no intermediate commit are permitted.

## TOCTOU protection

The authority treats every external read as time-sensitive. The target branch tip and PR head are revalidated immediately before mutation. If either changed after earlier validation, the run stops and must restart from G1.

The push itself must rely on Git's fast-forward semantics plus the active repository ruleset. A concurrent remote update therefore fails closed instead of being overwritten.

## Post-write verification

After a successful push, independently re-read:

- remote target SHA;
- target commit author and committer metadata;
- branch protection and repository ruleset state;
- required status/check state associated with the integrated SHA;
- all reachable public-ref metadata;
- Vercel project production settings;
- the live production domain deployment ID.

Success requires the target SHA to equal the requested head SHA and the live production deployment ID to remain unchanged.

## Receipt model

Every invocation writes one immutable JSON receipt containing: invocation ID, timestamps, repository, PR, expected and observed SHAs, each gate result, commands/actions performed, exit codes, post-write evidence, final state (`MERGED_SAFE`, `SAFE_STOP`, or `POST_WRITE_VERIFY_FAILED`), and a SHA-256 digest of the canonical receipt payload. Receipts are written once to a content-addressed filename derived from the digest and are never overwritten.

## Components

1. `scripts/safe_merge_authority.py` — orchestration and gate state machine.
2. `scripts/safe_merge_policy.py` — immutable repository policy, allowed repository/branch, required checks, and forbidden actions.
3. `scripts/safe_merge_evidence.py` — subprocess/API evidence collection with normalized records.
4. `scripts/safe_merge_receipt.py` — canonical JSON serialization and SHA-256 receipt digest.
5. `scripts/test_safe_merge_authority.py` — deterministic unit and integration tests using temporary Git repositories and fixture responses.

The executable must keep policy separate from evidence collection so tests can prove decisions without mutating real remotes.

## Error handling

All exceptions at or before the write boundary convert to `SAFE_STOP`; stack traces may be recorded locally but must not expose secrets in receipts. A push failure is never retried automatically because a concurrent update may have changed authority state. Verification failure after a successful fast-forward is reported as `POST_WRITE_VERIFY_FAILED` and must not trigger rollback or history rewrite.

## Testing strategy

Implementation follows red-green-refactor. Required adversarial cases include:

- wrong repository or remote;
- stale PR head;
- wrong base branch;
- missing, pending, or failed required check;
- privacy metadata violation;
- candidate not descendant of target;
- target branch changes between initial validation and pre-write check;
- Vercel production configuration drift;
- attempted force/non-branch/tag action rejected by policy;
- normal fast-forward success to an exact SHA;
- successful write followed by separate receipt verification;
- simulated post-write verification failure without rollback.

A real remote probe, if used, must target temporary non-production branches only and must leave zero probe refs afterward.

## Rollout

Phase 1 is dry-run only: evaluate all gates and generate receipts without writing. Phase 2 permits writes only to temporary non-production branches for adversarial validation. Phase 3 enables the authority for an explicitly named protected target branch after Phase 1 and Phase 2 pass.

No phase changes Vercel traffic policy. Promotion of a deployment remains a separate, explicit operation outside this authority.

## Acceptance criteria

The design is satisfied only when fresh evidence proves all of the following:

- the full automated test suite passes with zero failures;
- a prohibited non-fast-forward operation is blocked;
- a privacy-invalid candidate is blocked before mutation;
- a stale or concurrent-update scenario is blocked;
- a temporary-branch fast-forward succeeds only to the exact approved SHA;
- the resulting commit SHA is unchanged from the reviewed head;
- no GitHub server-generated commit is introduced by the authority;
- no tags or non-branch refs are created;
- no temporary remote refs remain after probes;
- receipts are deterministic, parseable, and digest-verifiable;
- current `main` remains unchanged during development and probe phases;
- the live production deployment remains unchanged during development and probe phases.

## Operational rule after acceptance

For this repository, PR integration is defined as promotion of a pre-existing audited commit, not creation of a merge artifact. The safe path is therefore exact-SHA fast-forward under server-side metadata and non-fast-forward rules, followed by separate verification and evidence receipt.

Any future request that requires history rewriting, generated merge commits, tag creation, ruleset bypass, or production deployment is outside this authority and must fail closed pending a separate explicit design and authorization.
