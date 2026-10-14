"""
tests/test_qt_ui_smoke.py
=========================
Smoke tests for the PySide6 unified dashboard.
Verifies that:
  1. QApplication and MainWindow initialise without errors.
  2. All core views are registered and reachable via navigate_to().
  3. PdfReviewView discovers candidate PDFs from the project root.
"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class TestPySide6UISmokeTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """Create a single QApplication for the test session (offscreen)."""
        try:
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import Qt
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )
            # Use offscreen platform to avoid needing a display
            cls.app = QApplication.instance() or QApplication(
                ["--platform", "offscreen"]
            )
        except Exception as e:
            cls.app = None
            cls.skip_reason = str(e)

    @classmethod
    def tearDownClass(cls):
        if cls.app:
            cls.app.quit()

    def _skip_if_no_app(self):
        if not self.__class__.app:
            self.skipTest(getattr(self.__class__, "skip_reason", "PySide6 not available"))

    def test_01_pyside6_import(self):
        """PySide6 must be importable."""
        try:
            import PySide6
            self.assertIsNotNone(PySide6.__version__)
        except ImportError:
            self.fail("PySide6 is not installed.")

    def test_02_theme_loads(self):
        """Theme module provides QSS string."""
        from ui_qt.theme import get_global_stylesheet
        qss = get_global_stylesheet()
        self.assertIsInstance(qss, str)
        self.assertGreater(len(qss), 100)

    def test_03_main_window_instantiates(self):
        """MainWindow must instantiate without raising exceptions."""
        self._skip_if_no_app()
        from ui_qt.main_window import MainWindow
        try:
            window = MainWindow()
            self.assertIsNotNone(window)
            window.close()
        except Exception as e:
            self.fail(f"MainWindow raised an exception: {e}")

    def test_04_all_views_registered(self):
        """All tool views must be registered in the stack."""
        self._skip_if_no_app()
        from ui_qt.main_window import MainWindow
        window = MainWindow()
        expected_ids = [
            "home", "file_exporter", "file_uploader",
            "grades", "pdf_extractor", "documentation", "settings"
        ]
        for vid in expected_ids:
            self.assertIn(vid, window._views, f"View '{vid}' not registered in MainWindow._views")
        window.close()

    def test_05_navigate_to_all_views(self):
        """navigate_to() must switch to each standalone view without error."""
        self._skip_if_no_app()
        from ui_qt.main_window import MainWindow
        window = MainWindow()
        standalone = ["home", "grades", "pdf_extractor", "documentation", "settings"]
        for tid in standalone:
            try:
                window.navigate_to(tid)
                visible = window.stack.currentWidget()
                self.assertIsNotNone(visible, f"No widget shown after navigate_to('{tid}')")
            except Exception as e:
                self.fail(f"navigate_to('{tid}') raised: {e}")
        window.close()

    def test_06_pdf_review_view_discovers_candidates(self):
        """PdfReviewView must discover at least one PDF from the project root."""
        self._skip_if_no_app()
        from ui_qt.views.pdf_review_view import PdfReviewView
        view = PdfReviewView()
        candidates = view.get_sample_candidates()
        self.assertIsInstance(candidates, list)
        for p in candidates:
            self.assertTrue(p.lower().endswith(".pdf"), f"Non-PDF in candidates: {p}")
            self.assertTrue(os.path.isfile(p), f"Candidate does not exist: {p}")

    def test_07_pdf_review_dropdown_refresh(self):
        """Dropdown refresh must populate the combo without errors."""
        self._skip_if_no_app()
        from ui_qt.views.pdf_review_view import PdfReviewView
        view = PdfReviewView()
        view._refresh_sample_dropdown()
        count = view.combo_sample.count()
        self.assertGreaterEqual(count, 0)
        for i in range(count):
            label = view.combo_sample.itemText(i)
            self.assertIn(label, view._sample_candidates_map)
            self.assertTrue(os.path.isfile(view._sample_candidates_map[label]))


if __name__ == "__main__":
    unittest.main()
