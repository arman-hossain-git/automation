"""
tests/test_qt_pdf_extractor.py
==============================
Comprehensive unit tests for the PySide6 PDF Extractor workspace and review view.
"""

import os
import sys
import json
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from ui_qt.views.pdf_extractor_view import PdfExtractorView, EditRulesDialog
from ui_qt.views.pdf_review_view import PdfReviewView
from modules.pdf_extractor import DEFAULT_CONFIG


class TestQtPdfExtractorFeature(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        cls.app = QApplication.instance() or QApplication(["--platform", "offscreen"])

    def test_01_tab_structure(self):
        """PdfExtractorView must have 5 tabs with exact names."""
        view = PdfExtractorView()
        self.assertEqual(view.notebook.count(), 5)
        tab_names = [view.notebook.tabText(i).strip() for i in range(5)]
        self.assertIn("1. Setup & Presets", tab_names[0])
        self.assertIn("2. Visual Field Mapper", tab_names[1])
        self.assertIn("3. Advanced JSON", tab_names[2])
        self.assertIn("4. Review & Self-Tune", tab_names[3])
        self.assertIn("5. Live Activity & Logs", tab_names[4])
        view.close()

    def test_02_action_center_tabs(self):
        """Action Center in Visual Field Mapper must have 4 sub-tabs."""
        view = PdfExtractorView()
        self.assertEqual(view.actions_nb.count(), 4)
        sub_tab_names = [view.actions_nb.tabText(i).strip() for i in range(4)]
        self.assertTrue(any("Labels" in t for t in sub_tab_names))
        self.assertTrue(any("Rows" in t for t in sub_tab_names))
        self.assertTrue(any("Exclude" in t for t in sub_tab_names))
        self.assertTrue(any("Sections" in t for t in sub_tab_names))
        view.close()

    def test_03_rules_table_sync(self):
        """Rules table should populate from loaded config."""
        view = PdfExtractorView()
        test_cfg = {
            "record_separator": "TEST_SEP",
            "exclude_patterns": ["Noise line 1", "Noise line 2"],
            "summary_fields": [{"name": "gpa", "pattern": r"GPA:\s*(\d+\.\d+)"}],
            "data_rows": {"pattern": r"^(\d+)\s+(.+)$", "columns": ["id", "desc"]}
        }
        view._load_config_into_editor(test_cfg)
        view._refresh_fields_table()

        rule_count = view._rules_model.rowCount()
        self.assertGreaterEqual(rule_count, 4)

        labels = [view._rules_model._rows[i]["label"] for i in range(rule_count)]
        self.assertTrue(any("Record Separator" in l for l in labels))
        self.assertTrue(any("Exclude" in l for l in labels))
        self.assertTrue(any("gpa" in l for l in labels))
        self.assertTrue(any("Data Row" in l for l in labels))
        view.close()

    def test_04_edit_rules_dialog_validate(self):
        """EditRulesDialog must validate valid and invalid JSON."""
        dialog = EditRulesDialog(None, initial_config=DEFAULT_CONFIG)
        self.assertIsNotNone(dialog)
        dialog.editor.setPlainText('{"valid": true}')
        dialog._save_and_close()
        self.assertEqual(dialog.result_config, {"valid": True})
        dialog.close()

    def test_05_pdf_review_view_two_tabs(self):
        """PdfReviewView must have 2 sub-tabs: Fields and Table Rows."""
        review = PdfReviewView()
        self.assertEqual(review.review_nb.count(), 2)
        tab_names = [review.review_nb.tabText(i).strip() for i in range(2)]
        self.assertTrue(any("Fields" in t for t in tab_names))
        self.assertTrue(any("Rows" in t for t in tab_names))
        review.close()

    def test_06_mark_exclude_in_mapper(self):
        """_mark_exclude in mapper appends escaped pattern to exclude_patterns."""
        view = PdfExtractorView()
        view._verify_lines = ["Line 1", "Page 1 of 5 (Footer Noise)", "Line 3"]
        view._verify_selected_line = 1

        view._mark_exclude()
        cfg, _ = view._parse_editor_json()
        self.assertIn("Page\\ 1\\ of\\ 5\\ \\(Footer\\ Noise\\)", cfg.get("exclude_patterns", []))
        view.close()


if __name__ == "__main__":
    unittest.main()
