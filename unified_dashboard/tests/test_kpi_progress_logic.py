"""
tests/test_kpi_progress_logic.py
================================
Unit tests for upload vs. download KPI logic, progress bar ranges,
distinct per-dimension fields (Students, Rows, Files, Size),
and time-series telemetry.
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
            "total_rows": 5,
            "total_files": 25,
            "total_bytes": 100 * 1024 * 1024,  # 100 MB
        })

        self.assertEqual(view.total_students, 5)
        self.assertEqual(view.total_rows, 5)
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

        # Dedicated KPI checks for upload mode:
        # 1. Students Processed: shows only students
        st_text = view.kpi_students.text()
        self.assertIn("0 / 5", st_text)

        # 2. CSV Rows / Records: shows only rows
        r_text = view.kpi_rows.text()
        self.assertIn("0 / 5", r_text)

        # 3. Total Files Processed: shows total files
        f_text = view.kpi_files_done.text()
        self.assertIn("2 / 25 files", f_text)

        # 4. Current Student Files: shows current student files
        sf_text = view.kpi_student_files.text()
        self.assertIn("2 / 5 files", sf_text)

        # 5. Data Volume / Size
        v_text = view.kpi_data_volume.text()
        self.assertIn("20.00 MB / 100.00 MB", v_text)

        # 6. Progress bar chips:
        self.assertIn("0 / 5", view.bar_lbl_students.text())
        self.assertIn("0 / 5", view.bar_lbl_rows.text())
        self.assertIn("2 / 25", view.bar_lbl_files.text())
        self.assertIn("20.00 MB / 100.00 MB", view.bar_lbl_size.text())

        # 7. Progress bar: byte-based
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
        # Students KPI: shows students
        st_text = view.kpi_students.text()
        self.assertEqual(st_text, "0 / 10 (0.0%)")

        # Files KPI: {files for student downloaded}/{Total files for that Student}
        f_text = view.kpi_student_files.text()
        self.assertIn("1 / 3 files", f_text)

        # Student finishes
        view._handle_kpi_event({
            "type": "student_done",
            "students_traversed": 1,
            "total_students": 10,
        })

        # Students KPI should now show 1 / 10
        st_text_after = view.kpi_students.text()
        self.assertEqual(st_text_after, "1 / 10 (10.0%)")

        # Progress bar in download mode is based on total students done / total students in csv
        self.assertEqual(view.progress_bar.value(), 1)
        self.assertEqual(view.lbl_progress_percent.text(), "10%")

    def test_total_files_not_overwritten_by_student_files_and_dedicated_bars(self):
        """
        Verify that:
        1. When init_totals sets files_total=90 and total_bytes=150 MB,
           subsequent per-student events (e.g. total=22, total_bytes=41.34 MB)
           do NOT overwrite or reduce the global files_total or total_bytes.
        2. Total Files Processed KPI displays global files (22 / 90 files),
           while Current Student Files KPI displays student files (22 / 22 files).
        3. All 4 dedicated progress bars (Students, Rows, Files, Size) reflect
           their respective dimensions correctly.
        """
        self._skip_if_no_app()
        from ui_qt.views.file_uploader_view import FileUploaderView

        view = FileUploaderView()

        # Init with 5 students, 90 files total, 150 MB total
        view._handle_kpi_event({
            "type": "init_totals",
            "total_students": 5,
            "total_rows": 5,
            "total_files": 90,
            "total_bytes": 150 * 1024 * 1024,
        })
        self.assertEqual(view.files_total, 90)
        self.assertEqual(view.total_bytes, 150 * 1024 * 1024)

        # Worker begins student with 22 files
        view._handle_kpi_event({
            "type": "files_total",
            "total": 22,            # Student's local count passed as total
            "student_files": 22,
            "total_bytes": int(41.34 * 1024 * 1024),  # Student's local bytes
        })

        # CRITICAL CHECK: Global totals MUST NOT be overwritten!
        self.assertEqual(view.files_total, 90)
        self.assertEqual(view.total_bytes, 150 * 1024 * 1024)
        self.assertEqual(view.student_files_total, 22)

        # 22 files uploaded for this student
        view._handle_kpi_event({
            "type": "file_done",
            "file_name": "Suha_Rec.pdf",
            "files_done": 22,            # global uploaded so far
            "files_total": 90,           # global files total
            "bytes_done": int(41.34 * 1024 * 1024),
            "student_files_done": 22,
            "student_files_total": 22,
            "students_traversed": 1,
            "total_students": 5,
        })

        # KPI grid checks
        self.assertIn("22 / 90 files", view.kpi_files_done.text())
        self.assertIn("22 / 22 files", view.kpi_student_files.text())
        self.assertIn("41.34 MB / 150.00 MB", view.kpi_data_volume.text())
        self.assertIn("1 / 5", view.kpi_students.text())

        # Dedicated 4 Progress Bars checks
        self.assertEqual(view.pb_students.maximum(), 5)
        self.assertEqual(view.pb_students.value(), 1)
        self.assertIn("1 / 5 (20.0%)", view.lbl_pb_students.text())

        self.assertEqual(view.pb_files.maximum(), 90)
        self.assertEqual(view.pb_files.value(), 22)
        self.assertIn("22 / 90 (24.4%)", view.lbl_pb_files.text())

        self.assertEqual(view.pb_size.maximum(), 1000)
        # 41.34 / 150 ~= 27.56% -> 275 / 1000
        self.assertTrue(270 <= view.pb_size.value() <= 280)
        self.assertIn("41.34 MB / 150.00 MB", view.lbl_pb_size.text())


if __name__ == "__main__":
    unittest.main()
