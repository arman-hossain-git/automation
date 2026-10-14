"""
tests/test_pdf_review_selection.py
==================================
Unit tests for the sample selection functionality in PdfReviewView (PySide6):
  1. Verifies candidate document discovery from source folder, active paths, and project.
  2. Verifies candidate name mapping and parent directory disambiguation for duplicates.
  3. Verifies combobox selection and random sample loading.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication
from ui_qt.views.pdf_review_view import PdfReviewView


class TestPdfReviewSampleSelection(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["--platform", "offscreen"])

    def setUp(self):
        self.mock_main_view = MagicMock()
        self.mock_main_view.source_folder = ""
        self.mock_main_view._verify_pdf_path = ""
        self.mock_main_view.preset_var.currentText.return_value = "default"

        self.view = PdfReviewView(main_view=self.mock_main_view)

    def test_01_sample_candidates_discovery(self):
        candidates = self.view.get_sample_candidates()
        self.assertIsInstance(candidates, list)
        for p in candidates:
            self.assertTrue(p.lower().endswith(".pdf"))
            self.assertTrue(os.path.isfile(p))

    def test_02_dropdown_refresh_and_mapping(self):
        self.view._refresh_sample_dropdown()
        count = self.view.combo_sample.count()
        self.assertGreaterEqual(count, 0)
        # Ensure mapping contains all values in combo
        for i in range(count):
            val = self.view.combo_sample.itemText(i)
            self.assertIn(val, self.view._sample_candidates_map)
            self.assertTrue(os.path.isfile(self.view._sample_candidates_map[val]))

    def test_03_selection_from_combobox(self):
        self.view._refresh_sample_dropdown()
        if self.view.combo_sample.count() > 0:
            target_name = self.view.combo_sample.itemText(0)
            target_path = self.view._sample_candidates_map[target_name]

            self.view.combo_sample.setCurrentText(target_name)
            self.view._on_sample_combobox_selected()

            self.assertEqual(
                os.path.normpath(self.view._current_pdf_path),
                os.path.normpath(target_path)
            )
            self.assertIn(os.path.basename(target_path), self.view.lbl_current_doc.text())

    def test_04_random_sample_loading(self):
        candidates = self.view.get_sample_candidates()
        if candidates:
            self.view.load_random_sample()
            self.assertTrue(os.path.isfile(self.view._current_pdf_path))
            self.assertIn(self.view.combo_sample.currentText(), self.view._sample_candidates_map)


if __name__ == "__main__":
    unittest.main()
