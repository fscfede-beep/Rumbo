# RUMBO Safe Merge Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a host-local, fail-closed authority that integrates an already-audited PR head by exact-SHA fast-forward only and emits independently verifiable receipts.

**Architecture:** Python standard-library modules under `scripts/` separate immutable policy, evidence collection, receipt generation, and orchestration. All external GitHub/Vercel/Git reads are injected behind an evidence interface so the gate state machine is deterministic under tests; only the real adapter may invoke `gh`, `git`, and `vercel`.

**Tech Stack:** Python 3.11+, `unittest`, Git 2.55+, GitHub CLI, Vercel CLI 59+, SHA-256 canonical JSON receipts.

**Spec:** `docs/superpowers/specs/2026-09-05-rumbo-safe-merge-authority-design.md`

## Global Constraints

- Repository is fixed to `RUMBO-IA/Rumbo`.
- Phase 3 target is initially `main`; other protected targets require a policy change and new evidence.
- Never use rebase merge, squash merge, merge-commit synthesis, force-push, tag creation, release creation, or GitHub connector commit writes.
- Production traffic promotion is outside this authority; Vercel auto-assignment must remain disabled.
- `commandForIgnoringBuildStep` must remain unset and Vercel production branch must remain `main`.
- Every failure before the write boundary returns `SAFE_STOP`; post-write verification failure returns `POST_WRITE_VERIFY_FAILED` without rollback.
- Every successful mutation is a normal fast-forward push of the exact reviewed SHA.
- Implementation is TDD: observe RED, make the minimum GREEN change, refactor only while green.

---
### Task 1: Immutable policy and request validation

**Files:**
- Create: `scripts/safe_merge_policy.py`
- Test: `scripts/test_safe_merge_authority.py`

**Interfaces:**
- Produces: `MergePolicy`, `MergeRequest`, `PolicyError`, `validate_request(request, policy)`, `validate_execution_mode(mode, target, policy)`.
- `MergePolicy` fixes repository, allowed targets, required checks, Vercel project/scope/domain, and forbidden ref prefixes.

- [ ] **Step 1: Write failing policy tests**

```python
class PolicyTests(unittest.TestCase):
    def test_main_request_is_allowed(self):
        req = policy.MergeRequest(40, "a" * 40, "main", "RUMBO-IA/Rumbo")
        policy.validate_request(req, policy.DEFAULT_POLICY)

    def test_non_main_target_is_rejected(self):
        with self.assertRaises(policy.PolicyError):
            policy.validate_execution_mode("main", "release", policy.DEFAULT_POLICY)
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest scripts.test_safe_merge_authority.PolicyTests -v`
Expected: import/module failure because `safe_merge_policy.py` does not exist.
- [ ] **Step 3: Implement the minimum policy**

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class MergeRequest:
    pr_number: int
    expected_head_sha: str
    expected_base: str
    repository: str

@dataclass(frozen=True)
class MergePolicy:
    repository: str
    phase3_targets: frozenset[str]
    probe_prefix: str
    required_checks: dict[str, tuple[str, ...]]
    vercel_project: str
    vercel_scope: str
    live_domain: str

class PolicyError(ValueError):
    pass

DEFAULT_POLICY = MergePolicy("RUMBO-IA/Rumbo", frozenset({"main"}), "probe/safe-merge-", {"main": ("privacy", "Vercel")}, "rumbo-ia-publica", "agent-ai-ingenieria", "rumbo.verso.fans")
```

`validate_request` rejects repository drift, non-40-hex SHA, and non-positive PR numbers. `validate_execution_mode(mode, target, policy)` allows `main` only for configured Phase 3 targets, allows `probe` only when `target.startswith("probe/safe-merge-")`, and never allows any other mode/target combination.

- [ ] **Step 4: Run GREEN and commit**

Run: `python -m unittest scripts.test_safe_merge_authority.PolicyTests -v`
Expected: PASS.
Commit: `git commit -am "feat(safe-merge): add immutable merge policy"` after adding the new files.
### Task 2: Canonical immutable receipts

**Files:**
- Create: `scripts/safe_merge_receipt.py`
- Modify: `scripts/test_safe_merge_authority.py`

**Interfaces:**
- Produces: `canonical_payload(data) -> bytes`, `receipt_digest(data) -> str`, `write_receipt(directory, data) -> Path`.
- Receipt files are named `<sha256>.json` and created with exclusive mode so they cannot be overwritten.

- [ ] **Step 1: Write failing receipt tests**

```python
class ReceiptTests(unittest.TestCase):
    def test_digest_is_order_invariant(self):
        self.assertEqual(receipt.receipt_digest({"b": 2, "a": 1}), receipt.receipt_digest({"a": 1, "b": 2}))

    def test_receipt_is_content_addressed_and_immutable(self):
        with tempfile.TemporaryDirectory() as td:
            path = receipt.write_receipt(Path(td), {"state": "SAFE_STOP"})
            self.assertEqual(path.stem, receipt.receipt_digest({"state": "SAFE_STOP"}))
            with self.assertRaises(FileExistsError):
                receipt.write_receipt(Path(td), {"state": "SAFE_STOP"})
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest scripts.test_safe_merge_authority.ReceiptTests -v`
Expected: missing receipt module/functions.

- [ ] **Step 3: Implement canonical JSON and exclusive write**

Use `json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`, SHA-256, `mkdir(parents=True, exist_ok=True)`, and `open(path, "xb")`.

- [ ] **Step 4: Run GREEN and commit**

Run the ReceiptTests and then `python -m unittest scripts.test_safe_merge_authority -v`.
Commit: `git commit -am "feat(safe-merge): add immutable evidence receipts"`.
### Task 3: Read-only evidence adapter

**Files:**
- Create: `scripts/safe_merge_evidence.py`
- Modify: `scripts/test_safe_merge_authority.py`

**Interfaces:**
- Produces: `CommandResult`, `EvidenceError`, `RealEvidence`, and a test double protocol with methods `pr()`, `checks()`, `target_sha()`, `is_ancestor()`, `privacy_ok()`, `vercel_state()`, `ruleset_ok()`.
- `RealEvidence` invokes only read commands plus `git fetch`; no push method exists in this task.

- [ ] **Step 1: Write failing evidence parsing tests**

```python
class FakeRunner:
    def __init__(self, responses):
        self.responses = responses

    def run(self, args, **_kwargs):
        args = tuple(args)
        for prefix, stdout in self.responses.items():
            if args[:len(prefix)] == prefix:
                return evidence.CommandResult(args, 0, stdout, "")
        return evidence.CommandResult(args, 1, "", "unexpected command")

class EvidenceTests(unittest.TestCase):
    def test_pr_snapshot_normalizes_exact_fields(self):
        payload = '{"number":40,"state":"OPEN","baseRefName":"main","headRefOid":"' + "a"*40 + '","headRepository":{"nameWithOwner":"RUMBO-IA/Rumbo"},"isCrossRepository":false,"statusCheckRollup":[]}'
        fake = FakeRunner({("gh", "pr", "view"): payload})
        ev = evidence.RealEvidence(ROOT, fake)
        self.assertEqual(ev.pr(40)["base"], "main")

    def test_nonzero_command_fails_closed(self):
        with self.assertRaises(evidence.EvidenceError):
            evidence.parse_json_result(evidence.CommandResult(("gh",), 1, "", "boom"))
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest scripts.test_safe_merge_authority.EvidenceTests -v`.
Expected: missing evidence module.

- [ ] **Step 3: Implement read commands exactly**

`gh pr view <n> -R RUMBO-IA/Rumbo --json number,state,baseRefName,headRefOid,headRepository,isCrossRepository,statusCheckRollup`;
`gh api repos/RUMBO-IA/Rumbo/branches/<target>`; `gh api repos/RUMBO-IA/Rumbo/rulesets/22317339`;
`git fetch --prune origin +refs/heads/*:refs/remotes/origin/*`; `git merge-base --is-ancestor <base> <candidate>`;
`vercel api /v9/projects/rumbo-ia-publica --scope agent-ai-ingenieria --raw`; `vercel inspect rumbo.verso.fans --scope agent-ai-ingenieria --json`.

- [ ] **Step 4: Privacy evidence**

Run `verify_public_privacy.commit_metadata_violations(candidate, deny)` in-process and execute `scripts/verify_public_privacy.py` with `RUMBO_PRIVACY_COMMIT_SHA=<candidate>` and `RUMBO_PRIVACY_SCAN_ALL_REFS=1`; missing deny hashes is an evidence failure, never an allow.

- [ ] **Step 5: Run GREEN and commit**

Run EvidenceTests plus existing privacy tests.
Commit: `git commit -am "feat(safe-merge): add read-only evidence adapter"`.
### Task 4: Pure gate state machine and dry-run mode

**Files:**
- Create: `scripts/safe_merge_authority.py`
- Modify: `scripts/test_safe_merge_authority.py`

**Interfaces:**
- Produces: `GateResult`, `MergeOutcome`, `evaluate(request, policy, evidence, mode="dry-run") -> MergeOutcome`.
- `evaluate` calls `validate_request` and `validate_execution_mode`; probe targets inherit the `main` required-check set while Phase 3 targets use their configured set.
- No external command is called directly by `evaluate`; all observations come from the injected evidence object.

- [ ] **Step 1: Write failing gate tests and deterministic evidence double**

```python
BASE = "1" * 40
HEAD = "2" * 40

def req(head=HEAD, base="main", repository="RUMBO-IA/Rumbo"):
    return policy.MergeRequest(40, head, base, repository)

class FakeEvidence:
    def __init__(self, *, head=HEAD, base="main", repo="RUMBO-IA/Rumbo", checks=None,
                 privacy=True, ancestor=True, vercel=None, target_sequence=None, ruleset=True):
        self.head, self.base, self.repo = head, base, repo
        self.check_states = checks or {"privacy": "SUCCESS", "Vercel": "SUCCESS"}
        self.privacy, self.ancestor, self.ruleset = privacy, ancestor, ruleset
        self.vercel = vercel or {"autoAssignCustomDomains": False, "commandForIgnoringBuildStep": None,
                                 "productionBranch": "main", "liveDeployment": "dpl_live"}
        self.targets = list(target_sequence or [BASE, BASE, HEAD])
        self.push_calls = []
    def pr(self, _n): return {"state": "OPEN", "base": self.base, "head": self.head, "repo": self.repo, "cross": False}
    def checks(self, _sha): return dict(self.check_states)
    def target_sha(self, _target): return self.targets.pop(0) if len(self.targets) > 1 else self.targets[0]
    def is_ancestor(self, _base, _candidate): return self.ancestor
    def privacy_ok(self, _candidate): return self.privacy
    def vercel_state(self): return dict(self.vercel)
    def ruleset_ok(self): return self.ruleset
    def fast_forward(self, target, expected_old, candidate):
        self.push_calls.append((target, expected_old, candidate)); return {"returncode": 0}

class AuthorityGateTests(unittest.TestCase):
    def test_stale_head_stops_at_g1(self):
        out = authority.evaluate(req(head="a" * 40), policy.DEFAULT_POLICY, FakeEvidence(head="b" * 40))
        self.assertEqual((out.state, out.failed_gate), ("SAFE_STOP", "PR_IDENTITY"))

    def test_wrong_repository_stops_at_g1(self):
        out = authority.evaluate(req(), policy.DEFAULT_POLICY, FakeEvidence(repo="other/repo"))
        self.assertEqual(out.failed_gate, "PR_IDENTITY")

    def test_missing_required_check_stops_at_g2(self):
        out = authority.evaluate(req(), policy.DEFAULT_POLICY, FakeEvidence(checks={"privacy": "SUCCESS"}))
        self.assertEqual(out.failed_gate, "REQUIRED_CHECKS")

    def test_privacy_violation_stops_at_g3(self):
        out = authority.evaluate(req(), policy.DEFAULT_POLICY, FakeEvidence(privacy=False))
        self.assertEqual(out.failed_gate, "PRIVACY_ANCESTRY")

    def test_non_descendant_stops_at_g3(self):
        out = authority.evaluate(req(), policy.DEFAULT_POLICY, FakeEvidence(ancestor=False))
        self.assertEqual(out.failed_gate, "PRIVACY_ANCESTRY")

    def test_vercel_drift_stops_at_g4(self):
        bad = {"autoAssignCustomDomains": True, "commandForIgnoringBuildStep": None, "productionBranch": "main", "liveDeployment": "dpl_live"}
        out = authority.evaluate(req(), policy.DEFAULT_POLICY, FakeEvidence(vercel=bad))
        self.assertEqual(out.failed_gate, "PRODUCTION_NO_GO")
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest scripts.test_safe_merge_authority.AuthorityGateTests -v`.

- [ ] **Step 3: Implement G1-G5 evaluation without writes**

Each successful gate appends `GateResult(name, passed=True, evidence=<normalized dict>)`. The first failure returns immediately with `SAFE_STOP`; dry-run success returns `DRY_RUN_PASS`, not `MERGED_SAFE`.

- [ ] **Step 4: Run GREEN and commit**

Run AuthorityGateTests and the full safe-merge test module.
Commit: `git commit -am "feat(safe-merge): implement fail-closed gate engine"`.
### Task 5: Exact-SHA fast-forward writer and TOCTOU barrier

**Files:**
- Modify: `scripts/safe_merge_evidence.py`
- Modify: `scripts/safe_merge_authority.py`
- Modify: `scripts/test_safe_merge_authority.py`

**Interfaces:**
- Adds: `RealEvidence.fast_forward(target: str, expected_old: str, candidate: str) -> CommandResult`.
- Writer may execute only `git push origin <candidate>:refs/heads/<target>` after a fresh fetch and equality check of the observed target tip.

- [ ] **Step 1: Write failing write-boundary tests**

```python
class FastForwardTests(unittest.TestCase):
    def test_concurrent_tip_change_blocks_write(self):
        target = "probe/safe-merge-test"
        ev = FakeEvidence(base=target, target_sequence=[BASE, "c" * 40])
        out = authority.evaluate(req(base=target), policy.DEFAULT_POLICY, ev, mode="probe")
        self.assertEqual(out.failed_gate, "FAST_FORWARD_ONLY")
        self.assertEqual(ev.push_calls, [])

    def test_probe_push_uses_exact_candidate_without_force(self):
        target = "probe/safe-merge-test"
        ev = FakeEvidence(base=target, target_sequence=[BASE, BASE, HEAD])
        out = authority.evaluate(req(base=target), policy.DEFAULT_POLICY, ev, mode="probe")
        self.assertEqual(out.state, "MERGED_SAFE")
        self.assertEqual(ev.push_calls, [(target, BASE, HEAD)])
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest scripts.test_safe_merge_authority.FastForwardTests -v`.

- [ ] **Step 3: Implement the real writer**

Before push: `git fetch origin refs/heads/<target>:refs/remotes/origin/<target>` and compare `git rev-parse refs/remotes/origin/<target>` to `expected_old`. Push command must be exactly `git push origin <candidate>:refs/heads/<target>`; reject target strings outside `refs/heads/` policy and never construct `--force` or `+<refspec>`.

- [ ] **Step 4: Post-write verification**

Re-fetch target, require remote SHA equals candidate, metadata remains approved, ruleset remains active/no-bypass/non-fast-forward, and Vercel live deployment ID equals the pre-write ID. Failure returns `POST_WRITE_VERIFY_FAILED` and performs no rollback.

- [ ] **Step 5: Run GREEN and commit**

Run FastForwardTests and the full module.
Commit: `git commit -am "feat(safe-merge): add exact-SHA fast-forward writer"`.
### Task 6: CLI, mode policy, and receipt emission

**Files:**
- Modify: `scripts/safe_merge_authority.py`
- Modify: `scripts/safe_merge_policy.py`
- Modify: `scripts/test_safe_merge_authority.py`

**Interfaces:**
- CLI: `python scripts/safe_merge_authority.py --pr N --head SHA --base BRANCH --mode dry-run|probe|main --receipt-dir PATH`.
- `dry-run` cannot write; `probe` only targets `probe/safe-merge-*`; `main` only targets `main` and requires the fixed production safety gates.

- [ ] **Step 1: Write failing CLI/mode tests**

```python
class CliTests(unittest.TestCase):
    def test_dry_run_never_calls_push(self):
        ev = FakeEvidence()
        out = authority.evaluate(req(), policy.DEFAULT_POLICY, ev, mode="dry-run")
        self.assertEqual(out.state, "DRY_RUN_PASS")
        self.assertFalse(ev.push_calls)

    def test_probe_mode_rejects_main(self):
        with self.assertRaises(policy.PolicyError):
            policy.validate_execution_mode("probe", "main", policy.DEFAULT_POLICY)
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest scripts.test_safe_merge_authority.CliTests -v`.

- [ ] **Step 3: Implement CLI and receipt lifecycle**

Use `argparse`; build one receipt dict from request, gate results, timestamps, command summaries, pre/post Vercel IDs, state, and error code. Do not include environment variables, tokens, raw authorization headers, or full stack traces.

- [ ] **Step 4: Run GREEN and commit**

Run the full safe-merge module and `python -m compileall scripts`.
Commit: `git commit -am "feat(safe-merge): add controlled CLI and receipts"`.

### Task 7: Full regression and temporary remote acceptance probe

**Files:**
- Modify only if tests expose a defect: the five safe-merge files above.
- Evidence output: local content-addressed receipt directory, not tracked by Git.

- [ ] **Step 1: Run repository baseline regression**

Run: `python -m unittest discover -s scripts -p "test_*.py" -v` and the research scope-binding test suite. Expected: zero failures.

- [ ] **Step 2: Execute real Phase 1 dry-run**

Use a currently open PR with exact head/base values read immediately before execution. Expected state: `DRY_RUN_PASS` or a justified `SAFE_STOP`; no remote ref changes.
- [ ] **Step 3: Execute a temporary Phase 2 probe**

Create two temporary `probe/safe-merge-*` branches from the clean `main` tip. Put one approved empty commit on the head branch, open a temporary PR to the probe base, run the authority in `probe` mode, and prove the base advances to exactly the reviewed head SHA. The probe must not target `main`.

- [ ] **Step 4: Adversarial remote checks**

Prove a non-fast-forward attempt is rejected by the active GitHub ruleset; prove a tag creation attempt is rejected; prove the local pre-push guard rejects non-branch refs. Do not weaken rulesets to perform these tests.

- [ ] **Step 5: Cleanup and final audit**

Delete all temporary probe branches, confirm `git ls-remote --heads origin "refs/heads/probe/*"` is empty for this test family, confirm remote tag list unchanged, and re-run reachable-ref metadata audit with zero violations. Confirm `main` is still the exact pre-development SHA during probe phases and Vercel live deployment ID is unchanged.

- [ ] **Step 6: Final verification**

Run: `git diff --check`, `python -m compileall scripts`, full unit tests, privacy gate, commercial coherence gate, and security-header gate. Capture exact counts and exit codes.

- [ ] **Step 7: Commit acceptance evidence**

Commit only source/tests/docs, never receipts containing environment-specific runtime evidence. Commit message: `test(safe-merge): verify fail-closed integration authority`.

## Completion gate

Do not promote the implementation into `main` merely because tests pass. Completion means the implementation branch has green tests, a successful temporary exact-SHA probe, zero residual probe refs, unchanged live production deployment, and an audit report. A separate explicit integration decision can then use the new authority itself after its acceptance evidence is reviewed.
