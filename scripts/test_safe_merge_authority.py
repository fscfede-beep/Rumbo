import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import safe_merge_policy as policy
import safe_merge_evidence as evidence
import safe_merge_authority as authority
import safe_merge_receipt as receipt


class PolicyTests(unittest.TestCase):
    def test_main_request_is_allowed(self):
        request = policy.MergeRequest(40, "a" * 40, "main", "RUMBO-IA/Rumbo")
        policy.validate_request(request, policy.DEFAULT_POLICY)
        policy.validate_execution_mode("main", request.expected_base, policy.DEFAULT_POLICY)

    def test_non_main_target_is_rejected(self):
        with self.assertRaises(policy.PolicyError):
            policy.validate_execution_mode("main", "release", policy.DEFAULT_POLICY)

    def test_probe_prefix_is_required(self):
        policy.validate_execution_mode("probe", "probe/safe-merge-case", policy.DEFAULT_POLICY)
        with self.assertRaises(policy.PolicyError):
            policy.validate_execution_mode("probe", "probe/other", policy.DEFAULT_POLICY)

    def test_main_uses_pull_request_attestation(self):
        self.assertEqual(policy.attestation_event("main", policy.DEFAULT_POLICY), "pull_request")

    def test_probe_uses_push_attestation(self):
        self.assertEqual(policy.attestation_event("probe/safe-merge-case", policy.DEFAULT_POLICY), "push")

    def test_privacy_workflow_identity_is_pinned(self):
        self.assertEqual(policy.DEFAULT_POLICY.privacy_workflow_id, 347174988)
        self.assertEqual(policy.DEFAULT_POLICY.privacy_workflow_path, ".github/workflows/privacy-gate.yml")
        self.assertEqual(policy.DEFAULT_POLICY.privacy_workflow_blob, "3ab38299fd55f9182e9e10834b04550cc832557a")
        self.assertEqual(policy.DEFAULT_POLICY.privacy_workflow_sha256, "e7fd1d50d447ac9ffcc255766d14cab0f41979d9115607845745943bb8e0d96b")


class ReceiptTests(unittest.TestCase):
    def test_digest_is_order_invariant(self):
        self.assertEqual(
            receipt.receipt_digest({"b": 2, "a": 1}),
            receipt.receipt_digest({"a": 1, "b": 2}),
        )

    def test_receipt_is_content_addressed_and_immutable(self):
        with tempfile.TemporaryDirectory() as td:
            payload = {"state": "SAFE_STOP"}
            path = receipt.write_receipt(Path(td), payload)
            self.assertEqual(path.stem, receipt.receipt_digest(payload))
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stored["sha256"], path.stem)
            self.assertEqual(stored["payload"], payload)
            with self.assertRaises(FileExistsError):
                receipt.write_receipt(Path(td), payload)


class FakeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def run(self, args, **_kwargs):
        args = tuple(args)
        self.calls.append(args)
        for prefix, result in self.responses.items():
            if args[:len(prefix)] == prefix:
                if isinstance(result, evidence.CommandResult):
                    return result
                return evidence.CommandResult(args, 0, result, "")
        return evidence.CommandResult(args, 1, "", "unexpected command")


class EvidenceTests(unittest.TestCase):
    def test_pr_snapshot_normalizes_exact_fields(self):
        payload = '{"number":40,"state":"OPEN","baseRefName":"main","headRefName":"feature/head","headRefOid":"' + "a" * 40 + '","headRepository":{"nameWithOwner":"RUMBO-IA/Rumbo"},"isCrossRepository":false,"statusCheckRollup":[]}'
        fake = FakeRunner({("gh", "pr", "view"): payload})
        ev = evidence.RealEvidence(ROOT, fake, policy.DEFAULT_POLICY)
        snapshot = ev.pr(40)
        self.assertEqual(snapshot["base"], "main")
        self.assertEqual(snapshot["head_branch"], "feature/head")
        self.assertEqual(snapshot["repo"], "RUMBO-IA/Rumbo")
        self.assertIn("--json", fake.calls[0])

    def test_nonzero_command_fails_closed(self):
        result = evidence.CommandResult(("gh",), 1, "", "boom")
        with self.assertRaises(evidence.EvidenceError):
            evidence.parse_json_result(result)

    def test_resolve_command_uses_platform_shim_without_changing_arguments(self):
        resolved = evidence.resolve_command(("vercel", "inspect"), which=lambda _name: r"C:\npm\vercel.CMD")
        self.assertEqual(resolved, (r"C:\npm\vercel.CMD", "inspect"))

    def test_vercel_state_normalizes_project_and_live_alias(self):
        project = '{"autoAssignCustomDomains":false,"commandForIgnoringBuildStep":null,"link":{"productionBranch":"main"}}'
        live = '{"id":"dpl_live","target":"production"}'
        fake = FakeRunner({("vercel", "api"): project, ("vercel", "inspect"): live})
        state = evidence.RealEvidence(ROOT, fake, policy.DEFAULT_POLICY).vercel_state()
        self.assertEqual(state["liveDeployment"], "dpl_live")
        self.assertFalse(state["autoAssignCustomDomains"])
        self.assertIsNone(state["commandForIgnoringBuildStep"])
        self.assertEqual(state["productionBranch"], "main")

    def _workflow_payload(self, *, blob=None, content=None):
        raw = content if content is not None else subprocess.check_output(["git", "show", f"HEAD:{policy.DEFAULT_POLICY.privacy_workflow_path}"], cwd=ROOT)
        return json.dumps({
            "sha": blob or policy.DEFAULT_POLICY.privacy_workflow_blob,
            "encoding": "base64",
            "content": base64.b64encode(raw).decode("ascii"),
        })

    def _run_payload(self, run):
        return json.dumps({"workflow_runs": [run]})

    def test_workflow_blob_accepts_both_pinned_digests(self):
        fake = FakeRunner({("gh", "api", f"repos/RUMBO-IA/Rumbo/contents/{policy.DEFAULT_POLICY.privacy_workflow_path}?ref=" + "a" * 40): self._workflow_payload()})
        observed = evidence.RealEvidence(ROOT, fake, policy.DEFAULT_POLICY).workflow_blob("a" * 40)
        self.assertEqual(observed["git_blob"], policy.DEFAULT_POLICY.privacy_workflow_blob)
        self.assertEqual(observed["sha256"], policy.DEFAULT_POLICY.privacy_workflow_sha256)

    def test_workflow_blob_rejects_git_blob_mismatch(self):
        fake = FakeRunner({("gh", "api", f"repos/RUMBO-IA/Rumbo/contents/{policy.DEFAULT_POLICY.privacy_workflow_path}?ref=" + "a" * 40): self._workflow_payload(blob="0" * 40)})
        with self.assertRaises(evidence.EvidenceError):
            evidence.RealEvidence(ROOT, fake, policy.DEFAULT_POLICY).workflow_blob("a" * 40)

    def test_workflow_blob_rejects_sha256_mismatch(self):
        fake = FakeRunner({("gh", "api", f"repos/RUMBO-IA/Rumbo/contents/{policy.DEFAULT_POLICY.privacy_workflow_path}?ref=" + "a" * 40): self._workflow_payload(content=b"changed")})
        with self.assertRaises(evidence.EvidenceError):
            evidence.RealEvidence(ROOT, fake, policy.DEFAULT_POLICY).workflow_blob("a" * 40)

    def test_main_accepts_exact_successful_pull_request_attestation(self):
        run = {"id": 123, "workflow_id": policy.DEFAULT_POLICY.privacy_workflow_id,
               "path": policy.DEFAULT_POLICY.privacy_workflow_path, "event": "pull_request",
               "status": "completed", "conclusion": "success", "head_sha": "a" * 40,
               "head_branch": "feature/head", "run_attempt": 1, "created_at": "2026-09-05T05:00:00Z",
               "pull_requests": [{"number": 40}]}
        fake = FakeRunner({
            ("gh", "api", f"repos/RUMBO-IA/Rumbo/contents/{policy.DEFAULT_POLICY.privacy_workflow_path}?ref=" + "a" * 40): self._workflow_payload(),
            ("gh", "api", "--method", "GET"): self._run_payload(run),
        })
        att = evidence.RealEvidence(ROOT, fake, policy.DEFAULT_POLICY).privacy_attestation(40, "a" * 40, "main", "feature/head")
        self.assertEqual((att["run_id"], att["event"], att["head_sha"]), (123, "pull_request", "a" * 40))

    def test_main_rejects_pull_request_attestation_for_other_pr(self):
        run = {"id": 123, "workflow_id": policy.DEFAULT_POLICY.privacy_workflow_id,
               "path": policy.DEFAULT_POLICY.privacy_workflow_path, "event": "pull_request",
               "status": "completed", "conclusion": "success", "head_sha": "a" * 40,
               "head_branch": "feature/head", "run_attempt": 1, "created_at": "2026-09-05T05:00:00Z",
               "pull_requests": [{"number": 41}]}
        fake = FakeRunner({
            ("gh", "api", f"repos/RUMBO-IA/Rumbo/contents/{policy.DEFAULT_POLICY.privacy_workflow_path}?ref=" + "a" * 40): self._workflow_payload(),
            ("gh", "api", "--method", "GET"): self._run_payload(run),
        })
        with self.assertRaises(evidence.EvidenceError):
            evidence.RealEvidence(ROOT, fake, policy.DEFAULT_POLICY).privacy_attestation(40, "a" * 40, "main", "feature/head")

    def test_probe_accepts_exact_successful_push_attestation_from_pr_head(self):
        run = {"id": 124, "workflow_id": policy.DEFAULT_POLICY.privacy_workflow_id,
               "path": policy.DEFAULT_POLICY.privacy_workflow_path, "event": "push",
               "status": "completed", "conclusion": "success", "head_sha": "a" * 40,
               "head_branch": "probe/safe-merge-head-x", "run_attempt": 1, "created_at": "2026-09-05T05:00:00Z",
               "pull_requests": []}
        fake = FakeRunner({
            ("gh", "api", f"repos/RUMBO-IA/Rumbo/contents/{policy.DEFAULT_POLICY.privacy_workflow_path}?ref=" + "a" * 40): self._workflow_payload(),
            ("gh", "api", "--method", "GET"): self._run_payload(run),
        })
        att = evidence.RealEvidence(ROOT, fake, policy.DEFAULT_POLICY).privacy_attestation(40, "a" * 40, "probe/safe-merge-base-x", "probe/safe-merge-head-x")
        self.assertEqual((att["run_id"], att["event"], att["head_branch"]), (124, "push", "probe/safe-merge-head-x"))

    def test_probe_rejects_push_attestation_from_other_branch(self):
        run = {"id": 124, "workflow_id": policy.DEFAULT_POLICY.privacy_workflow_id,
               "path": policy.DEFAULT_POLICY.privacy_workflow_path, "event": "push",
               "status": "completed", "conclusion": "success", "head_sha": "a" * 40,
               "head_branch": "probe/safe-merge-other", "run_attempt": 1, "created_at": "2026-09-05T05:00:00Z",
               "pull_requests": []}
        fake = FakeRunner({
            ("gh", "api", f"repos/RUMBO-IA/Rumbo/contents/{policy.DEFAULT_POLICY.privacy_workflow_path}?ref=" + "a" * 40): self._workflow_payload(),
            ("gh", "api", "--method", "GET"): self._run_payload(run),
        })
        with self.assertRaises(evidence.EvidenceError):
            evidence.RealEvidence(ROOT, fake, policy.DEFAULT_POLICY).privacy_attestation(40, "a" * 40, "probe/safe-merge-base-x", "probe/safe-merge-head-x")

    def test_attestation_rejects_newest_matching_failed_run(self):
        older = {"id": 1, "workflow_id": policy.DEFAULT_POLICY.privacy_workflow_id, "path": policy.DEFAULT_POLICY.privacy_workflow_path,
                 "event": "pull_request", "status": "completed", "conclusion": "success", "head_sha": "a" * 40,
                 "head_branch": "feature/head", "run_attempt": 1, "created_at": "2026-09-05T04:00:00Z", "pull_requests": [{"number": 40}]}
        newer = dict(older, id=2, conclusion="failure", run_attempt=2, created_at="2026-09-05T05:00:00Z")
        fake = FakeRunner({
            ("gh", "api", f"repos/RUMBO-IA/Rumbo/contents/{policy.DEFAULT_POLICY.privacy_workflow_path}?ref=" + "a" * 40): self._workflow_payload(),
            ("gh", "api", "--method", "GET"): json.dumps({"workflow_runs": [older, newer]}),
        })
        with self.assertRaises(evidence.EvidenceError):
            evidence.RealEvidence(ROOT, fake, policy.DEFAULT_POLICY).privacy_attestation(40, "a" * 40, "main", "feature/head")

    def test_commit_metadata_ok_does_not_require_local_deny_hashes(self):
        self.assertTrue(evidence.RealEvidence(ROOT, FakeRunner({}), policy.DEFAULT_POLICY).commit_metadata_ok("HEAD"))


BASE = "1" * 40
HEAD = "2" * 40


def merge_request(head=HEAD, base="main", repository="RUMBO-IA/Rumbo"):
    return policy.MergeRequest(40, head, base, repository)


class FakeEvidence:
    def __init__(self, *, head=HEAD, base="main", repo="RUMBO-IA/Rumbo", head_branch="feature/head", checks=None,
                 metadata=True, attestation_sequence=None, ancestor=True, vercel=None, target_sequence=None,
                 ruleset=True, branch_protection=True):
        self.head, self.base, self.repo, self.head_branch = head, base, repo, head_branch
        self.check_states = checks if checks is not None else {"privacy": "SUCCESS", "Vercel": "SUCCESS"}
        self.metadata, self.ancestor, self.ruleset = metadata, ancestor, ruleset
        self.attestations = list(attestation_sequence or [True, True])
        self.metadata_calls = 0
        self.attestation_calls = 0
        self.branch_protection = branch_protection
        self.vercel = vercel or {"autoAssignCustomDomains": False, "commandForIgnoringBuildStep": None,
                                 "productionBranch": "main", "liveDeployment": "dpl_live", "liveTarget": "production"}
        self.targets = list(target_sequence or [BASE, BASE, HEAD])
        self.push_calls = []

    def pr(self, _number):
        return {"state": "OPEN", "base": self.base, "head": self.head, "head_branch": self.head_branch,
                "repo": self.repo, "cross": False}

    def checks(self, _sha): return dict(self.check_states)
    def target_sha(self, _target): return self.targets.pop(0) if len(self.targets) > 1 else self.targets[0]
    def is_ancestor(self, _base, _candidate): return self.ancestor
    def commit_metadata_ok(self, _candidate):
        self.metadata_calls += 1
        return self.metadata
    def privacy_attestation(self, pr_number, candidate, target, head_branch):
        self.attestation_calls += 1
        current = self.attestations.pop(0) if len(self.attestations) > 1 else self.attestations[0]
        if not current:
            raise evidence.EvidenceError("attestation unavailable")
        return {"workflow_id": policy.DEFAULT_POLICY.privacy_workflow_id, "run_id": 123,
                "event": policy.attestation_event(target, policy.DEFAULT_POLICY), "head_sha": candidate,
                "head_branch": head_branch, "pr_number": pr_number if target == "main" else None}
    def vercel_state(self): return dict(self.vercel)
    def ruleset_ok(self): return self.ruleset
    def branch_protection_ok(self, _target, _required): return self.branch_protection
    def fast_forward(self, target, expected_old, candidate):
        self.push_calls.append((target, expected_old, candidate))
        return evidence.CommandResult(("git", "push"), 0, "", "")


class AuthorityGateTests(unittest.TestCase):
    def test_stale_head_stops_at_g1(self):
        out = authority.evaluate(merge_request(head="a" * 40), policy.DEFAULT_POLICY, FakeEvidence(head="b" * 40))
        self.assertEqual((out.state, out.failed_gate), ("SAFE_STOP", "PR_IDENTITY"))

    def test_wrong_repository_stops_at_g1(self):
        out = authority.evaluate(merge_request(), policy.DEFAULT_POLICY, FakeEvidence(repo="other/repo"))
        self.assertEqual(out.failed_gate, "PR_IDENTITY")

    def test_missing_required_check_stops_at_g2(self):
        out = authority.evaluate(merge_request(), policy.DEFAULT_POLICY, FakeEvidence(checks={"privacy": "SUCCESS"}))
        self.assertEqual(out.failed_gate, "REQUIRED_CHECKS")

    def test_metadata_violation_stops_at_g3(self):
        out = authority.evaluate(merge_request(), policy.DEFAULT_POLICY, FakeEvidence(metadata=False))
        self.assertEqual(out.failed_gate, "PRIVACY_ANCESTRY")

    def test_attestation_failure_stops_at_g3(self):
        out = authority.evaluate(merge_request(), policy.DEFAULT_POLICY, FakeEvidence(attestation_sequence=[False]))
        self.assertEqual((out.state, out.failed_gate), ("SAFE_STOP", "PRIVACY_ANCESTRY"))

    def test_non_descendant_stops_at_g3(self):
        out = authority.evaluate(merge_request(), policy.DEFAULT_POLICY, FakeEvidence(ancestor=False))
        self.assertEqual(out.failed_gate, "PRIVACY_ANCESTRY")
    def test_vercel_drift_stops_at_g4(self):
        bad = {
            "autoAssignCustomDomains": True,
            "commandForIgnoringBuildStep": None,
            "productionBranch": "main",
            "liveDeployment": "dpl_live",
            "liveTarget": "production",
        }
        out = authority.evaluate(merge_request(), policy.DEFAULT_POLICY, FakeEvidence(vercel=bad))
        self.assertEqual(out.failed_gate, "PRODUCTION_NO_GO")

    def test_target_tip_drift_stops_at_g5(self):
        ev = FakeEvidence(target_sequence=[BASE, "3" * 40])
        out = authority.evaluate(merge_request(), policy.DEFAULT_POLICY, ev)
        self.assertEqual(out.failed_gate, "FAST_FORWARD_ONLY")
        self.assertFalse(ev.push_calls)

    def test_clean_dry_run_passes_without_write(self):
        ev = FakeEvidence(target_sequence=[BASE, BASE])
        out = authority.evaluate(merge_request(), policy.DEFAULT_POLICY, ev)
        self.assertEqual(out.state, "DRY_RUN_PASS")
        self.assertIsNone(out.failed_gate)
        self.assertFalse(ev.push_calls)
        self.assertEqual((ev.metadata_calls, ev.attestation_calls), (1, 1))
        g3 = next(g for g in out.gates if g.name == "PRIVACY_ANCESTRY")
        self.assertEqual(g3.evidence["privacy_attestation"]["run_id"], 123)


class FastForwardTests(unittest.TestCase):
    def test_concurrent_tip_change_blocks_write(self):
        target = "probe/safe-merge-test"
        ev = FakeEvidence(base=target, target_sequence=[BASE, "3" * 40])
        out = authority.evaluate(merge_request(base=target), policy.DEFAULT_POLICY, ev, mode="probe")
        self.assertEqual(out.failed_gate, "FAST_FORWARD_ONLY")
        self.assertFalse(ev.push_calls)

    def test_probe_push_uses_exact_candidate_without_force(self):
        target = "probe/safe-merge-test"
        ev = FakeEvidence(base=target, target_sequence=[BASE, BASE, HEAD])
        out = authority.evaluate(merge_request(base=target), policy.DEFAULT_POLICY, ev, mode="probe")
        self.assertEqual(out.state, "MERGED_SAFE")
        self.assertEqual(ev.push_calls, [(target, BASE, HEAD)])
        self.assertEqual((ev.metadata_calls, ev.attestation_calls), (2, 2))

    def test_post_write_target_mismatch_never_rolls_back(self):
        target = "probe/safe-merge-test"
        ev = FakeEvidence(base=target, target_sequence=[BASE, BASE, "4" * 40])
        out = authority.evaluate(merge_request(base=target), policy.DEFAULT_POLICY, ev, mode="probe")
        self.assertEqual(out.state, "POST_WRITE_VERIFY_FAILED")
        self.assertEqual(ev.push_calls, [(target, BASE, HEAD)])


    def test_post_write_attestation_error_is_post_write_failure(self):
        target = "probe/safe-merge-test"
        ev = FakeEvidence(base=target, target_sequence=[BASE, BASE, HEAD], attestation_sequence=[True, False])
        out = authority.evaluate(merge_request(base=target), policy.DEFAULT_POLICY, ev, mode="probe")
        self.assertEqual((out.state, out.failed_gate), ("POST_WRITE_VERIFY_FAILED", "POST_WRITE_VERIFY"))
        self.assertEqual(ev.push_calls, [(target, BASE, HEAD)])

    def test_post_write_branch_protection_drift_fails_without_rollback(self):
        target = "probe/safe-merge-test"
        ev = FakeEvidence(base=target, target_sequence=[BASE, BASE, HEAD], branch_protection=False)
        out = authority.evaluate(merge_request(base=target), policy.DEFAULT_POLICY, ev, mode="probe")
        self.assertEqual(out.state, "POST_WRITE_VERIFY_FAILED")
        self.assertEqual(ev.push_calls, [(target, BASE, HEAD)])


class CliTests(unittest.TestCase):
    def test_probe_mode_rejects_main(self):
        with self.assertRaises(policy.PolicyError):
            policy.validate_execution_mode("probe", "main", policy.DEFAULT_POLICY)

    def test_execute_request_writes_digest_verified_receipt(self):
        ev = FakeEvidence(target_sequence=[BASE, BASE])
        with tempfile.TemporaryDirectory() as td:
            out, path = authority.execute_request(
                merge_request(),
                policy.DEFAULT_POLICY,
                ev,
                mode="dry-run",
                receipt_dir=Path(td),
                clock=lambda: "2026-09-05T04:30:00Z",
                invocation_id="inv-fixed",
            )
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(out.state, "DRY_RUN_PASS")
            self.assertEqual(stored["payload"]["state"], "DRY_RUN_PASS")
            self.assertEqual(stored["payload"]["invocation_id"], "inv-fixed")
            self.assertEqual(stored["sha256"], receipt.receipt_digest(stored["payload"]))
            serialized = json.dumps(stored, sort_keys=True)
            self.assertIn('"privacy_attestation"', serialized)
            self.assertNotIn("RUMBO_PRIVACY_DENY_HASHES", serialized)
            self.assertFalse(ev.push_calls)
