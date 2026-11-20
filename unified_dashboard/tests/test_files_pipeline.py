"""
tests/test_files_pipeline.py
============================
Unit tests for 3-Way File Management (Export, Upload, and Combined Pipeline)
with parallel workers support.
"""

import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication
from ui_qt.views.files_view import FilesView
from ui_qt.views.file_exporter_view import FileExporterView
from ui_qt.views.file_uploader_view import FileUploaderView
from modules.file_exporter import FileExporter
from modules.file_uploader import FileUploader

app = QApplication.instance() or QApplication([])


class TestFilesPipeline(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.dummy_cred = os.path.join(self.temp_dir, "creds.txt")
        with open(self.dummy_cred, "w") as f:
            f.write("username=admin\npassword=secret\nlogin_url=http://test.com\nlogin_btn_xpath=//button\n")

        self.dummy_csv = os.path.join(self.temp_dir, "links.csv")
        with open(self.dummy_csv, "w") as f:
            f.write("studentId,link\n1001,http://test.com/1001\n1002,http://test.com/1002\n")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_instantiation_modes(self):
        """Verify FilesView instantiates correctly with each default mode."""
        view_export = FilesView(default_mode="export")
        self.assertEqual(view_export.mode, "export")
        self.assertTrue(view_export.rb_export.isChecked())
        self.assertEqual(view_export.tool_mode, "download")

        view_upload = FilesView(default_mode="upload")
        self.assertEqual(view_upload.mode, "upload")
        self.assertTrue(view_upload.rb_upload.isChecked())
        self.assertEqual(view_upload.tool_mode, "upload")

        view_pipe = FilesView(default_mode="pipeline")
        self.assertEqual(view_pipe.mode, "pipeline")
        self.assertTrue(view_pipe.rb_pipeline.isChecked())

    def test_mode_switching_and_card_visibility(self):
        """Verify switching modes toggles option cards and pickers correctly."""
        view = FilesView()

        # Switch to upload
        view.rb_upload.setChecked(True)
        self.assertEqual(view.mode, "upload")
        self.assertTrue(view.card_export_options.isHidden())
        self.assertFalse(view.card_upload_options.isHidden())
        self.assertTrue(view.card_pipeline_options.isHidden())
        self.assertFalse(view.row_source_dir.isHidden())
        self.assertTrue(view.row_download_dir.isHidden())
        self.assertTrue(view.row_staging_dir.isHidden())

        # Switch to pipeline
        view.rb_pipeline.setChecked(True)
        self.assertEqual(view.mode, "pipeline")
        self.assertFalse(view.card_export_options.isHidden())
        self.assertFalse(view.card_upload_options.isHidden())
        self.assertFalse(view.card_pipeline_options.isHidden())
        self.assertTrue(view.row_source_dir.isHidden())
        self.assertTrue(view.row_download_dir.isHidden())
        self.assertFalse(view.row_staging_dir.isHidden())

        # Switch back to export
        view.rb_export.setChecked(True)
        self.assertEqual(view.mode, "export")
        self.assertFalse(view.card_export_options.isHidden())
        self.assertTrue(view.card_upload_options.isHidden())
        self.assertTrue(view.card_pipeline_options.isHidden())
        self.assertTrue(view.row_source_dir.isHidden())
        self.assertFalse(view.row_download_dir.isHidden())
        self.assertTrue(view.row_staging_dir.isHidden())

    def test_set_sub_feature_navigation_hook(self):
        """Verify set_sub_feature correctly selects the matching mode."""
        view = FilesView()

        view.set_sub_feature("file_uploader")
        self.assertEqual(view.mode, "upload")
        self.assertTrue(view.rb_upload.isChecked())

        view.set_sub_feature("file_pipeline")
        self.assertEqual(view.mode, "pipeline")
        self.assertTrue(view.rb_pipeline.isChecked())

        view.set_sub_feature("file_exporter")
        self.assertEqual(view.mode, "export")
        self.assertTrue(view.rb_export.isChecked())

    def test_parallel_workers_available_in_pipeline_inputs(self):
        """Verify spin_workers is configured and included in pipeline inputs."""
        view = FilesView(default_mode="pipeline")
        self.assertTrue(hasattr(view, "spin_workers"))
        self.assertEqual(view.spin_workers.minimum(), 1)
        self.assertEqual(view.spin_workers.maximum(), 50)

        view.cred_path = self.dummy_cred
        view.links_path = self.dummy_csv
        view.staging_dir = self.temp_dir
        view.spin_workers.setValue(6)

        inputs = view.get_current_inputs()
        self.assertEqual(inputs.get("mode"), "pipeline")
        self.assertEqual(inputs.get("n_workers"), 6)
        self.assertEqual(inputs.get("staging_dir"), self.temp_dir)

        desc = view.describe_task_inputs(inputs)
        self.assertIn("Pipeline", desc)
        self.assertIn("6 workers", desc)

    def test_pipeline_execution_calls_both_parallel_workers(self):
        """Verify pipeline execution with n_workers > 1 invokes run_parallel on both engines."""
        view = FilesView(default_mode="pipeline")

        inputs = {
            "mode": "pipeline",
            "cred_path": self.dummy_cred,
            "target_cred_path": self.dummy_cred,
            "links_path": self.dummy_csv,
            "target_links_path": self.dummy_csv,
            "staging_dir": self.temp_dir,
            "n_workers": 4,
            "folder_template": "{studentId}/{category}",
            "target_folder": "Root (Files & Documents)",
            "skip_dups": True,
            "cleanup_staging": False,
            "auto_devtools": False,
        }

        with patch.object(FileExporter, "run_parallel") as mock_exp_parallel, \
             patch.object(FileUploader, "run_parallel") as mock_up_parallel:

            view.execute_task_backend(
                inputs,
                log_callback=lambda msg, lvl: None,
                progress_callback=lambda c, t: None,
                kpi_callback=lambda d: None,
                is_aborted=lambda: False,
            )

            # Both engines should have run in parallel with 4 workers
            mock_exp_parallel.assert_called_once()
            self.assertEqual(mock_exp_parallel.call_args[1]["n_workers"], 4)
            self.assertEqual(mock_exp_parallel.call_args[1]["download_dir"], self.temp_dir)

            mock_up_parallel.assert_called_once()
            self.assertEqual(mock_up_parallel.call_args[1]["n_workers"], 4)
            self.assertEqual(mock_up_parallel.call_args[1]["source_dir"], self.temp_dir)

    def test_pipeline_execution_calls_sequential_when_workers_is_one(self):
        """Verify pipeline execution with n_workers = 1 invokes standard run on both engines."""
        view = FilesView(default_mode="pipeline")

        inputs = {
            "mode": "pipeline",
            "cred_path": self.dummy_cred,
            "target_cred_path": self.dummy_cred,
            "links_path": self.dummy_csv,
            "target_links_path": self.dummy_csv,
            "staging_dir": self.temp_dir,
            "n_workers": 1,
            "folder_template": "{studentId}/{category}",
            "target_folder": "Root (Files & Documents)",
            "skip_dups": True,
            "cleanup_staging": False,
            "auto_devtools": False,
        }

        with patch.object(FileExporter, "run") as mock_exp_run, \
             patch.object(FileUploader, "run") as mock_up_run:

            view.execute_task_backend(
                inputs,
                log_callback=lambda msg, lvl: None,
                progress_callback=lambda c, t: None,
                kpi_callback=lambda d: None,
                is_aborted=lambda: False,
            )

            mock_exp_run.assert_called_once()
            self.assertEqual(mock_exp_run.call_args[1]["download_dir"], self.temp_dir)

            mock_up_run.assert_called_once()
            self.assertEqual(mock_up_run.call_args[1]["source_dir"], self.temp_dir)

    def test_pipeline_aborts_before_upload_when_requested(self):
        """Verify pipeline immediately aborts and does not start uploader if aborted during exporter."""
        view = FilesView(default_mode="pipeline")

        inputs = {
            "mode": "pipeline",
            "cred_path": self.dummy_cred,
            "target_cred_path": self.dummy_cred,
            "links_path": self.dummy_csv,
            "target_links_path": self.dummy_csv,
            "staging_dir": self.temp_dir,
            "n_workers": 1,
        }

        abort_calls = [False]

        def fake_exporter_run(**kwargs):
            # User hits abort during exporter run
            abort_calls[0] = True

        with patch.object(FileExporter, "run", side_effect=fake_exporter_run), \
             patch.object(FileUploader, "run") as mock_up_run:

            view.execute_task_backend(
                inputs,
                log_callback=lambda msg, lvl: None,
                progress_callback=lambda c, t: None,
                kpi_callback=lambda d: None,
                is_aborted=lambda: abort_calls[0],
            )

            # Uploader must not be called
            mock_up_run.assert_not_called()

    def test_pipeline_staging_cleanup(self):
        """Verify staging directory is cleaned when cleanup_staging is True."""
        view = FilesView(default_mode="pipeline")
        staging_dir = os.path.join(self.temp_dir, "staging_test")
        os.makedirs(staging_dir, exist_ok=True)
        staged_file = os.path.join(staging_dir, "test.pdf")
        with open(staged_file, "wb") as f:
            f.write(b"data")

        inputs = {
            "mode": "pipeline",
            "cred_path": self.dummy_cred,
            "target_cred_path": self.dummy_cred,
            "links_path": self.dummy_csv,
            "target_links_path": self.dummy_csv,
            "staging_dir": staging_dir,
            "n_workers": 1,
            "cleanup_staging": True,
        }

        with patch.object(FileExporter, "run"), \
             patch.object(FileUploader, "run"):

            view.execute_task_backend(
                inputs,
                log_callback=lambda msg, lvl: None,
                progress_callback=lambda c, t: None,
                kpi_callback=lambda d: None,
                is_aborted=lambda: False,
            )

            # Staged file should be cleaned up
            self.assertFalse(os.path.exists(staged_file))


if __name__ == "__main__":
    unittest.main()
