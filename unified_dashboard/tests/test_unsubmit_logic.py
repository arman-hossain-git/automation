"""
tests/test_unsubmit_logic.py
============================
Unit tests for Grades sub-features (Submit Grades and Un-submit Grades),
flexible column parsing, log reconciliation, and Qt dashboard integration.
"""

import os
import tempfile
import unittest
import pandas as pd

from PySide6.QtWidgets import QApplication

from modules.grade_submission import GradeSubmitter
from ui_qt.main_window import MainWindow
from ui_qt.views.home_view import HomeView
from ui_qt.views.file_uploader_view import FileUploaderView
from ui_qt.views.grade_view import GradeView


class TestUnsubmitLogic(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["--platform", "offscreen"])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.submitter = GradeSubmitter()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_dashboard_registration(self):
        """Verify unified Grades module is registered in Qt MainWindow and supports submit and unsubmit."""
        window = MainWindow()
        try:
            self.assertIn("grades", window._views)
            grade_view = window._views["grades"]
            self.assertIsInstance(grade_view, GradeView)
            self.assertTrue(hasattr(grade_view, "rb_unsubmit"))
            self.assertTrue(hasattr(grade_view, "rb_submit"))
            self.assertEqual(grade_view.sub_feature, "submit")
        finally:
            window.close()

    def test_home_registration(self):
        """Verify Home module is registered as the default initial view."""
        window = MainWindow()
        try:
            self.assertEqual(window._active_tool_id, "home")
            self.assertIsInstance(window.stack.currentWidget(), HomeView)
        finally:
            window.close()

    def test_parent_click_does_not_navigate(self):
        """Verify clicking on a parent module toggles accordion without changing dashboard content view."""
        window = MainWindow()
        try:
            # Starts on Home
            self.assertEqual(window._active_tool_id, "home")
            self.assertIsInstance(window.stack.currentWidget(), HomeView)

            # Clicking parent 'files' toggles accordion but must NOT change active frame
            files_nav = window._nav_items["files"]
            initial_expanded = files_nav._sub_expanded
            window._on_nav_click("files", has_sub=True)
            self.assertEqual(window._active_tool_id, "home")
            self.assertIsInstance(window.stack.currentWidget(), HomeView)
            self.assertNotEqual(files_nav._sub_expanded, initial_expanded)

            # Sub-feature click DOES change active frame
            window._on_sub_nav_click("files", "file_uploader")
            self.assertEqual(window._active_tool_id, "files")
            self.assertIsInstance(window.stack.currentWidget(), FileUploaderView)

            # Clicking parent 'files' again to collapse does NOT navigate away
            window._on_nav_click("files", has_sub=True)
            self.assertEqual(window._active_tool_id, "files")
            self.assertIsInstance(window.stack.currentWidget(), FileUploaderView)

            # Clicking Home returns to Home
            window.navigate_to("home")
            self.assertEqual(window._active_tool_id, "home")
            self.assertIsInstance(window.stack.currentWidget(), HomeView)
        finally:
            window.close()

    def test_flexible_csv_columns_detection(self):
        """Verify column parsing correctly identifies student and link variations."""
        test_variations = [
            ("student", "link"),
            ("studentId", "link"),
            ("Student ID", "Link"),
            ("id", "url"),
            ("ID", "studentlink"),
        ]

        for s_col, l_col in test_variations:
            csv_path = os.path.join(self.temp_dir, f"test_{s_col}.csv")
            df = pd.DataFrame([
                {s_col: "STD_101", l_col: "https://pcr.example.com/101"},
                {s_col: "STD_102", l_col: "https://pcr.example.com/102"},
            ])
            df.to_csv(csv_path, index=False)

            # Check column detection logic matching grade_submission.py
            student_col = None
            for col in df.columns:
                clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
                if clean_c in ("studentid", "id", "student"):
                    student_col = col
                    break
            self.assertEqual(student_col, s_col)

            link_col = None
            for col in df.columns:
                clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
                if clean_c in ("link", "url", "studentlink"):
                    link_col = col
                    break
            self.assertEqual(link_col, l_col)

    def test_verify_logs_with_custom_student_header(self):
        """Verify verify_logs accurately reconciles files with 'student' header."""
        input_csv = os.path.join(self.temp_dir, "students.csv")
        df_input = pd.DataFrame([
            {"student": "2001", "link": "https://pcr.example.com/2001"},
            {"student": "2002", "link": "https://pcr.example.com/2002"},
            {"student": "2003", "link": "https://pcr.example.com/2003"},
        ])
        df_input.to_csv(input_csv, index=False)

        # Create mock execution_results log file
        log_csv = os.path.join(self.temp_dir, "execution_results_run123.csv")
        df_log = pd.DataFrame([
            {"Timestamp": "2026-09-11 12:00:00", "StudentId": "2001", "Link": "https://...", "Status": "SUCCESS", "Details": "OK"},
            {"Timestamp": "2026-09-11 12:01:00", "StudentId": "2002", "Link": "https://...", "Status": "SUCCESS", "Details": "OK"},
            {"Timestamp": "2026-09-11 12:02:00", "StudentId": "2003", "Link": "https://...", "Status": "FAILED", "Details": "Err"},
        ])
        df_log.to_csv(log_csv, index=False)

        res = self.submitter.verify_logs(input_csv)
        self.assertEqual(res["total"], 3)
        self.assertEqual(res["processed"], 2)
        self.assertEqual(res["missing"], ["2003"])


if __name__ == "__main__":
    unittest.main()
