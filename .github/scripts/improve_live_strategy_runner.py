from pathlib import Path

engine_path = Path("youtube_strategy_engine.py")
runner_path = Path("live_strategy_runner_page.py")
engine = engine_path.read_text(encoding="utf-8")
runner = runner_path.read_text(encoding="utf-8")

# 1) Make live matching enforce the same saved entry-time window used by the backtest,
# and treat an unavailable catalyst lookup as VERIFY rather than a false hard failure.
old_catalyst = '''    if rules.get("catalyst_required"):
        checks.append({"label": "Recent news catalyst", "actual": bool(metrics.get("has_catalyst")), "required": True, "status": "pass" if metrics.get("has_catalyst") else "fail"})

    # These triggers require actual recent bars; do not pretend a snapshot proves them.
'''
new_catalyst = '''    if rules.get("catalyst_required"):
        catalyst_value = metrics.get("has_catalyst")
        catalyst_status = "unknown" if catalyst_value is None else ("pass" if bool(catalyst_value) else "fail")
        checks.append(
            {
                "label": "Recent news catalyst",
                "actual": catalyst_value,
                "required": True,
                "status": catalyst_status,
            }
        )

    # The historical backtest already enforces session_start/session_end. The live
    # matcher must enforce the same saved entry window so an afternoon snapshot
    # cannot be presented as eligible for a morning-only strategy.
    session_start = parse_clock_minutes(rules.get("session_start"))
    session_end = parse_clock_minutes(rules.get("session_end"))
    if session_start is not None or session_end is not None:
        now_et = utc_now().astimezone(ET)
        clock_minute = now_et.hour * 60 + now_et.minute
        earliest = session_start if session_start is not None else 0
        latest = session_end if session_end is not None else 23 * 60 + 59
        if session_start is not None and session_end is not None:
            required_window = f"{session_start // 60:02d}:{session_start % 60:02d}–{session_end // 60:02d}:{session_end % 60:02d} ET"
        elif session_start is not None:
            required_window = f"at/after {session_start // 60:02d}:{session_start % 60:02d} ET"
        else:
            required_window = f"at/before {session_end // 60:02d}:{session_end % 60:02d} ET"
        checks.append(
            {
                "label": "Entry time window",
                "actual": now_et.strftime("%H:%M ET"),
                "required": required_window,
                "status": "pass" if earliest <= clock_minute <= latest else "fail",
            }
        )

    # These triggers require actual recent bars; do not pretend a snapshot proves them.
'''
if old_catalyst not in engine:
    if '"label": "Entry time window"' not in engine:
        raise SystemExit("Could not find live catalyst/match block in youtube_strategy_engine.py")
else:
    engine = engine.replace(old_catalyst, new_catalyst, 1)

# 2) Actually query recent Alpaca news when the strategy requires a catalyst.
old_enriched = '''    enriched = dict(metrics)
    if needs_chart_candles(strategy):
'''
new_enriched = '''    enriched = dict(metrics)
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    if rules.get("catalyst_required"):
        try:
            recent_news = market.news([ticker], hours=24)
            enriched["has_catalyst"] = bool(recent_news.get(ticker))
        except AppError as error:
            enriched["has_catalyst"] = None
            warnings.append(f"Recent-news check unavailable; catalyst rule needs verification: {error}")

    if needs_chart_candles(strategy):
'''
if old_enriched not in runner:
    if 'recent_news = market.news([ticker], hours=24)' not in runner:
        raise SystemExit("Could not find current_signal enrichment block in live_strategy_runner_page.py")
else:
    runner = runner.replace(old_enriched, new_enriched, 1)

# 3) Replace the vague live-decision section with an explicit actionable summary and checklist.
start_marker = '        st.markdown("### Current live decision")\n'
end_marker = '\n\n    execution = st.session_state.get("runner_execution_v2") or {}\n'
if start_marker not in runner or end_marker not in runner:
    if "#### Entry-condition checklist" not in runner:
        raise SystemExit("Could not find Current live decision UI block")
else:
    start = runner.index(start_marker)
    end = runner.index(end_marker, start)
    decision_block = '''        raw_checks = list(signal.get("checks") or [])
        passed_count = sum(str(item.get("status") or "").lower() == "pass" for item in raw_checks)
        failed_count = sum(str(item.get("status") or "").lower() == "fail" for item in raw_checks)
        unknown_count = sum(str(item.get("status") or "").lower() == "unknown" for item in raw_checks)
        total_count = len(raw_checks)

        if status == "MATCH":
            decision_label = "🟢 ENTRY MATCH"
        elif status == "VERIFY":
            decision_label = "🔵 WAIT / VERIFY"
        elif status == "WATCH":
            decision_label = "🟠 WATCH"
        else:
            decision_label = "🔴 NO ENTRY"

        st.markdown("### Current live decision")
        if status == "MATCH":
            st.success(f"ENTRY CONDITIONS MATCH — {passed_count} of {total_count} conditions passed.")
        elif failed_count > 0:
            extra = f" · {unknown_count} need verification" if unknown_count else ""
            st.error(
                f"NO ENTRY — {failed_count} of {total_count} conditions failed · "
                f"{passed_count} passed{extra}."
            )
        elif unknown_count > 0:
            st.info(
                f"WAIT / VERIFY — {passed_count} of {total_count} conditions passed · "
                f"{unknown_count} still need verification."
            )
        else:
            st.warning("NO ENTRY — this strategy has no currently measurable entry conditions to confirm.")

        st.caption(
            "A strong backtest means the strategy performed well when its entry setup occurred historically. "
            "This live decision only answers whether that setup is present right now."
        )

        cards = st.columns(5)
        cards[0].metric("Live decision", decision_label)
        cards[1].metric("Conditions passed", f"{passed_count} / {total_count}")
        cards[2].metric("Current price", money(metrics.get("price"), 4))
        cards[3].metric("Strategy stop", money(signal.get("suggested_stop"), 4))
        cards[4].metric("Strategy target", money(signal.get("suggested_target"), 4))
        st.caption(
            f'Checked {local_timestamp(snapshot.get("checked_at"))} · '
            f'Rule score {safe_float(signal.get("score"), 0.0):.0f}% · '
            "Stop/target are reference levels only; they are not an entry recommendation unless the strategy reaches a full MATCH."
        )

        details = st.columns(4)
        details[0].metric("Today", percent(metrics.get("day_change_pct"), signed=True))
        rvol = safe_float(metrics.get("relative_volume"))
        details[1].metric("Relative volume", f"{rvol:.2f}x" if rvol is not None else "—")
        details[2].metric("Spread", percent(metrics.get("spread_pct")))
        details[3].metric("VWAP", "Above" if metrics.get("above_vwap") else "Below / unavailable")

        for warning in snapshot.get("warnings") or []:
            st.warning(str(warning))

        st.markdown("#### Entry-condition checklist")
        if raw_checks:
            for item in raw_checks:
                check_status = str(item.get("status") or "").lower()
                check_icon = {"pass": "✅", "fail": "❌", "unknown": "❓"}.get(check_status, "•")
                label = str(item.get("label") or "Strategy condition")
                actual = item.get("actual")
                required = item.get("required")
                actual_text = "Unavailable" if actual is None else str(actual)
                required_text = "—" if required is None else str(required)
                st.markdown(
                    f"{check_icon} **{label}** — Current: `{actual_text}` · Required: `{required_text}`"
                )

            checks_table = [
                {
                    "Rule": item.get("label"),
                    "Current": item.get("actual"),
                    "Required": item.get("required"),
                    "Result": str(item.get("status") or "").upper(),
                }
                for item in raw_checks
            ]
            with st.expander("Technical rule table", expanded=False):
                st.dataframe(pd.DataFrame(checks_table), hide_index=True, width="stretch")
        else:
            st.caption("No measurable entry rules are currently saved for this strategy.")
'''
    runner = runner[:start] + decision_block + runner[end:]

# Static verification before writing.
required_engine_markers = [
    '"label": "Entry time window"',
    'catalyst_status = "unknown"',
]
required_runner_markers = [
    'recent_news = market.news([ticker], hours=24)',
    '#### Entry-condition checklist',
    'NO ENTRY — {failed_count} of {total_count} conditions failed',
    'Stop/target are reference levels only',
]
for marker in required_engine_markers:
    if marker not in engine:
        raise SystemExit(f"Engine verification failed: missing {marker}")
for marker in required_runner_markers:
    if marker not in runner:
        raise SystemExit(f"Runner verification failed: missing {marker}")

engine_path.write_text(engine, encoding="utf-8")
runner_path.write_text(runner, encoding="utf-8")
print("Improved live strategy matching, catalyst lookup, and decision checklist.")
