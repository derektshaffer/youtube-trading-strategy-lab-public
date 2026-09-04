"""Selectable, automatically refreshed search status without blocking Qt."""
from __future__ import annotations

import queue
import json
import threading
import time

from .display_time import format_timestamp

from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout

from .pages import Card
from .window import clean_error


def action_key(row):
    """Never attach cancellation feedback to another run or cloud library."""
    return (row.get("key"), row.get("identity"), json.dumps(row.get("binding"), sort_keys=True))


class SearchMonitorCard(Card):
    refresh_requested = Signal()
    cancel_requested = Signal(dict)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        title = QLabel("Current searches · select a row for details")
        title.setObjectName("SectionTitle")
        self.refresh = QPushButton("Refresh searches")
        self.refresh.clicked.connect(self.refresh_requested.emit)
        heading.addWidget(title, 1)
        heading.addWidget(self.refresh)
        layout.addLayout(heading)
        self.status = QLabel("Search status has not been checked yet.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.action_notice = QLabel()
        self.action_notice.setTextFormat(Qt.TextFormat.PlainText)
        self.action_notice.setWordWrap(True)
        self.action_notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.action_notice)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Stock", "Search", "Location", "Reported status", "Stage", "Progress", "Last update (UTC)"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(145)
        self.table.setMaximumHeight(230)
        self.table.itemSelectionChanged.connect(self._selection)
        layout.addWidget(self.table)
        self.detail = QLabel("Includes cloud Finder and Strategy Lab searches, plus this Mac's saved searches. Recent finished runs remain selectable.")
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.detail)
        self.cancel = QPushButton("Cancel selected search")
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self._request_cancel)
        layout.addWidget(self.cancel)
        self.rows = []
        self.busy = False
        self.actions = {}

    def selected(self):
        row = self.table.currentRow()
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def _selection(self):
        row = self.selected()
        action = self.actions.get(action_key(row)) if row else None
        self.cancel.setEnabled(bool(row and row.get("can_cancel") and not self.busy
                                    and (not action or action["state"] == "unconfirmed")))
        self.cancel.setText(("Stop running cloud search" if row.get("status") in {"running", "cancelling"}
                             else "Cancel queued cloud search") if row and row.get("target") == "Cloud" else "Cancel selected search")
        if row:
            self.detail.setText(
                f"{row['symbol']} · {row['kind']} · depth {row.get('profile') or '—'} · {row['target']}\n"
                f"Run: {row.get('run_id') or row['id']}\n"
                + (f"{action['title']}: {action['message']}\n" if action else f"{row.get('message') or ''}\n")
                + (f"Saved checkpoint: {format_timestamp(row['checkpoint_at'])} UTC\n" if row.get("checkpoint_at") else "")
                + "Status and progress are last reported, not proof the worker is still alive.\n"
                + (row.get("cancel_reason", "") if not action or action["state"] == "unconfirmed"
                   else "No further cancellation will be sent automatically.")
            )

    def _request_cancel(self):
        row = self.selected()
        if row and self.cancel.isEnabled():
            self.cancel_requested.emit(dict(row))

    def set_actions(self, actions):
        self.actions = actions
        # Keep feedback visible even if selection changes or the run falls out of
        # the bounded status snapshot. Refresh errors belong to a separate label.
        self.action_notice.setText("\n".join(
            f"{action['row']['symbol']} · {action['row']['id']} · {action['title']}: {action['message']}"
            for action in actions.values()
        ))
        self._selection()

    def set_busy(self, busy):
        self.busy = busy
        self.refresh.setEnabled(not busy)
        self._selection()

    def render(self, snapshot):
        selected_key = (self.selected() or {}).get("key")
        self.rows = list(snapshot.get("rows") or [])
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        selected_index = -1
        for index, row in enumerate(self.rows):
            values = [row["symbol"], row["kind"], row["target"], row["status"].replace("_", " "),
                      row["stage"].replace("_", " "), f"{row['progress'] * 100:.0f}%", format_timestamp(row["updated_at"], "Unknown")]
            action = self.actions.get(action_key(row))
            if action and row["status"] not in {"cancelled", "complete", "completed", "failed", "worker_stopped"}:
                values[3] = action["title"]
                if action["state"] == "confirmed":
                    values[4] = "Older snapshot; cancellation confirmed"
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(str(value)))
            self.table.item(index, 3).setToolTip("Last reported search status: " + row["status"])
            if row["key"] == selected_key:
                selected_index = index
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        if selected_index >= 0:
            self.table.selectRow(selected_index)
        self.table.blockSignals(False)
        count = snapshot.get("active_count", 0)
        checked = format_timestamp(snapshot.get("checked_at"), "not yet") + " UTC"
        warning = snapshot.get("warning") or ""
        if snapshot.get("stale"):
            self.status.setText(f"Cloud status unavailable or stale · last successful check: {checked}. {warning}")
        else:
            self.status.setText(f"{count} unfinished searches reported · checked {checked}. {warning}")
        if selected_index < 0:
            self.detail.setText("Select a search to inspect its saved progress and cancellation availability.")
        self._selection()

    def failed(self, message):
        self.status.setText("Search status unavailable; displayed rows may be stale. " + message)
        for row in self.rows:
            row.update(can_cancel=False, cancel_reason="Refresh successfully before cancelling.")
        self._selection()


class SearchMonitorController(QObject):
    def __init__(self, window, pages):
        super().__init__(window)
        self.window = window
        self.panels = []
        self.results = queue.Queue()
        self.busy = False
        self.last_refresh = 0.0
        self.actions = {}
        self.cancel_row = None
        self.confirming = False
        for page in pages:
            panel = SearchMonitorCard()
            # Above the run form: current work must be visible before starting more.
            page.layout().insertWidget(1, panel)
            panel.refresh_requested.connect(lambda: self.refresh(force=True))
            panel.cancel_requested.connect(self.confirm_cancel)
            self.panels.append((page, panel))
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def tick(self):
        try:
            purpose, result, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if purpose == "cancel":
                row = self.cancel_row
                state = "unconfirmed" if error else ("confirmed" if result.get("status") == "cancelled" else "pending")
                self._action(row, state, error or result.get("message", "Waiting for confirmation."))
                self.cancel_row = None
            elif not error and not result.get("stale"):
                for row in result.get("rows", []):
                    action = self.actions.get(action_key(row))
                    if action and row.get("status") == "cancelled":
                        self._action(action["row"], "confirmed", "Cancellation confirmed by the saved search status.")
                    elif action and action["state"] != "confirmed" and row.get("status") in {"complete", "completed", "failed", "worker_stopped"}:
                        self._action(action["row"], "finished", "Search ended with status " + row["status"] + "; it is no longer queued or running.")
            for _, panel in self.panels:
                panel.set_busy(False)
                if error:
                    if purpose == "refresh":
                        panel.failed(error)
                    else:
                        panel.status.setText("Cancellation not confirmed. Refresh searches to check the latest state; no automatic cancellation retry will be sent.")
                elif purpose == "refresh":
                    panel.render(result)
            if purpose == "cancel" and not error:
                self.refresh(force=True)
                return
        if not self.busy and time.monotonic() - self.last_refresh >= 15 and any(
            self.window.stack.currentWidget() is page for page, _ in self.panels
        ):
            self.refresh()

    def _start(self, purpose, method, path, body=None):
        if self.busy or self.confirming:
            return
        self.busy = True
        self.last_refresh = time.monotonic()
        if purpose == "cancel":
            self.cancel_row = dict(body)
            self._action(body, "pending", "Sending cancellation request; the cloud has not confirmed it yet.")
        for _, panel in self.panels:
            panel.set_busy(True)
            panel.status.setText("Checking saved search status…" if purpose == "refresh" else "Requesting cancellation; not yet confirmed…")
        runtime, results = self.window.runtime, self.results
        def work():
            try:
                result = runtime.request_json(method, path, body, timeout=180.0)
                results.put((purpose, result, ""))
            except Exception as exc:
                results.put((purpose, None, clean_error(exc)))
        threading.Thread(target=work, name="desktop-search-monitor", daemon=True).start()

    def _action(self, row, state, message):
        titles = {"pending": "Cancellation pending", "unconfirmed": "Cancellation not confirmed",
                  "confirmed": "Cancelled", "finished": "Search finished"}
        self.actions[action_key(row)] = {"row": dict(row), "state": state, "title": titles[state], "message": message}
        for _, panel in self.panels:
            panel.set_actions(self.actions)

    def finder_cancellation(self, job_id, link):
        for action in reversed(list(self.actions.values())):
            row = action["row"]
            if row.get("key") == "local:" + job_id:
                return action
            if (row.get("key") == "cloud:" + str(link.get("remote_job_id") or "")
                    and row.get("binding")
                    and all(link.get(key) == value for key, value in row["binding"].items())):
                return action
        return None

    def refresh(self, *, force=False):
        self._start("refresh", "GET", "/v1/searches" + ("?refresh=true" if force else ""))

    def confirm_cancel(self, row):
        action = self.actions.get(action_key(row))
        if self.busy or self.confirming or (action and action["state"] != "unconfirmed"):
            return
        # QMessageBox runs a nested Qt event loop. Do not let its refresh timer
        # take the single request slot and silently discard the user's Yes.
        self.confirming = True
        try:
            answer = QMessageBox.question(self.window, "Cancel selected search?",
                f"Cancel {row['symbol']} · {row['kind']} ({row['target']})?\nRun: {row['id']}\n"
                "The current state will be checked again before cancellation. Other searches and saved results are preserved.\n"
                + row.get("cancel_reason", ""),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        finally:
            self.confirming = False
        if answer == QMessageBox.StandardButton.Yes:
            self._start("cancel", "POST", "/v1/searches/cancel", row)
