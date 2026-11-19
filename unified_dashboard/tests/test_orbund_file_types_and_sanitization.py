"""
tests/test_orbund_file_types_and_sanitization.py
================================================
Unit tests for Orbund allowed file types whitelist, MIME type mappings,
filename sanitization, and upload rejection handling.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.utils import (
    ORBUND_ALLOWED_FILE_TYPES,
    ORBUND_ALLOWED_EXTENSIONS,
    is_orbund_allowed_file_type,
    get_orbund_mime_type,
    extract_file_extension,
    sanitize_upload_filename,
)
from core.learning_system import learning_engine
from modules.file_uploader import FileUploader


class TestOrbundAllowedFileTypes(unittest.TestCase):
    """Verify that all 42 Orbund allowed extensions and MIME types are correctly defined."""

    EXPECTED_MAPPINGS = {
        'txt': 'text/plain',
        'png': 'image/png',
        'jpeg': 'image/jpeg',
        'doc': 'application/msword',
        'pdf': 'application/pdf',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'xls': 'application/vnd.ms-excel',
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'ppt': 'application/vnd.ms-powerpoint',
        'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'jpg': 'image/jpeg',
        'gif': 'image/gif',
        'ico': 'image/x-icon',
        'bmp': 'image/x-ms-bmp',
        'svg': 'image/svg+xml',
        'tif': 'image/tiff',
        'tiff': 'image/tiff',
        'mp3': 'audio/mpeg',
        'ogg': 'audio/ogg',
        'm4a': 'audio/mp4',
        'flv': 'video/x-flv',
        'm4v': 'video/mp4',
        'mp4': 'video/mp4',
        'mpeg': 'video/mpeg',
        'mpg': 'video/mpeg',
        '3gpp': 'video/3gpp',
        '3gp': 'video/3gpp',
        'avi': 'video/x-msvideo',
        'mov': 'video/quicktime',
        'wmv': 'video/x-ms-wmv',
        'rar': 'application/x-rar-compressed',
        'pkt': 'application/octet-stream',
        'ai': 'application/postscript',
        'psd': 'application/octet-stream',
        'indd': 'application/octet-stream',
        'sketch': 'application/octet-stream',
        'fig': 'application/octet-stream',
        'xd': 'application/octet-stream',
        'dwg': 'application/octet-stream',
        'dxf': 'application/octet-stream',
        'qbm': 'application/octet-stream',
        'qbw': 'application/octet-stream',
    }

    def test_all_expected_extensions_present(self):
        self.assertEqual(len(ORBUND_ALLOWED_EXTENSIONS), 42)
        for ext, mime in self.EXPECTED_MAPPINGS.items():
            self.assertIn(ext, ORBUND_ALLOWED_EXTENSIONS, f"Extension '{ext}' missing from allowed set")
            self.assertEqual(ORBUND_ALLOWED_FILE_TYPES[ext], mime, f"MIME type mismatch for '{ext}'")

    def test_is_orbund_allowed_file_type_positive(self):
        for ext in self.EXPECTED_MAPPINGS:
            # Test direct extension
            self.assertTrue(is_orbund_allowed_file_type(ext), f"Failed for {ext}")
            # Test with leading dot
            self.assertTrue(is_orbund_allowed_file_type(f".{ext}"), f"Failed for .{ext}")
            # Test uppercase
            self.assertTrue(is_orbund_allowed_file_type(ext.upper()), f"Failed for uppercase {ext}")
            # Test in full filename
            self.assertTrue(is_orbund_allowed_file_type(f"student_document.{ext}"), f"Failed for filename with {ext}")
            # Test in full filename uppercase
            self.assertTrue(is_orbund_allowed_file_type(f"MY_FILE.{ext.upper()}"), f"Failed for uppercase filename with {ext}")

    def test_is_orbund_allowed_file_type_negative(self):
        disallowed = [
            'exe', 'dll', 'bat', 'cmd', 'sh', 'zip', 'tar', 'gz', '7z',
            'html', 'htm', 'js', 'css', 'py', 'php', 'json', 'xml',
            'csv', 'tsv', 'bin', 'iso', 'apk', 'dmg', '', None
        ]
        for bad in disallowed:
            self.assertFalse(is_orbund_allowed_file_type(bad), f"Should not allow '{bad}'")
            if bad:
                self.assertFalse(is_orbund_allowed_file_type(f"test.{bad}"), f"Should not allow 'test.{bad}'")

    def test_get_orbund_mime_type(self):
        self.assertEqual(get_orbund_mime_type('sample.pdf'), 'application/pdf')
        self.assertEqual(get_orbund_mime_type('IMAGE.PNG'), 'image/png')
        self.assertEqual(get_orbund_mime_type('.mp4'), 'video/mp4')
        self.assertEqual(get_orbund_mime_type('rar'), 'application/x-rar-compressed')
        self.assertIsNone(get_orbund_mime_type('malware.exe'))
        self.assertIsNone(get_orbund_mime_type('data.csv'))

    def test_extract_file_extension(self):
        self.assertEqual(extract_file_extension('doc.pdf'), 'pdf')
        self.assertEqual(extract_file_extension('DOC.PDF'), 'pdf')
        self.assertEqual(extract_file_extension('.pdf'), 'pdf')
        self.assertEqual(extract_file_extension('pdf'), 'pdf')
        self.assertEqual(extract_file_extension('archive.tar.gz'), 'gz')
        self.assertEqual(extract_file_extension('no_ext'), 'no_ext')
        self.assertEqual(extract_file_extension(''), '')


class TestFilenameSanitization(unittest.TestCase):
    """Verify that sanitize_upload_filename cleans filenames safely for Orbund."""

    def test_multiple_dots(self):
        self.assertEqual(
            sanitize_upload_filename("student.transcript.v2.pdf"),
            "student_transcript_v2.pdf"
        )

    def test_quotes_and_apostrophes(self):
        # Single, double, backtick, curly quotes
        fn = "John's \"Official\" `Transcript` ‘2024’ “Final”.pdf"
        sanitized = sanitize_upload_filename(fn)
        for q in ("'", '"', '`', '\u2018', '\u2019', '\u201c', '\u201d'):
            self.assertNotIn(q, sanitized)
        self.assertTrue(sanitized.endswith(".pdf"))

    def test_unicode_accents_transliteration(self):
        fn = "Bayamón_informe_año_2024.pdf"
        sanitized = sanitize_upload_filename(fn)
        self.assertEqual(sanitized, "Bayamon_informe_ano_2024.pdf")

    def test_special_characters_replacement(self):
        fn = "Report & Analysis #1 (Final)+Version;Rev:1|Test?.xlsx"
        sanitized = sanitize_upload_filename(fn)
        self.assertEqual(sanitized, "Report_and_Analysis_1_Final_Version_Rev_1_Test.xlsx")

    def test_spaces_and_hyphens(self):
        fn = "Student  Transcript - Fall  2024.docx"
        sanitized = sanitize_upload_filename(fn)
        self.assertEqual(sanitized, "Student_Transcript_Fall_2024.docx")

    def test_edge_cases_and_empty_base(self):
        self.assertEqual(sanitize_upload_filename("....pdf"), "file.pdf")
        self.assertEqual(sanitize_upload_filename("---.pdf"), "file.pdf")
        self.assertEqual(sanitize_upload_filename(""), "file")
        self.assertEqual(sanitize_upload_filename("   "), "file")

    def test_uppercase_extension_lowercased(self):
        self.assertEqual(sanitize_upload_filename("Document.PDF"), "Document.pdf")
        self.assertEqual(sanitize_upload_filename("Photo.JPG"), "Photo.jpg")

    def test_length_capping(self):
        long_name = "a" * 150 + ".png"
        sanitized = sanitize_upload_filename(long_name)
        base, ext = sanitized.rsplit('.', 1)
        self.assertLessEqual(len(base), 100)
        self.assertEqual(ext, "png")


class TestLearningEngineOrbundImmunity(unittest.TestCase):
    """Ensure valid Orbund file types are NEVER recorded as rejected."""

    def test_record_rejected_file_type_ignores_valid_types(self):
        url = "https://bcc.orbundsis.com/login"
        for ext in ['pdf', 'docx', 'png', 'xlsx', 'txt', 'jpg']:
            learning_engine.record_rejected_file_type(url, ext)
            self.assertFalse(
                learning_engine.is_file_type_rejected(url, ext),
                f"Valid extension '{ext}' should never be considered rejected"
            )
            self.assertNotIn(
                ext,
                learning_engine.get_rejected_file_types(url),
                f"Valid extension '{ext}' should never appear in rejected set"
            )

    def test_record_rejected_file_type_allows_invalid_types(self):
        url = "https://bcc.orbundsis.com/login"
        learning_engine.record_rejected_file_type(url, "exe")
        self.assertIn("exe", learning_engine.get_rejected_file_types(url))


class TestFileUploaderDisallowedFileTypeSkipping(unittest.TestCase):
    """Verify that FileUploader rejects disallowed files upfront and logs them."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.source_dir = self.tmp_dir.name
        self.student_id = "STU1001"
        self.student_dir = os.path.join(self.source_dir, self.student_id)
        os.makedirs(self.student_dir, exist_ok=True)

        # Create valid and disallowed test files
        self.valid_file = os.path.join(self.student_dir, "transcript.pdf")
        with open(self.valid_file, "wb") as f:
            f.write(b"%PDF-1.4 test")

        self.disallowed_exe = os.path.join(self.student_dir, "virus.exe")
        with open(self.disallowed_exe, "wb") as f:
            f.write(b"MZ\x90\x00test")

        self.disallowed_csv = os.path.join(self.student_dir, "data.csv")
        with open(self.disallowed_csv, "w") as f:
            f.write("a,b,c\n1,2,3")

        self.disallowed_zip = os.path.join(self.student_dir, "archive.zip")
        with open(self.disallowed_zip, "wb") as f:
            f.write(b"PK\x03\x04test")

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch("modules.file_uploader.launch_chrome_driver")
    @patch("modules.file_uploader.safe_quit_driver")
    def test_disallowed_files_not_uploaded_and_logged(self, mock_quit, mock_launch_driver):
        # Add a file needing sanitization
        messy_file = os.path.join(self.student_dir, "John's Transcript & Report #1.pdf")
        with open(messy_file, "wb") as f:
            f.write(b"%PDF-1.4 test 2")

        mock_element = MagicMock()
        mock_element.is_displayed.return_value = True
        mock_element.is_enabled.return_value = True

        mock_driver = MagicMock()
        mock_driver.current_url = "https://bcc.orbundsis.com/files"
        mock_driver.find_element.return_value = mock_element
        mock_driver.find_elements.return_value = [mock_element]
        mock_driver.page_source = "<html><body><table></table></body></html>"
        mock_launch_driver.return_value = mock_driver

        uploader = FileUploader()
        logs = []
        progress_events = []

        config = {
            'login_url': 'https://bcc.orbundsis.com/login',
            'username': 'admin',
            'password': 'password',
            'login_btn_xpath': '//button',
            'upload_tool_button_xpath': '//button[@id="upload"]',
            'file_input_xpath': '//input[@type="file"]',
            'upload_submit_xpath': '//button[@id="submit"]',
            'upload_popup_ok_xpath': '//button[@id="ok"]',
            'folder_listing_table_xpath': '//table[@id="SortedDataTable"]',
        }
        students = [{'studentId': self.student_id, 'link': 'https://bcc.orbundsis.com/student/1'}]

        with patch.object(uploader, "_perform_login", return_value=True), \
             patch.object(uploader, "_open_files_and_documents", return_value="https://bcc.orbundsis.com/files"), \
             patch.object(uploader, "_go_to_target_folder", return_value=True), \
             patch("modules.file_uploader.get_portal_files_in_current_table", return_value=[]), \
             patch("modules.file_uploader.get_portal_table_snapshot", return_value={'files': [], 'by_name': {}, 'count': 0}), \
             patch("modules.file_uploader.get_portal_table_snapshot_adaptive", return_value={'files': [], 'by_name': {}, 'count': 0}), \
             patch("modules.file_uploader.file_exists_in_listing", return_value=False), \
             patch("modules.file_uploader.process_item_with_retry", return_value=(True, True, None)), \
             patch("modules.file_uploader.verify_uploaded_file_strict", return_value=(True, "verified_dom")):

            uploader.run(
                config=config,
                students=students,
                source_dir=self.source_dir,
                target_folder_name="",
                allow_duplicates=True,
                on_progress=lambda evt: progress_events.append(evt),
                log=lambda lvl, msg: logs.append((lvl, msg))
            )

        # Check log files written to source_dir
        log_csvs = [f for f in os.listdir(self.source_dir) if f.startswith("uploads_execution_log_") and f.endswith(".csv")]
        self.assertEqual(len(log_csvs), 1, "Log CSV should have been created")

        log_path = os.path.join(self.source_dir, log_csvs[0])
        import csv
        with open(log_path, mode='r', encoding='utf-8') as f:
            reader = list(csv.DictReader(f))

        # Check that disallowed files are in the CSV log marked skipped_unsupported
        logged_files = {row['fileName']: row for row in reader}

        self.assertIn("virus.exe", logged_files)
        self.assertEqual(logged_files["virus.exe"]["action"], "skipped_unsupported")
        self.assertEqual(logged_files["virus.exe"]["validation"], "skipped")
        self.assertIn("Disallowed file extension '.exe'", logged_files["virus.exe"]["actionComment"])

        self.assertIn("data.csv", logged_files)
        self.assertEqual(logged_files["data.csv"]["action"], "skipped_unsupported")
        self.assertEqual(logged_files["data.csv"]["validation"], "skipped")

        self.assertIn("archive.zip", logged_files)
        self.assertEqual(logged_files["archive.zip"]["action"], "skipped_unsupported")
        self.assertEqual(logged_files["archive.zip"]["validation"], "skipped")

        # Check that valid files were processed
        self.assertIn("transcript.pdf", logged_files)
        self.assertEqual(logged_files["transcript.pdf"]["action"], "uploaded")

        # Check that messy_file was sanitized
        self.assertIn("John's Transcript & Report #1.pdf", logged_files)
        self.assertEqual(
            logged_files["John's Transcript & Report #1.pdf"]["uploadedAs"],
            "Johns_Transcript_and_Report_1.pdf"
        )

    @patch("modules.file_uploader.launch_chrome_driver")
    @patch("modules.file_uploader.safe_quit_driver")
    def test_portal_error_on_valid_type_does_not_blacklist_extension(self, mock_quit, mock_launch_driver):
        from core.session_manager import MalformedFileError

        mock_element = MagicMock()
        mock_element.is_displayed.return_value = True
        mock_element.is_enabled.return_value = True

        mock_driver = MagicMock()
        mock_driver.current_url = "https://bcc.orbundsis.com/files"
        mock_driver.find_element.return_value = mock_element
        mock_driver.find_elements.return_value = [mock_element]
        mock_driver.page_source = "<html><body><table></table></body></html>"
        mock_launch_driver.return_value = mock_driver

        uploader = FileUploader()
        logs = []

        config = {
            'login_url': 'https://bcc.orbundsis.com/login',
            'username': 'admin',
            'password': 'password',
            'login_btn_xpath': '//button',
            'upload_tool_button_xpath': '//button[@id="upload"]',
            'file_input_xpath': '//input[@type="file"]',
            'upload_submit_xpath': '//button[@id="submit"]',
            'upload_popup_ok_xpath': '//button[@id="ok"]',
            'folder_listing_table_xpath': '//table[@id="SortedDataTable"]',
        }
        students = [{'studentId': self.student_id, 'link': 'https://bcc.orbundsis.com/student/1'}]

        with patch.object(uploader, "_perform_login", return_value=True), \
             patch.object(uploader, "_open_files_and_documents", return_value="https://bcc.orbundsis.com/files"), \
             patch.object(uploader, "_go_to_target_folder", return_value=True), \
             patch("modules.file_uploader.get_portal_files_in_current_table", return_value=[]), \
             patch("modules.file_uploader.get_portal_table_snapshot", return_value={'files': [], 'by_name': {}, 'count': 0}), \
             patch("modules.file_uploader.get_portal_table_snapshot_adaptive", return_value={'files': [], 'by_name': {}, 'count': 0}), \
             patch("modules.file_uploader.file_exists_in_listing", return_value=False), \
             patch("modules.file_uploader.process_item_with_retry", return_value=(False, False, MalformedFileError("Malformed or invalid file name"))), \
             patch("modules.file_uploader.verify_uploaded_file_strict", return_value=(False, "unverified")):

            uploader.run(
                config=config,
                students=students,
                source_dir=self.source_dir,
                target_folder_name="",
                allow_duplicates=True,
                log=lambda lvl, msg: logs.append((lvl, msg))
            )

        # Critical: PDF extension must NOT be added to rejected extensions
        with FileUploader._global_cache_lock:
            self.assertNotIn("pdf", FileUploader._global_rejected_extensions)
        self.assertFalse(learning_engine.is_file_type_rejected("https://bcc.orbundsis.com/login", "pdf"))

        # Verify log output mentioned ORBUND REJECTION
        rejection_logs = [m for lvl, m in logs if "[ORBUND REJECTION]" in m]
        self.assertTrue(len(rejection_logs) > 0, "Should have logged [ORBUND REJECTION]")


if __name__ == "__main__":
    unittest.main()
