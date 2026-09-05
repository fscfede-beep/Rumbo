import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_public_privacy as gate


class PrivacyGateRegressionTests(unittest.TestCase):
    def test_email_candidates_preserve_punctuation(self):
        value = "private.person+tag" + "@" + "example.test"
        self.assertEqual(list(gate.email_candidates(f"contact={value}")), [value])

    def test_denied_email_is_detected_as_exact_value(self):
        value = "private.person" + "@" + "example.test"
        deny = {gate.sha(value)}
        self.assertTrue(gate.text_has_denied_value(f"owner: {value}", deny))

    def test_unapproved_email_is_rejected_without_secret_hash(self):
        value = "private.person" + "@" + "example.test"
        self.assertTrue(gate.text_has_unapproved_email(f"owner: {value}"))

    def test_approved_business_email_is_allowed(self):
        value = "sebastian@rumbo.verso.fans"
        self.assertFalse(gate.text_has_unapproved_email(f"contact: {value}"))

    def test_github_noreply_email_is_allowed(self):
        value = "293577326+fscfede-beep@users.noreply.github.com"
        self.assertFalse(gate.text_has_unapproved_email(f"commit: {value}"))

    def test_github_managed_context_does_not_bypass_private_author_name(self):
        value = "Private Author Name"
        env = {
            "RUMBO_GITHUB_MANAGED_COMMIT": "1",
            "RUMBO_GITHUB_MANAGED_SHA": "abc123",
            "RUMBO_GITHUB_EVENT_NAME": "push",
            "RUMBO_GITHUB_REF": "refs/heads/main",
            "RUMBO_GITHUB_ACTOR": gate.TRUSTED_GITHUB_ACTOR,
            "RUMBO_GITHUB_SENDER": gate.TRUSTED_GITHUB_ACTOR,
            "RUMBO_GITHUB_FORCED": "false",
        }
        with mock.patch.dict(gate.os.environ, env, clear=True):
            self.assertFalse(gate.approved_head_author_name(value, "abc123", set()))

    def test_github_managed_context_does_not_bypass_private_author_email(self):
        value = "private.author" + "@" + "example.test"
        env = {
            "RUMBO_GITHUB_MANAGED_COMMIT": "1",
            "RUMBO_GITHUB_MANAGED_SHA": "abc123",
            "RUMBO_GITHUB_EVENT_NAME": "push",
            "RUMBO_GITHUB_REF": "refs/heads/main",
            "RUMBO_GITHUB_ACTOR": gate.TRUSTED_GITHUB_ACTOR,
            "RUMBO_GITHUB_SENDER": gate.TRUSTED_GITHUB_ACTOR,
            "RUMBO_GITHUB_FORCED": "false",
        }
        with mock.patch.dict(gate.os.environ, env, clear=True):
            self.assertFalse(gate.approved_head_author_email(value, "abc123", set()))

    def test_committer_name_must_be_public(self):
        self.assertNotIn("Private Committer Name", gate.APPROVED_COMMITTER_NAMES)
        self.assertIn("GitHub", gate.APPROVED_COMMITTER_NAMES)

    def test_full_ancestry_metadata_scan_passes_current_clean_history(self):
        self.assertEqual(gate.commit_metadata_violations("HEAD", set()), [])

    def test_plugin_metadata_requires_exact_content_hash(self):
        root = Path(__file__).resolve().parents[1]
        rel = Path(".agents/plugins/marketplace.json")
        text = (root / rel).read_text(encoding="utf-8")
        violations = []
        self.assertTrue(gate.approved_plugin_metadata(rel, text, violations))
        self.assertEqual(violations, [])
        drifted = text.rstrip()[:-1] + ',"unexpected":true}'
        drift_violations = []
        self.assertFalse(gate.approved_plugin_metadata(rel, drifted, drift_violations))
        self.assertIn(f"{rel.as_posix()}:metadata-drift", drift_violations)

    def test_plugin_metadata_does_not_bypass_direct_profile_guard(self):
        marker = "linkedin.com" + "/in/example"
        self.assertTrue(gate.text_has_direct_person_profile(marker))

    def test_metadata_scan_refs_include_selected_ref_and_all_public_refs(self):
        env = {
            "RUMBO_PRIVACY_COMMIT_SHA": "abc123",
            "RUMBO_PRIVACY_SCAN_ALL_REFS": "1",
        }
        with mock.patch.dict(gate.os.environ, env, clear=True):
            self.assertEqual(gate.metadata_scan_refs(), ["abc123", "--all"])

    def test_metadata_scan_refs_default_to_selected_ref_only(self):
        with mock.patch.dict(gate.os.environ, {"RUMBO_PRIVACY_COMMIT_SHA": "abc123"}, clear=True):
            self.assertEqual(gate.metadata_scan_refs(), ["abc123"])

    def test_ci_enables_repository_wide_metadata_scan(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/privacy-gate.yml").read_text(encoding="utf-8")
        self.assertIn("RUMBO_PRIVACY_SCAN_ALL_REFS", workflow)

    def test_public_founder_display_is_first_name_only(self):
        self.assertEqual(gate.PUBLIC_NAME, "Sebasti\u00e1n")

    def test_direct_person_profile_link_is_rejected(self):
        link = "https://www.linkedin.com" + "/in/example"
        self.assertTrue(gate.text_has_direct_person_profile(link))

    def test_rel_me_identity_link_is_rejected(self):
        marker = "rel=" + chr(34) + "me" + chr(34)
        self.assertTrue(gate.text_has_direct_person_profile("<link " + marker + " href=https://example.test/profile>"))

    def test_word_ngram_deny_behavior_is_preserved(self):
        deny = {gate.sha("private surname")}
        self.assertTrue(gate.text_has_denied_value("hello private surname world", deny))


    def test_openai_landing_founder_uses_public_name(self):
        text = (Path(__file__).resolve().parents[1] / "apps/landing-publica/index-en-openai.html").read_text(encoding="utf-8")
        match = gate.re.search(r"RUMBO IA is developed by\s+([^<\r\n]+)", text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).strip(), gate.PUBLIC_NAME)


if __name__ == "__main__":
    unittest.main()
