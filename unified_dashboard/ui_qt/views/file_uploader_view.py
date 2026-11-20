"""
ui_qt/views/file_uploader_view.py
=================================
File Uploader view wrapper around the unified FilesView.
Maintains 100% backward compatibility with all test suites and existing caller code.
"""

from ui_qt.views.files_view import FilesView


class FileUploaderView(FilesView):
    """File Uploader UI view in PySide6 (preset to 'upload' mode)."""

    def __init__(self, parent=None):
        super().__init__(parent=parent, default_mode="upload")
