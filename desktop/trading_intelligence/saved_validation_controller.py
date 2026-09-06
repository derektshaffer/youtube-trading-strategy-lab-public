"""Open existing validation evidence without invoking any execution controller."""
from copy import deepcopy
import queue
import threading

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QDialog, QVBoxLayout, QPushButton

from .saved_validation_page import SavedValidationPage
from .window import clean_error


class SavedValidationController(QObject):
    def __init__(self, window, monitor):
        super().__init__(window)
        self.window = window
        self.results = queue.Queue()
        self.busy = False
        self.dialog = None
        self.buttons = []
        for _, panel in monitor.panels:
            button = QPushButton("Open Saved Validation")
            button.setEnabled(False)
            panel.layout().addWidget(button)
            button.clicked.connect(lambda checked=False, p=panel: self.open(p.selected()))
            panel.table.itemSelectionChanged.connect(self.update_buttons)
            self.buttons.append((panel, button))
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def update_buttons(self):
        for panel, button in self.buttons:
            row = panel.selected() or {}
            button.setEnabled(not self.busy and row.get("kind") in {"Strategy Lab", "Strategy Validation"}
                              and row.get("status") in {"complete", "failed"})

    def open(self, row):
        if self.busy or not row or row.get("kind") not in {"Strategy Lab", "Strategy Validation"} or row.get("status") not in {"complete", "failed"}:
            return
        request = deepcopy({k: row.get(k) for k in ("key", "id", "identity", "binding")})
        self.busy = True
        self.update_buttons()
        runtime, results = self.window.runtime, self.results
        def read():
            try:
                response = runtime.request_json("POST", "/v1/saved-validations/result", request, timeout=180.0)
                results.put((response, ""))
            except Exception as exc:
                results.put((None, clean_error(exc)))
        threading.Thread(target=read, name="saved-validation-read", daemon=True).start()

    def tick(self):
        try:
            response, error = self.results.get_nowait()
        except queue.Empty:
            return
        self.busy = False
        self.update_buttons()
        if error:
            self.window.top_status.setText("Saved validation unavailable: " + error)
            return
        self.dialog = QDialog(self.window)
        self.dialog.setWindowTitle("Saved Validation - " + response["ticker"])
        self.dialog.resize(1150, 780)
        layout = QVBoxLayout(self.dialog)
        self.page = SavedValidationPage(response)
        layout.addWidget(self.page, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.dialog.close)
        layout.addWidget(close)
        self.dialog.show()
