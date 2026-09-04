import unittest

from codex_thread_scope_probe import evaluate_thread_snapshot


AUTHORITY = {
    "scope_id": "project-b",
    "source": "trusted-project-selection",
    "project_id": "project-b",
    "environment_id": "env-b",
    "resolved_cwd": "/work/project-b",
    "resolved_workspace_roots": ["/work/project-b"],
}

THREAD = {
    "projectId": "project-b",
    "cwd": "/work/project-b",
    "environments": [{
        "environmentId": "env-b",
        "cwd": "/work/project-b",
        "runtimeWorkspaceRoots": ["/work/project-b"],
    }],
}


class CodexThreadScopeProbeTests(unittest.TestCase):
    def test_selected_thread_without_runtime_proof_is_not_runtime_bound(self):
        result = evaluate_thread_snapshot(AUTHORITY, THREAD, set())
        self.assertFalse(result["ok"])
        self.assertEqual("SELECTION_ONLY", result["decision"])

    def test_thread_project_must_match_external_authority(self):
        thread = dict(THREAD)
        thread["projectId"] = "project-a"
        result = evaluate_thread_snapshot(AUTHORITY, thread, {"env-b"})
        self.assertFalse(result["ok"])
        self.assertIn("THREAD_PROJECT_AUTHORITY_MISMATCH", result["errors"])

    def test_thread_top_level_cwd_must_match_authority(self):
        thread = dict(THREAD)
        thread["cwd"] = "/work/project-a"
        result = evaluate_thread_snapshot(AUTHORITY, thread, {"env-b"})
        self.assertFalse(result["ok"])
        self.assertIn("THREAD_CWD_AUTHORITY_MISMATCH", result["errors"])

    def test_complete_thread_snapshot_and_runtime_proof_binds(self):
        result = evaluate_thread_snapshot(AUTHORITY, THREAD, {"env-b"})
        self.assertEqual(
            {"ok": True, "decision": "RUNTIME_SCOPE_BOUND", "errors": []},
            result,
        )


if __name__ == "__main__":
    unittest.main()

class CodexThreadScopePayloadTests(unittest.TestCase):
    def test_official_ready_status_can_prove_runtime(self):
        from codex_thread_scope_probe import evaluate_payload
        payload = {
            "authority": AUTHORITY,
            "thread": THREAD,
            "environmentStatuses": {"env-b": {"status": "ready"}},
        }
        result = evaluate_payload(payload)
        self.assertEqual("RUNTIME_SCOPE_BOUND", result["decision"])

    def test_pending_status_remains_selection_only(self):
        from codex_thread_scope_probe import evaluate_payload
        payload = {
            "authority": AUTHORITY,
            "thread": THREAD,
            "environmentStatuses": {"env-b": {"status": "pending"}},
        }
        result = evaluate_payload(payload)
        self.assertFalse(result["ok"])
        self.assertEqual("SELECTION_ONLY", result["decision"])
        self.assertIn("ENVIRONMENT_STATUS_NOT_READY:pending", result["errors"])

    def test_required_write_scope_needs_permission_evidence(self):
        from codex_thread_scope_probe import evaluate_payload
        authority = dict(AUTHORITY)
        authority["required_writable_roots"] = ["/work/project-b"]
        payload = {
            "authority": authority,
            "thread": THREAD,
            "environmentStatuses": {"env-b": {"status": "ready"}},
        }
        result = evaluate_payload(payload)
        self.assertFalse(result["ok"])
        self.assertIn("REQUIRED_WRITABLE_ROOT_MISSING", result["errors"])

    def test_permission_evidence_is_separate_from_workspace_roots(self):
        from codex_thread_scope_probe import evaluate_payload
        authority = dict(AUTHORITY)
        authority["required_writable_roots"] = ["/work/project-b"]
        authority["permission_source"] = "app-server-thread-settings"
        authority["permission_profile_id"] = "dev-b"
        payload = {
            "authority": authority,
            "thread": THREAD,
            "environmentStatuses": {"env-b": {"status": "ready"}},
            "permissionEvidence": {
                "source": "app-server-thread-settings",
                "activePermissionProfile": {"id": "dev-b", "extends": None},
                "writableRoots": ["/work/project-b"],
            },
        }
        result = evaluate_payload(payload)
        self.assertEqual("RUNTIME_SCOPE_BOUND", result["decision"])

class PermissionSourceAuthorityTests(unittest.TestCase):
    def test_permission_source_must_match_external_authority(self):
        from codex_thread_scope_probe import evaluate_payload
        authority = dict(AUTHORITY)
        authority["required_writable_roots"] = ["/work/project-b"]
        authority["permission_source"] = "trusted-effective-permission-profile"
        payload = {
            "authority": authority,
            "thread": THREAD,
            "environmentStatuses": {"env-b": {"status": "ready"}},
            "permissionEvidence": {
                "source": "self-declared",
                "writableRoots": ["/work/project-b"],
            },
        }
        result = evaluate_payload(payload)
        self.assertFalse(result["ok"])
        self.assertIn("PERMISSION_EVIDENCE_SOURCE_MISMATCH", result["errors"])

    def test_permission_evidence_requires_authorized_source_identity(self):
        from codex_thread_scope_probe import evaluate_payload
        authority = dict(AUTHORITY)
        authority["required_writable_roots"] = ["/work/project-b"]
        payload = {
            "authority": authority,
            "thread": THREAD,
            "environmentStatuses": {"env-b": {"status": "ready"}},
            "permissionEvidence": {
                "source": "effective-permission-profile",
                "writableRoots": ["/work/project-b"],
            },
        }
        result = evaluate_payload(payload)
        self.assertFalse(result["ok"])
        self.assertIn("PERMISSION_AUTHORITY_SOURCE_REQUIRED", result["errors"])

    def test_active_permission_profile_must_match_authority(self):
        from codex_thread_scope_probe import evaluate_payload
        authority = dict(AUTHORITY)
        authority["required_writable_roots"] = ["/work/project-b"]
        authority["permission_source"] = "app-server-thread-settings"
        authority["permission_profile_id"] = "dev-b"
        payload = {
            "authority": authority,
            "thread": THREAD,
            "environmentStatuses": {"env-b": {"status": "ready"}},
            "permissionEvidence": {
                "source": "app-server-thread-settings",
                "activePermissionProfile": {"id": "dev-a", "extends": None},
                "writableRoots": ["/work/project-b"],
            },
        }
        result = evaluate_payload(payload)
        self.assertFalse(result["ok"])
        self.assertIn("PERMISSION_PROFILE_AUTHORITY_MISMATCH", result["errors"])

    def test_permission_evidence_requires_active_profile_provenance(self):
        from codex_thread_scope_probe import evaluate_payload
        authority = dict(AUTHORITY)
        authority["required_writable_roots"] = ["/work/project-b"]
        authority["permission_source"] = "app-server-thread-settings"
        authority["permission_profile_id"] = "dev-b"
        payload = {
            "authority": authority,
            "thread": THREAD,
            "environmentStatuses": {"env-b": {"status": "ready"}},
            "permissionEvidence": {
                "source": "app-server-thread-settings",
                "writableRoots": ["/work/project-b"],
            },
        }
        result = evaluate_payload(payload)
        self.assertFalse(result["ok"])
        self.assertIn("ACTIVE_PERMISSION_PROFILE_REQUIRED", result["errors"])
