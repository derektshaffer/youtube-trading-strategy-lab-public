import unittest

import pandas as pd

from causal_volume_profile import (
    apply_causal_volume_profile_features,
    volume_profile_snapshot,
)


def frame_from(rows):
    start = pd.Timestamp("2026-08-20 13:30:00", tz="UTC")
    data = []
    for i, (open_, high, low, close, volume) in enumerate(rows):
        data.append(
            {
                "timestamp": start + pd.Timedelta(minutes=i),
                "session": "2026-08-20",
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    return pd.DataFrame(data)


class CausalVolumeProfileTests(unittest.TestCase):
    def test_high_volume_price_area_drives_poc(self):
        frame = frame_from(
            [
                (10.0, 10.2, 9.9, 10.1, 100),
                (10.0, 10.2, 9.9, 10.1, 100),
                (11.0, 11.2, 10.9, 11.1, 5000),
                (10.0, 10.2, 9.9, 10.1, 100),
            ]
        )
        profile = volume_profile_snapshot(frame, bins=12)
        self.assertGreater(profile["vp_poc"], 10.7)
        self.assertGreater(profile["vp_poc_volume_share"], 0.8)

    def test_future_bars_cannot_change_prior_profile_features(self):
        rows = [
            (10 + i * 0.01, 10.2 + i * 0.01, 9.9 + i * 0.01, 10.1 + i * 0.01, 100 + i)
            for i in range(20)
        ]
        original = apply_causal_volume_profile_features(
            frame_from(rows),
            lookback_bars=12,
            bins=12,
            minimum_bars=5,
        )
        altered_rows = list(rows)
        altered_rows[-1] = (20.0, 25.0, 19.0, 24.0, 100_000)
        altered = apply_causal_volume_profile_features(
            frame_from(altered_rows),
            lookback_bars=12,
            bins=12,
            minimum_bars=5,
        )
        for column in (
            "vp_poc",
            "vp_distance_to_poc_pct",
            "vp_profile_entropy",
            "upper_exhaustion_pressure",
            "lower_exhaustion_pressure",
        ):
            self.assertAlmostEqual(
                float(original.loc[15, column]),
                float(altered.loc[15, column]),
                places=10,
            )

    def test_volume_climax_with_upper_rejection_scores_exhaustion(self):
        rows = [
            (10.0, 10.2, 9.9, 10.1, 1000)
            for _ in range(12)
        ]
        rows.append((10.1, 12.0, 10.0, 10.2, 6000))
        result = apply_causal_volume_profile_features(
            frame_from(rows),
            lookback_bars=20,
            bins=12,
            minimum_bars=5,
        )
        row = result.iloc[-1]
        self.assertGreater(float(row["volume_climax_ratio"]), 4.0)
        self.assertGreater(float(row["upper_exhaustion_pressure"]), 60.0)


if __name__ == "__main__":
    unittest.main()
