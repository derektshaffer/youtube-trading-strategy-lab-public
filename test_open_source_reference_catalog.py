import unittest

from open_source_reference_catalog import reference_rows


class OpenSourceReferenceCatalogTests(unittest.TestCase):
    def test_catalog_has_broad_categories_and_conservative_license_posture(self):
        rows = reference_rows()
        self.assertGreaterEqual(len(rows), 10)
        categories = {str(item.get("category") or "") for item in rows}
        for expected in (
            "Anchored VWAP",
            "Indicators / patterns",
            "Backtesting architecture",
            "Order book / imbalance",
            "Level 2 / regime detection",
        ):
            self.assertIn(expected, categories)

        for item in rows:
            license_name = str(item.get("license") or "").lower()
            posture = str(item.get("posture") or "")
            if any(
                token in license_name
                for token in (
                    "gpl",
                    "agpl",
                    "commons clause",
                    "unknown",
                    "no license",
                )
            ):
                self.assertNotEqual(posture, "candidate_dependency")


if __name__ == "__main__":
    unittest.main()
