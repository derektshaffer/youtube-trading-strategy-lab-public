"""Tests for deterministic SEC catalyst intelligence."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import sec_catalyst_intelligence as secintel


class SecCatalystTests(unittest.TestCase):
    def test_resolve_ticker_and_recent_filings_preserve_primary_evidence(self):
        ticker_payload = {
            "0": {"cik_str": 123456, "ticker": "TEST", "title": "Test Corp"}
        }
        submissions = {
            "name": "Test Corp",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000123456-26-000001"],
                    "filingDate": ["2026-08-29"],
                    "reportDate": ["2026-08-29"],
                    "acceptanceDateTime": ["20260829164512"],
                    "act": ["33"],
                    "form": ["S-3"],
                    "fileNumber": ["333-123456"],
                    "filmNumber": ["26123456"],
                    "items": [""],
                    "size": [12345],
                    "isXBRL": [0],
                    "isInlineXBRL": [0],
                    "primaryDocument": ["forms-3.htm"],
                    "primaryDocDescription": ["FORM S-3"],
                }
            },
        }

        with patch.object(
            secintel,
            "_sec_json",
            side_effect=[ticker_payload, submissions],
        ):
            client = secintel.SecEdgarClient("Trading Lab contact@example.com")
            payload = client.recent_filings(
                "TEST",
                days=30,
                as_of=datetime(2026, 8, 29, 17, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(payload["cik"], "0000123456")
        self.assertEqual(len(payload["filings"]), 1)
        filing = payload["filings"][0]
        self.assertEqual(filing["form"], "S-3")
        self.assertEqual(filing["accepted_at"], "2026-08-29T16:45:12Z")
        self.assertIn("/123456/000012345626000001/forms-3.htm", filing["filing_url"])

    def test_s3_is_potential_dilution_not_proven_immediate_offering(self):
        row = secintel.classify_sec_filing(
            {"form": "S-3", "accepted_at": "2026-08-29T16:00:00Z"}
        )
        self.assertEqual(row["category"], "securities registration / potential dilution")
        self.assertTrue(row["is_dilution_risk"])
        self.assertLess(row["score"], 0)

    def test_424b5_is_high_severity_offering_risk(self):
        row = secintel.classify_sec_filing(
            {"form": "424B5", "accepted_at": "2026-08-29T16:00:00Z"}
        )
        self.assertEqual(row["severity"], "high")
        self.assertTrue(row["is_dilution_risk"])
        self.assertEqual(row["score"], -8.0)

    def test_8k_item_302_is_dilution_risk(self):
        row = secintel.classify_sec_filing(
            {
                "form": "8-K",
                "items": "1.01, 3.02",
                "accepted_at": "2026-08-29T16:00:00Z",
            }
        )
        self.assertEqual(row["category"], "unregistered equity issuance / dilution risk")
        self.assertTrue(row["is_dilution_risk"])
        self.assertIn("3.02", row["items_list"])

    def test_form4_stays_directionally_neutral_without_transaction_parse(self):
        row = secintel.classify_sec_filing(
            {"form": "4", "accepted_at": "2026-08-29T16:00:00Z"}
        )
        self.assertEqual(row["category"], "insider transaction filing")
        self.assertEqual(row["score"], 0.0)
        self.assertFalse(row["is_positive"])
        self.assertFalse(row["is_negative"])

    def test_missing_sec_user_agent_is_rejected_before_request(self):
        with self.assertRaises(secintel.AppError):
            secintel.SecEdgarClient("")


if __name__ == "__main__":
    unittest.main()
