#!/usr/bin/env python3
"""Evaluate a Codex app-server Thread snapshot against trusted scope authority."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from thread_environment_adapter import evaluate_thread_environments
from workspace_scope_guard import normalize_path


def _fail(*errors: str) -> dict[str, Any]:
    return {
        "ok": False,
        "decision": "SCOPE_UNRESOLVED",
        "errors": sorted(set(errors)),
    }


def evaluate_thread_snapshot(
    authority: dict[str, Any],
    thread: dict[str, Any],
    connected_environment_ids: set[str],
    *,
    effective_writable_roots: list[str] | None = None,
) -> dict[str, Any]:
    expected_project = str(authority.get("project_id") or "").strip()
    observed_project = str(thread.get("projectId") or "").strip()
    if not expected_project:
        return _fail("PROJECT_AUTHORITY_REQUIRED")
    if observed_project != expected_project:
        return _fail("THREAD_PROJECT_AUTHORITY_MISMATCH")

    expected_cwd = normalize_path(str(authority.get("resolved_cwd") or ""))
    observed_cwd = normalize_path(str(thread.get("cwd") or ""))
    if not expected_cwd:
        return _fail("SCOPE_AUTHORITY_CWD_REQUIRED")
    if observed_cwd != expected_cwd:
        return _fail("THREAD_CWD_AUTHORITY_MISMATCH")

    expected_environment_id = str(authority.get("environment_id") or "").strip()
    runtime_connected = expected_environment_id in connected_environment_ids
    return evaluate_thread_environments(
        authority,
        thread.get("environments"),
        runtime_connected=runtime_connected,
        effective_writable_roots=effective_writable_roots,
    )


def evaluate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    authority = payload.get("authority")
    thread = payload.get("thread")
    statuses = payload.get("environmentStatuses")
    connected = payload.get("connectedEnvironmentIds", [])
    if not isinstance(authority, dict):
        return _fail("SCOPE_AUTHORITY_REQUIRED")
    if not isinstance(thread, dict):
        return _fail("THREAD_SNAPSHOT_REQUIRED")

    writable_roots: list[str] | None = None
    permission_evidence = payload.get("permissionEvidence")
    if permission_evidence is not None:
        if not isinstance(permission_evidence, dict):
            return _fail("PERMISSION_EVIDENCE_INVALID")
        permission_source = str(permission_evidence.get("source") or "").strip()
        if not permission_source:
            return _fail("PERMISSION_EVIDENCE_SOURCE_REQUIRED")
        expected_permission_source = str(authority.get("permission_source") or "").strip()
        if not expected_permission_source:
            return _fail("PERMISSION_AUTHORITY_SOURCE_REQUIRED")
        if permission_source != expected_permission_source:
            return _fail("PERMISSION_EVIDENCE_SOURCE_MISMATCH")
        active_profile = permission_evidence.get("activePermissionProfile")
        if not isinstance(active_profile, dict):
            return _fail("ACTIVE_PERMISSION_PROFILE_REQUIRED")
        active_profile_id = str(active_profile.get("id") or "").strip()
        if not active_profile_id:
            return _fail("ACTIVE_PERMISSION_PROFILE_ID_REQUIRED")
        expected_profile_id = str(authority.get("permission_profile_id") or "").strip()
        if not expected_profile_id:
            return _fail("PERMISSION_PROFILE_AUTHORITY_REQUIRED")
        if active_profile_id != expected_profile_id:
            return _fail("PERMISSION_PROFILE_AUTHORITY_MISMATCH")
        raw_writable = permission_evidence.get("writableRoots")
        if not isinstance(raw_writable, list):
            return _fail("PERMISSION_WRITABLE_ROOTS_INVALID")
        writable_roots = [str(item) for item in raw_writable]

    expected_id = str(authority.get("environment_id") or "").strip()
    if isinstance(statuses, dict) and expected_id in statuses:
        raw_status = statuses[expected_id]
        status = raw_status.get("status") if isinstance(raw_status, dict) else raw_status
        status = str(status or "").strip().lower()
        if status != "ready":
            return {
                "ok": False,
                "decision": "SELECTION_ONLY",
                "errors": [f"ENVIRONMENT_STATUS_NOT_READY:{status or 'missing'}"],
            }
        return evaluate_thread_snapshot(
            authority,
            thread,
            {expected_id},
            effective_writable_roots=writable_roots,
        )

    if not isinstance(connected, list):
        return _fail("CONNECTED_ENVIRONMENT_IDS_INVALID")
    connected_ids = {str(item).strip() for item in connected if str(item).strip()}
    return evaluate_thread_snapshot(
        authority,
        thread,
        connected_ids,
        effective_writable_roots=writable_roots,
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: codex_thread_scope_probe.py SNAPSHOT.json", file=sys.stderr)
        return 64
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result = evaluate_payload(payload)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
