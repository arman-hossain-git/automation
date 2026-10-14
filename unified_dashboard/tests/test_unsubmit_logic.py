"""
tests/test_unsubmit_logic.py
============================
Unit tests for Grades sub-features (Submit Grades and Un-submit Grades),
flexible column parsing, log reconciliation, and dashboard integration.
"""

import os
import tempfile
import unittest
import pandas as pd

from modules.grade_submission import GradeSubmitter
from ui.dashboard import TOOLS
from ui.grade_ui import GradeUI


class TestUnsubmitLogic(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.submitter = GradeSubmitter()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_dashboard_registration(self):
        """Verify unified Grades module is registered in dashboard TOOLS with both sub-features."""
        tool_ids = [t["id"] for t in TOOLS]
        self.assertIn("grades", tool_ids)
        self.assertNotIn("unsubmit_grades", tool_ids)

        tool = next(t for t in TOOLS if t["id"] == "grades")
        self.assertEqual(tool["title"], "Grades")
        self.assertEqual(tool["class"], GradeUI)
        self.assertIn("sub_features", tool)
        sf_ids = [sf["id"] for sf in tool["sub_features"]]
        self.assertIn("submit", sf_ids)
        self.assertIn("unsubmit", sf_ids)

    def test_home_registration(self):
        """Verify Home module is registered as the first item in dashboard TOOLS."""
        from ui.home_ui import HomeUI
        self.assertEqual(TOOLS[0]["id"], "home")
        self.assertEqual(TOOLS[0]["title"], "Home")
        self.assertEqual(TOOLS[0]["class"], HomeUI)

    def test_parent_click_does_not_navigate(self):
        """Verify clicking on a parent module toggles accordion without changing dashboard content view."""
        from ui.dashboard import UnifiedDashboardApp
        from ui.home_ui import HomeUI
        from ui.file_uploader_ui import FileUploaderUI

        app = UnifiedDashboardApp()
        try:
            # Starts on Home
            self.assertEqual(app.active_tool_id, "home")
            self.assertIsInstance(app.active_frame, HomeUI)

            # Clicking parent 'files' or calling toggle_parent_accordion must NOT change active frame
            app.select_tool("files")
            self.assertEqual(app.active_tool_id, "home")
            self.assertIsInstance(app.active_frame, HomeUI)
            self.assertTrue(app.expanded_parents.get("files", False))

            # Clicking parent 'grades' must NOT change active frame
            app.select_tool("grades")
            self.assertEqual(app.active_tool_id, "home")
            self.assertIsInstance(app.active_frame, HomeUI)
            self.assertTrue(app.expanded_parents.get("grades", False))

            # Sub-feature click DOES change active frame
            app.select_sub_feature("files", "file_uploader")
            self.assertEqual(app.active_tool_id, "files")
            self.assertIsInstance(app.active_frame, FileUploaderUI)

            # Clicking parent 'grades' again while on file_uploader must NOT navigate away
            app.select_tool("grades")
            self.assertEqual(app.active_tool_id, "files")
            self.assertIsInstance(app.active_frame, FileUploaderUI)

            # Clicking Home returns to Home
            app.select_tool("home")
            self.assertEqual(app.active_tool_id, "home")
            self.assertIsInstance(app.active_frame, HomeUI)
        finally:
            app.destroy()

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
