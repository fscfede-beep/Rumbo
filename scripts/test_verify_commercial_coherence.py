import unittest
from pathlib import Path

from scripts import verify_commercial_coherence as verifier

ROOT = Path(__file__).resolve().parents[1]


class CommercialCoherenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.landing_en = (ROOT / "apps/landing-publica/index.html").read_text(encoding="utf-8")
        cls.landing_es = (ROOT / "apps/landing-publica/index-es.html").read_text(encoding="utf-8")

    def test_current_public_surface_passes(self):
        self.assertEqual(verifier.check(self.readme, self.html), [])

    def test_all_tracked_public_landings_are_commercially_coherent(self):
        combined = self.html + "\n" + self.landing_en + "\n" + self.landing_es
        self.assertEqual(verifier.check(self.readme, combined), [])

    def test_stale_social_handle_is_rejected(self):
        errors = verifier.check(self.readme, self.html + "\n@rumbo_ia")
        self.assertIn("STALE_SOCIAL_HANDLE", errors)

    def test_monthly_pricing_wording_is_rejected(self):
        for wording in ("USD 149 a month", "USD 149 al mes"):
            with self.subTest(wording=wording):
                errors = verifier.check(self.readme, self.html + "\n" + wording)
                self.assertIn("MONTHLY_PRICE", errors)

    def test_monthly_pricing_is_rejected(self):
        errors = verifier.check(self.readme, self.html + "\n<div>USD 149/mes</div>")
        self.assertIn("MONTHLY_PRICE", errors)

    def test_any_public_mailto_is_rejected(self):
        errors = verifier.check(
            self.readme,
            self.html + '\n<a href="mailto:' + "test" + "@" + 'example.com">Contacto</a>',
        )
        self.assertIn("PUBLIC_MAILTO", errors)

    def test_noncanonical_typeform_is_rejected(self):
        changed = self.html.replace(
            verifier.TYPEFORM,
            "https://form.typeform.com/to/WRONG123",
        )
        errors = verifier.check(self.readme, changed)
        self.assertIn("NONCANONICAL_TYPEFORM", errors)
        self.assertIn("CANONICAL_TYPEFORM_ANCHOR_MISSING", errors)

    def test_internal_contact_cta_is_required(self):
        changed = self.html.replace('href="#contacto"', 'href="#producto"', 1)
        errors = verifier.check(self.readme, changed)
        self.assertIn("CONTACT_CTA_MISSING", errors)


if __name__ == "__main__":
    unittest.main()
