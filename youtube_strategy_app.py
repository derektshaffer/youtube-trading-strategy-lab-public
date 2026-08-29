"""Streamlit entrypoint for the Trading Intelligence Lab."""

import runpy


# Streamlit reruns this wrapper in the same interpreter. Run the current
# Trading Intelligence workspace on every rerun instead of the legacy
# simplified dashboard so the research sidebar and Stock Strategy Finder
# are the primary deployed experience.
runpy.run_module("trading_intelligence_app", run_name="__main__")
