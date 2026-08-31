import unittest

from trading_readiness import build_trust_readiness_summary


class TrustReadinessSummaryTests(unittest.TestCase):
    def test_separates_historical_shadow_and_production_readiness(self):
        strategies = [
            {"validation_status": "validated", "paper_validation_status": "ready"},
            {"validation_status": "validated", "paper_validation_status": "not_ready"},
            {"validation_status": "research_only"},
        ]
        library = {
            "predictive_ml_runs": [
                {
                    "probability_models": [
                        {
                            "shadow_scoring_enabled": True,
                            "research_only": True,
                            "production_enabled": False,
                        }
                    ]
                }
            ]
        }

        result = build_trust_readiness_summary(strategies, library)

        self.assertEqual(result["historically_validated_strategies"], 2)
        self.assertEqual(result["paper_ready_strategies"], 1)
        self.assertEqual(result["shadow_probability_models"], 1)
        self.assertEqual(result["production_probability_models"], 0)
        self.assertFalse(result["production_probability_ready"])

    def test_requires_explicit_nonresearch_production_model(self):
        library = {
            "predictive_ml_runs": [
                {
                    "probability_model": {
                        "research_only": False,
                        "production_enabled": True,
                    }
                }
            ]
        }

        result = build_trust_readiness_summary([], library)

        self.assertTrue(result["production_probability_ready"])
        self.assertEqual(result["production_probability_models"], 1)


if __name__ == "__main__":
    unittest.main()
