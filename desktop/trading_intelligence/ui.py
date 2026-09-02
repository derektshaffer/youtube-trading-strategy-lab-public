"""Launch the production PySide6 Trading Intelligence shell."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .finder_window import MainWindow, clean_error, write_metrics


def run_gui(
    runtime: Any,
    *,
    smoke: bool = False,
    metrics_output: str = "",
) -> int:
    started = time.perf_counter()
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

    application = QApplication.instance() or QApplication([])
    application.setApplicationName("Trading Intelligence")
    application.setOrganizationName("Derek Shaffer")
    application.setFont(QFont("-apple-system", 12))
    application.aboutToQuit.connect(runtime.stop)
    window = MainWindow(
        runtime,
        smoke=smoke,
        metrics_output=metrics_output,
    )
    window.show()
    return_code = application.exec()
    if smoke:
        return window.smoke_return_code
    return return_code
