"""
unified_dashboard/tests/test_file_exporter_checksum.py
======================================================
Unit tests for SHA-256 content deduplication in File Exporter.
"""

import os
import sys
import tempfile
import hashlib
import unittest
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules.file_exporter import (
    FileExporter,
    _file_content_hash,
    _find_matching_sha256_file,
    _get_unique_target_path,
)


class TestFileExporterChecksum(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.folder_path = self.tmp_dir.name
        self.content_a = b"Sample export document version A with unique body text 12345"
        self.content_b = b"Sample export document version B with different text 67890"

        self.hash_a = hashlib.sha256(self.content_a).hexdigest()
        self.hash_b = hashlib.sha256(self.content_b).hexdigest()

        self.file_a = os.path.join(self.folder_path, "Transcript.pdf")
        with open(self.file_a, "wb") as f:
            f.write(self.content_a)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_file_content_hash_computes_sha256(self):
        computed = _file_content_hash(self.file_a)
        self.assertEqual(len(computed), 64)
        self.assertEqual(computed, self.hash_a)

    def test_find_matching_sha256_file_direct_match(self):
        # Direct target name match
        matched = _find_matching_sha256_file(
            self.folder_path,
            incoming_sha256=self.hash_a,
            incoming_size=len(self.content_a),
            safe_name="Transcript.pdf",
        )
        self.assertEqual(os.path.abspath(matched), os.path.abspath(self.file_a))

    def test_find_matching_sha256_file_canonical_clean_base_match(self):
        # Incoming request wants to name it Transcript__2.pdf (e.g. from seen_names or second link)
        # but the content matches Transcript.pdf already on disk.
        matched = _find_matching_sha256_file(
            self.folder_path,
            incoming_sha256=self.hash_a,
            incoming_size=len(self.content_a),
            safe_name="Transcript__2.pdf",
        )
        self.assertEqual(os.path.abspath(matched), os.path.abspath(self.file_a))

    def test_find_matching_sha256_file_different_name_match(self):
        # Incoming request has a totally different name (e.g. Official_Transcript.pdf)
        # but content is identical to Transcript.pdf on disk.
        matched = _find_matching_sha256_file(
            self.folder_path,
            incoming_sha256=self.hash_a,
            incoming_size=len(self.content_a),
            safe_name="Official_Transcript.pdf",
        )
        self.assertEqual(os.path.abspath(matched), os.path.abspath(self.file_a))

    def test_find_matching_sha256_file_different_content_returns_none(self):
        # Content B does not match Transcript.pdf (Content A)
        matched = _find_matching_sha256_file(
            self.folder_path,
            incoming_sha256=self.hash_b,
            incoming_size=len(self.content_b),
            safe_name="Transcript.pdf",
        )
        self.assertIsNone(matched)

    def test_get_unique_target_path(self):
        # When Transcript.pdf exists, next should be Transcript__2.pdf
        next_path = _get_unique_target_path(self.folder_path, "Transcript.pdf")
        self.assertEqual(os.path.basename(next_path), "Transcript__2.pdf")

        # If Transcript__2.pdf also exists, next should be Transcript__3.pdf
        with open(next_path, "wb") as f:
            f.write(self.content_b)
        third_path = _get_unique_target_path(self.folder_path, "Transcript.pdf")
        self.assertEqual(os.path.basename(third_path), "Transcript__3.pdf")

    def test_force_download_via_fetch_deduplicates_by_sha256(self):
        exporter = FileExporter()
        exporter._log = MagicMock()

        # Mock driver execute_async_script returning base64 for content_a
        import base64
        b64_content = base64.b64encode(self.content_a).decode("utf-8")
        data_url = f"data:application/pdf;base64,{b64_content}"

        driver = MagicMock()
        driver.execute_async_script.return_value = data_url

        # Attempt to download into the same folder with identical content
        result = exporter._force_download_via_fetch(
            driver,
            file_url="https://portal.edu/download/1",
            folder_path=self.folder_path,
            file_name="Transcript.pdf",
        )
        self.assertEqual(result, "duplicate")
        # Ensure no extra file was created
        files_in_dir = os.listdir(self.folder_path)
        self.assertEqual(files_in_dir, ["Transcript.pdf"])

    def test_force_download_via_fetch_saves_different_content_with_versioning(self):
        exporter = FileExporter()
        exporter._log = MagicMock()

        import base64
        b64_content_b = base64.b64encode(self.content_b).decode("utf-8")
        data_url = f"data:application/pdf;base64,{b64_content_b}"

        driver = MagicMock()
        driver.execute_async_script.return_value = data_url

        # Download genuinely different document with same name
        result = exporter._force_download_via_fetch(
            driver,
            file_url="https://portal.edu/download/2",
            folder_path=self.folder_path,
            file_name="Transcript.pdf",
        )
        self.assertEqual(result, "downloaded")
        # Ensure Transcript__2.pdf was created
        self.assertTrue(os.path.exists(os.path.join(self.folder_path, "Transcript__2.pdf")))
        self.assertEqual(_file_content_hash(os.path.join(self.folder_path, "Transcript__2.pdf")), self.hash_b)

    def test_resolve_native_download_removes_sha256_duplicate(self):
        exporter = FileExporter()
        exporter._log = MagicMock()

        # Simulate Chrome downloading an identical file into folder_path
        downloaded_file = os.path.join(self.folder_path, "unconfirmed_1234.pdf")
        with open(downloaded_file, "wb") as f:
            f.write(self.content_a)

        # Before click had no unconfirmed file
        files_before = {"Transcript.pdf"}

        result = exporter._resolve_native_download(
            folder_path=self.folder_path,
            file_name="Transcript.pdf",
            files_before=files_before,
        )
        self.assertEqual(result, "duplicate")
        # The unconfirmed duplicate must have been deleted
        self.assertFalse(os.path.exists(downloaded_file))
        # Original file remains
        self.assertTrue(os.path.exists(self.file_a))

    def test_resolve_native_download_versions_different_content(self):
        exporter = FileExporter()
        exporter._log = MagicMock()

        # Simulate Chrome downloading a DIFFERENT file into folder_path
        downloaded_file = os.path.join(self.folder_path, "unconfirmed_9999.pdf")
        with open(downloaded_file, "wb") as f:
            f.write(self.content_b)

        files_before = {"Transcript.pdf"}

        result = exporter._resolve_native_download(
            folder_path=self.folder_path,
            file_name="Transcript.pdf",
            files_before=files_before,
        )
        self.assertEqual(result, "downloaded")
        # The downloaded file must have been renamed to Transcript__2.pdf
        v2_path = os.path.join(self.folder_path, "Transcript__2.pdf")
        self.assertTrue(os.path.exists(v2_path))
        self.assertEqual(_file_content_hash(v2_path), self.hash_b)


if __name__ == "__main__":
    unittest.main()
