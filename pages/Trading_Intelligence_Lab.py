"""Trading Intelligence Lab page wrapper for the existing Streamlit deployment."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source_path = ROOT / "trading_intelligence_app.py"
source = source_path.read_text(encoding="utf-8")
code = compile(source, str(source_path), "exec")
exec(code, globals(), globals())
