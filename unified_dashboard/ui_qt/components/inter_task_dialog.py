"""
ui_qt/components/inter_task_dialog.py
=====================================
Modal dialog presented between queued automation tasks with an inactivity countdown timer.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QGroupBox
)
from ui_qt.theme import (
    BG_BASE, BG_CARD, BG_CARD_ALT, BORDER_SUBTLE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_WARNING, ACCENT_DANGER, ACCENT_INFO
)


class InterTaskDialog(QDialog):
    """
    Modal dialog displayed between sequential automation tasks.
    Counts down from timeout_minutes (e.g. 15 min).
    """

    def __init__(self, parent, task_label: str, completed_idx: int, total_count: int, timeout_minutes: int = 15):
        super().__init__(parent)
        self.setWindowTitle("Task Completed — Verification & Next Step")
        self.setFixedSize(620, 520)
        self.setModal(True)

        self.choice = 3  # Default: Proceed immediately to next queued task
        self.timeout_occurred = False
        self.seconds_remaining = int(timeout_minutes * 60)

        self._build_ui(task_label, completed_idx, total_count)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _build_ui(self, task_label: str, completed_idx: int, total_count: int):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(14)

        # Header Frame
        hdr = QFrame()
        hdr.setStyleSheet(f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; border-radius: 8px; padding: 12px;")
        hdr_layout = QVBoxLayout(hdr)
        hdr_layout.setContentsMargins(12, 10, 12, 10)

        lbl_success = QLabel(f"✓  Task {completed_idx} of {total_count} Completed Successfully")
        lbl_success.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {ACCENT_SUCCESS}; border: none;")
        lbl_details = QLabel(f"Details: {task_label}")
        lbl_details.setWordWrap(True)
        lbl_details.setStyleSheet(f"font-size: 13px; color: {TEXT_PRIMARY}; border: none; margin-top: 4px;")

        hdr_layout.addWidget(lbl_success)
        hdr_layout.addWidget(lbl_details)
        root_layout.addWidget(hdr)

        # Countdown Timer Card
        timer_card = QFrame()
        timer_card.setStyleSheet(f"background-color: {BG_CARD}; border: 1px solid {BORDER_SUBTLE}; border-radius: 8px;")
        t_layout = QVBoxLayout(timer_card)
        t_layout.setContentsMargins(16, 12, 16, 12)
        t_layout.setSpacing(4)

        lbl_timer_title = QLabel("⏱️  INACTIVITY COUNTDOWN TIMER")
        lbl_timer_title.setAlignment(Qt.AlignCenter)
        lbl_timer_title.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {TEXT_MUTED}; letter-spacing: 0.5px; border: none;")

        self.lbl_timer = QLabel("")
        self.lbl_timer.setAlignment(Qt.AlignCenter)
        self.lbl_timer.setStyleSheet(f"font-size: 26px; font-weight: 800; color: {ACCENT_PRIMARY}; border: none;")
        self._update_timer_label()

        lbl_timer_sub = QLabel(
            "If no action is selected before the countdown reaches 0:00, the system safely\n"
            "assumes you are away from your desk, pauses execution, and safely closes."
        )
        lbl_timer_sub.setAlignment(Qt.AlignCenter)
        lbl_timer_sub.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED}; border: none;")

        t_layout.addWidget(lbl_timer_title)
        t_layout.addWidget(self.lbl_timer)
        t_layout.addWidget(lbl_timer_sub)
        root_layout.addWidget(timer_card)

        # Action Choices Card
        choices_group = QGroupBox("Select Next Action")
        c_layout = QVBoxLayout(choices_group)
        c_layout.setSpacing(8)

        def add_choice_button(title: str, subtitle: str, color_hex: str, choice_val: int):
            btn_frame = QFrame()
            btn_frame.setStyleSheet(f"background-color: {BG_CARD_ALT}; border: 1px solid {BORDER_SUBTLE}; border-radius: 6px;")
            b_layout = QVBoxLayout(btn_frame)
            b_layout.setContentsMargins(10, 8, 10, 8)
            b_layout.setSpacing(2)

            btn = QPushButton(title)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color_hex};
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 6px 12px;
                    font-size: 13px;
                    font-weight: 600;
                    text-align: left;
                }}
            """)
            btn.clicked.connect(lambda: self._select_choice(choice_val))

            desc = QLabel(subtitle)
            desc.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; border: none;")

            b_layout.addWidget(btn)
            b_layout.addWidget(desc)
            c_layout.addWidget(btn_frame)

        add_choice_button("1. Verify current task and discard next tasks", "Verifies current results now, then discards remaining tasks.", ACCENT_INFO, 1)
        add_choice_button("2. Pause queue (review current task now, resume later)", "Stops runner temporarily without discarding remaining queue tasks.", ACCENT_WARNING, 2)
        add_choice_button("3. Proceed immediately to next queued task", "Default: Immediately continues automation with next task.", ACCENT_SUCCESS, 3)
        add_choice_button("4. Abort all execution and exit", "Stops current run and purges all remaining tasks.", ACCENT_DANGER, 4)

        root_layout.addWidget(choices_group)

    def _tick(self):
        self.seconds_remaining -= 1
        if self.seconds_remaining <= 0:
            self._timer.stop()
            self.timeout_occurred = True
            self.choice = 4  # Timeout abort
            self.accept()
            return
        self._update_timer_label()

    def _update_timer_label(self):
        mins = self.seconds_remaining // 60
        secs = self.seconds_remaining % 60
        self.lbl_timer.setText(f"{mins:02d}:{secs:02d}")

    def _select_choice(self, choice_val: int):
        self._timer.stop()
        self.choice = choice_val
        self.accept()
