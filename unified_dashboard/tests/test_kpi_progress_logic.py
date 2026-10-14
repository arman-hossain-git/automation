"""
tests/test_kpi_progress_logic.py
================================
Unit tests for upload vs. download KPI logic, progress bar ranges,
per-student file tracking, and time-series telemetry.
"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class TestKPIProgressLogic(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import Qt
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )
            cls.app = QApplication.instance() or QApplication(
                ["--platform", "offscreen"]
            )
        except Exception as e:
            cls.app = None
            cls.skip_reason = str(e)

    def _skip_if_no_app(self):
        if not self.__class__.app:
            self.skipTest(getattr(self.__class__, "skip_reason", "PySide6 not available"))

    def test_upload_mode_kpi_and_progress_bar(self):
        self._skip_if_no_app()
        from ui_qt.views.file_uploader_view import FileUploaderView

        view = FileUploaderView()
        self.assertEqual(view.tool_mode, "upload")

        # Simulate init_totals event from uploader
        view._handle_kpi_event({
            "type": "init_totals",
            "total_students": 5,
            "total_files": 25,
            "total_bytes": 100 * 1024 * 1024,  # 100 MB
        })

        self.assertEqual(view.total_students, 5)
        self.assertEqual(view.files_total, 25)
        self.assertEqual(view.total_bytes, 100 * 1024 * 1024)
        # Progress bar maximum should be scaled to 1000 for byte progress
        self.assertEqual(view.progress_bar.maximum(), 1000)

        # Simulate student 1 start and file total
        view._handle_kpi_event({
            "type": "student_start",
            "student_id": "STU001",
            "students_traversed": 0,
            "total_students": 5,
        })
        view._handle_kpi_event({
            "type": "files_total",
            "total": 25,
            "student_files": 5,
            "bytes_done": 0,
            "total_bytes": 100 * 1024 * 1024,
        })

        self.assertEqual(view.student_files_total, 5)
        self.assertEqual(view.student_files_done, 0)

        # Simulate 2 files done for STU001
        view._handle_kpi_event({
            "type": "file_done",
            "file_name": "essay.pdf",
            "file_size": 20 * 1024 * 1024,
            "files_done": 2,  # global total uploaded
            "files_total": 25,
            "bytes_done": 20 * 1024 * 1024,
            "total_bytes": 100 * 1024 * 1024,
            "student_files_done": 2,
            "student_files_total": 5,
        })

        # KPI checks for upload mode
        # 1. Students / Items Traversed: {students done}/{Total Students} and {files uploaded}/{total files in storage}
        st_text = view.kpi_students.text()
        self.assertIn("0/5", st_text)
        self.assertIn("2/25 files", st_text)

        # 2. Files / Tasks Processed: {files for student uploaded}/{Total files for that Student}
        f_text = view.kpi_files_done.text()
        self.assertIn("2/5", f_text)

        # 3. Progress bar: byte-based
        # 20 MB / 100 MB = 20% -> pb_val = 200 / 1000
        self.assertEqual(view.progress_bar.value(), 200)
        self.assertEqual(view.lbl_progress_percent.text(), "20%")

    def test_download_mode_kpi_and_progress_bar(self):
        self._skip_if_no_app()
        from ui_qt.views.file_exporter_view import FileExporterView

        view = FileExporterView()
        self.assertEqual(view.tool_mode, "download")

        # Simulate total event
        view._handle_kpi_event({
            "type": "total",
            "total": 10,
        })
        self.assertEqual(view.total_students, 10)
        self.assertEqual(view.progress_bar.maximum(), 10)

        # Student 1 with 3 files
        view._handle_kpi_event({
            "type": "student_start",
            "student_id": "STU100",
            "students_traversed": 0,
            "total_students": 10,
        })
        view._handle_kpi_event({
            "type": "files_total",
            "total": 3,
            "student_files": 3,
        })

        # Process 1 file
        view._handle_kpi_event({
            "type": "file_done",
            "file_name": "report.pdf",
            "files_done": 1,
            "student_files_done": 1,
            "student_files_total": 3,
        })

        # In download mode:
        # Students KPI: {students done}/{Total Students}
        st_text = view.kpi_students.text()
        self.assertEqual(st_text, "0/10 (0.0%)")

        # Files KPI: {files for student downloaded}/{Total files for that Student}
        f_text = view.kpi_files_done.text()
        self.assertIn("1/3", f_text)

        # Student finishes
        view._handle_kpi_event({
            "type": "student_done",
            "students_traversed": 1,
            "total_students": 10,
        })

        # Students KPI should now show 1/10
        st_text_after = view.kpi_students.text()
        self.assertEqual(st_text_after, "1/10 (10.0%)")

        # Progress bar in download mode is based on total students done / total students in csv
        self.assertEqual(view.progress_bar.value(), 1)
        self.assertEqual(view.lbl_progress_percent.text(), "10%")


if __name__ == "__main__":
    unittest.main()
