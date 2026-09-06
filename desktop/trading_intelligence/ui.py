"""Launch the production PySide6 Trading Intelligence shell."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

# The final wrapper restores the web app's primary navigation and Market
# Discovery flow while retaining the beta recovery/capability safeguards.
from .parity_window import MainWindow, clean_error, write_metrics


def run_gui(
    runtime: Any,
    *,
    smoke: bool = False,
    metrics_output: str = "",
) -> int:
    started = time.perf_counter()
    # A native platform initialization failure can abort Python outright.
    # Initialize Qt before spawning the sidecar so failed launches cannot leave
    # orphan workers competing over the same development database.
    application = QApplication.instance() or QApplication([])
    try:
        runtime.start()
    except BaseException as exc:
        if smoke:
            write_metrics(
                metrics_output,
                {
                    "status": "failed",
                    "framework": "pyside6",
                    "product": "trading-intelligence-desktop",
                    "error_type": type(exc).__name__,
                    "error": clean_error(exc),
                    "total_seconds": round(time.perf_counter() - started, 4),
                },
            )
        runtime.stop()
        return 1

    development = bool(getattr(runtime, "is_development", False))
    application.setApplicationName("Trading Lab Dev" if development else "Trading Intelligence")
    application.setOrganizationName("Derek Shaffer")
    application.setFont(QFont("-apple-system", 12))
    application.aboutToQuit.connect(runtime.stop)
    window = MainWindow(
        runtime,
        smoke=smoke,
        metrics_output=metrics_output,
    )
    if development:
        from .dev import DEV_NAME, ROOT
        window.setWindowTitle(f"{DEV_NAME} — Local Source — {ROOT.name}")
        window.statusBar().showMessage(f"DEVELOPMENT • {ROOT}")
    window.show()
    if development:
        from .dev import record_launch
        record_launch(window, runtime)
    return_code = application.exec()
    if smoke:
        return window.smoke_return_code
    return return_code
