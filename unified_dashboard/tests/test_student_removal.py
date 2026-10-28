"""
tests/test_student_removal.py
==============================
Unit tests for the Student Removal automation feature.
Tests cover:
  - StudentRemover class initialization & methods
  - Canonical removal key generation & registry operations
  - Flexible column parsing (studentId, link, and variations)
  - PySide6 StudentRemovalView and Tkinter StudentRemovalUI integration
  - Navigation sidebar and tool registration
"""

import os
import sys
import tempfile
import unittest
import pandas as pd
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules.student_removal import (
    StudentRemover,
    make_removal_key,
    get_completed_removal_items,
    mark_removal_item_completed,
    remove_removal_item_completed,
    MODULE_ID
)
from ui.dashboard import TOOLS
from ui.student_removal_ui import StudentRemovalUI

from PySide6.QtWidgets import QApplication
qt_app = QApplication.instance() or QApplication([])

from ui_qt.views.student_removal_view import StudentRemovalView
from ui_qt.main_window import MainWindow


class TestStudentRemoval(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.remover = StudentRemover()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_remover_methods_exist(self):
        """Verify StudentRemover provides run, run_parallel, and verify_logs."""
        self.assertTrue(hasattr(self.remover, "run"))
        self.assertTrue(callable(self.remover.run))
        self.assertTrue(hasattr(self.remover, "run_parallel"))
        self.assertTrue(callable(self.remover.run_parallel))
        self.assertTrue(hasattr(self.remover, "verify_logs"))
        self.assertTrue(callable(self.remover.verify_logs))

    def test_make_removal_key(self):
        """Test composite key generation for student + link tracking."""
        key = make_removal_key("10245", "https://portal.com/class/1/")
        self.assertEqual(key, "10245::https://portal.com/class/1")

        # Empty link fallback
        key_no_link = make_removal_key("10245", "")
        self.assertEqual(key_no_link, "10245")

    def test_completion_registry(self):
        """Test marking and reading completed removal items."""
        test_csv = os.path.join(self.temp_dir, "test_removal.csv")
        df = pd.DataFrame({
            "studentId": ["101", "102"],
            "link": ["https://portal.com/class1", "https://portal.com/class2"]
        })
        df.to_csv(test_csv, index=False)

        mark_removal_item_completed(test_csv, "101", "https://portal.com/class1")
        completed = get_completed_removal_items(test_csv)
        self.assertIn("101::https://portal.com/class1", completed)

        remove_removal_item_completed(test_csv, "101", "https://portal.com/class1")
        completed_after = get_completed_removal_items(test_csv)
        self.assertNotIn("101::https://portal.com/class1", completed_after)

    def test_dashboard_registration(self):
        """Verify student_removal tool is registered in dashboard TOOLS list."""
        tool_ids = [t["id"] for t in TOOLS]
        self.assertIn("student_removal", tool_ids)

        tool = next(t for t in TOOLS if t["id"] == "student_removal")
        self.assertEqual(tool["title"], "Student Removal")
        self.assertEqual(tool["class"], StudentRemovalUI)

    def test_qt_view_initialization(self):
        """Verify PySide6 StudentRemovalView initializes with inputs and spinbox."""
        view = StudentRemovalView()
        self.assertEqual(view.title_text, "Student Removal")
        self.assertTrue(hasattr(view, "spin_workers"))
        self.assertEqual(view.spin_workers.value(), 1)
        self.assertEqual(view.spin_workers.minimum(), 1)
        self.assertEqual(view.spin_workers.maximum(), 50)
        self.assertTrue(hasattr(view, "chk_skip_completed"))
        self.assertTrue(view.chk_skip_completed.isChecked())
        view.close()

    def test_qt_view_inputs_validation(self):
        """Verify get_current_inputs validates credentials and CSV existence."""
        view = StudentRemovalView()
        with patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
            self.assertEqual(view.get_current_inputs(), {})
            mock_warn.assert_called()

        dummy_cred = os.path.join(self.temp_dir, "creds.txt")
        with open(dummy_cred, "w") as f:
            f.write("user=admin\npassword=secret\nurl=https://portal.com\n")

        dummy_csv = os.path.join(self.temp_dir, "removal.csv")
        with open(dummy_csv, "w") as f:
            f.write("studentId,link\n101,https://portal.com/c1\n")

        view.cred_path = dummy_cred
        view.csv_path = dummy_csv
        view.spin_workers.setValue(3)

        inputs = view.get_current_inputs()
        self.assertEqual(inputs.get("cred_path"), dummy_cred)
        self.assertEqual(inputs.get("csv_path"), dummy_csv)
        self.assertEqual(inputs.get("n_workers"), 3)

        desc = view.describe_task_inputs(inputs)
        self.assertIn("Student Removal", desc)
        self.assertIn("3 workers", desc)
        view.close()

    def test_main_window_lazy_view_registration(self):
        """Verify MainWindow handles student_removal view creation."""
        win = MainWindow()
        self.assertIn("student_removal", win._views._registered_keys)
        view = win._create_view("student_removal")
        self.assertIsNotNone(view)
        self.assertIsInstance(view, StudentRemovalView)
        win.close()

    def test_log_csv_status_lock_resilience(self):
        """Verify _log_csv_status writes log without context manager error even when _csv_lock is None."""
        test_csv = os.path.join(self.temp_dir, "test_removal.csv")
        df = pd.DataFrame({
            "studentId": ["101"],
            "link": ["https://portal.com/class1"]
        })
        df.to_csv(test_csv, index=False)

        remover = StudentRemover()
        logged_errors = []
        remover._log = lambda lvl, msg: logged_errors.append(msg) if lvl == 'error' else None

        # 1. Test with default initialized remover
        remover._log_csv_status(test_csv, "101", "https://portal.com/class1", "SUCCESS", "Removed student")
        self.assertEqual(logged_errors, [])

        # 2. Test even if _csv_lock is explicitly set to None
        remover._csv_lock = None
        remover._log_csv_status(test_csv, "102", "https://portal.com/class2", "SUCCESS", "Removed student 2")
        self.assertEqual(logged_errors, [])

        # Verify master CSV was updated
        master_csv = os.path.join(self.temp_dir, "execution_results.csv")
        self.assertTrue(os.path.isfile(master_csv))
        master_df = pd.read_csv(master_csv)
        self.assertEqual(len(master_df), 2)

    def test_select_student_row_option_element(self):
        """Verify select_student_row properly handles <select> and <option> tags."""
        from core.selenium_helpers import select_student_row

        mock_driver = MagicMock()
        mock_wait = MagicMock()

        # Mock option element
        mock_option = MagicMock()
        mock_option.tag_name = "option"
        mock_option.get_attribute.side_effect = lambda attr: "opt_123" if attr == "value" else "123456 - John Doe"
        mock_option.text = "123456 - John Doe"

        # Mock select parent
        mock_select_parent = MagicMock()
        mock_select_parent.tag_name = "select"
        mock_select_parent.find_elements.return_value = [mock_option]

        mock_option.find_element.return_value = mock_select_parent
        mock_driver.find_elements.return_value = [mock_option]

        res = select_student_row(mock_driver, mock_wait, "//select[@id='withdrawn']", "123456", "Withdrawn")
        self.assertEqual(res, mock_option)
        # Verify execute_script was called to ensure JS selection dispatch
        mock_driver.execute_script.assert_called()


if __name__ == "__main__":
    unittest.main()

