from __future__ import annotations

import ast
from pathlib import Path


PATH = Path("youtube_strategy_engine.py")
source = PATH.read_text(encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_in_region(
    text: str,
    start_marker: str,
    end_marker: str,
    old: str,
    new: str,
    label: str,
) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    region = text[start:end]
    region = replace_once(region, old, new, label)
    return text[:start] + region + text[end:]


# 1) Centralize the exact set of strategy parameters that change prepared indicators.
insert_at = source.index("\ndef parse_clock_minutes", source.index("def apply_strategy_specific_indicators("))
helper = r'''

def strategy_indicator_signature(strategy: dict[str, Any]) -> tuple[Any, ...]:
    """Return the effective rule inputs that can change prepared indicator values.

    Execution/risk parameters such as stop size, reward/risk, and position sizing are
    deliberately excluded. Keeping this signature centralized prevents optimizer caches
    from silently reusing indicator frames when an EMA or Anchored-VWAP input changes.
    """
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    tolerance = safe_float(rules.get("pullback_touch_tolerance_pct"))
    mode = str(rules.get("avwap_anchor_mode") or "").strip().casefold()
    if mode not in SUPPORTED_AVWAP_ANCHOR_MODES:
        mode = ""
    avwap_confirm = None
    avwap_anchor_minute = None
    avwap_pullback_tolerance = None
    if mode:
        avwap_confirm = max(
            1,
            min(20, int(safe_float(rules.get("avwap_pivot_confirm_bars"), 2) or 2)),
        )
        avwap_anchor_minute = max(
            0,
            min(390, int(safe_float(rules.get("avwap_anchor_session_minute"), 0) or 0)),
        )
        avwap_pullback_tolerance = max(
            0.01,
            min(
                20.0,
                float(safe_float(rules.get("avwap_pullback_tolerance_pct"), 0.5) or 0.5),
            ),
        )
    return (
        int(rules.get("breakout_lookback_bars") or 20),
        int(rules.get("opening_range_minutes") or 15),
        int(rules["fast_ema_period"]) if rules.get("fast_ema_period") is not None else None,
        int(rules["slow_ema_period"]) if rules.get("slow_ema_period") is not None else None,
        int(rules["trend_ema_period"]) if rules.get("trend_ema_period") is not None else None,
        None if tolerance is None else round(float(tolerance), 8),
        mode,
        avwap_confirm,
        avwap_anchor_minute,
        None if avwap_pullback_tolerance is None else round(avwap_pullback_tolerance, 8),
    )


def prepare_backtest_payload(
    data: pd.DataFrame,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Materialize immutable-by-convention row/session data once for repeated trials."""
    if data.empty:
        return [], []
    records = data.to_dict("records")
    sessions = list(dict.fromkeys(data["session"].tolist())) if "session" in data.columns else []
    return records, sessions
'''
source = source[:insert_at] + helper + source[insert_at:]

# 2) Let repeated optimizer trials reuse the expensive DataFrame -> records conversion.
source = replace_once(
    source,
    '''    *,\n    prepared_indicators: pd.DataFrame | None = None,\n) -> dict[str, Any]:''',
    '''    *,\n    prepared_indicators: pd.DataFrame | None = None,\n    prepared_records: list[dict[str, Any]] | None = None,\n    prepared_sessions: list[str] | None = None,\n) -> dict[str, Any]:''',
    "run_backtest signature",
)
source = replace_in_region(
    source,
    "def run_backtest(",
    "\ndef generate_strategy_variants(",
    '    sessions = list(dict.fromkeys(data["session"].tolist()))\n',
    '''    sessions = (\n        list(prepared_sessions)\n        if prepared_sessions is not None\n        else list(dict.fromkeys(data["session"].tolist()))\n    )\n''',
    "run_backtest sessions",
)
source = replace_in_region(
    source,
    "def run_backtest(",
    "\ndef generate_strategy_variants(",
    '    records = data.to_dict("records")\n',
    '''    records = (\n        prepared_records\n        if prepared_records is not None\n        else data.to_dict("records")\n    )\n''',
    "run_backtest records",
)

# 3) Historical optimizer: fix the indicator cache identity and reuse record/session payloads.
historical_old = r'''        indicator_cache: dict[tuple[bool, int, int], pd.DataFrame] = {}

        def frame_for_settings(chosen_settings: BacktestSettings) -> pd.DataFrame:
            if chosen_settings.allow_extended_hours:
                return frame
            if "is_regular_hours" in frame.columns:
                return frame[frame["is_regular_hours"].fillna(False)].copy().reset_index(drop=True)
            return frame

        def effective_settings(candidate_rules: dict[str, Any], chosen_settings: BacktestSettings) -> BacktestSettings:
            return _automatic_slippage_settings(
                frame_for_settings(chosen_settings),
                candidate_rules,
                chosen_settings,
                optimizer.automatic_slippage,
            )

        def evaluate(candidate_rules: dict[str, Any], chosen_settings: BacktestSettings) -> dict[str, Any]:
            candidate_strategy = {**source_strategy, "machine_rules": candidate_rules}
            key = (
                bool(chosen_settings.allow_extended_hours),
                int(candidate_rules.get("breakout_lookback_bars") or 20),
                int(candidate_rules.get("opening_range_minutes") or 15),
            )
            if key not in indicator_cache:
                indicator_cache[key] = add_indicators(frame_for_settings(chosen_settings), candidate_strategy)
            return run_backtest(
                [], candidate_strategy, target_symbol, chosen_settings,
                prepared_indicators=indicator_cache[key],
            )
'''
historical_new = r'''        frame_cache: dict[bool, pd.DataFrame] = {True: frame}
        base_indicator_cache: dict[bool, pd.DataFrame] = {}
        indicator_cache: dict[
            tuple[bool, tuple[Any, ...]],
            tuple[pd.DataFrame, list[dict[str, Any]], list[str]],
        ] = {}

        def frame_for_settings(chosen_settings: BacktestSettings) -> pd.DataFrame:
            extended = bool(chosen_settings.allow_extended_hours)
            if extended not in frame_cache:
                if "is_regular_hours" in frame.columns:
                    frame_cache[extended] = frame[
                        frame["is_regular_hours"].fillna(False)
                    ].copy().reset_index(drop=True)
                else:
                    frame_cache[extended] = frame
            return frame_cache[extended]

        def effective_settings(candidate_rules: dict[str, Any], chosen_settings: BacktestSettings) -> BacktestSettings:
            return _automatic_slippage_settings(
                frame_for_settings(chosen_settings),
                candidate_rules,
                chosen_settings,
                optimizer.automatic_slippage,
            )

        def evaluate(candidate_rules: dict[str, Any], chosen_settings: BacktestSettings) -> dict[str, Any]:
            candidate_strategy = {**source_strategy, "machine_rules": candidate_rules}
            extended = bool(chosen_settings.allow_extended_hours)
            if extended not in base_indicator_cache:
                base_indicator_cache[extended] = add_indicators(
                    frame_for_settings(chosen_settings),
                    {"machine_rules": {}},
                )
            key = (extended, strategy_indicator_signature(candidate_strategy))
            if key not in indicator_cache:
                prepared = apply_strategy_specific_indicators(
                    base_indicator_cache[extended],
                    candidate_strategy,
                )
                records, prepared_sessions = prepare_backtest_payload(prepared)
                indicator_cache[key] = (prepared, records, prepared_sessions)
            prepared, records, prepared_sessions = indicator_cache[key]
            return run_backtest(
                [],
                candidate_strategy,
                target_symbol,
                chosen_settings,
                prepared_indicators=prepared,
                prepared_records=records,
                prepared_sessions=prepared_sessions,
            )
'''
source = replace_in_region(
    source,
    "def _optimize_stock_strategies_historical(",
    "\ndef _screen_historical_strategies(",
    historical_old,
    historical_new,
    "historical optimizer cache",
)

# 4) Cheap screening: prepare invariant features once per session mode and reuse them
# across the entire stop/reward grid.
screen_start = source.index("def _screen_historical_strategies(")
screen_end = source.index("\ndef _optimize_stock_timeframes_historical(", screen_start)
screen_old = source[screen_start:screen_end]
screen_new = r'''def _screen_historical_strategies(
    rows: list[dict[str, Any]],
    strategies: list[dict[str, Any]],
    symbol: str,
    settings: BacktestSettings,
    maximum_drawdown_pct: float,
    minimum_historical_trades: int | None = None,
    automatic_slippage: bool = False,
) -> list[dict[str, Any]]:
    """Cheaply rank saved strategies before expensive adaptive optimization.

    Each strategy gets the same small stop/target sweep. This is intentionally only a
    screening stage; the winners are fully optimized afterward.
    """
    frame = bars_to_frame(rows, include_extended_hours=True)
    if frame.empty:
        return []
    ranking_minimum_historical_trades = (
        None if minimum_historical_trades is None else max(1, int(minimum_historical_trades))
    )
    frame_cache: dict[bool, pd.DataFrame] = {True: frame}
    base_indicator_cache: dict[bool, pd.DataFrame] = {}
    indicator_cache: dict[
        tuple[bool, tuple[Any, ...]],
        tuple[pd.DataFrame, list[dict[str, Any]], list[str]],
    ] = {}

    def frame_for_mode(allow_extended_hours: bool) -> pd.DataFrame:
        extended = bool(allow_extended_hours)
        if extended not in frame_cache:
            if "is_regular_hours" in frame.columns:
                frame_cache[extended] = frame[
                    frame["is_regular_hours"].fillna(False)
                ].copy().reset_index(drop=True)
            else:
                frame_cache[extended] = frame
        return frame_cache[extended]

    def prepared_for(
        candidate_strategy: dict[str, Any],
        allow_extended_hours: bool,
    ) -> tuple[pd.DataFrame, list[dict[str, Any]], list[str]]:
        extended = bool(allow_extended_hours)
        if extended not in base_indicator_cache:
            base_indicator_cache[extended] = add_indicators(
                frame_for_mode(extended),
                {"machine_rules": {}},
            )
        key = (extended, strategy_indicator_signature(candidate_strategy))
        if key not in indicator_cache:
            prepared = apply_strategy_specific_indicators(
                base_indicator_cache[extended],
                candidate_strategy,
            )
            records, prepared_sessions = prepare_backtest_payload(prepared)
            indicator_cache[key] = (prepared, records, prepared_sessions)
        return indicator_cache[key]

    candidates: list[dict[str, Any]] = []
    stop_grid = [2.0, 4.0, 5.0, 7.5, 10.0]
    reward_grid = [1.0, 1.5, 2.0, 3.0]
    for strategy in strategies:
        if not isinstance(strategy, dict) or not strategy.get("id"):
            continue
        if str(strategy.get("direction", "long")).lower() not in {"long", "both"}:
            continue
        target = str(strategy.get("optimized_for_symbol") or "").strip().upper()
        if target and target != symbol:
            continue
        original = normalize_machine_rules(strategy.get("machine_rules"))
        baseline_stop = safe_float(original.get("stop_loss_pct"), settings.default_stop_pct) or settings.default_stop_pct
        baseline_reward = safe_float(original.get("reward_risk"), settings.default_reward_risk) or settings.default_reward_risk
        stops = list(dict.fromkeys([round(float(baseline_stop), 4), *stop_grid]))
        rewards = list(dict.fromkeys([round(float(baseline_reward), 4), *reward_grid]))
        best: dict[str, Any] | None = None
        for stop in stops:
            for reward in rewards:
                rules = normalize_machine_rules({**original, "stop_loss_pct": stop, "reward_risk": reward})
                candidate_strategy = {**strategy, "machine_rules": rules}
                base_settings = replace(
                    settings,
                    default_stop_pct=float(stop),
                    default_reward_risk=float(reward),
                )
                for behavior_settings in (base_settings, legacy_behavior_settings(base_settings)):
                    behavior_frame = frame_for_mode(behavior_settings.allow_extended_hours)
                    candidate_settings = _automatic_slippage_settings(
                        behavior_frame, rules, behavior_settings, automatic_slippage
                    )
                    prepared, records, prepared_sessions = prepared_for(
                        candidate_strategy,
                        candidate_settings.allow_extended_hours,
                    )
                    result = run_backtest(
                        [],
                        candidate_strategy,
                        symbol,
                        candidate_settings,
                        prepared_indicators=prepared,
                        prepared_records=records,
                        prepared_sessions=prepared_sessions,
                    )
                    metrics = result.get("metrics") or {}
                    record = {
                        "strategy": strategy,
                        "strategy_id": strategy.get("id"),
                        "strategy_name": strategy.get("name") or "Unnamed strategy",
                        "metrics": metrics,
                        "rules": rules,
                        "settings": candidate_settings,
                    }
                    if best is None or _historical_metric_key(metrics, maximum_drawdown_pct, ranking_minimum_historical_trades) > _historical_metric_key(best["metrics"], maximum_drawdown_pct, ranking_minimum_historical_trades):
                        best = record
        if best is not None:
            candidates.append(best)
    candidates.sort(
        key=lambda item: _historical_metric_key(item["metrics"], maximum_drawdown_pct, ranking_minimum_historical_trades),
        reverse=True,
    )
    return candidates
'''
source = source[:screen_start] + screen_new + source[screen_end:]

# 5) Strict validated optimizer: preserve its base-feature split, but cache regular
# session frames and materialized row/session payloads, and use the complete signature.
validated_old = r'''    base_indicator_cache: dict[tuple[str, bool], pd.DataFrame] = {}
    indicator_cache: dict[
        tuple[str, bool, int, int, int | None, int | None, int | None, float],
        pd.DataFrame,
    ] = {}

    def frame_for_settings(period: str, chosen_settings: BacktestSettings) -> pd.DataFrame:
        candidate_frame = frames[period]
        if chosen_settings.allow_extended_hours:
            return candidate_frame
        if "is_regular_hours" in candidate_frame.columns:
            return candidate_frame[candidate_frame["is_regular_hours"].fillna(False)].copy().reset_index(drop=True)
        return candidate_frame

    def effective_settings(rules: dict[str, Any], chosen_settings: BacktestSettings) -> BacktestSettings:
        return _automatic_slippage_settings(
            frame_for_settings("training", chosen_settings),
            rules,
            chosen_settings,
            optimizer.automatic_slippage,
        )

    def evaluate(candidate_strategy: dict[str, Any], period: str, chosen_settings: BacktestSettings) -> dict[str, Any]:
        rules = normalize_machine_rules(candidate_strategy.get("machine_rules"))
        base_key = (period, bool(chosen_settings.allow_extended_hours))
        if base_key not in base_indicator_cache:
            base_indicator_cache[base_key] = add_indicators(
                frame_for_settings(period, chosen_settings),
                {"machine_rules": {}},
            )
        key = (
            period,
            bool(chosen_settings.allow_extended_hours),
            int(rules.get("breakout_lookback_bars") or 20),
            int(rules.get("opening_range_minutes") or 15),
            int(rules["fast_ema_period"]) if rules.get("fast_ema_period") is not None else None,
            int(rules["slow_ema_period"]) if rules.get("slow_ema_period") is not None else None,
            int(rules["trend_ema_period"]) if rules.get("trend_ema_period") is not None else None,
            round(float(safe_float(rules.get("pullback_touch_tolerance_pct"), 0.5) or 0.5), 8),
        )
        if key not in indicator_cache:
            indicator_cache[key] = apply_strategy_specific_indicators(
                base_indicator_cache[base_key],
                candidate_strategy,
            )
        return run_backtest(
            [],
            candidate_strategy,
            target_symbol,
            chosen_settings,
            prepared_indicators=indicator_cache[key],
        )
'''
validated_new = r'''    frame_cache: dict[tuple[str, bool], pd.DataFrame] = {
        (period, True): candidate_frame
        for period, candidate_frame in frames.items()
    }
    base_indicator_cache: dict[tuple[str, bool], pd.DataFrame] = {}
    indicator_cache: dict[
        tuple[str, bool, tuple[Any, ...]],
        tuple[pd.DataFrame, list[dict[str, Any]], list[str]],
    ] = {}

    def frame_for_settings(period: str, chosen_settings: BacktestSettings) -> pd.DataFrame:
        key = (period, bool(chosen_settings.allow_extended_hours))
        if key not in frame_cache:
            candidate_frame = frames[period]
            if "is_regular_hours" in candidate_frame.columns:
                frame_cache[key] = candidate_frame[
                    candidate_frame["is_regular_hours"].fillna(False)
                ].copy().reset_index(drop=True)
            else:
                frame_cache[key] = candidate_frame
        return frame_cache[key]

    def effective_settings(rules: dict[str, Any], chosen_settings: BacktestSettings) -> BacktestSettings:
        return _automatic_slippage_settings(
            frame_for_settings("training", chosen_settings),
            rules,
            chosen_settings,
            optimizer.automatic_slippage,
        )

    def evaluate(candidate_strategy: dict[str, Any], period: str, chosen_settings: BacktestSettings) -> dict[str, Any]:
        base_key = (period, bool(chosen_settings.allow_extended_hours))
        if base_key not in base_indicator_cache:
            base_indicator_cache[base_key] = add_indicators(
                frame_for_settings(period, chosen_settings),
                {"machine_rules": {}},
            )
        key = (
            period,
            bool(chosen_settings.allow_extended_hours),
            strategy_indicator_signature(candidate_strategy),
        )
        if key not in indicator_cache:
            prepared = apply_strategy_specific_indicators(
                base_indicator_cache[base_key],
                candidate_strategy,
            )
            records, prepared_sessions = prepare_backtest_payload(prepared)
            indicator_cache[key] = (prepared, records, prepared_sessions)
        prepared, records, prepared_sessions = indicator_cache[key]
        return run_backtest(
            [],
            candidate_strategy,
            target_symbol,
            chosen_settings,
            prepared_indicators=prepared,
            prepared_records=records,
            prepared_sessions=prepared_sessions,
        )
'''
source = replace_in_region(
    source,
    "def optimize_stock_strategies(",
    "\ndef combine_stock_timeframe_reports(",
    validated_old,
    validated_new,
    "validated optimizer cache",
)

ast.parse(source, filename=str(PATH))
PATH.write_text(source, encoding="utf-8")
print("optimizer feature-reuse patch applied")
