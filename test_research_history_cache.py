from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from research_history_cache import load_or_fetch_research_history


UTC = timezone.utc


class FakeMarket:
    historical_feed = "sip"

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.rows = [
            {
                "t": "2026-09-01T14:30:00Z",
                "o": 10.0,
                "h": 10.2,
                "l": 9.9,
                "c": 10.1,
                "v": 1000,
                "vw": 10.08,
                "n": 40,
            }
        ]

    def bars(self, symbols, **kwargs):
        self.calls.append({"symbols": list(symbols), **kwargs})
        if kwargs.get("progress"):
            kwargs["progress"](1)
        return {symbols[0]: [dict(item) for item in self.rows]}


class FilteringMarket(FakeMarket):
    def __init__(self, rows: list[dict]) -> None:
        super().__init__()
        self.rows = rows

    def bars(self, symbols, **kwargs):
        self.calls.append({"symbols": list(symbols), **kwargs})
        start = kwargs["start"]
        end = kwargs["end"]
        selected = []
        for item in self.rows:
            stamp = datetime.fromisoformat(str(item["t"]).replace("Z", "+00:00"))
            if start <= stamp <= end:
                selected.append(dict(item))
        if kwargs.get("progress"):
            kwargs["progress"](1)
        return {symbols[0]: selected}


def minute_row(stamp: datetime, close: float) -> dict:
    return {
        "t": stamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "o": close - 0.02,
        "h": close + 0.04,
        "l": close - 0.04,
        "c": close,
        "v": 1000,
        "vw": close,
        "n": 20,
    }


class ResearchHistoryCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.market = FakeMarket()
        self.start = datetime(2026, 8, 25, 14, 0, tzinfo=UTC)
        self.end = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, *, observed_at: datetime, timeframe: str = "1Min", adjustment: str = "raw"):
        return load_or_fetch_research_history(
            self.market,
            symbol="SDOT",
            start=self.start,
            end=self.end,
            timeframe=timeframe,
            adjustment=adjustment,
            max_pages=30,
            data_dir=self.root,
            observed_at=observed_at,
        )

    def test_finalized_exact_window_survives_restart_without_provider_request(self):
        first = self.request(observed_at=self.end + timedelta(minutes=2))
        self.assertTrue(first.metadata["finalized"])
        self.assertTrue(first.metadata["network_request"])
        self.assertEqual(first.metadata["reuse_mode"], "network_full")
        self.assertEqual(len(self.market.calls), 1)
        path = Path(first.metadata["artifact_path"])
        self.assertTrue(path.is_file())

        second = self.request(observed_at=self.end + timedelta(hours=1))
        self.assertTrue(second.metadata["cache_hit"])
        self.assertFalse(second.metadata["network_request"])
        self.assertEqual(second.metadata["reuse_mode"], "exact_window")
        self.assertEqual(second.rows, first.rows)
        self.assertEqual(len(self.market.calls), 1)

    def test_near_live_window_is_not_reused_until_refetched_after_bar_finalizes(self):
        first = self.request(observed_at=self.end + timedelta(seconds=20))
        self.assertFalse(first.metadata["finalized"])
        self.assertEqual(len(self.market.calls), 1)

        second = self.request(observed_at=self.end + timedelta(minutes=2))
        self.assertFalse(second.metadata["cache_hit"])
        self.assertTrue(second.metadata["finalized"])
        self.assertEqual(len(self.market.calls), 2)

        third = self.request(observed_at=self.end + timedelta(minutes=3))
        self.assertTrue(third.metadata["cache_hit"])
        self.assertEqual(len(self.market.calls), 2)

    def test_timeframe_adjustment_and_feed_are_part_of_cache_identity(self):
        base = self.request(observed_at=self.end + timedelta(hours=1))
        self.assertEqual(len(self.market.calls), 1)

        other_timeframe = self.request(
            observed_at=self.end + timedelta(hours=1),
            timeframe="5Min",
        )
        self.assertFalse(other_timeframe.metadata["cache_hit"])
        self.assertEqual(len(self.market.calls), 2)

        other_adjustment = self.request(
            observed_at=self.end + timedelta(hours=1),
            adjustment="split",
        )
        self.assertFalse(other_adjustment.metadata["cache_hit"])
        self.assertEqual(len(self.market.calls), 3)

        self.market.historical_feed = "iex"
        other_feed = self.request(observed_at=self.end + timedelta(hours=1))
        self.assertFalse(other_feed.metadata["cache_hit"])
        self.assertEqual(len(self.market.calls), 4)
        self.assertNotEqual(base.metadata["fingerprint"], other_feed.metadata["fingerprint"])

    def test_tampered_artifact_fails_closed_and_refetches(self):
        first = self.request(observed_at=self.end + timedelta(hours=1))
        path = Path(first.metadata["artifact_path"])
        path.write_bytes(b"not-a-valid-gzip")

        second = self.request(observed_at=self.end + timedelta(hours=2))
        self.assertFalse(second.metadata["cache_hit"])
        self.assertEqual(len(self.market.calls), 2)

    def test_raw_provider_shape_is_preserved_for_backtester(self):
        result = self.request(observed_at=self.end + timedelta(hours=1))
        self.assertEqual(result.rows[0]["t"], "2026-09-01T14:30:00Z")
        self.assertIn("o", result.rows[0])
        self.assertIn("vw", result.rows[0])
        self.assertIn("n", result.rows[0])

    def test_covering_finalized_window_can_serve_shorter_exact_window_without_network(self):
        base = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
        rows = [minute_row(base + timedelta(minutes=index), 20 + index * 0.01) for index in range(180)]
        market = FilteringMarket(rows)
        first = load_or_fetch_research_history(
            market,
            symbol="AAPL",
            start=base,
            end=base + timedelta(minutes=150),
            timeframe="1Min",
            adjustment="raw",
            data_dir=self.root,
            observed_at=base + timedelta(minutes=180),
        )
        self.assertEqual(len(market.calls), 1)
        second = load_or_fetch_research_history(
            market,
            symbol="AAPL",
            start=base + timedelta(minutes=30),
            end=base + timedelta(minutes=120),
            timeframe="1Min",
            adjustment="raw",
            data_dir=self.root,
            observed_at=base + timedelta(minutes=180),
        )
        self.assertTrue(first.metadata["finalized"])
        self.assertTrue(second.metadata["cache_hit"])
        self.assertFalse(second.metadata["network_request"])
        self.assertEqual(second.metadata["reuse_mode"], "covering_window")
        self.assertEqual(len(market.calls), 1)
        self.assertEqual(second.rows[0]["t"], rows[30]["t"])
        self.assertEqual(second.rows[-1]["t"], rows[120]["t"])

    def test_rolling_window_reuses_finalized_prefix_and_downloads_only_suffix_overlap(self):
        base = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
        rows = [minute_row(base + timedelta(minutes=index), 30 + index * 0.01) for index in range(300)]
        market = FilteringMarket(rows)
        first_end = base + timedelta(minutes=180)
        first = load_or_fetch_research_history(
            market,
            symbol="MSFT",
            start=base,
            end=first_end,
            timeframe="1Min",
            adjustment="raw",
            data_dir=self.root,
            observed_at=first_end + timedelta(minutes=5),
        )
        self.assertTrue(first.metadata["finalized"])
        self.assertEqual(len(market.calls), 1)

        second_start = base + timedelta(minutes=60)
        second_end = base + timedelta(minutes=240)
        second = load_or_fetch_research_history(
            market,
            symbol="MSFT",
            start=second_start,
            end=second_end,
            timeframe="1Min",
            adjustment="raw",
            data_dir=self.root,
            observed_at=second_end + timedelta(minutes=5),
        )
        self.assertTrue(second.metadata["cache_hit"])
        self.assertTrue(second.metadata["network_request"])
        self.assertEqual(second.metadata["reuse_mode"], "incremental_prefix")
        self.assertEqual(len(market.calls), 2)
        self.assertEqual(
            market.calls[-1]["start"],
            first_end - timedelta(minutes=3),
        )
        self.assertGreater(second.metadata["reused_row_count"], 0)
        self.assertLess(second.metadata["provider_row_count"], len(second.rows))
        self.assertEqual(second.rows[0]["t"], rows[60]["t"])
        self.assertEqual(second.rows[-1]["t"], rows[240]["t"])


if __name__ == "__main__":
    unittest.main()
