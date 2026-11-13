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


if __name__ == "__main__":
    unittest.main()

