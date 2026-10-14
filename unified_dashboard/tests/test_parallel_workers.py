"""
tests/test_parallel_workers.py
==============================
Unit tests for parallel worker feature in File Exporter and File Uploader.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules.file_exporter import FileExporter
from modules.file_uploader import FileUploader
from ui_qt.views.file_exporter_view import FileExporterView
from ui_qt.views.file_uploader_view import FileUploaderView
from PySide6.QtWidgets import QApplication

# Ensure single QApplication instance for Qt tests
app = QApplication.instance() or QApplication([])


class TestParallelWorkers(unittest.TestCase):

    def test_exporter_run_parallel_method_exists(self):
        """Verify FileExporter has run_parallel method with expected parameters."""
        exporter = FileExporter()
        self.assertTrue(hasattr(exporter, "run_parallel"))
        self.assertTrue(callable(exporter.run_parallel))

    def test_uploader_run_parallel_method_exists(self):
        """Verify FileUploader has run_parallel method with expected parameters."""
        uploader = FileUploader()
        self.assertTrue(hasattr(uploader, "run_parallel"))
        self.assertTrue(callable(uploader.run_parallel))

    def test_qt_views_have_spin_workers(self):
        """Verify FileExporterView and FileUploaderView expose spin_workers properly configured."""
        exp_view = FileExporterView()
        self.assertTrue(hasattr(exp_view, "spin_workers"))
        self.assertEqual(exp_view.spin_workers.value(), 1)
        self.assertEqual(exp_view.spin_workers.minimum(), 1)
        self.assertEqual(exp_view.spin_workers.maximum(), 50)

        up_view = FileUploaderView()
        self.assertTrue(hasattr(up_view, "spin_workers"))
        self.assertEqual(up_view.spin_workers.value(), 1)
        self.assertEqual(up_view.spin_workers.minimum(), 1)
        self.assertEqual(up_view.spin_workers.maximum(), 50)

    def test_qt_views_include_n_workers_in_inputs(self):
        """Verify n_workers is included in get_current_inputs and describe_task_inputs."""
        exp_view = FileExporterView()
        exp_view.cred_path = __file__
        exp_view.links_path = __file__
        exp_view.download_dir = "."
        exp_view.spin_workers.setValue(4)
        inputs = exp_view.get_current_inputs()
        self.assertEqual(inputs.get("n_workers"), 4)
        desc = exp_view.describe_task_inputs(inputs)
        self.assertIn("4 workers", desc)

        up_view = FileUploaderView()
        up_view.cred_path = __file__
        up_view.source_dir = "."
        up_view.links_path = __file__
        up_view.spin_workers.setValue(8)
        inputs_up = up_view.get_current_inputs()
        self.assertEqual(inputs_up.get("n_workers"), 8)
        desc_up = up_view.describe_task_inputs(inputs_up)
        self.assertIn("8 workers", desc_up)

    def test_exporter_parallel_chunk_distribution(self):
        """Verify exporter run_parallel chunks students properly across worker threads."""
        students = [{"studentId": f"STU_{i:03d}", "link": f"http://test.com/{i}"} for i in range(10)]
        exporter = FileExporter()

        spawned_chunks = []

        def fake_worker_run(self_worker, **kwargs):
            spawned_chunks.append(kwargs.get("students", []))

        with patch.object(FileExporter, "run", new=fake_worker_run), \
             patch("core.session_manager.SessionManager.start_session", return_value={"completed_item_ids": []}), \
             patch("core.session_manager.SessionManager.clear_session"):
            exporter.run_parallel(
                config={"login_url": "http://test.com"},
                students=students,
                download_dir=".",
                n_workers=3,
            )

        # 10 students into 3 workers -> ~4, 4, 2
        total_students_processed = sum(len(c) for c in spawned_chunks)
        self.assertEqual(total_students_processed, 10)
        self.assertEqual(len(spawned_chunks), 3)

    def test_uploader_parallel_chunk_distribution(self):
        """Verify uploader run_parallel chunks students properly across worker threads."""
        students = [{"studentId": f"STU_{i:03d}", "link": f"http://test.com/{i}"} for i in range(12)]
        uploader = FileUploader()

        spawned_chunks = []

        def fake_worker_run(self_worker, **kwargs):
            spawned_chunks.append(kwargs.get("students", []))

        with patch.object(FileUploader, "run", new=fake_worker_run), \
             patch("core.session_manager.SessionManager.start_session", return_value={"completed_item_ids": []}), \
             patch("core.session_manager.SessionManager.clear_session"):
            uploader.run_parallel(
                config={"login_url": "http://test.com"},
                students=students,
                source_dir=".",
                target_folder_name="",
                n_workers=4,
            )

        # 12 students into 4 workers -> 3 each
        total_students_processed = sum(len(c) for c in spawned_chunks)
        self.assertEqual(total_students_processed, 12)
        self.assertEqual(len(spawned_chunks), 4)

    def test_session_manager_worker_sync_and_aggregation(self):
        """Verify SessionManager updates worker and parent sessions and aggregates them."""
        from core.session_manager import SessionManager
        test_mod = "test_mod_xyz"
        try:
            SessionManager.clear_session(test_mod)
            SessionManager.start_session(test_mod, {"key": "val"}, total_items=10)
            SessionManager.start_session(f"{test_mod}_w0", {"key": "val"}, total_items=5)
            SessionManager.start_session(f"{test_mod}_w1", {"key": "val"}, total_items=5)

            SessionManager.update_item_status(f"{test_mod}_w0", "ITEM_1", success=True)
            SessionManager.update_item_status(f"{test_mod}_w1", "ITEM_2", success=True)

            # Both items should be in get_all_completed_item_ids for base module
            completed = SessionManager.get_all_completed_item_ids(test_mod)
            self.assertIn("ITEM_1", completed)
            self.assertIn("ITEM_2", completed)

            # Base session itself should also reflect the synced items
            base_sess = SessionManager.get_unfinished_session(test_mod)
            self.assertIsNotNone(base_sess)
            self.assertIn("ITEM_1", base_sess.get("completed_item_ids", []))
            self.assertIn("ITEM_2", base_sess.get("completed_item_ids", []))
        finally:
            SessionManager.clear_session(test_mod)
            # Verify clean up of base and worker sessions
            self.assertEqual(len(SessionManager.get_all_completed_item_ids(test_mod)), 0)


if __name__ == "__main__":
    unittest.main()

