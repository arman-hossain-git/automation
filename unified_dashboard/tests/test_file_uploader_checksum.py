"""
tests/test_file_uploader_checksum.py
====================================
Unit tests for content hash/checksum duplicate detection in File Uploader.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.utils import compute_file_sha256
from core.selenium_helpers import (
    compute_portal_file_sha256,
    verify_portal_file_content,
    file_exists_in_listing,
)


class TestFileUploaderChecksum(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_file = os.path.join(self.tmp_dir.name, "sample_transcript.pdf")
        with open(self.test_file, "wb") as f:
            f.write(b"%PDF-1.4 test document content for sha256 checksum testing 12345")
        self.expected_hash = compute_file_sha256(self.test_file)

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch("requests.Session")
    def test_compute_portal_file_sha256(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [b"%PDF-1.4 test document content for sha256 checksum testing 12345"]
        mock_session.get.return_value = mock_resp

        driver = MagicMock()
        driver.get_cookies.return_value = [{"name": "session_id", "value": "abc"}]
        driver.current_url = "https://pcr.school.edu/files"

        computed = compute_portal_file_sha256(driver, "https://pcr.school.edu/download?id=1")
        self.assertEqual(computed, self.expected_hash)

    @patch("core.selenium_helpers.compute_portal_file_sha256")
    def test_verify_portal_file_content_match_and_mismatch(self, mock_compute_sha):
        driver = MagicMock()

        # Match
        mock_compute_sha.return_value = self.expected_hash
        self.assertTrue(verify_portal_file_content(driver, "https://pcr/doc", self.test_file))

        # Mismatch
        mock_compute_sha.return_value = "0000000000000000000000000000000000000000000000000000000000000000"
        self.assertFalse(verify_portal_file_content(driver, "https://pcr/doc", self.test_file))

    @patch("core.selenium_helpers.compute_portal_file_sha256")
    def test_file_exists_in_listing_checksum_match_different_name(self, mock_compute_sha):
        driver = MagicMock()
        mock_compute_sha.return_value = self.expected_hash

        # Table snapshot where the portal truncated or altered the name to 'sample_tran...pdf'
        snapshot = {
            'files': [
                {
                    'name': 'sample_tran...pdf',
                    'href': 'https://pcr/download?id=99',
                    'raw_text': 'sample_tran...pdf',
                }
            ],
            'by_name': {
                'sample_tran...pdf': {
                    'name': 'sample_tran...pdf',
                    'href': 'https://pcr/download?id=99',
                }
            }
        }

        # Should match by content hash even if the filename differs!
        found = file_exists_in_listing(
            driver=driver,
            file_name="sample_transcript.pdf",
            config={},
            local_path=self.test_file,
            table_snapshot=snapshot
        )
        self.assertTrue(found)

    def test_orbund_sorted_data_table_snapshot_extraction(self):
        from core.selenium_helpers import get_portal_table_snapshot
        driver = MagicMock()
        driver.current_url = "https://sjsm.orbundsis.com/einstein-freshair/touch/files_and_documents.jsp"

        # Mock a row representing an Orbund SortedDataTable row
        mock_row = MagicMock()
        mock_row.get_attribute.side_effect = lambda attr: (
            "Contract_20222023_346641614.pdf (133 KB)" if attr == "textContent" else ""
        )

        mock_name_anchor = MagicMock()
        mock_name_anchor.is_displayed.return_value = True
        mock_name_anchor.get_attribute.side_effect = lambda attr: (
            "Contract_20222023_346641614.pdf" if attr in ("textContent", "innerText")
            else "javascript:openInViewer('123');" if attr == "href"
            else ""
        )
        mock_name_anchor.text = "Contract_20222023_346641614.pdf"

        mock_dl_anchor = MagicMock()
        mock_dl_anchor.get_attribute.side_effect = lambda attr: (
            "get_repository_file.jsp?folderFileReltnId=123" if attr == "href" else "Download File"
        )

        mock_size_cell = MagicMock()
        mock_size_cell.get_attribute.side_effect = lambda attr: (
            "(133 KB)" if attr in ("textContent", "innerText") else ""
        )

        # When searching inside the row for elements
        def row_find_elements(by, xpath):
            if "get_repository_file" in xpath or "download" in xpath.lower():
                return [mock_dl_anchor]
            if ".//a |" in xpath:
                return [mock_name_anchor, mock_size_cell]
            if ".//td" in xpath or xpath == ".//td":
                return [mock_size_cell]
            if ".//a" in xpath:
                return [mock_name_anchor]
            return []

        mock_row.find_elements.side_effect = row_find_elements

        # When driver searches for rows
        def driver_find_elements(by, xpath):
            if "SortedDataTable" in xpath:
                return [mock_row]
            return []

        driver.find_elements.side_effect = driver_find_elements

        snapshot = get_portal_table_snapshot(driver, config={}, wait_timeout=0.1)
        self.assertEqual(len(snapshot['files']), 1)
        file_info = snapshot['files'][0]
        self.assertEqual(file_info['name'], "Contract_20222023_346641614.pdf")
        self.assertIn("get_repository_file.jsp?folderFileReltnId=123", file_info['href'])
        self.assertIn("133 KB", file_info.get('size_text', ''))
        self.assertIn("contract_20222023_346641614.pdf", snapshot['by_name'])

    def test_deduplicate_local_files_by_sha256_preserves_first_and_identifies_later(self):
        from core.utils import deduplicate_local_files_by_sha256

        # Write test files: 2 files with identical hash, 1 unique
        f1 = os.path.join(self.tmp_dir.name, "Contract_20212022_346641029.pdf")
        f2 = os.path.join(self.tmp_dir.name, "Contract_20212022_346641029__2.pdf")
        f3 = os.path.join(self.tmp_dir.name, "Contract_20212022_346641029__3.pdf")

        content_identical = b"Contract 2021-2022 identical body content for student 346641029"
        content_different = b"Contract 2021-2022 DIFFERENT amended content"

        with open(f1, "wb") as f:
            f.write(content_identical)
        with open(f2, "wb") as f:
            f.write(content_identical)
        with open(f3, "wb") as f:
            f.write(content_different)

        files = [
            "Contract_20212022_346641029__2.pdf",
            "Contract_20212022_346641029.pdf",
            "Contract_20212022_346641029__3.pdf",
        ]

        unique_files, duplicate_info, calculated_hashes = deduplicate_local_files_by_sha256(
            files, self.tmp_dir.name
        )

        # The canonical first file must be kept in unique_files
        self.assertIn("Contract_20212022_346641029.pdf", unique_files)
        # The unique amended file must be kept in unique_files
        self.assertIn("Contract_20212022_346641029__3.pdf", unique_files)
        # The duplicate with __2 suffix must be identified as duplicate
        self.assertNotIn("Contract_20212022_346641029__2.pdf", unique_files)
        self.assertIn("Contract_20212022_346641029__2.pdf", duplicate_info)
        self.assertEqual(
            duplicate_info["Contract_20212022_346641029__2.pdf"]["first_file"],
            "Contract_20212022_346641029.pdf"
        )

    @patch("core.selenium_helpers.delete_portal_file_by_name")
    def test_remove_destination_duplicate_files_by_sha256(self, mock_delete):
        from core.selenium_helpers import remove_destination_duplicate_files_by_sha256

        mock_delete.return_value = True
        driver = MagicMock()
        wait = MagicMock()

        portal_files = [
            {'name': 'Contract_20212022_346641029.pdf', 'href': 'https://portal/doc/1'},
            {'name': 'Contract_20212022_346641029__2.pdf', 'href': 'https://portal/doc/2'},
            {'name': 'Other_Report.pdf', 'href': 'https://portal/doc/3'},
        ]
        portal_hashes = {
            'https://portal/doc/1': 'sha_hash_abc_123',
            'https://portal/doc/2': 'sha_hash_abc_123',  # Identical hash!
            'https://portal/doc/3': 'sha_hash_xyz_789',
        }

        updated = remove_destination_duplicate_files_by_sha256(
            driver=driver,
            wait=wait,
            config={},
            current_portal_files=portal_files,
            portal_file_hashes=portal_hashes,
            preferred_names={'Contract_20212022_346641029.pdf'},
            student_id="346641029",
            category_name="Finance"
        )

        # Deletion must be invoked for the later duplicate, NOT the first file
        mock_delete.assert_called_once()
        call_args = mock_delete.call_args[0]
        deleted_name = call_args[2]
        self.assertEqual(deleted_name, 'Contract_20212022_346641029__2.pdf')

        # Remaining files must retain the first file and the distinct file
        remaining_names = [f['name'] for f in updated]
        self.assertIn('Contract_20212022_346641029.pdf', remaining_names)
        self.assertIn('Other_Report.pdf', remaining_names)
        self.assertNotIn('Contract_20212022_346641029__2.pdf', remaining_names)


if __name__ == "__main__":
    unittest.main()

