import unittest

from indicator_cross_validation import cross_validate_indicators


def bar(i, price, volume=1000):
    from datetime import datetime, timedelta, timezone
    start = datetime(2026, 8, 20, 13, 30, tzinfo=timezone.utc)
    timestamp = start + timedelta(minutes=i)
    return {
        "t": timestamp.isoformat().replace("+00:00", "Z"),
        "o": price - 0.03,
        "h": price + 0.08,
        "l": price - 0.07,
        "c": price,
        "v": volume,
    }


class IndicatorCrossValidationTests(unittest.TestCase):
    def test_equivalent_indicator_definitions_match_independent_reference(self):
        rows = [
            bar(i, 10.0 + i * 0.03 + (0.02 if i % 3 == 0 else 0.0), 1000 + i * 50)
            for i in range(60)
        ]
        report = cross_validate_indicators(
            rows,
            ema_period=9,
            atr_window=14,
        )
        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["checks"]["ema"]["passed"])
        self.assertTrue(report["checks"]["atr"]["passed"])
        self.assertTrue(report["checks"]["session_vwap"]["passed"])
        self.assertIn("rolling-window", report["checks"]["session_vwap"]["note"])


if __name__ == "__main__":
    unittest.main()
