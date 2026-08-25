from pathlib import Path

path = Path("youtube_strategy_app.py")
text = path.read_text(encoding="utf-8")

old_progress = '''                        def optimization_progress(completed: int, total: int, message: str) -> None:
                            progress_bar.progress(min(1.0, completed / max(total, 1)), text=message)
'''
new_progress = '''                        def optimization_progress(completed: int, total: int, message: str) -> None:
                            # Adaptive refinement can add work after the engine's original coarse
                            # progress total. Never show 100% until the report has actually returned
                            # and been stored in session state below.
                            reported_fraction = completed / max(total, 1)
                            progress_bar.progress(
                                min(0.97, max(0.0, reported_fraction)),
                                text=message,
                            )
'''
if new_progress not in text:
    if old_progress not in text:
        raise SystemExit("Could not find optimizer progress callback")
    text = text.replace(old_progress, new_progress, 1)

old_store = '''                        report["history_days"] = int(optimizer_history_days)
                        report["observed_spread_bps"] = observed_spread
                        if quote_warning:
                            report["warnings"] = list(dict.fromkeys([quote_warning, *(report.get("warnings") or [])]))
                        st.session_state["stock_optimization_report"] = report
                        progress_bar.progress(1.0, text=f"Strategy optimization complete for {ticker}")
                    except AppError as error:
'''
new_store = '''                        if not isinstance(report, dict) or not report.get("rankings") or not report.get("winner"):
                            raise AppError(
                                f"The optimizer finished for {ticker}, but it did not return a usable ranked result. "
                                "Try a shorter history or a smaller search depth, then report this message if it repeats."
                            )
                        returned_symbol = str(report.get("symbol") or "").strip().upper()
                        if returned_symbol != ticker:
                            raise AppError(
                                f"The optimizer returned results for {returned_symbol or 'an unknown ticker'} instead of {ticker}. "
                                "The mismatched result was discarded."
                            )
                        report["history_days"] = int(optimizer_history_days)
                        report["observed_spread_bps"] = observed_spread
                        if quote_warning:
                            report["warnings"] = list(dict.fromkeys([quote_warning, *(report.get("warnings") or [])]))
                        st.session_state["stock_optimization_report"] = report
                        st.session_state["optimizer_last_completed_symbol"] = ticker
                        progress_bar.progress(1.0, text=f"Strategy optimization complete for {ticker} — results ready below")
                        st.success(
                            f"Optimization complete for {ticker}: "
                            f"{int(report.get('variants_tested', 0)):,} settings tested. Results are shown below."
                        )
                    except AppError as error:
'''
if new_store not in text:
    if old_store not in text:
        raise SystemExit("Could not find optimizer report-store block")
    text = text.replace(old_store, new_store, 1)

old_error = '''                    except AppError as error:
                        # Keep the previous result cleared if the new ticker fails to optimize.
                        st.session_state.pop("stock_optimization_report", None)
                        st.error(str(error))

        saved_notice = st.session_state.pop("optimizer_saved_notice", None)
'''
new_error = '''                    except AppError as error:
                        # Keep the previous result cleared if the new ticker fails to optimize.
                        st.session_state.pop("stock_optimization_report", None)
                        progress_bar.progress(0.0, text=f"Optimization failed for {ticker}")
                        st.error(str(error))
                    except Exception as error:
                        # Do not silently lose unexpected failures after a long adaptive run.
                        st.session_state.pop("stock_optimization_report", None)
                        progress_bar.progress(0.0, text=f"Optimization failed for {ticker}")
                        st.error(
                            f"The optimizer hit an unexpected {type(error).__name__} while processing {ticker}: {error}"
                        )

        saved_notice = st.session_state.pop("optimizer_saved_notice", None)
'''
if new_error not in text:
    if old_error not in text:
        raise SystemExit("Could not find optimizer error block")
    text = text.replace(old_error, new_error, 1)

# If a stored report exists but has somehow become unusable, say so instead of
# silently rendering nothing.
old_display = '''        if optimization_report.get("rankings"):
            optimized_symbol = str(optimization_report.get("symbol") or "?")
'''
new_display = '''        if optimization_report and not optimization_report.get("rankings"):
            st.warning(
                "An optimization report was returned without ranked results, so it was not displayed. "
                "Run the optimizer again; if this repeats, the visible error message will identify the failure."
            )
        if optimization_report.get("rankings"):
            optimized_symbol = str(optimization_report.get("symbol") or "?")
'''
if new_display not in text:
    if old_display not in text:
        raise SystemExit("Could not find optimizer display gate")
    text = text.replace(old_display, new_display, 1)

path.write_text(text, encoding="utf-8")
