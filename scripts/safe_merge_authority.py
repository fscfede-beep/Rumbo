import argparse
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import safe_merge_evidence as evidence_module
import safe_merge_policy as policy
import safe_merge_receipt as receipt_module


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    evidence: dict[str, Any]


@dataclass(frozen=True)
class MergeOutcome:
    state: str
    failed_gate: str | None
    gates: tuple[GateResult, ...]
    pre_write_target_sha: str | None = None
    live_deployment_before: str | None = None


def _stop(gates, gate: str, evidence: dict[str, Any], *, target_sha=None, live=None) -> MergeOutcome:
    gates.append(GateResult(gate, False, evidence))
    return MergeOutcome("SAFE_STOP", gate, tuple(gates), target_sha, live)


def _post_fail(gates, evidence: dict[str, Any], *, target_sha=None, live=None) -> MergeOutcome:
    gates.append(GateResult("POST_WRITE_VERIFY", False, evidence))
    return MergeOutcome("POST_WRITE_VERIFY_FAILED", "POST_WRITE_VERIFY", tuple(gates), target_sha, live)


def _required_checks(target: str, merge_policy: policy.MergePolicy) -> tuple[str, ...]:
    if target in merge_policy.required_checks:
        return merge_policy.required_checks[target]
    if target.startswith(merge_policy.probe_prefix):
        return merge_policy.required_checks["main"]
    raise policy.PolicyError("required checks are undefined for target")


def evaluate(request: policy.MergeRequest, merge_policy: policy.MergePolicy, evidence, mode: str = "dry-run") -> MergeOutcome:
    gates: list[GateResult] = []
    current_gate = "POLICY"
    target_sha: str | None = None
    live_id: str | None = None
    write_completed = False
    try:
        policy.validate_request(request, merge_policy)
        policy.validate_execution_mode(mode, request.expected_base, merge_policy)

        current_gate = "PR_IDENTITY"
        pr = evidence.pr(request.pr_number)
        identity_ok = (
            pr.get("state") == "OPEN"
            and pr.get("base") == request.expected_base
            and pr.get("head") == request.expected_head_sha
            and bool(pr.get("head_branch"))
            and pr.get("repo") == request.repository
            and not pr.get("cross")
        )
        if not identity_ok:
            return _stop(gates, current_gate, {"reason": "PR identity drift"})
        gates.append(GateResult(current_gate, True, {
            "head": request.expected_head_sha,
            "head_branch": pr.get("head_branch"),
            "base": request.expected_base,
        }))

        current_gate = "REQUIRED_CHECKS"
        states = evidence.checks(request.expected_head_sha)
        required = _required_checks(request.expected_base, merge_policy)
        missing_or_bad = {name: states.get(name, "MISSING") for name in required if states.get(name) != "SUCCESS"}
        if missing_or_bad:
            return _stop(gates, current_gate, {"checks": missing_or_bad})
        gates.append(GateResult(current_gate, True, {"checks": {name: "SUCCESS" for name in required}}))

        current_gate = "PRIVACY_ANCESTRY"
        target_sha = evidence.target_sha(request.expected_base)
        if not evidence.is_ancestor(target_sha, request.expected_head_sha):
            return _stop(gates, current_gate, {"reason": "candidate is not descendant"}, target_sha=target_sha)
        if not evidence.commit_metadata_ok(request.expected_head_sha):
            return _stop(gates, current_gate, {"reason": "public commit metadata failed"}, target_sha=target_sha)
        attestation = evidence.privacy_attestation(
            request.pr_number, request.expected_head_sha, request.expected_base, pr.get("head_branch")
        )
        gates.append(GateResult(current_gate, True, {
            "target_sha": target_sha,
            "candidate": request.expected_head_sha,
            "privacy_attestation": attestation,
        }))

        current_gate = "PRODUCTION_NO_GO"
        vercel = evidence.vercel_state()
        live_id = vercel.get("liveDeployment")
        production_ok = (
            vercel.get("autoAssignCustomDomains") is False
            and vercel.get("commandForIgnoringBuildStep") in (None, "")
            and vercel.get("productionBranch") == "main"
            and bool(live_id)
            and vercel.get("liveTarget") == "production"
        )
        if not production_ok:
            return _stop(gates, current_gate, {"reason": "Vercel production safety drift"}, target_sha=target_sha, live=live_id)
        gates.append(GateResult(current_gate, True, {"liveDeployment": live_id}))

        current_gate = "FAST_FORWARD_ONLY"
        if not evidence.ruleset_ok():
            return _stop(gates, current_gate, {"reason": "branch ruleset drift"}, target_sha=target_sha, live=live_id)
        fresh_target = evidence.target_sha(request.expected_base)
        if fresh_target != target_sha:
            return _stop(gates, current_gate, {"reason": "target changed before write", "fresh_target": fresh_target}, target_sha=target_sha, live=live_id)
        gates.append(GateResult(current_gate, True, {"target_sha": target_sha, "ruleset": "active"}))

        if mode == "dry-run":
            return MergeOutcome("DRY_RUN_PASS", None, tuple(gates), target_sha, live_id)

        evidence.fast_forward(request.expected_base, target_sha, request.expected_head_sha)
        write_completed = True
        current_gate = "POST_WRITE_VERIFY"

        post_target = evidence.target_sha(request.expected_base)
        if post_target != request.expected_head_sha:
            return _post_fail(gates, {"reason": "target SHA mismatch", "observed": post_target}, target_sha=target_sha, live=live_id)

        post_checks = evidence.checks(request.expected_head_sha)
        if any(post_checks.get(name) != "SUCCESS" for name in required):
            return _post_fail(gates, {"reason": "required checks changed after write"}, target_sha=target_sha, live=live_id)
        if not evidence.commit_metadata_ok(request.expected_head_sha):
            return _post_fail(gates, {"reason": "public commit metadata failed after write"}, target_sha=target_sha, live=live_id)
        post_attestation = evidence.privacy_attestation(
            request.pr_number, request.expected_head_sha, request.expected_base, pr.get("head_branch")
        )
        if not evidence.ruleset_ok():
            return _post_fail(gates, {"reason": "ruleset drift after write"}, target_sha=target_sha, live=live_id)
        if not evidence.branch_protection_ok(request.expected_base, required):
            return _post_fail(gates, {"reason": "branch protection drift after write"}, target_sha=target_sha, live=live_id)

        post_vercel = evidence.vercel_state()
        post_prod_ok = (
            post_vercel.get("autoAssignCustomDomains") is False
            and post_vercel.get("commandForIgnoringBuildStep") in (None, "")
            and post_vercel.get("productionBranch") == "main"
            and post_vercel.get("liveDeployment") == live_id
            and post_vercel.get("liveTarget") == "production"
        )
        if not post_prod_ok:
            return _post_fail(gates, {"reason": "Vercel drift after write"}, target_sha=target_sha, live=live_id)

        gates.append(GateResult("POST_WRITE_VERIFY", True, {
            "target": post_target,
            "liveDeployment": live_id,
            "privacy_attestation": post_attestation,
        }))
        return MergeOutcome("MERGED_SAFE", None, tuple(gates), target_sha, live_id)
    except Exception as exc:
        failure = {"reason": "exception", "type": type(exc).__name__}
        if write_completed:
            return _post_fail(gates, failure, target_sha=target_sha, live=live_id)
        return _stop(gates, current_gate, failure, target_sha=target_sha, live=live_id)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def execute_request(
    request: policy.MergeRequest,
    merge_policy: policy.MergePolicy,
    evidence,
    *,
    mode: str,
    receipt_dir: Path,
    clock=_utc_now,
    invocation_id: str | None = None,
):
    invocation = invocation_id or uuid.uuid4().hex
    started_at = clock()
    outcome = evaluate(request, merge_policy, evidence, mode=mode)
    finished_at = clock()
    command_log = getattr(evidence, "command_log", lambda: [])()
    payload = {
        "invocation_id": invocation,
        "started_at": started_at,
        "finished_at": finished_at,
        "repository": request.repository,
        "pr_number": request.pr_number,
        "mode": mode,
        "expected": {"head": request.expected_head_sha, "base": request.expected_base},
        "observed": {"target_before": outcome.pre_write_target_sha, "live_deployment_before": outcome.live_deployment_before},
        "gates": [asdict(gate) for gate in outcome.gates],
        "commands": command_log,
        "state": outcome.state,
        "error_code": outcome.failed_gate,
    }
    path = receipt_module.write_receipt(Path(receipt_dir), payload)
    return outcome, path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RUMBO exact-SHA safe merge authority")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--mode", choices=("dry-run", "probe", "main"), required=True)
    parser.add_argument("--receipt-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    request = policy.MergeRequest(
        pr_number=args.pr,
        expected_head_sha=args.head,
        expected_base=args.base,
        repository=policy.DEFAULT_POLICY.repository,
    )
    root = Path(__file__).resolve().parents[1]
    real_evidence = evidence_module.RealEvidence(root, merge_policy=policy.DEFAULT_POLICY)
    outcome, receipt_path = execute_request(
        request,
        policy.DEFAULT_POLICY,
        real_evidence,
        mode=args.mode,
        receipt_dir=args.receipt_dir,
    )
    print(f"SAFE_MERGE_STATE={outcome.state}")
    print(f"SAFE_MERGE_RECEIPT={receipt_path}")
    return {"DRY_RUN_PASS": 0, "MERGED_SAFE": 0, "SAFE_STOP": 2, "POST_WRITE_VERIFY_FAILED": 3}.get(outcome.state, 4)


if __name__ == "__main__":
    raise SystemExit(main())
