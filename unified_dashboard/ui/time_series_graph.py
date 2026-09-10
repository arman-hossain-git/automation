"""
ui/time_series_graph.py
=======================
Real-time Time-Series Telemetry & Throughput Graph for Automation Dashboards.
Provides responsive, high-performance canvas-based visualization of:
  - Transfer throughput & upload speeds (KB/s, MB/s)
  - Cumulative volume transferred (MB)
  - Live performance stats (Current Speed, Peak Speed, Average Speed, Total Data)
  - Time-indexed progress ticks
"""

import time
import datetime
import tkinter as tk
from tkinter import ttk

from ui.theme import (
    BG_CARD,
    BG_CARD_ALT,
    BG_LOG,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_MUTED,
    TEXT_ACCENT,
    ACCENT_PRIMARY,
    ACCENT_SUCCESS,
    ACCENT_WARNING,
    ACCENT_DANGER,
    BORDER_COLOR,
    FONT_FAMILY,
    FONT_BODY,
    FONT_BODY_BOLD,
    FONT_SMALL,
    FONT_SMALL_BOLD,
    FONT_SUBTITLE,
    get_active_palette,
)
from core.utils import format_bytes, format_speed_bps, format_time_remaining


class TimeSeriesTelemetryGraph(tk.Frame):
    """Interactive real-time canvas chart plotting upload speed & cumulative volume over time."""

    def __init__(self, parent, title: str = "Live Upload Telemetry & Throughput Time-Series", height: int = 175, **kwargs):
        super().__init__(parent, bg=BG_CARD, bd=1, relief="solid", **kwargs)
        self.title_text = title
        self.graph_height = height

        # Telemetry Data Store
        # Each entry: {'time': float (epoch), 'elapsed': float (sec), 'speed': float (bps), 'cumulative_bytes': int, 'file_name': str}
        self.data_points = []
        self.total_expected_bytes = 0
        self.peak_speed_bps = 0.0
        self.current_speed_bps = 0.0
        self.start_epoch = None

        self._build_ui()

    def _build_ui(self):
        # 1. Header Bar with Title & Live Telemetry Metric Badges
        self.header_frame = tk.Frame(self, bg=BG_CARD)
        self.header_frame.pack(fill="x", padx=12, pady=(8, 4))

        # Title on Left
        self.lbl_title = tk.Label(
            self.header_frame,
            text=f"📈 {self.title_text}",
            font=FONT_SUBTITLE,
            fg=TEXT_PRIMARY,
            bg=BG_CARD,
            anchor="w",
        )
        self.lbl_title.pack(side="left")

        # Telemetry Badges on Right
        self.badges_frame = tk.Frame(self.header_frame, bg=BG_CARD)
        self.badges_frame.pack(side="right")

        def _make_badge(parent, icon, label_text, default_val, text_color):
            card = tk.Frame(parent, bg=BG_CARD_ALT, padx=8, pady=2, bd=0)
            card.pack(side="left", padx=4)
            lbl_title = tk.Label(card, text=f"{icon} {label_text}:", font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_CARD_ALT)
            lbl_title.pack(side="left", padx=(0, 4))
            lbl_val = tk.Label(card, text=default_val, font=FONT_SMALL_BOLD, fg=text_color, bg=BG_CARD_ALT)
            lbl_val.pack(side="left")
            return lbl_val

        self.badge_speed = _make_badge(self.badges_frame, "⚡", "Speed", "- B/s", TEXT_ACCENT)
        self.badge_peak = _make_badge(self.badges_frame, "🚀", "Peak", "- B/s", ACCENT_SUCCESS)
        self.badge_avg = _make_badge(self.badges_frame, "📊", "Avg", "- B/s", TEXT_PRIMARY)
        self.badge_volume = _make_badge(self.badges_frame, "💾", "Volume", "0 B", ACCENT_WARNING)

        # 2. Canvas Container
        self.canvas_container = tk.Frame(self, bg=BG_LOG, bd=0)
        self.canvas_container.pack(fill="both", expand=True, padx=12, pady=(2, 8))

        self.canvas = tk.Canvas(
            self.canvas_container,
            bg=BG_LOG,
            height=self.graph_height,
            bd=0,
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Configure>", lambda e: self.redraw())

        # Draw initial empty placeholder grid
        self.after(50, self.redraw)

    def reset(self, total_bytes: int = 0):
        """Clear telemetry history and reset counters for a new automation run."""
        def _apply():
            try:
                self.data_points.clear()
                self.total_expected_bytes = total_bytes
                self.peak_speed_bps = 0.0
                self.current_speed_bps = 0.0
                self.start_epoch = time.time()

                self.badge_speed.config(text="- B/s")
                self.badge_peak.config(text="- B/s")
                self.badge_avg.config(text="- B/s")
                self.badge_volume.config(text=f"0 B / {format_bytes(total_bytes)}" if total_bytes > 0 else "0 B")

                self.redraw()
            except Exception:
                pass
        self.after(0, _apply)

    def set_total_bytes(self, total_bytes: int):
        """Update the expected total bytes payload."""
        def _apply():
            try:
                self.total_expected_bytes = total_bytes
                if self.data_points:
                    last_bytes = self.data_points[-1]['cumulative_bytes']
                    self.badge_volume.config(text=f"{format_bytes(last_bytes)} / {format_bytes(total_bytes)}" if total_bytes > 0 else format_bytes(last_bytes))
            except Exception:
                pass
        self.after(0, _apply)

    def add_data_point(self, cumulative_bytes: int, speed_bps: float = None, file_name: str = None, total_bytes: int = None):
        """Record a live telemetry sample (thread-safe)."""
        now = time.time()
        if self.start_epoch is None:
            self.start_epoch = now

        elapsed = max(0.1, now - self.start_epoch)

        if total_bytes and total_bytes > 0:
            self.total_expected_bytes = total_bytes

        # Calculate or refine speed
        if speed_bps is None:
            if self.data_points:
                prev = self.data_points[-1]
                dt = max(0.05, now - prev['time'])
                db = max(0, cumulative_bytes - prev['cumulative_bytes'])
                speed_bps = db / dt
            else:
                speed_bps = cumulative_bytes / elapsed

        self.current_speed_bps = speed_bps
        if speed_bps > self.peak_speed_bps:
            self.peak_speed_bps = speed_bps

        avg_speed_bps = cumulative_bytes / elapsed if elapsed > 0 else 0.0

        self.data_points.append({
            'time': now,
            'elapsed': elapsed,
            'speed': speed_bps,
            'cumulative_bytes': cumulative_bytes,
            'file_name': file_name,
        })

        def _update_ui():
            try:
                # Update Badges
                self.badge_speed.config(text=format_speed_bps(speed_bps))
                self.badge_peak.config(text=format_speed_bps(self.peak_speed_bps))
                self.badge_avg.config(text=format_speed_bps(avg_speed_bps))

                if self.total_expected_bytes > 0:
                    pct = (cumulative_bytes / self.total_expected_bytes) * 100
                    self.badge_volume.config(text=f"{format_bytes(cumulative_bytes)} / {format_bytes(self.total_expected_bytes)} ({pct:.1f}%)")
                else:
                    self.badge_volume.config(text=format_bytes(cumulative_bytes))

                self.redraw()
            except Exception:
                pass

        self.after(0, _update_ui)

    def redraw(self):
        """Render the telemetry chart onto the Tkinter canvas (thread-safe)."""
        try:
            self.canvas.delete("all")

            w = self.canvas.winfo_width()
            h = self.canvas.winfo_height()
            if w < 60 or h < 40:
                return

            pal = get_active_palette()
            c_bg = pal.get("BG_LOG", "#08090c")
            c_grid = pal.get("BG_CARD_ALT", "#222636")
            c_axis = pal.get("BORDER_COLOR", "#30363d")
            c_text = pal.get("TEXT_MUTED", "#6e7681")
            c_speed = pal.get("ACCENT_PRIMARY", "#388bfd")
            c_speed_fill = "#13233a"
            c_vol = pal.get("ACCENT_WARNING", "#d29922")

            # Margins: Left for speed Y-axis, Right for volume Y-axis, Bottom for X-axis, Top for padding
            PAD_L = 65
            PAD_R = 65
            PAD_T = 16
            PAD_B = 26

            plot_w = w - PAD_L - PAD_R
            plot_h = h - PAD_T - PAD_B

            if plot_w <= 10 or plot_h <= 10:
                return

            # 1. Background & Plot Area
            self.canvas.create_rectangle(0, 0, w, h, fill=c_bg, width=0)
            self.canvas.create_rectangle(PAD_L, PAD_T, PAD_L + plot_w, PAD_T + plot_h, fill=c_bg, outline=c_axis, width=1)

            # Determine Y ranges
            max_speed = max(1024 * 100, self.peak_speed_bps * 1.15)  # minimum 100 KB/s scale
            for dp in self.data_points:
                if dp['speed'] > max_speed:
                    max_speed = dp['speed'] * 1.15

            max_vol = max(1024 * 1024, self.total_expected_bytes)  # minimum 1 MB scale
            if self.data_points:
                last_bytes = self.data_points[-1]['cumulative_bytes']
                if last_bytes > max_vol:
                    max_vol = last_bytes * 1.1

            # Determine X range
            max_time = 30.0  # minimum 30 seconds
            if self.data_points:
                max_time = max(max_time, self.data_points[-1]['elapsed'] * 1.05)

            # 2. Horizontal Gridlines & Y-Axis Labels
            NUM_Y_TICKS = 4
            for i in range(NUM_Y_TICKS + 1):
                y_ratio = i / NUM_Y_TICKS
                y_pos = PAD_T + plot_h - (y_ratio * plot_h)

                # Gridline (except on border)
                if 0 < i < NUM_Y_TICKS:
                    self.canvas.create_line(PAD_L, y_pos, PAD_L + plot_w, y_pos, fill=c_grid, dash=(2, 4), width=1)

                # Left Y-Axis: Speed
                speed_val = y_ratio * max_speed
                self.canvas.create_text(PAD_L - 8, y_pos, text=format_speed_bps(speed_val), fill=c_speed, font=(FONT_FAMILY, 8), anchor="e")

                # Right Y-Axis: Volume
                vol_val = y_ratio * max_vol
                self.canvas.create_text(PAD_L + plot_w + 8, y_pos, text=format_bytes(vol_val), fill=c_vol, font=(FONT_FAMILY, 8), anchor="w")

            # 3. Vertical Gridlines & X-Axis Time Labels
            NUM_X_TICKS = 5
            for i in range(NUM_X_TICKS + 1):
                x_ratio = i / NUM_X_TICKS
                x_pos = PAD_L + (x_ratio * plot_w)
                t_sec = x_ratio * max_time

                mins, secs = divmod(int(t_sec), 60)
                time_str = f"{mins:02d}:{secs:02d}"

                if 0 < i < NUM_X_TICKS:
                    self.canvas.create_line(x_pos, PAD_T, x_pos, PAD_T + plot_h, fill=c_grid, dash=(2, 4), width=1)

                self.canvas.create_text(x_pos, PAD_T + plot_h + 12, text=time_str, fill=c_text, font=(FONT_FAMILY, 8), anchor="center")

            # 4. Legend Labels in Top-Left and Top-Right of Plot
            self.canvas.create_line(PAD_L + 8, PAD_T + 8, PAD_L + 24, PAD_T + 8, fill=c_speed, width=2)
            self.canvas.create_text(PAD_L + 28, PAD_T + 8, text="Speed (KB/s or MB/s)", fill=c_speed, font=(FONT_FAMILY, 8, "bold"), anchor="w")

            self.canvas.create_line(PAD_L + plot_w - 145, PAD_T + 8, PAD_L + plot_w - 129, PAD_T + 8, fill=c_vol, width=2)
            self.canvas.create_text(PAD_L + plot_w - 125, PAD_T + 8, text="Cumulative Volume (MB)", fill=c_vol, font=(FONT_FAMILY, 8, "bold"), anchor="w")

            if not self.data_points:
                # Idle placeholder message
                self.canvas.create_text(
                    PAD_L + plot_w / 2,
                    PAD_T + plot_h / 2,
                    text="Awaiting upload activity... Telemetry will stream live during execution.",
                    fill=c_text,
                    font=(FONT_FAMILY, 9, "italic"),
                    anchor="center",
                )
                return

            # 5. Plot Cumulative Volume Curve (Amber)
            vol_coords = []
            for dp in self.data_points:
                x = PAD_L + (dp['elapsed'] / max_time) * plot_w
                y = PAD_T + plot_h - (dp['cumulative_bytes'] / max_vol) * plot_h
                vol_coords.append((x, y))

            if len(vol_coords) >= 2:
                flat_vol = [coord for pt in vol_coords for coord in pt]
                self.canvas.create_line(flat_vol, fill=c_vol, width=2, smooth=True)
            elif len(vol_coords) == 1:
                x, y = vol_coords[0]
                self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=c_vol, outline="")

            # 6. Plot Speed Curve & Gradient-like Area (Electric Blue)
            speed_coords = []
            for dp in self.data_points:
                x = PAD_L + (dp['elapsed'] / max_time) * plot_w
                y = PAD_T + plot_h - (dp['speed'] / max_speed) * plot_h
                speed_coords.append((x, y))

            if len(speed_coords) >= 2:
                # Filled polygon under curve
                poly_coords = [(PAD_L, PAD_T + plot_h)] + speed_coords + [(speed_coords[-1][0], PAD_T + plot_h)]
                flat_poly = [coord for pt in poly_coords for coord in pt]
                try:
                    self.canvas.create_polygon(flat_poly, fill=c_speed_fill, outline="", smooth=True)
                except Exception:
                    pass

                flat_speed = [coord for pt in speed_coords for coord in pt]
                self.canvas.create_line(flat_speed, fill=c_speed, width=2, smooth=True)

                # Draw glowing dot on the latest live point
                lx, ly = speed_coords[-1]
                self.canvas.create_oval(lx - 4, ly - 4, lx + 4, ly + 4, fill=c_speed, outline="white", width=1)
            elif len(speed_coords) == 1:
                x, y = speed_coords[0]
                self.canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=c_speed, outline="white", width=1)

            # 7. File Completion Markers
            for dp in self.data_points:
                if dp.get('file_name'):
                    x = PAD_L + (dp['elapsed'] / max_time) * plot_w
                    y = PAD_T + plot_h - (dp['speed'] / max_speed) * plot_h
                    # Subtle file marker pulse
                    self.canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=pal.get("ACCENT_SUCCESS", "#238636"), outline="")
        except Exception:
            pass
