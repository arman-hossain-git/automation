"""
ui_qt/views/file_exporter_view.py
=================================
File Exporter view wrapper around the unified FilesView.
Maintains 100% backward compatibility with all test suites and existing caller code.
"""

from ui_qt.views.files_view import FilesView


class FileExporterView(FilesView):
    """File Exporter UI view in PySide6 (preset to 'export' mode)."""

    def __init__(self, parent=None):
        super().__init__(parent=parent, default_mode="export")
