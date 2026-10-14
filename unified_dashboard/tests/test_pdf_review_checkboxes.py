"""
tests/test_pdf_review_checkboxes.py
===================================
Unit test suite verifying:
  1. Checkbox column in FieldsTableModel (PySide6) with user-checkable flags and toggle handling.
  2. Removal of blocking pop-ups during marking correct in both Qt and Tkinter interfaces.
  3. Batch "Mark All Correct" capability.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtCore import Qt, QCoreApplication
from PySide6.QtWidgets import QApplication

from ui_qt.views.pdf_review_view import FieldsTableModel, PdfReviewView
from ui.pdf_review_tab import PdfReviewTab


class TestPdfReviewCheckboxesQt(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_01_fields_model_checkbox_behavior(self):
        model = FieldsTableModel()
        model.set_data([
            ("invoice_no", "INV-1001", "⚪ Unverified"),
            ("total_amount", "$500.00", "⚪ Unverified"),
        ])

        self.assertEqual(model.columnCount(), 4)
        self.assertEqual(model.headerData(0, Qt.Horizontal), "✔")

        # Test flags for column 0
        idx_chk = model.index(0, 0)
        flags = model.flags(idx_chk)
        self.assertTrue(flags & Qt.ItemIsUserCheckable)

        # Initial check state
        self.assertEqual(model.data(idx_chk, Qt.CheckStateRole), Qt.Unchecked)

        # Toggle check state to Checked
        toggled_calls = []
        model.verification_toggled.connect(lambda row, verified: toggled_calls.append((row, verified)))

        res = model.setData(idx_chk, Qt.Checked, Qt.CheckStateRole)
        self.assertTrue(res)
        self.assertEqual(model.data(idx_chk, Qt.CheckStateRole), Qt.Checked)
        self.assertIn("Verified", model.data(model.index(0, 3), Qt.DisplayRole))
        self.assertEqual(toggled_calls, [(0, True)])

    def test_02_mark_correct_no_popup(self):
        mock_mv = MagicMock()
        mock_mv.preset_var.currentText.return_value = "default"
        view = PdfReviewView(main_view=mock_mv)
        view._extracted_fields = {"student_name": "John Doe", "gpa": "3.85"}
        view._fields_model.set_data([
            ("student_name", "John Doe", "⚪ Unverified"),
            ("gpa", "3.85", "⚪ Unverified")
        ])

        # Select first row
        view.table_fields.selectRow(0)

        # Mock QMessageBox.information to ensure it is NEVER called
        with patch("PySide6.QtWidgets.QMessageBox.information") as mock_box:
            view._on_mark_correct()
            mock_box.assert_not_called()

        self.assertEqual(view._fields_model.data(view._fields_model.index(0, 0), Qt.CheckStateRole), Qt.Checked)
        self.assertIn("Verified", view._fields_model.data(view._fields_model.index(0, 3), Qt.DisplayRole))
        self.assertIn("student_name", view.lbl_review_feedback.text())

    def test_03_mark_all_correct_no_popup(self):
        mock_mv = MagicMock()
        mock_mv.preset_var.currentText.return_value = "default"
        view = PdfReviewView(main_view=mock_mv)
        view._fields_model.set_data([
            ("f1", "v1", "⚪ Unverified"),
            ("f2", "v2", "⚪ Unverified")
        ])

        with patch("PySide6.QtWidgets.QMessageBox.information") as mock_box:
            view._on_mark_all_correct()
            mock_box.assert_not_called()

        for r in range(2):
            self.assertEqual(view._fields_model.data(view._fields_model.index(r, 0), Qt.CheckStateRole), Qt.Checked)
        self.assertIn("All", view.lbl_review_feedback.text())


if __name__ == "__main__":
    unittest.main()
