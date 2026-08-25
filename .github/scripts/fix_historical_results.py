from pathlib import Path

path = Path("youtube_strategy_app.py")
text = path.read_text(encoding="utf-8")

start_marker = "            st.markdown(\n                f'**Top strategy:**"
end_marker = "            for warning in optimization_report.get(\"warnings\") or []:\n"
start = text.index(start_marker)
end = text.index(end_marker, start)

replacement = '''            training_sessions = optimization_report.get("training_sessions") or []
            validation_sessions = optimization_report.get("validation_sessions") or []
            holdout_sessions = optimization_report.get("holdout_sessions") or []
            result_period_lines = [
                f'**Top strategy:** {escape(winning.get("strategy_name") or "Unnamed strategy")}',
                f'**Recommended candle interval:** {winning.get("timeframe") or optimization_report.get("timeframe") or "5Min"}',
            ]
            if historical_fit_mode:
                if training_sessions:
                    result_period_lines.append(
                        f'**Historical optimization window:** {training_sessions[0]} to {training_sessions[-1]}'
                    )
            else:
                if training_sessions:
                    result_period_lines.append(f'**Training:** {training_sessions[0]} to {training_sessions[-1]}')
                if validation_sessions:
                    result_period_lines.append(f'**Validation:** {validation_sessions[0]} to {validation_sessions[-1]}')
                else:
                    result_period_lines.append('**Validation:** No separate validation sessions available')
                if holdout_sessions:
                    result_period_lines.append(
                        f'**Final untouched holdout:** {holdout_sessions[0]} to {holdout_sessions[-1]}'
                    )
                else:
                    result_period_lines.append('**Final untouched holdout:** No separate holdout sessions available')
            st.markdown("  \\n".join(result_period_lines))
'''

path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")
