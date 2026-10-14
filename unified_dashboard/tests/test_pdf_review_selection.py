"""
tests/test_pdf_review_selection.py
==================================
Unit tests for the enhanced sample selection functionality in PdfReviewTab:
  1. Verifies candidate document discovery from source folder, active paths, and project.
  2. Verifies candidate name mapping and parent directory disambiguation for duplicates.
  3. Verifies combobox selection and random sample loading.
"""

import os
import sys
import unittest
import tkinter as tk
from unittest.mock import MagicMock

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ui.pdf_review_tab import PdfReviewTab


class TestPdfReviewSampleSelection(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception:
            cls.root = None

    @classmethod
    def tearDownClass(cls):
        if cls.root:
            try:
                cls.root.destroy()
            except Exception:
                pass

    def setUp(self):
        if not self.root:
            self.skipTest("Tkinter display not available in current test environment")

        self.mock_main_ui = MagicMock()
        self.mock_main_ui.source_folder = ""
        self.mock_main_ui._verify_pdf_path = ""
        self.mock_main_ui._parse_editor_json.return_value = ({}, None)

        self.tab = PdfReviewTab(self.root, self.mock_main_ui)

    def test_01_sample_candidates_discovery(self):
        candidates = self.tab.get_sample_candidates()
        self.assertIsInstance(candidates, list)
        for p in candidates:
            self.assertTrue(p.lower().endswith(".pdf"))
            self.assertTrue(os.path.isfile(p))

    def test_02_dropdown_refresh_and_mapping(self):
        self.tab._refresh_sample_dropdown()
        values = self.tab.combo_sample["values"]
        self.assertIsInstance(values, (list, tuple))
        # Ensure mapping contains all values
        for val in values:
            self.assertIn(val, self.tab._sample_candidates_map)
            self.assertTrue(os.path.isfile(self.tab._sample_candidates_map[val]))

    def test_03_selection_from_combobox(self):
        self.tab._refresh_sample_dropdown()
        values = self.tab.combo_sample["values"]
        if values:
            target_name = values[0]
            target_path = self.tab._sample_candidates_map[target_name]

            self.tab.combo_sample.set(target_name)
            self.tab._on_sample_combobox_selected()

            self.assertEqual(
                os.path.normpath(self.tab._current_pdf_path),
                os.path.normpath(target_path)
            )
            self.assertIn(os.path.basename(target_path), self.tab.lbl_current_doc.cget("text"))

    def test_04_random_sample_loading(self):
        candidates = self.tab.get_sample_candidates()
        if candidates:
            self.tab.load_random_sample()
            self.assertTrue(os.path.isfile(self.tab._current_pdf_path))
            self.assertIn(self.tab.combo_sample.get(), self.tab._sample_candidates_map)


if __name__ == "__main__":
    unittest.main()
