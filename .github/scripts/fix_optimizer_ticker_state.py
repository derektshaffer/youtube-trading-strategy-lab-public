from pathlib import Path

path = Path("youtube_strategy_app.py")
text = path.read_text(encoding="utf-8")

submit_old = '''        if optimization_requested:
            requested_symbols = parse_symbols(optimizer_symbol_raw)
'''
submit_new = '''        if optimization_requested:
            # A new optimization request must never leave a previous ticker's report on screen.
            st.session_state.pop("stock_optimization_report", None)
            requested_symbols = parse_symbols(optimizer_symbol_raw)
'''
if submit_new not in text:
    if submit_old not in text:
        raise SystemExit("Could not find optimizer submit block")
    text = text.replace(submit_old, submit_new, 1)

error_old = '''                    except AppError as error:
                        st.error(str(error))

        saved_notice = st.session_state.pop("optimizer_saved_notice", None)
'''
error_new = '''                    except AppError as error:
                        # Keep the previous result cleared if the new ticker fails to optimize.
                        st.session_state.pop("stock_optimization_report", None)
                        st.error(str(error))

        saved_notice = st.session_state.pop("optimizer_saved_notice", None)
'''
if error_new not in text:
    if error_old not in text:
        raise SystemExit("Could not find optimizer error block")
    text = text.replace(error_old, error_new, 1)

display_old = '''        optimization_report = st.session_state.get("stock_optimization_report") or {}
        if optimization_report.get("rankings"):
            optimized_symbol = str(optimization_report.get("symbol") or "?")
'''
display_new = '''        optimization_report = st.session_state.get("stock_optimization_report") or {}
        current_optimizer_symbols = parse_symbols(optimizer_symbol_raw)
        current_optimizer_symbol = current_optimizer_symbols[0] if len(current_optimizer_symbols) == 1 else ""
        stored_optimizer_symbol = str(optimization_report.get("symbol") or "").strip().upper()
        if optimization_report.get("rankings") and (
            not current_optimizer_symbol or stored_optimizer_symbol != current_optimizer_symbol
        ):
            if stored_optimizer_symbol:
                st.info(
                    f"Previous {stored_optimizer_symbol} optimization results are hidden because the optimizer is now set to "
                    f"{current_optimizer_symbol or 'a different/invalid ticker'}. Run the optimizer to create a new matching report."
                )
            optimization_report = {}
        if optimization_report.get("rankings"):
            optimized_symbol = str(optimization_report.get("symbol") or "?")
'''
if display_new not in text:
    if display_old not in text:
        raise SystemExit("Could not find optimizer display block")
    text = text.replace(display_old, display_new, 1)

path.write_text(text, encoding="utf-8")
