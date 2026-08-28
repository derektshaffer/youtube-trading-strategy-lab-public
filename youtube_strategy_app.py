"""Streamlit entrypoint for the simplified trading dashboard."""

import runpy


# Streamlit reruns this wrapper in the same interpreter. A normal import would
# be cached after the first render and leave later widget reruns blank.
runpy.run_module("simple_dashboard_core", run_name="__main__")
