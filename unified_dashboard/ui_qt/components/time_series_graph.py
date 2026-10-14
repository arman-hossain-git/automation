"""
ui_qt/components/time_series_graph.py
=====================================
Real-time Time-Series Telemetry & Throughput Graph for PySide6.
Supports:
  - Record/Form throughput (items/min, records/min) for batch web automations
  - Transfer throughput (KB/s, MB/s) for file upload/download operations
  - Interactive mouse inspection (crosshair, hover halo dot, and floating HUD tooltip)
  - Hardware-accelerated 2D antialiased canvas
"""

import time
import datetime
from typing import Optional
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QLinearGradient, QFont
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from ui_qt.theme import (
    BG_BASE, BG_CARD, BG_CARD_ALT, BORDER_SUBTLE, BORDER_MUTED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_WARNING, ACCENT_INFO
)
from core.utils import format_bytes, format_speed_bps, format_timedelta


class TelemetryCanvas(QWidget):
    """Antialiased 2D canvas that renders the throughput curve and telemetry area
    with interactive hover crosshair and instance inspection.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data_points = []  # dicts: {'elapsed', 'speed', 'cumulative', 'label', 'wall_time'}
        self.peak_speed = 0.0
        self.current_speed = 0.0
        self.unit_type = "items"  # "items" (items/min) or "bytes" (B/s)
        self.setMinimumHeight(140)
        self.setMouseTracking(True)
        self.hover_index = -1
        self.hover_pos = None

    def set_data(self, points, peak, unit_type="items", current_speed=0.0):
        self.data_points = points
        self.peak_speed = peak
        self.unit_type = unit_type
        self.current_speed = current_speed
        self.update()

    def mouseMoveEvent(self, event):
        if not self.data_points:
            self.hover_index = -1
            self.hover_pos = None
            self.update()
            return

        w = self.width()
        pad_l, pad_r = 65, 20
        plot_w = max(10, w - pad_l - pad_r)
        max_time = max(self.data_points[-1]["elapsed"], 5.0)

        mouse_x = event.position().x()
        self.hover_pos = event.position()

        # Find closest point
        best_idx = -1
        best_dist = float("inf")
        for i, pt in enumerate(self.data_points):
            t = pt["elapsed"]
            x = pad_l + (t / max_time) * plot_w
            dist = abs(x - mouse_x)
            if dist < best_dist:
                best_dist = dist
                best_idx = i

        self.hover_index = best_idx
        self.update()

    def leaveEvent(self, event):
        self.hover_index = -1
        self.hover_pos = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # Background
        painter.fillRect(0, 0, w, h, QColor(BG_BASE))

        pad_l, pad_r, pad_t, pad_b = 65, 20, 16, 26
        plot_w = max(10, w - pad_l - pad_r)
        plot_h = max(10, h - pad_t - pad_b)

        # Draw Grid Lines
        pen_grid = QPen(QColor(BORDER_SUBTLE))
        pen_grid.setStyle(Qt.DotLine)
        pen_grid.setWidth(1)
        painter.setPen(pen_grid)

        # 3 horizontal gridlines
        for i in range(4):
            y = pad_t + (plot_h * i / 3)
            painter.drawLine(pad_l, int(y), pad_l + plot_w, int(y))

        # Y-Axis Range & Labels
        font_axis = QFont("Segoe UI", 9)
        painter.setFont(font_axis)
        painter.setPen(QColor(TEXT_MUTED))

        if self.unit_type == "bytes":
            # Auto-scale: use 90th-percentile of visible speeds + 15% headroom, min 1 KB/s
            if self.data_points:
                speeds = sorted(pt["speed"] for pt in self.data_points)
                p90_idx = max(0, int(len(speeds) * 0.9) - 1)
                p90_speed = speeds[p90_idx]
                max_val = max(p90_speed * 1.35, self.current_speed * 1.5, 1024.0)
            else:
                max_val = 1024.0
            for i in range(4):
                y = pad_t + (plot_h * i / 3)
                val = max_val * (1.0 - (i / 3))
                txt = format_speed_bps(val)
                painter.drawText(0, int(y) - 6, pad_l - 6, 14, Qt.AlignRight | Qt.AlignVCenter, txt)
        else:
            # Auto-scale: use 90th-percentile of visible speeds + 25% headroom, min 6/min
            if self.data_points:
                speeds = sorted(pt["speed"] for pt in self.data_points)
                p90_idx = max(0, int(len(speeds) * 0.9) - 1)
                p90_speed = speeds[p90_idx]
                max_val = max(p90_speed * 1.35, self.current_speed * 1.5, 6.0)
            else:
                max_val = 6.0
            for i in range(4):
                y = pad_t + (plot_h * i / 3)
                val = max_val * (1.0 - (i / 3))
                txt = f"{val:.1f}/m" if val < 10 else f"{int(round(val))}/m"
                painter.drawText(0, int(y) - 6, pad_l - 6, 14, Qt.AlignRight | Qt.AlignVCenter, txt)

        if not self.data_points:
            # Empty state
            painter.setPen(QColor(TEXT_MUTED))
            msg = "Waiting for live throughput telemetry (updates as items process)..."
            painter.drawText(pad_l, pad_t, plot_w, plot_h, Qt.AlignCenter, msg)
            return

        # Prepare Points — X-axis spans the VISIBLE window (oldest → newest kept point)
        t_first = self.data_points[0]["elapsed"]
        t_last  = self.data_points[-1]["elapsed"]
        time_range = max(t_last - t_first, 5.0)
        qpoints = []
        for pt in self.data_points:
            t = pt["elapsed"]
            s = pt["speed"]
            x = pad_l + ((t - t_first) / time_range) * plot_w
            y = pad_t + plot_h - (s / max_val) * plot_h
            # Clamp to plot area
            y = max(pad_t, min(pad_t + plot_h, y))
            qpoints.append(QPointF(x, y))

        # Bottom Time Ticks — show the elapsed timestamps of the visible window edges
        painter.setPen(QColor(TEXT_MUTED))
        t_start_str = format_timedelta(datetime.timedelta(seconds=int(t_first)))
        t_mid_sec   = t_first + time_range / 2.0
        t_mid_str   = format_timedelta(datetime.timedelta(seconds=int(t_mid_sec)))
        t_end_str   = format_timedelta(datetime.timedelta(seconds=int(t_last)))

        painter.drawText(pad_l - 10, h - 18, 55, 14, Qt.AlignLeft,   t_start_str)
        painter.drawText(int(pad_l + plot_w / 2 - 25), h - 18, 50, 14, Qt.AlignCenter, t_mid_str)
        painter.drawText(pad_l + plot_w - 55, h - 18, 55, 14, Qt.AlignRight,  t_end_str)

        # Area fill with gradient
        gradient = QLinearGradient(0, pad_t, 0, pad_t + plot_h)
        gradient.setColorAt(0.0, QColor(59, 130, 246, 70))
        gradient.setColorAt(1.0, QColor(59, 130, 246, 5))

        area_polygon = [QPointF(pad_l, pad_t + plot_h)] + qpoints + [QPointF(qpoints[-1].x(), pad_t + plot_h)]
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(area_polygon)

        # Throughput curve line
        pen_curve = QPen(QColor(ACCENT_PRIMARY))
        pen_curve.setWidth(2)
        painter.setPen(pen_curve)
        for i in range(len(qpoints) - 1):
            painter.drawLine(qpoints[i], qpoints[i + 1])

        # Current point dot (last point)
        if qpoints:
            last_pt = qpoints[-1]
            painter.setBrush(QBrush(QColor(ACCENT_INFO)))
            painter.setPen(QPen(QColor("#FFFFFF"), 1))
            painter.drawEllipse(last_pt, 4, 4)

        # ── Interactive Hover Crosshair & Tooltip ───────────────────────
        if 0 <= self.hover_index < len(self.data_points) and len(qpoints) > self.hover_index:
            hover_pt = qpoints[self.hover_index]
            hover_data = self.data_points[self.hover_index]

            # Vertical crosshair guide
            pen_cross = QPen(QColor(ACCENT_INFO))
            pen_cross.setStyle(Qt.DashLine)
            pen_cross.setWidth(1)
            painter.setPen(pen_cross)
            painter.drawLine(int(hover_pt.x()), pad_t, int(hover_pt.x()), pad_t + plot_h)

            # Outer glow halo
            painter.setBrush(QBrush(QColor(59, 130, 246, 60)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(hover_pt, 8, 8)

            # Center marker
            painter.setBrush(QBrush(QColor("#FFFFFF")))
            painter.setPen(QPen(QColor(ACCENT_INFO), 2))
            painter.drawEllipse(hover_pt, 4, 4)

            # Tooltip HUD Box
            tooltip_w = 200
            tooltip_h = 76
            tooltip_x = hover_pt.x() + 12
            if tooltip_x + tooltip_w > w - 10:
                tooltip_x = hover_pt.x() - tooltip_w - 12
            tooltip_y = max(pad_t, min(hover_pt.y() - tooltip_h / 2, pad_t + plot_h - tooltip_h))

            # Tooltip background
            painter.setBrush(QBrush(QColor(24, 24, 27, 240)))
            painter.setPen(QPen(QColor(BORDER_MUTED), 1))
            painter.drawRoundedRect(QRectF(tooltip_x, tooltip_y, tooltip_w, tooltip_h), 6, 6)

            # Tooltip text
            font_tt_title = QFont("Segoe UI", 9, QFont.Bold)
            font_tt_val = QFont("Segoe UI", 9, QFont.Normal)

            elapsed_str = format_timedelta(datetime.timedelta(seconds=int(hover_data["elapsed"])))
            wall_time = hover_data.get("wall_time", "")
            time_label = f"⏱  {elapsed_str} ({wall_time})" if wall_time else f"⏱  Elapsed: {elapsed_str}"

            if self.unit_type == "bytes":
                speed_str = f"⚡ Speed: {format_speed_bps(hover_data['speed'])}"
                prog_str = f"📦 Volume: {format_bytes(hover_data['cumulative'])}"
            else:
                speed_str = f"⚡ Speed: {hover_data['speed']:.1f} items/min"
                prog_str = f"📋 Processed: {hover_data['cumulative']} items"

            lbl_detail = hover_data.get("label", "")
            detail_str = f"🎯 {lbl_detail}" if lbl_detail else ""

            painter.setFont(font_tt_title)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(int(tooltip_x + 8), int(tooltip_y + 16), time_label)

            painter.setFont(font_tt_val)
            painter.setPen(QColor(ACCENT_INFO))
            painter.drawText(int(tooltip_x + 8), int(tooltip_y + 34), speed_str)

            painter.setPen(QColor(TEXT_PRIMARY))
            painter.drawText(int(tooltip_x + 8), int(tooltip_y + 51), prog_str)

            if detail_str:
                painter.setPen(QColor(TEXT_MUTED))
                if len(detail_str) > 28:
                    detail_str = detail_str[:26] + "…"
                painter.drawText(int(tooltip_x + 8), int(tooltip_y + 68), detail_str)


class TimeSeriesTelemetryGraph(QFrame):
    """Telemetry container with stats bar and live interactive canvas."""

    def __init__(self, parent=None, title="Live Telemetry & Throughput Time-Series"):
        super().__init__(parent)
        self.title = title
        self.data_points = []
        self.peak_speed = 0.0
        self.current_speed = 0.0
        self.start_epoch = None
        self.unit_type = "items"

        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 8px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Header with Live KPI Badges
        hdr_layout = QHBoxLayout()
        hdr_layout.setContentsMargins(0, 0, 0, 0)

        lbl_title = QLabel(f"📈  {self.title}")
        lbl_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {TEXT_PRIMARY}; border: none;")
        hdr_layout.addWidget(lbl_title)

        lbl_hint = QLabel("(Hover over graph to inspect any instance)")
        lbl_hint.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; border: none; margin-left: 6px;")
        hdr_layout.addWidget(lbl_hint)
        hdr_layout.addStretch()

        self.lbl_cur_speed = QLabel("Speed: 0.0/min")
        self.lbl_cur_speed.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {ACCENT_INFO}; border: none;")
        hdr_layout.addWidget(self.lbl_cur_speed)

        self.lbl_peak_speed = QLabel("Peak: 0.0/min")
        self.lbl_peak_speed.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {ACCENT_PRIMARY}; border: none; margin-left: 10px;")
        hdr_layout.addWidget(self.lbl_peak_speed)

        self.lbl_total_progress = QLabel("Total: 0")
        self.lbl_total_progress.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {ACCENT_SUCCESS}; border: none; margin-left: 10px;")
        hdr_layout.addWidget(self.lbl_total_progress)

        layout.addLayout(hdr_layout)

        # Canvas
        self.canvas = TelemetryCanvas(self)
        layout.addWidget(self.canvas)

    def record_datapoint(
        self,
        speed: float,
        cumulative: int,
        label: str = "",
        unit_type: str = "items",
        total_label: Optional[str] = None
    ):
        """Record a throughput datapoint and refresh the live curve."""
        now = time.time()
        if self.start_epoch is None:
            self.start_epoch = now

        elapsed = max(0.1, now - self.start_epoch)
        self.current_speed = float(max(0.0, speed))
        self.unit_type = unit_type

        if self.current_speed > self.peak_speed:
            self.peak_speed = self.current_speed

        wall_time = datetime.datetime.now().strftime("%H:%M:%S")

        self.data_points.append({
            "elapsed": elapsed,
            "speed": self.current_speed,
            "cumulative": cumulative,
            "label": label,
            "wall_time": wall_time
        })

        if len(self.data_points) > 600:
            self.data_points = self.data_points[-600:]

        if unit_type == "bytes":
            self.lbl_cur_speed.setText(f"Speed: {format_speed_bps(self.current_speed)}")
            self.lbl_peak_speed.setText(f"Peak: {format_speed_bps(self.peak_speed)}")
            if total_label:
                self.lbl_total_progress.setText(total_label)
            else:
                self.lbl_total_progress.setText(f"Total: {format_bytes(cumulative)}")
        else:
            self.lbl_cur_speed.setText(f"Speed: {self.current_speed:.1f}/min")
            self.lbl_peak_speed.setText(f"Peak: {self.peak_speed:.1f}/min")
            if total_label:
                self.lbl_total_progress.setText(total_label)
            else:
                self.lbl_total_progress.setText(f"Total: {cumulative} items")

        self.canvas.set_data(self.data_points, self.peak_speed, unit_type, current_speed=self.current_speed)

    def reset_graph(self, unit_type: str = "items"):
        self.data_points.clear()
        self.peak_speed = 0.0
        self.current_speed = 0.0
        self.start_epoch = None
        self.unit_type = unit_type
        if unit_type == "bytes":
            self.lbl_cur_speed.setText("Speed: 0 B/s")
            self.lbl_peak_speed.setText("Peak: 0 B/s")
            self.lbl_total_progress.setText("Total: 0 B")
        else:
            self.lbl_cur_speed.setText("Speed: 0.0/min")
            self.lbl_peak_speed.setText("Peak: 0.0/min")
            self.lbl_total_progress.setText("Total: 0")
        self.canvas.set_data([], 0.0, unit_type)
