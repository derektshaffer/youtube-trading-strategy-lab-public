"""Native candlestick chart for real Trading Intelligence desktop data."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class CandleChart(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.candles: list[dict[str, Any]] = []
        self.visible_bars = 90
        self.offset_bars = 0
        self.dragging = False
        self.drag_start_x = 0.0
        self.drag_start_offset = 0
        self.hover_position: QPointF | None = None
        self.show_vwap = True
        self.show_ema = True
        self.last_render_ms = 0.0
        self.setMinimumHeight(360)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_candles(self, candles: list[dict[str, Any]]) -> None:
        self.candles = [dict(row) for row in candles if isinstance(row, dict)]
        self.visible_bars = min(90, max(24, len(self.candles)))
        self.offset_bars = 0
        self.hover_position = None
        self.update()

    def reset_view(self) -> None:
        self.visible_bars = min(90, max(24, len(self.candles)))
        self.offset_bars = 0
        self.hover_position = None
        self.update()

    def _clamp_window(self) -> None:
        total = len(self.candles)
        self.visible_bars = max(24, min(max(24, total), int(self.visible_bars)))
        maximum_offset = max(0, total - self.visible_bars)
        self.offset_bars = max(0, min(maximum_offset, int(self.offset_bars)))

    def visible_rows(self) -> list[dict[str, Any]]:
        self._clamp_window()
        end = max(0, len(self.candles) - self.offset_bars)
        start = max(0, end - self.visible_bars)
        return self.candles[start:end]

    def paintEvent(self, _event: Any) -> None:
        started = time.perf_counter()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#08131f"))
        rows = self.visible_rows()
        if not rows:
            painter.setPen(QColor("#7890a8"))
            painter.drawText(18, 30, "Enter a ticker to load real market data…")
            self.last_render_ms = (time.perf_counter() - started) * 1000.0
            return

        width = float(self.width())
        height = float(self.height())
        left, right, top, bottom = 12.0, 68.0, 15.0, 28.0
        plot_width = max(40.0, width - left - right)
        plot_height = max(40.0, height - top - bottom)
        values: list[float] = []
        for row in rows:
            values.extend([float(row["low"]), float(row["high"])])
            if self.show_vwap:
                values.append(float(row.get("vwap") or row["close"]))
            if self.show_ema:
                values.append(float(row.get("ema_9") or row["close"]))
        minimum = min(values)
        maximum = max(values)
        span = max(0.01, maximum - minimum)
        minimum -= span * 0.08
        maximum += span * 0.08
        x_step = plot_width / max(1, len(rows))
        candle_width = max(1.2, min(9.0, x_step * 0.64))

        def x_at(index: int) -> float:
            return left + (index + 0.5) * x_step

        def y_at(value: float) -> float:
            return top + (maximum - value) / (maximum - minimum) * plot_height

        painter.setFont(QFont("-apple-system", 9))
        grid_pen = QPen(QColor("#172a3e"))
        grid_pen.setWidthF(1.0)
        painter.setPen(grid_pen)
        for line in range(6):
            y = top + plot_height * line / 5
            value = maximum - (maximum - minimum) * line / 5
            painter.drawLine(QPointF(left, y), QPointF(left + plot_width, y))
            painter.setPen(QColor("#70869c"))
            painter.drawText(int(left + plot_width + 7), int(y + 3), f"{value:.2f}")
            painter.setPen(grid_pen)

        for marker in range(5):
            index = min(
                len(rows) - 1,
                round(marker * (len(rows) - 1) / 4),
            )
            stamp = time.strftime("%H:%M", time.localtime(int(rows[index]["time"])))
            painter.setPen(QColor("#63798f"))
            painter.drawText(int(max(2.0, x_at(index) - 18.0)), int(height - 9), stamp)

        def draw_indicator(key: str, color: str) -> None:
            pen = QPen(QColor(color))
            pen.setWidthF(1.35)
            painter.setPen(pen)
            points = [
                QPointF(x_at(index), y_at(float(row.get(key) or row["close"])))
                for index, row in enumerate(rows)
            ]
            for first, second in zip(points, points[1:]):
                painter.drawLine(first, second)

        if self.show_vwap:
            draw_indicator("vwap", "#56c9f2")
        if self.show_ema:
            draw_indicator("ema_9", "#d5a75d")

        for index, row in enumerate(rows):
            opening = float(row["open"])
            closing = float(row["close"])
            high = float(row["high"])
            low = float(row["low"])
            color = QColor("#4cdda4" if closing >= opening else "#f06f7b")
            x = x_at(index)
            painter.setPen(QPen(color, 1.0))
            painter.drawLine(QPointF(x, y_at(high)), QPointF(x, y_at(low)))
            top_y = y_at(max(opening, closing))
            bottom_y = y_at(min(opening, closing))
            body_height = max(1.2, bottom_y - top_y)
            painter.fillRect(
                QRectF(x - candle_width / 2, top_y, candle_width, body_height),
                color,
            )

        if self.hover_position is not None:
            x = max(left, min(left + plot_width, float(self.hover_position.x())))
            index = max(0, min(len(rows) - 1, int((x - left) / max(1.0, x_step))))
            row = rows[index]
            candle_x = x_at(index)
            candle_y = y_at(float(row["close"]))
            crosshair_pen = QPen(QColor("#718ba3"))
            crosshair_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(crosshair_pen)
            painter.drawLine(QPointF(candle_x, top), QPointF(candle_x, top + plot_height))
            painter.drawLine(QPointF(left, candle_y), QPointF(left + plot_width, candle_y))
            painter.setPen(QColor("#d9e8f5"))
            painter.setFont(QFont("-apple-system", 10))
            painter.drawText(
                int(left + 8),
                int(top + 15),
                (
                    f"O {float(row['open']):.2f}  H {float(row['high']):.2f}  "
                    f"L {float(row['low']):.2f}  C {float(row['close']):.2f}"
                ),
            )

        self.last_render_ms = (time.perf_counter() - started) * 1000.0

    def wheelEvent(self, event: Any) -> None:
        direction = 1 if event.angleDelta().y() < 0 else -1
        factor = 1.12 if direction > 0 else 0.89
        self.visible_bars = round(self.visible_bars * factor)
        self._clamp_window()
        self.update()
        event.accept()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.drag_start_x = float(event.position().x())
            self.drag_start_offset = self.offset_bars
            event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        self.hover_position = event.position()
        if self.dragging:
            plot_width = max(40.0, float(self.width()) - 80.0)
            pixels_per_bar = plot_width / max(1, self.visible_bars)
            delta = round(
                (float(event.position().x()) - self.drag_start_x)
                / max(1.0, pixels_per_bar)
            )
            self.offset_bars = self.drag_start_offset + delta
            self._clamp_window()
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            event.accept()

    def leaveEvent(self, event: Any) -> None:
        if not self.dragging:
            self.hover_position = None
            self.update()
        super().leaveEvent(event)
