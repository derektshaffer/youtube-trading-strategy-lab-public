"""Simplified Trading Dashboard entrypoint with access to the full original lab."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
source_path = ROOT / "simple_dashboard_core.py"
source = source_path.read_text(encoding="utf-8")

# Keep the simplified dashboard as Home while making the original research tools easy to reach.
source = source.replace(
    'if st.button("Full Trading Lab", use_container_width=True):\n            st.switch_page("pages/Advanced_Trading_Lab.py")',
    'if st.button("Upload / Analyze Videos + Full Lab", use_container_width=True):\n            st.switch_page("pages/Full_Trading_Lab.py")',
)
source = source.replace(
    'st.switch_page("pages/Advanced_Machine_Learning.py")',
    'st.switch_page("pages/Machine_Learning_Lab.py")',
)

# Streamlit number_input format strings accept printf-style numeric formats only;
# put the currency symbol in the plain-English label instead of in `format`.
source = source.replace(
    '"Max loss per trade",\n            min_value=1.0,',
    '"Max loss per trade ($)",\n            min_value=1.0,',
)
source = source.replace('format="$%.0f",', 'format="%.0f",')

code = compile(source, str(source_path), "exec")
exec(code, globals(), globals())
