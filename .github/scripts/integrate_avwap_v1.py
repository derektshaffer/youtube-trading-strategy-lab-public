from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def write(name: str, content: str) -> None:
    (ROOT / name).write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Missing patch anchor: {label}")
    return text.replace(old, new, 1)


def function_span(text: str, name: str) -> tuple[int, int]:
    start = text.find(f"def {name}(")
    if start < 0:
        raise RuntimeError(f"Function not found: {name}")
    next_def = text.find("\ndef ", start + 1)
    next_class = text.find("\n@dataclass", start + 1)
    candidates = [value for value in (next_def, next_class) if value >= 0]
    end = min(candidates) if candidates else len(text)
    return start, end


def patch_function(text: str, name: str, transform) -> str:
    start, end = function_span(text, name)
    block = text[start:end]
    updated = transform(block)
    if updated == block:
        raise RuntimeError(f"Patch made no change in {name}")
    return text[:start] + updated + text[end:]


def insert_before_last_return(block: str, statement: str) -> str:
    marker = "    return data"
    position = block.rfind(marker)
    if position < 0:
        raise RuntimeError("return data not found")
    return block[:position] + statement + block[position:]


def patch_engine() -> None:
    path = "youtube_strategy_engine.py"
    text = read(path)
    if "avwap_anchor_mode" in text and "apply_anchored_vwap_indicators" in text:
        return

    text = replace_once(
        text,
        "import pandas as pd\n",
        "import pandas as pd\n\nfrom anchored_vwap_engine import (\n    SUPPORTED_AVWAP_ANCHOR_MODES,\n    apply_anchored_vwap_indicators,\n)\n",
        "engine AVWAP import",
    )
    text = replace_once(
        text,
        '        "max_vwap_distance_pct": NULLABLE_NUMBER,\n',
        '        "max_vwap_distance_pct": NULLABLE_NUMBER,\n'
        '        "avwap_anchor_mode": NULLABLE_STRING,\n'
        '        "avwap_pivot_confirm_bars": NULLABLE_INTEGER,\n'
        '        "avwap_anchor_session_minute": NULLABLE_INTEGER,\n'
        '        "require_price_above_avwap": NULLABLE_BOOLEAN,\n'
        '        "avwap_reclaim": NULLABLE_BOOLEAN,\n'
        '        "max_avwap_distance_pct": NULLABLE_NUMBER,\n'
        '        "require_avwap_rising": NULLABLE_BOOLEAN,\n'
        '        "require_avwap_pullback": NULLABLE_BOOLEAN,\n'
        '        "avwap_pullback_tolerance_pct": NULLABLE_NUMBER,\n'
        '        "stop_below_avwap": NULLABLE_BOOLEAN,\n'
        '        "stop_avwap_buffer_pct": NULLABLE_NUMBER,\n'
        '        "exit_below_avwap": NULLABLE_BOOLEAN,\n',
        "engine schema AVWAP fields",
    )

    # Teach future source extraction to preserve AVWAP anchors instead of collapsing them to session VWAP.
    text = replace_once(
        text,
        "- Preserve explicitly named EMA periods. For moving-average setups, use fast_ema_period,\n",
        "- Preserve anchored-VWAP structure explicitly. When the source clearly identifies a causal anchor,\n"
        "  use avwap_anchor_mode with one of the supported modes rather than substituting session VWAP.\n"
        "  Use require_price_above_avwap, avwap_reclaim, require_avwap_rising, require_avwap_pullback,\n"
        "  stop_below_avwap, or exit_below_avwap only when the source states that relationship. If the\n"
        "  source uses several simultaneous AVWAPs, an IPO-only anchor, multi-day persistence, or a\n"
        "  discretionary event anchor that cannot be reproduced causally, preserve it as unresolved.\n"
        "- Preserve explicitly named EMA periods. For moving-average setups, use fast_ema_period,\n",
        "video extraction AVWAP guidance",
    )

    def normalize(block: str) -> str:
        block = block.replace(
            '"max_spread_pct", "max_vwap_distance_pct", "volume_surge_ratio", "stop_loss_pct", "reward_risk",',
            '"max_spread_pct", "max_vwap_distance_pct", "max_avwap_distance_pct", "avwap_pullback_tolerance_pct", "stop_avwap_buffer_pct", "volume_surge_ratio", "stop_loss_pct", "reward_risk",',
        )
        block = block.replace(
            '"fast_ema_period", "slow_ema_period", "trend_ema_period", "max_pullback_number",',
            '"fast_ema_period", "slow_ema_period", "trend_ema_period", "max_pullback_number",\n        "avwap_pivot_confirm_bars", "avwap_anchor_session_minute",',
        )
        block = block.replace(
            '"stop_below_fast_ema", "exit_below_vwap", "exit_below_fast_ema",',
            '"stop_below_fast_ema", "exit_below_vwap", "exit_below_fast_ema",\n        "require_price_above_avwap", "avwap_reclaim", "require_avwap_rising",\n        "require_avwap_pullback", "stop_below_avwap", "exit_below_avwap",',
        )
        block = block.replace(
            '    for name in MACHINE_RULE_SCHEMA["properties"]:\n',
            '    string_fields = {"avwap_anchor_mode"}\n'
            '    for name in MACHINE_RULE_SCHEMA["properties"]:\n',
            1,
        )
        block = block.replace(
            '        elif name in boolean_fields:\n            result[name] = safe_bool(value)\n        else:\n            text = str(value).strip() if value is not None else ""\n            result[name] = text if re.fullmatch(r"(?:[01]\\d|2[0-3]):[0-5]\\d", text) else None\n',
            '        elif name in boolean_fields:\n            result[name] = safe_bool(value)\n'
            '        elif name in string_fields:\n            text = str(value).strip().casefold() if value is not None else ""\n            result[name] = text or None\n'
            '        else:\n            text = str(value).strip() if value is not None else ""\n            result[name] = text if re.fullmatch(r"(?:[01]\\d|2[0-3]):[0-5]\\d", text) else None\n',
            1,
        )
        # The second numeric validation set is a separate literal in this function.
        block = block.replace(
            '"min_previous_day_volume_ratio", "max_spread_pct", "max_vwap_distance_pct",\n        "volume_surge_ratio", "stop_loss_pct", "reward_risk",',
            '"min_previous_day_volume_ratio", "max_spread_pct", "max_vwap_distance_pct",\n        "max_avwap_distance_pct", "avwap_pullback_tolerance_pct", "stop_avwap_buffer_pct",\n        "volume_surge_ratio", "stop_loss_pct", "reward_risk",',
        )
        validation_anchor = '    if result["stop_loss_pct"] is not None and not 0 < result["stop_loss_pct"] < 100:\n'
        if validation_anchor not in block:
            raise RuntimeError("normalize validation anchor missing")
        avwap_validation = (
            '    if result.get("avwap_anchor_mode") not in SUPPORTED_AVWAP_ANCHOR_MODES:\n'
            '        result["avwap_anchor_mode"] = None\n'
            '    if result.get("avwap_pivot_confirm_bars") is not None and not 1 <= int(result["avwap_pivot_confirm_bars"]) <= 20:\n'
            '        result["avwap_pivot_confirm_bars"] = None\n'
            '    if result.get("avwap_anchor_session_minute") is not None and not 0 <= int(result["avwap_anchor_session_minute"]) <= 390:\n'
            '        result["avwap_anchor_session_minute"] = None\n'
            '    if result.get("max_avwap_distance_pct") is not None and result["max_avwap_distance_pct"] > 100:\n'
            '        result["max_avwap_distance_pct"] = None\n'
            '    if result.get("avwap_pullback_tolerance_pct") is not None and result["avwap_pullback_tolerance_pct"] > 20:\n'
            '        result["avwap_pullback_tolerance_pct"] = None\n'
            '    if result.get("stop_avwap_buffer_pct") is not None and result["stop_avwap_buffer_pct"] > 20:\n'
            '        result["stop_avwap_buffer_pct"] = None\n'
        )
        block = block.replace(validation_anchor, avwap_validation + validation_anchor, 1)
        return block

    text = patch_function(text, "normalize_machine_rules", normalize)

    for function_name in ("add_indicators", "apply_strategy_specific_indicators"):
        def add_avwap(block: str) -> str:
            return insert_before_last_return(
                block,
                '    data = apply_anchored_vwap_indicators(data, rules)\n',
            )
        text = patch_function(text, function_name, add_avwap)

    def evaluate(block: str) -> str:
        anchor = '    for rule_name, field_name in (\n'
        if anchor not in block:
            raise RuntimeError("evaluate_signal AVWAP insertion anchor missing")
        checks = '''    if rules.get("avwap_anchor_mode") is not None and not has_number("avwap"):\n        return False\n    if rules.get("require_price_above_avwap") is True and (not has_number("avwap") or close <= float(row["avwap"])):\n        return False\n    if rules.get("require_price_above_avwap") is False and (not has_number("avwap") or close >= float(row["avwap"])):\n        return False\n    if rules.get("avwap_reclaim"):\n        if not all(has_number(name) for name in ("previous_close", "previous_avwap", "avwap")):\n            return False\n        if not (float(row["previous_close"]) <= float(row["previous_avwap"]) and close > float(row["avwap"])):\n            return False\n    max_avwap_distance = safe_float(rules.get("max_avwap_distance_pct"))\n    if max_avwap_distance is not None:\n        if not has_number("avwap_distance_pct") or abs(float(row["avwap_distance_pct"])) > max_avwap_distance:\n            return False\n    if rules.get("require_avwap_rising") is True and not bool(row.get("avwap_rising")):\n        return False\n    if rules.get("require_avwap_rising") is False:\n        if not has_number("previous_avwap") or not has_number("avwap") or float(row["avwap"]) >= float(row["previous_avwap"]):\n            return False\n    if rules.get("require_avwap_pullback") is True and not bool(row.get("avwap_pullback_recent")):\n        return False\n\n'''
        return block.replace(anchor, checks + anchor, 1)

    text = patch_function(text, "evaluate_signal", evaluate)

    def run_backtest(block: str) -> str:
        stop_anchor = '                risk_per_share = entry - stop_price\n'
        if stop_anchor not in block:
            raise RuntimeError("run_backtest structural stop anchor missing")
        avwap_stop = '''                if rules.get("stop_below_avwap") is True:\n                    signal_avwap = safe_float(previous.get("avwap"))\n                    if signal_avwap is not None and signal_avwap > 0:\n                        avwap_buffer = max(0.0, safe_float(rules.get("stop_avwap_buffer_pct"), 0.0) or 0.0)\n                        structural_stop = signal_avwap * (1.0 - avwap_buffer / 100.0)\n                        if 0 < structural_stop < entry:\n                            stop_price = structural_stop\n'''
        block = block.replace(stop_anchor, avwap_stop + stop_anchor, 1)

        exit_anchor = '            elif reason is None and max_hold is not None:\n'
        if exit_anchor not in block:
            raise RuntimeError("run_backtest exit anchor missing")
        avwap_exit = '''            elif reason is None and rules.get("exit_below_avwap") is True and safe_float(current.get("avwap")) is not None and float(current["close"]) < float(current["avwap"]):\n                raw_exit = float(current["close"])\n                reason = "Anchored VWAP loss"\n'''
        block = block.replace(exit_anchor, avwap_exit + exit_anchor, 1)
        return block

    text = patch_function(text, "run_backtest", run_backtest)

    # Any AVWAP close-loss exit is dynamic management and must suppress a fabricated default target.
    text = text.replace(
        '            "exit_below_fast_ema",\n',
        '            "exit_below_fast_ema",\n            "exit_below_avwap",\n',
    )

    # Add AVWAP numeric parameters to coarse/fine optimizer neighborhoods.
    text = text.replace(
        '("max_vwap_distance_pct", (0.50, 0.70, 0.85, 1.20, 1.50, 2.0), 0.05, 100.0, False),\n',
        '("max_vwap_distance_pct", (0.50, 0.70, 0.85, 1.20, 1.50, 2.0), 0.05, 100.0, False),\n'
        '        ("max_avwap_distance_pct", (0.50, 0.70, 0.85, 1.20, 1.50, 2.0), 0.05, 100.0, False),\n'
        '        ("avwap_pivot_confirm_bars", (0.50, 0.75, 1.50, 2.0), 1.0, 10.0, True),\n'
        '        ("avwap_pullback_tolerance_pct", (0.50, 0.75, 1.25, 1.50, 2.0), 0.05, 10.0, False),\n'
        '        ("stop_avwap_buffer_pct", (0.50, 0.75, 1.25, 1.50, 2.0), 0.0, 10.0, False),\n',
        1,
    )
    text = text.replace(
        '"max_vwap_distance_pct": ((2.0, 1.0, 0.5, 0.25), 0.05, 100.0, False),\n',
        '"max_vwap_distance_pct": ((2.0, 1.0, 0.5, 0.25), 0.05, 100.0, False),\n'
        '        "max_avwap_distance_pct": ((2.0, 1.0, 0.5, 0.25), 0.05, 100.0, False),\n'
        '        "avwap_pivot_confirm_bars": ((2.0, 1.0), 1.0, 10.0, True),\n'
        '        "avwap_pullback_tolerance_pct": ((0.75, 0.50, 0.25, 0.10), 0.05, 10.0, False),\n'
        '        "stop_avwap_buffer_pct": ((0.50, 0.25, 0.10, 0.05), 0.0, 10.0, False),\n',
        1,
    )
    text = text.replace(
        '"max_vwap_distance_pct": ((0.50, 0.25, 0.10), 0.05, 100.0, False),\n',
        '"max_vwap_distance_pct": ((0.50, 0.25, 0.10), 0.05, 100.0, False),\n'
        '        "max_avwap_distance_pct": ((0.50, 0.25, 0.10), 0.05, 100.0, False),\n'
        '        "avwap_pivot_confirm_bars": ((1.0,), 1.0, 10.0, True),\n'
        '        "avwap_pullback_tolerance_pct": ((0.25, 0.10, 0.05), 0.05, 10.0, False),\n'
        '        "stop_avwap_buffer_pct": ((0.20, 0.10, 0.05), 0.0, 10.0, False),\n',
        1,
    )

    def chart(block: str) -> str:
        block = block.replace(
            '        "fast_ema_pullback_recent", "fast_ema_pullback_number", "pullback_breakout",\n',
            '        "fast_ema_pullback_recent", "fast_ema_pullback_number", "pullback_breakout",\n'
            '        "avwap", "previous_avwap", "avwap_rising", "avwap_pullback_recent",\n'
            '        "avwap_anchor_active", "avwap_reclaim",\n',
            1,
        )
        block = block.replace(
            '    for field_name in ("fast_ema", "slow_ema", "trend_ema", "fast_ema_pullback_number"):\n',
            '    for field_name in ("fast_ema", "slow_ema", "trend_ema", "fast_ema_pullback_number", "avwap", "previous_avwap"):\n',
            1,
        )
        block = block.replace(
            '    for field_name in ("fast_ema_rising", "fast_ema_pullback_recent", "pullback_breakout"):\n',
            '    for field_name in ("fast_ema_rising", "fast_ema_pullback_recent", "pullback_breakout", "avwap_rising", "avwap_pullback_recent", "avwap_anchor_active"):\n',
            1,
        )
        anchor = '    if rules.get("previous_day_high_breakout"):\n'
        if anchor not in block:
            raise RuntimeError("chart_trigger_checks AVWAP reclaim anchor missing")
        reclaim = '''    if rules.get("avwap_reclaim"):\n        recent = enriched.tail(3)\n        eligible = recent.dropna(subset=["previous_close", "previous_avwap", "avwap"])\n        if not eligible.empty:\n            crosses = (eligible["previous_close"] <= eligible["previous_avwap"]) & (eligible["close"] > eligible["avwap"])\n            outcome["avwap_reclaim"] = bool(crosses.any())\n\n'''
        return block.replace(anchor, reclaim + anchor, 1)

    text = patch_function(text, "chart_trigger_checks", chart)

    def match(block: str) -> str:
        anchor = '    for rule_name, chart_field, label in (\n'
        if anchor not in block:
            raise RuntimeError("match_strategy AVWAP insertion anchor missing")
        checks = '''    avwap_value = safe_float(chart_checks.get("avwap"))\n    if rules.get("avwap_anchor_mode") is not None:\n        checks.append({\n            "label": "Anchored VWAP available",\n            "actual": avwap_value,\n            "required": rules.get("avwap_anchor_mode"),\n            "status": "pass" if avwap_value is not None else "unknown",\n        })\n    if rules.get("require_price_above_avwap") is not None:\n        price_value = safe_float(metrics.get("price"))\n        required = bool(rules.get("require_price_above_avwap"))\n        actual = None if price_value is None or avwap_value is None else price_value > avwap_value\n        checks.append({\n            "label": "Price above anchored VWAP",\n            "actual": actual,\n            "required": required,\n            "status": "unknown" if actual is None else ("pass" if actual == required else "fail"),\n        })\n    if rules.get("require_avwap_rising") is not None:\n        observed = chart_checks.get("avwap_rising")\n        required = bool(rules.get("require_avwap_rising"))\n        checks.append({\n            "label": "Anchored VWAP rising",\n            "actual": observed,\n            "required": required,\n            "status": "unknown" if observed is None else ("pass" if bool(observed) == required else "fail"),\n        })\n    if rules.get("require_avwap_pullback") is True:\n        observed = chart_checks.get("avwap_pullback_recent")\n        checks.append({\n            "label": "Recent pullback to anchored VWAP",\n            "actual": observed,\n            "required": True,\n            "status": "unknown" if observed is None else ("pass" if bool(observed) else "fail"),\n        })\n    if rules.get("avwap_reclaim") is True:\n        observed = chart_checks.get("avwap_reclaim")\n        checks.append({\n            "label": "Anchored VWAP reclaim",\n            "actual": observed,\n            "required": True,\n            "status": "unknown" if observed is None else ("pass" if bool(observed) else "fail"),\n        })\n\n'''
        block = block.replace(anchor, checks + anchor, 1)
        stop_anchor = '    target = price + (price - stop) * ratio if price and stop is not None and ratio else None\n'
        if stop_anchor not in block:
            raise RuntimeError("match_strategy stop calculation anchor missing")
        stop_adjust = '''    if rules.get("stop_below_avwap") is True and avwap_value is not None and price:\n        buffer_pct = max(0.0, safe_float(rules.get("stop_avwap_buffer_pct"), 0.0) or 0.0)\n        candidate_stop = avwap_value * (1.0 - buffer_pct / 100.0)\n        if 0 < candidate_stop < price:\n            stop = candidate_stop\n'''
        block = block.replace(stop_anchor, stop_adjust + stop_anchor, 1)
        return block

    text = patch_function(text, "match_strategy", match)
    write(path, text)


def patch_core() -> None:
    path = "trading_intelligence_core.py"
    text = read(path)
    if "Multi-anchor AVWAP compression structure" in text:
        return

    text = text.replace("NATIVE_RULE_SCHEMA_VERSION = 4", "NATIVE_RULE_SCHEMA_VERSION = 5", 1)
    text = replace_once(
        text,
        "- Preserve the author's trade-management logic instead of substituting a generic fixed target.\n",
        "- Preserve anchored-VWAP structure. If the source clearly states the anchor event, trend direction,\n"
        "  pullback/reclaim relationship, stop, or exit, map those to the AVWAP machine fields. Do not\n"
        "  substitute session VWAP. Multi-anchor pinches, IPO-only context, and multi-day anchors stay\n"
        "  unresolved until the historical engine can reproduce that extra context.\n"
        "- Preserve the author's trade-management logic instead of substituting a generic fixed target.\n",
        "book extraction AVWAP guidance",
    )

    # Rule compiler guidance.
    compiler_anchor = '- Preserve exit logic. An explicit "exit/close if VWAP is lost" can map to exit_below_vwap=true;\n'
    if compiler_anchor in text:
        text = text.replace(
            compiler_anchor,
            '- Preserve anchored-VWAP logic when it is causal and source-defined. Use avwap_anchor_mode only\n'
            '  for an identifiable anchor such as a confirmed swing low/high, higher-low/lower-high handoff,\n'
            '  breakout bar, previous-day-high break, or explicit session minute. Numeric pivot confirmation,\n'
            '  pullback tolerance, and stop buffers are RESEARCH ASSUMPTIONS unless the source states them.\n'
            + compiler_anchor,
            1,
        )

    # Explicit/native AVWAP migrations + labeled research assumptions.
    def upgrade(block: str) -> str:
        anchor = '    item["machine_rules"] = rules\n'
        if anchor not in block:
            raise RuntimeError("upgrade_native_strategy_rules AVWAP anchor missing")
        migration = '''    avwap_language = "anchored vwap" in text or "avwap" in text\n    if avwap_language:\n        direction = str(item.get("direction") or "").strip().casefold()\n        mode = rules.get("avwap_anchor_mode")\n        if mode is None:\n            if "higher low" in text or ("handoff" in text and direction in {"long", "both"}):\n                mode = "higher_low_handoff"\n            elif "lower high" in text or ("handoff" in text and direction == "short"):\n                mode = "lower_high_handoff"\n            elif "swing low" in text or ("cross purchase" in text and "dip" in text) or "rising avwap" in text:\n                mode = "swing_low"\n            elif "swing high" in text or ("cross short" in text and "rip" in text) or "declining avwap" in text:\n                mode = "swing_high"\n            elif re.search(r"anchor(?:ed|ing)?[^.]{0,50}(?:previous|prior)[^.]{0,30}day[^.]{0,20}high", text):\n                mode = "previous_day_high_break"\n            elif re.search(r"anchor(?:ed|ing)?[^.]{0,50}breakout(?: bar)?", text):\n                mode = "breakout_bar"\n            elif re.search(r"anchor(?:ed|ing)?[^.]{0,60}(?:second|2nd) minute", text):\n                mode = "session_minute"\n                if rules.get("avwap_anchor_session_minute") is None:\n                    rules["avwap_anchor_session_minute"] = 1\n                    explicit_migrations.append({\n                        "rule": "avwap_anchor_session_minute",\n                        "value": 1,\n                        "basis": "Saved source text explicitly anchors AVWAP to the second session minute.",\n                    })\n                    changed = True\n        if mode is not None and rules.get("avwap_anchor_mode") is None:\n            rules["avwap_anchor_mode"] = mode\n            explicit_migrations.append({\n                "rule": "avwap_anchor_mode",\n                "value": mode,\n                "basis": "Saved source text identifies a causal anchored-VWAP reference structure.",\n            })\n            changed = True\n\n        if rules.get("avwap_anchor_mode") in {"swing_low", "swing_high", "higher_low_handoff", "lower_high_handoff"}:\n            if overrides.get("avwap_pivot_confirm_bars") is None and rules.get("avwap_pivot_confirm_bars") is None:\n                overrides["avwap_pivot_confirm_bars"] = 2\n                assumptions = list(item.get("compiler_assumptions") or [])\n                if not any(isinstance(record, dict) and record.get("target_rule") == "avwap_pivot_confirm_bars" for record in assumptions):\n                    assumptions.append({\n                        "target_rule": "avwap_pivot_confirm_bars",\n                        "value": 2,\n                        "source_requirement": "Use a causally confirmed AVWAP swing/handoff anchor.",\n                        "rationale": "Two right-side bars are a starting research assumption, not an author-stated threshold.",\n                        "confidence": 80.0,\n                        "accepted_at": _utc_iso(),\n                        "model": "native-rule-upgrade",\n                        "accepted_by": "ai_autopilot",\n                        "is_research_assumption": True,\n                    })\n                item["compiler_assumptions"] = assumptions[-150:]\n                changed = True\n\n        if "rising avwap" in text and rules.get("require_avwap_rising") is None:\n            rules["require_avwap_rising"] = True\n            explicit_migrations.append({"rule": "require_avwap_rising", "value": True, "basis": "Saved source text explicitly requires a rising AVWAP."})\n            changed = True\n        if "declining avwap" in text and rules.get("require_avwap_rising") is None:\n            rules["require_avwap_rising"] = False\n            explicit_migrations.append({"rule": "require_avwap_rising", "value": False, "basis": "Saved source text explicitly requires a declining AVWAP."})\n            changed = True\n        if any(phrase in text for phrase in ("above avwap", "above the avwap", "above anchored vwap", "above the anchored vwap")) and rules.get("require_price_above_avwap") is None:\n            rules["require_price_above_avwap"] = True\n            explicit_migrations.append({"rule": "require_price_above_avwap", "value": True, "basis": "Saved source text explicitly requires price above AVWAP."})\n            changed = True\n        if any(phrase in text for phrase in ("below avwap", "below the avwap", "below anchored vwap", "below the anchored vwap")) and rules.get("require_price_above_avwap") is None:\n            rules["require_price_above_avwap"] = False\n            explicit_migrations.append({"rule": "require_price_above_avwap", "value": False, "basis": "Saved source text explicitly requires price below AVWAP."})\n            changed = True\n        if "reclaim" in text and rules.get("avwap_reclaim") is None:\n            rules["avwap_reclaim"] = True\n            explicit_migrations.append({"rule": "avwap_reclaim", "value": True, "basis": "Saved source text explicitly describes an AVWAP reclaim."})\n            changed = True\n        if any(phrase in text for phrase in ("pullback", "pull back", "support")) and rules.get("require_avwap_pullback") is None:\n            rules["require_avwap_pullback"] = True\n            explicit_migrations.append({"rule": "require_avwap_pullback", "value": True, "basis": "Saved source text explicitly uses AVWAP as pullback/support structure."})\n            changed = True\n            if overrides.get("avwap_pullback_tolerance_pct") is None and rules.get("avwap_pullback_tolerance_pct") is None:\n                overrides["avwap_pullback_tolerance_pct"] = 0.5\n                assumptions = list(item.get("compiler_assumptions") or [])\n                if not any(isinstance(record, dict) and record.get("target_rule") == "avwap_pullback_tolerance_pct" for record in assumptions):\n                    assumptions.append({\n                        "target_rule": "avwap_pullback_tolerance_pct",\n                        "value": 0.5,\n                        "source_requirement": "Price pulls back near/to AVWAP support.",\n                        "rationale": "0.5% is a starting research tolerance, not an author-stated distance.",\n                        "confidence": 75.0,\n                        "accepted_at": _utc_iso(),\n                        "model": "native-rule-upgrade",\n                        "accepted_by": "ai_autopilot",\n                        "is_research_assumption": True,\n                    })\n                item["compiler_assumptions"] = assumptions[-150:]\n                changed = True\n\n        avwap_exit_text = " ".join(str(value or "") for value in (*(item.get("exit_conditions") or []), *(item.get("risk_rules") or []))).casefold()\n        if rules.get("exit_below_avwap") is None and re.search(r"(?:exit|sell|close)[^.]{0,80}(?:below|lose|loses)[^.]{0,40}(?:avwap|anchored vwap)", avwap_exit_text):\n            rules["exit_below_avwap"] = True\n            explicit_migrations.append({"rule": "exit_below_avwap", "value": True, "basis": "Saved source text explicitly exits on loss of AVWAP."})\n            changed = True\n        if rules.get("stop_below_avwap") is None and re.search(r"stop[^.]{0,80}below[^.]{0,40}(?:avwap|anchored vwap)", avwap_exit_text):\n            rules["stop_below_avwap"] = True\n            explicit_migrations.append({"rule": "stop_below_avwap", "value": True, "basis": "Saved source text explicitly places the stop below AVWAP."})\n            changed = True\n            if overrides.get("stop_avwap_buffer_pct") is None and rules.get("stop_avwap_buffer_pct") is None:\n                overrides["stop_avwap_buffer_pct"] = 0.3\n                changed = True\n\n'''
        return block.replace(anchor, migration + anchor, 1)

    text = patch_function(text, "upgrade_native_strategy_rules", upgrade)

    # Replace the blanket AVWAP block with requirement-specific fidelity checks.
    old = '''    if any(phrase in text for phrase in ("anchored vwap", "avwap")):\n        add(\n            "Anchored VWAP structure",\n            dimension="structure",\n            modeled_override=False,\n            limitation="The current VWAP rule is session VWAP, not a source-defined anchored VWAP.",\n        )\n'''
    if old not in text:
        raise RuntimeError("strategy_semantic_coverage legacy AVWAP block missing")
    new = '''    if any(phrase in text for phrase in ("anchored vwap", "avwap")):\n        avwap_mode = rules.get("avwap_anchor_mode")\n        add(\n            "Anchored VWAP structure",\n            ("avwap_anchor_mode",),\n            dimension="structure",\n            modeled_override=bool(avwap_mode),\n            limitation="A causal AVWAP anchor has not yet been identified for this source strategy.",\n        )\n        if avwap_mode in {"swing_low", "swing_high", "higher_low_handoff", "lower_high_handoff"}:\n            add(\n                "Causal AVWAP pivot confirmation",\n                ("avwap_pivot_confirm_bars",),\n                dimension="structure",\n                limitation="Swing/handoff anchors require an explicit or research-assumption confirmation window.",\n            )\n        if "rising avwap" in text or "declining avwap" in text:\n            add("AVWAP trend direction", ("require_avwap_rising",), dimension="structure")\n        if "reclaim" in text:\n            add("Anchored VWAP reclaim", ("avwap_reclaim",), dimension="entry")\n        if any(phrase in text for phrase in ("pullback", "pull back", "support")):\n            add("Anchored VWAP pullback/support", ("require_avwap_pullback",), dimension="entry")\n            add("Objective AVWAP pullback tolerance", ("avwap_pullback_tolerance_pct",), dimension="entry")\n        if any(phrase in text for phrase in ("compression", "pinch", "multiple anchored vwap", "multiple avwap")):\n            add(\n                "Multi-anchor AVWAP compression structure",\n                dimension="structure",\n                modeled_override=False,\n                limitation="AVWAP v1 models one causal anchor at a time; multi-anchor pinch/compression logic is intentionally not approximated.",\n            )\n        if any(phrase in text for phrase in ("ipo day-one", "ipo day one", "first trading day of an ipo")):\n            add(\n                "Historical IPO day-one context",\n                dimension="universe",\n                modeled_override=False,\n                limitation="The historical engine does not yet have point-in-time IPO listing-date context.",\n            )\n        if any(phrase in text for phrase in ("multi-day avwap", "multi day avwap", "day two")):\n            add(\n                "Multi-day AVWAP persistence",\n                dimension="structure",\n                modeled_override=False,\n                limitation="AVWAP v1 intentionally resets supported anchors by session and does not yet carry an anchor across trading days.",\n            )\n'''
    text = text.replace(old, new, 1)

    # Paper Auto must remain fail-closed until live execution has proven AVWAP parity.
    paper_anchor = '    unsupported = [\n        label\n        for rule_name, label in PAPER_EXECUTION_UNSUPPORTED_DYNAMIC_EXITS.items()\n        if rules.get(rule_name) is not None and rules.get(rule_name) is not False\n    ]\n'
    if paper_anchor not in text:
        raise RuntimeError("paper_execution_fidelity unsupported anchor missing")
    text = text.replace(
        paper_anchor,
        paper_anchor +
        '    if rules.get("avwap_anchor_mode") is not None:\n'
        '        unsupported.append("Anchored VWAP signal/management parity")\n',
        1,
    )
    write(path, text)


def patch_live_runner() -> None:
    path = "live_strategy_runner_page.py"
    text = read(path)
    if '"avwap_anchor_mode",' in text:
        return
    anchor = '            "vwap_reclaim",\n'
    if anchor not in text:
        raise RuntimeError("live needs_chart_candles anchor missing")
    text = text.replace(
        anchor,
        anchor +
        '            "avwap_anchor_mode",\n'
        '            "require_price_above_avwap",\n'
        '            "avwap_reclaim",\n'
        '            "require_avwap_rising",\n'
        '            "require_avwap_pullback",\n'
        '            "stop_below_avwap",\n'
        '            "exit_below_avwap",\n',
        1,
    )
    write(path, text)


def patch_validation_workflow() -> None:
    path = ".github/workflows/validate-trading-intelligence.yml"
    text = read(path)
    if 'test_avwap_integration.py' not in text:
        # Add push path and PR path using the stable requirements anchor in each block.
        text = text.replace(
            '      - "requirements.txt"\n',
            '      - "anchored_vwap_engine.py"\n      - "test_anchored_vwap_engine.py"\n      - "test_avwap_integration.py"\n      - "requirements.txt"\n',
            1,
        )
        second = text.find('      - "requirements.txt"\n', text.find('pull_request:'))
        if second >= 0:
            text = text[:second] + '      - "anchored_vwap_engine.py"\n      - "test_anchored_vwap_engine.py"\n      - "test_avwap_integration.py"\n' + text[second:]
        compile_anchor = '          python -m py_compile youtube_strategy_engine.py\n'
        text = replace_once(
            text,
            compile_anchor,
            compile_anchor + '          python -m py_compile anchored_vwap_engine.py\n',
            "validation AVWAP compile",
        )
        run_anchor = 'test_application_security.py -q\n'
        if run_anchor not in text:
            raise RuntimeError("validation test command anchor missing")
        text = text.replace(
            run_anchor,
            'test_application_security.py test_anchored_vwap_engine.py test_avwap_integration.py -q\n',
            1,
        )
    write(path, text)


def main() -> None:
    patch_engine()
    patch_core()
    patch_live_runner()
    patch_validation_workflow()


if __name__ == "__main__":
    main()
