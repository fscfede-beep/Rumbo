import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import safe_merge_policy as policy
import verify_public_privacy

BRANCH_RULESET_ID = 22317339


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class EvidenceError(RuntimeError):
    pass


def resolve_command(args, *, which=shutil.which) -> tuple[str, ...]:
    logical = tuple(args)
    if not logical:
        return logical
    resolved = which(logical[0])
    if not resolved:
        return logical
    return (resolved, *logical[1:])


def parse_json_result(result: CommandResult) -> Any:
    if result.returncode != 0:
        raise EvidenceError(f"command failed ({result.returncode}): {' '.join(result.args)}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise EvidenceError("command returned invalid JSON") from exc


class SubprocessRunner:
    def __init__(self, root: Path):
        self.root = root
        self.results: list[CommandResult] = []

    def run(self, args, *, env=None) -> CommandResult:
        logical_args = tuple(args)
        execution_args = resolve_command(logical_args)
        completed = subprocess.run(
            list(execution_args),
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        result = CommandResult(logical_args, completed.returncode, completed.stdout, completed.stderr)
        self.results.append(result)
        return result


class RealEvidence:
    def __init__(self, root: Path, runner=None, merge_policy=policy.DEFAULT_POLICY):
        self.root = Path(root)
        self.runner = runner or SubprocessRunner(self.root)
        self.policy = merge_policy

    def _json(self, args) -> Any:
        return parse_json_result(self.runner.run(tuple(args)))
    def pr(self, number: int) -> dict[str, Any]:
        data = self._json((
            "gh", "pr", "view", str(number),
            "-R", self.policy.repository,
            "--json", "number,state,baseRefName,headRefName,headRefOid,headRepository,isCrossRepository,statusCheckRollup",
        ))
        head_repo = data.get("headRepository") or {}
        return {
            "number": data.get("number"),
            "state": str(data.get("state", "")).upper(),
            "base": data.get("baseRefName"),
            "head_branch": data.get("headRefName"),
            "head": data.get("headRefOid"),
            "repo": head_repo.get("nameWithOwner"),
            "cross": bool(data.get("isCrossRepository")),
        }

    @staticmethod
    def _check_state(status: str | None, conclusion: str | None) -> str:
        if status and status.lower() != "completed":
            return "PENDING"
        if conclusion is None:
            return "PENDING"
        return conclusion.upper()

    @staticmethod
    def _merge_check_state(old: str | None, new: str) -> str:
        if old is None or old == "SUCCESS":
            return new
        return old
    def checks(self, sha: str) -> dict[str, str]:
        owner, repo = self.policy.repository.split("/", 1)
        check_data = self._json(("gh", "api", f"repos/{owner}/{repo}/commits/{sha}/check-runs"))
        status_data = self._json(("gh", "api", f"repos/{owner}/{repo}/commits/{sha}/status"))
        states: dict[str, str] = {}
        for item in check_data.get("check_runs", []):
            name = item.get("name")
            if not name:
                continue
            state = self._check_state(item.get("status"), item.get("conclusion"))
            states[name] = self._merge_check_state(states.get(name), state)
        for item in status_data.get("statuses", []):
            name = item.get("context")
            if not name:
                continue
            raw = str(item.get("state", "")).upper()
            state = "SUCCESS" if raw == "SUCCESS" else (raw or "PENDING")
            states[name] = self._merge_check_state(states.get(name), state)
        return states

    def _run_ok(self, args, *, allowed=(0,)) -> CommandResult:
        result = self.runner.run(tuple(args))
        if result.returncode not in allowed:
            raise EvidenceError(f"command failed ({result.returncode}): {' '.join(result.args)}")
        return result
    def fetch_public_refs(self) -> None:
        self._run_ok((
            "git", "fetch", "--prune", "origin",
            "+refs/heads/*:refs/remotes/origin/*",
            "+refs/tags/*:refs/tags/*",
        ))

    def target_sha(self, target: str) -> str:
        self.fetch_public_refs()
        result = self._run_ok(("git", "rev-parse", f"refs/remotes/origin/{target}"))
        return result.stdout.strip()

    def is_ancestor(self, base_sha: str, candidate: str) -> bool:
        result = self.runner.run(("git", "merge-base", "--is-ancestor", base_sha, candidate))
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise EvidenceError("git merge-base failed")

    @staticmethod
    def _deny_hashes() -> set[str]:
        raw = os.environ.get("RUMBO_PRIVACY_DENY_HASHES", "")
        values = {item.strip().lower() for item in raw.split(",") if item.strip()}
        if not values or any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in values):
            raise EvidenceError("privacy deny-hash evidence is missing or malformed")
        return values
    def privacy_ok(self, candidate: str) -> bool:
        deny = self._deny_hashes()
        if verify_public_privacy.commit_metadata_violations(candidate, deny):
            return False
        with tempfile.TemporaryDirectory(prefix="rumbo-safe-privacy-") as td:
            candidate_root = Path(td) / "candidate"
            added = self.runner.run(("git", "worktree", "add", "--detach", str(candidate_root), candidate))
            if added.returncode != 0:
                raise EvidenceError("could not create candidate privacy worktree")
            try:
                env = os.environ.copy()
                env["RUMBO_PRIVACY_COMMIT_SHA"] = candidate
                env["RUMBO_PRIVACY_SCAN_ALL_REFS"] = "1"
                check = self.runner.run(
                    (sys.executable, str(candidate_root / "scripts" / "verify_public_privacy.py")),
                    env=env,
                )
                return check.returncode == 0 and "PRIVACY_GATE_PASS" in check.stdout
            finally:
                cleanup = self.runner.run(("git", "worktree", "remove", "--force", str(candidate_root)))
                if cleanup.returncode != 0:
                    raise EvidenceError("candidate privacy worktree cleanup failed")

    def commit_metadata_ok(self, candidate: str) -> bool:
        return not verify_public_privacy.commit_metadata_violations(candidate, set())

    def workflow_blob(self, candidate_sha: str) -> dict[str, Any]:
        owner, repo = self.policy.repository.split("/", 1)
        endpoint = f"repos/{owner}/{repo}/contents/{self.policy.privacy_workflow_path}?ref={candidate_sha}"
        data = self._json(("gh", "api", endpoint))
        git_blob = str(data.get("sha") or "")
        if git_blob != self.policy.privacy_workflow_blob:
            raise EvidenceError("privacy workflow Git blob does not match pinned policy")
        if data.get("encoding") != "base64":
            raise EvidenceError("privacy workflow content encoding is not base64")
        encoded = "".join(str(data.get("content") or "").split())
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise EvidenceError("privacy workflow content is invalid base64") from exc
        digest = hashlib.sha256(raw).hexdigest()
        if digest != self.policy.privacy_workflow_sha256:
            raise EvidenceError("privacy workflow SHA-256 does not match pinned policy")
        return {
            "path": self.policy.privacy_workflow_path,
            "git_blob": git_blob,
            "sha256": digest,
        }

    @staticmethod
    def _run_sort_key(run: dict[str, Any]) -> tuple[str, int, int]:
        try:
            attempt = int(run.get("run_attempt") or 0)
        except (TypeError, ValueError):
            attempt = 0
        try:
            run_id = int(run.get("id") or 0)
        except (TypeError, ValueError):
            run_id = 0
        return (str(run.get("created_at") or ""), attempt, run_id)

    def privacy_attestation(self, pr_number: int, candidate_sha: str, target: str, head_branch: str) -> dict[str, Any]:
        workflow = self.workflow_blob(candidate_sha)
        event = policy.attestation_event(target, self.policy)
        owner, repo = self.policy.repository.split("/", 1)
        endpoint = f"repos/{owner}/{repo}/actions/workflows/{self.policy.privacy_workflow_id}/runs"
        data = self._json((
            "gh", "api", "--method", "GET", endpoint,
            "-f", f"head_sha={candidate_sha}",
            "-f", f"event={event}",
            "-f", "per_page=100",
        ))
        runs = data.get("workflow_runs")
        if not isinstance(runs, list):
            raise EvidenceError("privacy workflow runs response is malformed")
        matches: list[dict[str, Any]] = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            if run.get("workflow_id") != self.policy.privacy_workflow_id:
                continue
            if run.get("path") != self.policy.privacy_workflow_path:
                continue
            if run.get("event") != event or run.get("head_sha") != candidate_sha:
                continue
            if event == "pull_request":
                pull_requests = run.get("pull_requests") or []
                if not any(item.get("number") == pr_number for item in pull_requests if isinstance(item, dict)):
                    continue
            elif run.get("head_branch") != head_branch:
                continue
            matches.append(run)
        if not matches:
            raise EvidenceError("no exact privacy workflow attestation exists")
        latest = max(matches, key=self._run_sort_key)
        if latest.get("status") != "completed" or latest.get("conclusion") != "success":
            raise EvidenceError("newest exact privacy workflow attestation is not successful")
        return {
            "workflow_id": self.policy.privacy_workflow_id,
            "workflow_path": workflow["path"],
            "workflow_git_blob": workflow["git_blob"],
            "workflow_sha256": workflow["sha256"],
            "run_id": latest.get("id"),
            "run_attempt": latest.get("run_attempt"),
            "created_at": latest.get("created_at"),
            "event": event,
            "status": latest.get("status"),
            "conclusion": latest.get("conclusion"),
            "head_sha": candidate_sha,
            "head_branch": latest.get("head_branch"),
            "pr_number": pr_number if event == "pull_request" else None,
        }

    def vercel_state(self) -> dict[str, Any]:
        project = self._json((
            "vercel", "api", f"/v9/projects/{self.policy.vercel_project}",
            "--scope", self.policy.vercel_scope, "--raw",
        ))
        live = self._json((
            "vercel", "inspect", self.policy.live_domain,
            "--scope", self.policy.vercel_scope, "--json",
        ))
        return {
            "autoAssignCustomDomains": project.get("autoAssignCustomDomains"),
            "commandForIgnoringBuildStep": project.get("commandForIgnoringBuildStep"),
            "productionBranch": (project.get("link") or {}).get("productionBranch"),
            "liveDeployment": live.get("id"),
            "liveTarget": live.get("target"),
        }


    def command_log(self) -> list[dict[str, Any]]:
        results = getattr(self.runner, "results", [])
        return [
            {
                "tool": item.args[0] if item.args else "",
                "operation": item.args[1] if len(item.args) > 1 else "",
                "returncode": item.returncode,
            }
            for item in results
        ]

    def ruleset_ok(self) -> bool:
        owner, repo = self.policy.repository.split("/", 1)
        data = self._json(("gh", "api", f"repos/{owner}/{repo}/rulesets/{BRANCH_RULESET_ID}"))
        rule_types = {item.get("type") for item in data.get("rules", [])}
        includes = set((data.get("conditions") or {}).get("ref_name", {}).get("include", []))
        return (
            data.get("enforcement") == "active"
            and "~ALL" in includes
            and not data.get("bypass_actors")
            and {"commit_author_email_pattern", "committer_email_pattern", "non_fast_forward"} <= rule_types
        )


    def branch_protection_ok(self, target: str, required: tuple[str, ...]) -> bool:
        if target not in self.policy.phase3_targets:
            return True
        owner, repo = self.policy.repository.split("/", 1)
        data = self._json(("gh", "api", f"repos/{owner}/{repo}/branches/{target}/protection"))
        status = data.get("required_status_checks") or {}
        contexts = set(status.get("contexts") or [])
        contexts.update(item.get("context") for item in status.get("checks") or [] if item.get("context"))
        return (
            set(required) <= contexts
            and bool((data.get("enforce_admins") or {}).get("enabled"))
            and bool((data.get("required_linear_history") or {}).get("enabled"))
            and not bool((data.get("allow_force_pushes") or {}).get("enabled"))
            and bool((data.get("required_conversation_resolution") or {}).get("enabled"))
        )

    def fast_forward(self, target: str, expected_old: str, candidate: str) -> CommandResult:
        authorized = target in self.policy.phase3_targets or target.startswith(self.policy.probe_prefix)
        if not authorized:
            raise EvidenceError("target is outside safe-merge policy")
        check_ref = self.runner.run(("git", "check-ref-format", f"refs/heads/{target}"))
        if check_ref.returncode != 0:
            raise EvidenceError("target is not a valid branch ref")
        if self.target_sha(target) != expected_old:
            raise EvidenceError("target changed at write boundary")
        result = self.runner.run(("git", "push", "origin", f"{candidate}:refs/heads/{target}"))
        if result.returncode != 0:
            raise EvidenceError("fast-forward push failed")
        return result
