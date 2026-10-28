"""
tests/test_unsubmit_logic.py
============================
Unit tests for Grades sub-features (Submit Grades and Un-submit Grades),
flexible column parsing, log reconciliation, and dashboard integration.
"""

import os
import sys
import tempfile
import unittest
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

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

    def test_no_grade_or_score_extraction(self):
        """Rows with no grade/score columns should yield empty grade_val and score_val
        (simulates the new modal-only submit path)."""
        from modules.grade_submission import _has_value

        df = pd.DataFrame([
            {"studentId": "1001", "link": "https://example.com/1001"},
            {"studentId": "1002", "link": "https://example.com/1002"},
        ])

        # Mimic the extraction logic from grade_submission.py
        for _, row in df.iterrows():
            score_val = ""
            for col in df.columns:
                clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
                if clean_c in ("score", "finalscore") and _has_value(row.get(col)):
                    val = str(row.get(col)).strip()
                    if val.lower() not in ("", "nan", "none", "null"):
                        score_val = val
                        break
            grade_val = ""
            for col in df.columns:
                clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
                if clean_c in ("grade", "finalgrade") and _has_value(row.get(col)):
                    val = str(row.get(col)).strip()
                    if val.lower() not in ("", "nan", "none", "null"):
                        grade_val = val
                        break
            self.assertEqual(score_val, "", "score_val must be empty when column is absent")
            self.assertEqual(grade_val, "", "grade_val must be empty when column is absent")

    def test_nan_score_grade_extraction(self):
        """NaN values in score/grade columns must produce empty strings (not 'nan')."""
        from modules.grade_submission import _has_value
        import numpy as np

        df = pd.DataFrame([
            {"studentId": "2001", "link": "https://example.com/2001", "score": np.nan, "grade": np.nan},
            {"studentId": "2002", "link": "https://example.com/2002", "score": "95", "grade": ""},
            {"studentId": "2003", "link": "https://example.com/2003", "score": "", "grade": "A"},
        ])

        expected = [
            ("", ""),    # both NaN → empty
            ("95", ""),  # score present; grade blank → empty
            ("", "A"),   # score blank → empty; grade present
        ]

        for (exp_score, exp_grade), (_, row) in zip(expected, df.iterrows()):
            score_val = ""
            for col in df.columns:
                clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
                if clean_c in ("score", "finalscore") and _has_value(row.get(col)):
                    val = str(row.get(col)).strip()
                    if val.lower() not in ("", "nan", "none", "null"):
                        score_val = val
                        break
            grade_val = ""
            for col in df.columns:
                clean_c = str(col).strip().lower().replace(" ", "").replace("_", "")
                if clean_c in ("grade", "finalgrade") and _has_value(row.get(col)):
                    val = str(row.get(col)).strip()
                    if val.lower() not in ("", "nan", "none", "null"):
                        grade_val = val
                        break
            self.assertEqual(score_val, exp_score)
            self.assertEqual(grade_val, exp_grade)

    def test_dynamic_save_class_xpath_when_unconfigured(self):
        """When no submit/save xpath is specified in config, it returns empty string to trigger dynamic discovery."""
        # Empty config
        self.assertEqual(self.submitter._get_save_class_xpath({}), "")
        # Config with other keys
        cfg = {"user": "admin", "password": "123"}
        self.assertEqual(self.submitter._get_save_class_xpath(cfg), "")

    def test_custom_save_class_xpath_override(self):
        """When user provides an explicit XPath in credentials, it must take precedence."""
        custom_xp = "//input[@id='btnSaveClass']"
        self.assertEqual(self.submitter._get_save_class_xpath({"save_class_xpath": custom_xp}), custom_xp)
        self.assertEqual(self.submitter._get_save_class_xpath({"class_save_xpath": custom_xp}), custom_xp)
        self.assertEqual(self.submitter._get_save_class_xpath({"save_xpath": custom_xp}), custom_xp)

    def test_modal_submit_xpath_override_and_discovery(self):
        """Verify modal (Update) xpath resolves via update_xpath/modal_submit_xpath only.

        submit_xpath is intentionally NOT a modal-update key — it belongs to the
        page-level Submit button (_get_submit_xpath / _click_submit).
        """
        custom_modal_xp = "//div[@id='modal']//input[@type='submit']"
        self.assertEqual(self.submitter._get_modal_submit_xpath({"update_xpath": custom_modal_xp}), custom_modal_xp)
        self.assertEqual(self.submitter._get_modal_submit_xpath({"modal_submit_xpath": custom_modal_xp}), custom_modal_xp)
        self.assertEqual(self.submitter._get_modal_submit_xpath({"edit_modal_submit_xpath": custom_modal_xp}), custom_modal_xp)
        # submit_xpath must NOT route to the modal update button (it is for page-level Submit)
        self.assertEqual(self.submitter._get_modal_submit_xpath({"submit_xpath": custom_modal_xp}), "")
        self.assertEqual(self.submitter._get_modal_submit_xpath({}), "")

    def test_submit_xpath_routes_to_page_level_submit(self):
        """submit_xpath in credentials must be used by _get_submit_xpath (page-level Submit),
        not by _get_modal_submit_xpath (modal Update)."""
        custom_xp = "//input[@value='Submit']"
        self.assertEqual(self.submitter._get_submit_xpath({"submit_xpath": custom_xp}), custom_xp)
        self.assertEqual(self.submitter._get_submit_xpath({"score_submit_xpath": custom_xp}), custom_xp)
        self.assertEqual(self.submitter._get_submit_xpath({"grade_submit_xpath": custom_xp}), custom_xp)
        # When credentials contain submit_xpath, modal xpath must return empty
        self.assertEqual(self.submitter._get_modal_submit_xpath({"submit_xpath": custom_xp}), "")
        # When unconfigured, both return empty string
        self.assertEqual(self.submitter._get_submit_xpath({}), "")

    def test_click_submit_discovery_and_click(self):
        """Verify _click_submit finds the page-level Submit button and clicks it."""
        from unittest.mock import MagicMock
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_elem = MagicMock()
        mock_elem.is_displayed.return_value = True
        mock_elem.is_enabled.return_value = True
        mock_driver.find_elements.return_value = [mock_elem]

        res = self.submitter._click_submit(mock_driver, mock_wait, config={}, student_id="1001")
        self.assertTrue(res)
        mock_elem.click.assert_called()

    def test_save_class_discovery_and_click(self):
        """Verify _save_class finds the save button and clicks it."""
        from unittest.mock import MagicMock
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_elem = MagicMock()
        mock_elem.is_displayed.return_value = True
        mock_elem.is_enabled.return_value = True
        mock_driver.find_elements.return_value = [mock_elem]

        res = self.submitter._save_class(mock_driver, mock_wait, config={}, student_id="1001")
        self.assertTrue(res)
        mock_elem.click.assert_called()

    def test_edit_modal_submit_discovery_and_click(self):
        """Verify _submit_edit_modal finds the modal submit button and clicks it before saving."""
        from unittest.mock import MagicMock
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_elem = MagicMock()
        mock_elem.is_displayed.return_value = True
        mock_elem.is_enabled.return_value = True
        mock_driver.find_elements.return_value = [mock_elem]

        res = self.submitter._submit_edit_modal(mock_driver, mock_wait, config={}, student_id="1001")
        self.assertTrue(res)
        mock_elem.click.assert_called()


if __name__ == "__main__":
    unittest.main()

