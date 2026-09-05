# RUMBO Privacy Attestation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let RUMBO Safe Merge Authority validate privacy for an exact candidate SHA without copying the deny-hash secret from GitHub Actions to the host.

**Architecture:** Pin the reviewed privacy workflow by workflow ID, path, Git blob SHA, and SHA-256. `main` consumes an exact-SHA `pull_request` attestation; `probe/safe-merge-*` consumes an exact-SHA `push` attestation. Local ancestry and public commit-metadata validation remain mandatory and no attestation can write refs by itself.

**Tech Stack:** Python 3.11+, stdlib `unittest`, Git CLI, GitHub CLI/API, Vercel CLI.

**Spec:** `docs/superpowers/specs/2026-09-05-rumbo-privacy-attestation-design.md`

## Global Constraints

- Repository is fixed to `RUMBO-IA/Rumbo`.
- `RUMBO_PRIVACY_DENY_HASHES` remains only in GitHub Actions; never retrieve, print, copy, or persist it.
- Pinned workflow ID: `347174988`.
- Pinned workflow path: `.github/workflows/privacy-gate.yml`.
- Pinned workflow Git blob: `3ab38299fd55f9182e9e10834b04550cc832557a`.
- Pinned workflow SHA-256: `e7fd1d50d447ac9ffcc255766d14cab0f41979d9115607845745943bb8e0d96b`.
- Phase 3 target `main` requires event `pull_request`; probe targets require event `push`.
- Any ambiguous, stale, failed, missing, mismatched, or unreadable evidence yields `SAFE_STOP` before branch writes.
- No force push, generated merge commit, workflow mutation, tag creation, production promotion, or branch-protection weakening.

---

### Task 1: Pin attestation policy and target-to-event mapping

**Files:**
- Modify: `scripts/safe_merge_policy.py`
- Test: `scripts/test_safe_merge_authority.py`
**Interfaces:**
- Produces: immutable `MergePolicy` fields `privacy_workflow_id`, `privacy_workflow_path`, `privacy_workflow_blob`, `privacy_workflow_sha256`.
- Produces: `attestation_event(target: str, merge_policy: MergePolicy) -> str` returning exactly `pull_request` or `push`.

- [ ] **Step 1: Write failing policy tests**

```python
def test_main_uses_pull_request_attestation():
    self.assertEqual(policy.attestation_event("main", policy.DEFAULT_POLICY), "pull_request")

def test_probe_uses_push_attestation():
    self.assertEqual(policy.attestation_event("probe/safe-merge-case", policy.DEFAULT_POLICY), "push")
```

Also assert all four pinned workflow values exactly equal the reviewed values in Global Constraints.

- [ ] **Step 2: Run RED**

Run: `python -m unittest scripts.test_safe_merge_authority.PolicyTests -v`
Expected: FAIL because attestation policy fields/function do not exist.

- [ ] **Step 3: Add only the pinned fields and mapping function**

```python
def attestation_event(target: str, merge_policy: MergePolicy) -> str:
    if target in merge_policy.phase3_targets:
        return "pull_request"
    if target.startswith(merge_policy.probe_prefix):
        return "push"
    raise PolicyError("privacy attestation event is undefined for target")
```

- [ ] **Step 4: Run GREEN and commit**

Run: `python -m unittest scripts.test_safe_merge_authority.PolicyTests -v`
Expected: PASS.
Commit: `feat(safe-merge): pin privacy attestation policy`

### Task 2: Read and validate workflow/run attestation evidence

**Files:**
- Modify: `scripts/safe_merge_evidence.py`
- Test: `scripts/test_safe_merge_authority.py`
**Interfaces:**
- Extends: `pr(number)` normalized snapshot with `head_branch` from `headRefName`.
- Produces: `workflow_blob(candidate_sha: str) -> dict[str, Any]` with `path`, `git_blob`, `sha256`.
- Produces: `privacy_attestation(pr_number: int, candidate_sha: str, target: str, head_branch: str) -> dict[str, Any]` with normalized non-secret run evidence.
- Produces: `commit_metadata_ok(candidate: str) -> bool`, without local deny-hash input.

- [ ] **Step 1: Write failing evidence tests**

Cover: exact workflow digest accepted; blob mismatch rejected; content SHA-256 mismatch rejected; exact successful PR run accepted for `main`; wrong PR/SHA/event rejected; exact successful push run accepted for the exact PR head branch; push run from a branch other than the exact PR head branch rejected; pending/failed/missing run rejected; GitHub read error raises `EvidenceError`.

Example fixture shape:

```python
run = {"id": 123, "workflow_id": 347174988, "event": "pull_request",
       "status": "completed", "conclusion": "success", "head_sha": HEAD,
       "head_branch": "feature", "pull_requests": [{"number": 40}]}
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest scripts.test_safe_merge_authority.EvidenceTests -v`
Expected: FAIL because attestation methods are absent.

- [ ] **Step 3: Implement minimal read-only evidence**

Use `gh api` only. Extend `gh pr view` fields with `headRefName`. Fetch workflow content at the candidate SHA, compute SHA-256 locally, and compare both digests with policy. List runs from `repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs` with `head_sha` and `event`, normalize only required fields, choose the newest matching exact run, and fail closed on mismatch.

`commit_metadata_ok()` must call the existing metadata scanner without requiring `RUMBO_PRIVACY_DENY_HASHES`; it validates public author/committer policy only. Do not execute the full secret-backed privacy script locally.

- [ ] **Step 4: Run GREEN and existing privacy regression**

Run: `python -m unittest scripts.test_safe_merge_authority.EvidenceTests -v`
Run: `python scripts/test_verify_public_privacy.py`
Expected: PASS.
Commit: `feat(safe-merge): consume pinned privacy attestations`

### Task 3: Compose G3 and persist non-secret receipt evidence

**Files:**
- Modify: `scripts/safe_merge_authority.py`
- Modify: `scripts/test_safe_merge_authority.py`
**Interfaces:**
- Consumes: `commit_metadata_ok(candidate)`, `privacy_attestation(pr_number, candidate, target, head_branch)`.
- Produces: G3 evidence containing `target_sha`, `candidate`, and `privacy_attestation` identifiers only.

- [ ] **Step 1: Write failing authority tests**

Add tests proving a successful attestation is still rejected when ancestry fails or metadata fails; attestation failure stops at `PRIVACY_ANCESTRY`; clean dry-run passes without a local deny-hash environment variable; post-write verification rechecks metadata and attestation.

- [ ] **Step 2: Run RED**

Run: `python -m unittest scripts.test_safe_merge_authority.AuthorityGateTests scripts.test_safe_merge_authority.FastForwardTests scripts.test_safe_merge_authority.CliTests -v`
Expected: FAIL against the old `privacy_ok()` contract.

- [ ] **Step 3: Replace G3 secret-local contract**

G3 order must be: target SHA → ancestry → public commit metadata → pinned privacy attestation. Store only normalized attestation identifiers in `GateResult.evidence`. Post-write verification repeats metadata + attestation checks. No fallback to local deny hashes.

- [ ] **Step 4: Run GREEN and inspect receipt**

Run: `python -m unittest scripts.test_safe_merge_authority -v`
Expected: PASS. Create a temporary test receipt and assert its serialized content contains no `RUMBO_PRIVACY_DENY_HASHES`, no environment dump, and no authorization material.
Commit: `feat(safe-merge): require CI privacy attestation in G3`

### Task 4: Real Phase B dry-run against PR #41

**Files:**
- No production code changes unless the real evidence exposes a tested defect.
- Output: content-addressed receipt under a temporary local receipt directory.

- [ ] **Step 1: Wait for exact-head required checks**

Resolve PR #41 head SHA with `gh pr view`; require `privacy=SUCCESS` and `Vercel=SUCCESS` on that exact SHA.

- [ ] **Step 2: Execute real dry-run**

Run: `python scripts/safe_merge_authority.py --pr 41 --head <exact-head> --base main --mode dry-run --receipt-dir <temp-receipts>`.
Expected: `SAFE_MERGE_STATE=DRY_RUN_PASS`, exit 0, zero `git push` commands.

- [ ] **Step 3: Validate receipt and invariants**

Recompute receipt SHA-256, confirm G1-G5 all passed, confirm privacy attestation workflow/run/SHA identifiers match exact head, confirm no secret material, and independently confirm remote `main` is unchanged.

### Task 5: Phase C temporary probe acceptance

**Files:**
- No permanent repository files required.
- [ ] **Step 1: Create isolated probe topology**

Create a temporary base `probe/safe-merge-<nonce>` at current `main`. Create a separate temporary candidate branch from that base, add one harmless approved commit using the configured public Git identity, and push the candidate branch normally so the pinned privacy workflow produces a `push` attestation for the exact candidate SHA and PR head branch.

- [ ] **Step 2: Create a temporary PR targeting the probe base**

The PR exists only to exercise G1 identity binding. It must be same-repository, open, base equal to the probe target, and head equal to the exact candidate SHA. Do not use GitHub server merge methods.

- [ ] **Step 3: Execute authority in probe mode**

After the candidate `push` privacy run succeeds and required `Vercel` status is successful, run the authority with `--mode probe`. Expected: one normal exact-SHA fast-forward push advancing only the probe base, then `MERGED_SAFE` after post-write checks.

- [ ] **Step 4: Adversarial assertions**

Confirm NFF/force attempts remain blocked, tag creation remains blocked, a stale target is rejected, and no command generated a merge/squash/rebase commit.

- [ ] **Step 5: Cleanup**

Delete every temporary probe/candidate remote branch using allowed branch deletion. Close the temporary PR if still open. Confirm zero `probe/safe-merge-*` refs and zero tags remain. Confirm `main` and the live Vercel production deployment are unchanged.

### Task 6: Final regression and audit readiness

**Files:**
- Test: all existing script and scope-binding suites.
- Output: final acceptance evidence/receipt; no `main` integration in this task.

- [ ] **Step 1: Run complete local regression**

Run the full `scripts` unittest discovery, scope-binding tests, commercial coherence validation, security headers validation, `git diff --check`, and `python -m compileall scripts`.

- [ ] **Step 2: Verify remote CI**

Push the final implementation branch normally. Require exact-head `privacy`, `Vercel`, and CodeQL/analysis checks to complete successfully where applicable.

- [ ] **Step 3: Audit immutable invariants**

Confirm branch metadata ruleset remains active, tag creation restriction remains active, branch protection remains intact, production deployment ID/target are unchanged, `main` remains unchanged, no probe refs/tags remain, and public reachable metadata has no known violation.

- [ ] **Step 4: Completion gate**

Invoke `superpowers:verification-before-completion`, record fresh evidence, then invoke `superpowers:finishing-a-development-branch`. Do not integrate PR #41 into `main` without a separate explicit integration decision.
