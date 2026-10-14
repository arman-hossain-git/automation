"""
modules/pdf_extractor.py
=========================
Config-driven BATCH PDF -> Excel extractor.

Takes a folder of same-type PDF documents plus a JSON extraction config
(record separator, exclude patterns, name/address detection, repeating
sections, summary fields, tabular data-row pattern) and produces a single
consolidated .xlsx workbook.

The matching engine (record splitting, field/section/table extraction) is
ported from the standalone pdf2csv_generic.py prototype. Unlike that script,
the config here is never hard-coded -- it is authored/edited live in the UI
(ui/pdf_extractor_ui.py) and can be saved/loaded as named JSON presets, so
the same tool can be reused across different document types.
"""

import os
import re
import csv
import json
import logging
import datetime
from pathlib import Path

import pdfplumber
import pandas as pd

from core.utils import sanitize_filename, describe_exception

# ---------------------------------------------------------------------------
# Config presets storage
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS_DIR = os.path.join(PROJECT_ROOT, "pdf_extractor_configs")

# A minimal starter template shown in the UI editor by default. Every key is
# optional except "record_separator" (use "" to treat each whole PDF as one
# record) -- all extraction stages below are skipped gracefully if their
# config block is missing/empty.
DEFAULT_CONFIG = {
    "record_separator": "",
    "exclude_patterns": [],
    "name_detection": {
        "output_column": "record_name",
        "pattern": r"^(.+)$",
        "stop_at": ""
    },
    "address_block": None,
    "repeating_sections": [],
    "summary_fields": [],
    "data_rows": None
}

# Bundled example preset (the user's original Bayamon Community College
# transcript layout) seeded into CONFIGS_DIR the first time it's created, so
# there's a working real-world example to load/inspect/clone.
_BUILTIN_PRESETS = {
    "Example - Bayamon Transcript": {
        "record_separator": "Bayamon Community College",
        "exclude_patterns": [
            r"17\s+Calle\s+Maceo",
            r"Bayam[o\u00f3\u00c3\u00b3]+n,?\s*PR\s+\d+",
            r"\(787\)\s*780-4242",
            r"https?://bccpr\.org",
            r"bccpr\.org",
            r"Course Offering Grade Book Transcript",
            r"Student Transcript",
            r"Official Signature:",
            r"Thursday,.*Page \d+",
            r"^\s*Absent\s+Tardy\s+Left Early",
            r"^\s*Hours\s+Hours\s+Hours",
            r"^\s*Term\s+Course\s+Attended",
            r"\(Cumulative GPA\)",
        ],
        "name_detection": {
            "output_column": "student_name",
            "pattern": r"^([A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u00ff\s]+,\s*[A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u00ff\s]+)$",
            "stop_at": "Program Description:"
        },
        "address_block": {
            "output_column": "address",
            "stop_prefixes": [
                "Program Description:", "First Term:", "Status:",
                "FT/PT:", "Exp Grad Date:", "Grad Date:",
                "Drop Date:", "Group:", "Session:",
            ]
        },
        "repeating_sections": [
            {
                "name": "program",
                "marker": "Program Description:",
                "marker_line_fields": [
                    {"name": "drop_date", "pattern": r"Drop Date:\s*(\d{2}/\d{2}/\d{4})"},
                    {"name": "group", "pattern": r"Group:\s*(\S+)"},
                ],
                "next_line_value": {"name": "program"},
                "sub_fields": [
                    {"name": "first_term", "pattern": r"First Term:\s*(\d{2}/\d{2}/\d{4})"},
                    {"name": "exp_grad_date", "pattern": r"Exp Grad Date:\s*(\d{2}/\d{2}/\d{4})"},
                    {"name": "ft_pt", "pattern": r"FT/PT:\s*(.+?)$"},
                    {"name": "status", "pattern": r"Status:\s*(.+?)(?:\s+Grad Date:|\s+Session:|$)"},
                    {"name": "grad_date", "pattern": r"Grad Date:\s*(\d{2}/\d{2}/\d{4})"},
                    {"name": "session", "pattern": r"Session:\s*(.+?)$"},
                ],
            }
        ],
        "summary_fields": [
            {
                "name": "cumulative_gpa",
                "pattern": r"Transferred:.*?(\d+\.\d+)\s*$",
                "flags": "MULTILINE",
            }
        ],
        "data_rows": {
            "pattern": (
                r"^(\d{2}/\d{2}/\d{4})\s+"
                r"([A-Z]+\d+)\s+"
                r"\(O\s*-\s*(\d+)\)\s+"
                r"(\d+\.?\d*)\s+"
                r"(\d+\.?\d*)\s+"
                r"(\d+\.?\d*)\s+"
                r"(\d+)\s+"
                r"(\d+\.?\d*)\s+"
                r"(\d+)\s+"
                r"(\d+\.?\d*)\s+"
                r"(\d+)\s+"
                r"(\d+\.?\d*)\s+"
                r"(\d+\.?\d*)\s+%\s+"
                r"(\d+\.?\d*)\s+"
                r"([A-FW])$"
            ),
            "columns": [
                "term", "course", "course_version",
                "hours_attended", "hours_missed", "hours_scheduled",
                "absent_count", "absent_hours_missed",
                "tardy_count", "tardy_hours_missed",
                "left_early_count", "left_early_hours_missed",
                "attendance_percentage", "numeric_grade", "final_grade",
            ],
            "transforms": {
                "course_version": "lambda v: f'O-{v}'",
            },
        },
    }
}


def ensure_configs_dir() -> str:
    """Create the presets folder (and seed bundled examples) if missing."""
    os.makedirs(CONFIGS_DIR, exist_ok=True)
    for name, cfg in _BUILTIN_PRESETS.items():
        path = os.path.join(CONFIGS_DIR, f"{sanitize_filename(name)}.json")
        if not os.path.isfile(path):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
            except OSError:
                pass
    return CONFIGS_DIR


def list_presets() -> list[str]:
    ensure_configs_dir()
    return sorted(
        os.path.splitext(f)[0] for f in os.listdir(CONFIGS_DIR) if f.endswith(".json")
    )


def load_preset(name: str) -> dict:
    path = os.path.join(CONFIGS_DIR, f"{sanitize_filename(name)}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_preset(name: str, config: dict) -> str:
    ensure_configs_dir()
    path = os.path.join(CONFIGS_DIR, f"{sanitize_filename(name)}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    return path


def delete_preset(name: str) -> bool:
    path = os.path.join(CONFIGS_DIR, f"{sanitize_filename(name)}.json")
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


# ---------------------------------------------------------------------------
# Logging helpers  (written by BatchPDFToExcelExporter)
# ---------------------------------------------------------------------------

def setup_run_logger(output_dir: str) -> logging.Logger:
    """Create / refresh a file logger that writes to pdf_extractor_debug.log
    inside *output_dir*.  Clears any existing handlers so multiple runs in
    the same session don't pile up duplicate entries."""
    log_path = os.path.join(output_dir, "pdf_extractor_debug.log")
    logger = logging.getLogger("pdf_extractor_run")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-7s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(fh)
    logger.info("=" * 70)
    logger.info("New PDF → Excel extraction run started.")
    return logger


def write_execution_log_csv(output_dir: str, rows: list[dict]) -> str:
    """Append *rows* to pdf_extractor_execution_log.csv inside *output_dir*.

    Each dict in *rows* must have the keys written by BatchPDFToExcelExporter:
      source_file, status, records_found, rows_written, error_message, timestamp
    """
    log_path = os.path.join(output_dir, "pdf_extractor_execution_log.csv")
    file_exists = os.path.isfile(log_path)
    fieldnames = [
        "timestamp", "source_file", "status",
        "records_found", "rows_written", "error_message",
    ]
    try:
        with open(log_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerows(rows)
    except OSError as e:
        pass  # Non-fatal; don't crash the extraction over a log write failure
    return log_path


# ---------------------------------------------------------------------------
# Extraction engine (single-PDF matching logic)
# ---------------------------------------------------------------------------

class ConfigurableExtractor:
    """Applies a JSON extraction config to a single PDF's text."""

    def __init__(self, config: dict):
        self.config = config or {}
        self.exclude_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.config.get("exclude_patterns", []) or []
        ]

    def _clean_line(self, line):
        cleaned = line
        for pat in self.exclude_patterns:
            cleaned = pat.sub('', cleaned)
        return cleaned.strip()

    def _line_is_pure_exclude(self, line):
        return self._clean_line(line) == ''

    # ── PDF Reading & Record Splitting ──

    def read_pdf(self, pdf_path):
        page_texts = []
        page_offsets = []
        current_offset = 0

        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ''
                page_offsets.append((current_offset, page_num))
                page_texts.append(text)
                current_offset += len(text) + 1

        return '\n'.join(page_texts), page_offsets

    def read_pdf_lines(self, pdf_path: str) -> list[str]:
        """Return the raw extracted lines (no exclude filtering) for the
        Visual Config Verification panel."""
        full_text, _ = self.read_pdf(pdf_path)
        return full_text.splitlines()

    def split_records(self, full_text, page_offsets):
        """Split text into (block_text, page_number) records.

        If record_separator is blank, the whole PDF is treated as one record.
        """
        separator = (self.config.get("record_separator") or "").strip()
        if not separator:
            text = full_text.strip()
            if not text:
                return []
            first_page = page_offsets[0][1] if page_offsets else 1
            return [(text, first_page)]

        results = []
        search_start = 0
        positions = []
        while True:
            idx = full_text.find(separator, search_start)
            if idx == -1:
                break
            positions.append(idx)
            search_start = idx + len(separator)

        for k, pos in enumerate(positions):
            block_start = pos + len(separator)
            block_end = positions[k + 1] if k + 1 < len(positions) else len(full_text)
            block_text = full_text[block_start:block_end].strip()
            if not block_text:
                continue

            page_num = 1
            for offset, pnum in page_offsets:
                if pos >= offset:
                    page_num = pnum
                else:
                    break

            results.append((block_text, page_num))

        return results

    # ── Field Extraction ──

    def extract_name(self, block):
        cfg = self.config.get("name_detection")
        if not cfg or not cfg.get("pattern"):
            return 'N/A', -1

        lines = block.split('\n')
        stop_at = cfg.get("stop_at", "")
        pattern = re.compile(cfg["pattern"])

        for i, line in enumerate(lines):
            clean = line.strip()
            if not clean:
                continue
            if stop_at and stop_at in clean:
                break
            cleaned = self._clean_line(clean)
            if not cleaned:
                continue
            match = pattern.match(cleaned)
            if match:
                return match.group(1).strip(), i

        return 'N/A', -1

    def extract_address(self, block, name_index):
        cfg = self.config.get("address_block")
        if not cfg or name_index == -1:
            return 'N/A'

        lines = block.split('\n')
        stop_prefixes = cfg.get("stop_prefixes", [])
        address_lines = []

        for j in range(name_index + 1, len(lines)):
            line = lines[j].strip()
            if not line:
                continue
            if any(line.startswith(p) for p in stop_prefixes):
                break
            if self._line_is_pure_exclude(line):
                continue

            cleaned = self._clean_line(line)
            if cleaned:
                address_lines.append(cleaned)

        return '\n'.join(address_lines) if address_lines else 'N/A'

    def extract_repeating_sections(self, block):
        all_sections = {}

        for sec_cfg in self.config.get("repeating_sections", []) or []:
            section_name = sec_cfg["name"]
            marker = sec_cfg["marker"]
            sections = []
            lines = block.split('\n')

            i = 0
            while i < len(lines):
                line = lines[i].strip()

                if line.startswith(marker):
                    section = {}
                    remainder = line[len(marker):].strip()

                    for field_cfg in sec_cfg.get("marker_line_fields", []):
                        m = re.search(field_cfg["pattern"], remainder)
                        section[field_cfg["name"]] = m.group(1).strip() if m else 'N/A'

                    nlv_cfg = sec_cfg.get("next_line_value")
                    if nlv_cfg:
                        section[nlv_cfg["name"]] = 'N/A'

                    j = i + 1
                    while j < len(lines) and j <= i + 6:
                        next_line = lines[j].strip()
                        if not next_line:
                            j += 1
                            continue

                        cleaned = self._clean_line(next_line)
                        if not cleaned:
                            j += 1
                            continue

                        matched_sub = False
                        for sf in sec_cfg.get("sub_fields", []):
                            m = re.search(sf["pattern"], next_line)
                            if m:
                                val = m.group(1).strip()
                                if val and val not in ('Status', 'Session'):
                                    section[sf["name"]] = val
                                matched_sub = True

                        if matched_sub:
                            j += 1
                            continue

                        if (cleaned.startswith(marker) or
                                re.match(r'\d{2}/\d{2}/\d{4}', cleaned)):
                            break

                        if nlv_cfg and section.get(nlv_cfg["name"], 'N/A') == 'N/A':
                            section[nlv_cfg["name"]] = cleaned
                            j += 1
                            continue

                        j += 1

                    for sf in sec_cfg.get("sub_fields", []):
                        if sf["name"] not in section:
                            section[sf["name"]] = 'N/A'

                    sections.append(section)

                i += 1

            all_sections[section_name] = sections

        return all_sections

    def extract_summary_fields(self, block):
        results = {}
        for sf in self.config.get("summary_fields", []) or []:
            flags = 0
            if sf.get("flags") == "MULTILINE":
                flags = re.MULTILINE
            m = re.search(sf["pattern"], block, flags)
            results[sf["name"]] = m.group(1).strip() if m else 'N/A'
        return results

    def _extract_student_ledger_data_rows(self, block):
        lines = block.split('\n')
        ENTRY_START = re.compile(r'^(\d{2}/\d{2}/\d{4})\s+([A-Z][A-Z0-9\-]*)\s+')
        AMOUNT_END = re.compile(r'(-?\$[\d,]+)\s*$')
        PROG_RE = re.compile(r'(Certified\s+\S+\s+Welding)')
        PP_RE = re.compile(r'^(\d+\s*-\s*\d+\s+hours)\s*')
        HEADER_FOOTER_RES = [re.compile(p, re.IGNORECASE) for p in [
            r'KENTUCKY WELDING INSTITUTE',
            r'^\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}',
            r'^1828 Maysville',
            r'^Phone:',
            r'^https?://',
            r'^Date\s+Code\s+Description',
            r'^Student Ledger\s*-',
            r'^Current Balance',
            r'^Generated:',
            r'^\*\s*\*\s*pending',
            r'Page \d+ of \d+',
        ]]

        entries = []
        curr = []
        for line in lines:
            if not line.strip() or any(p.search(line) for p in HEADER_FOOTER_RES):
                continue
            if ENTRY_START.match(line) and AMOUNT_END.search(line):
                if curr:
                    entries.append(curr)
                curr = [line]
            elif curr:
                curr.append(line)
        if curr:
            entries.append(curr)

        parsed_rows = []
        for entry in entries:
            p_line = entry[0]
            c_lines = entry[1:]

            sm = ENTRY_START.match(p_line)
            am = AMOUNT_END.search(p_line)
            if not sm or not am:
                continue

            date = sm.group(1)
            code = sm.group(2)
            amount = am.group(1)
            rest = p_line[sm.end():am.start()].strip()

            pm = PROG_RE.search(rest)
            if pm:
                desc = rest[:pm.start()].strip()
                prog_base = pm.group(1)
                after_p = rest[pm.end():].strip()
                ppm = PP_RE.match(after_p)
                if ppm:
                    pp = ppm.group(1).strip()
                    n_start = after_p[ppm.end():].strip()
                else:
                    parts = after_p.split(None, 1)
                    pp = 'N/A'
                    n_start = parts[1].strip() if len(parts) > 1 else ''
            else:
                desc = rest
                prog_base = 'N/A'
                pp = 'N/A'
                n_start = ''

            n_parts = [n_start] if n_start else []
            prog = prog_base
            for cl in c_lines:
                cl_str = cl.strip()
                if cl_str.startswith('(CPW)'):
                    prog = (prog_base + ' (CPW)') if prog_base != 'N/A' else '(CPW)'
                    rem = cl_str[5:].strip()
                    if rem:
                        n_parts.append(rem)
                else:
                    n_parts.append(cl_str)

            notes = re.sub(r'-\s+', '', ' '.join(n_parts)).strip() or 'N/A'
            parsed_rows.append({
                'date': date,
                'code': code,
                'description': desc,
                'program': prog,
                'payment_period': pp,
                'notes': notes,
                'amount': amount,
            })

        return parsed_rows

    def extract_data_rows(self, block):
        cfg = self.config.get("data_rows")
        if not cfg:
            return []

        if cfg.get("mode") == "student_ledger":
            return self._extract_student_ledger_data_rows(block)

        if not cfg.get("pattern"):
            return []

        pattern = re.compile(cfg["pattern"])
        columns = cfg["columns"]
        transforms = cfg.get("transforms", {})
        rows = []

        for line in block.split('\n'):
            m = pattern.match(line.strip())
            if not m:
                continue

            row = {}
            for idx, col_name in enumerate(columns):
                val = m.group(idx + 1)
                if col_name in transforms:
                    transform_fn = eval(transforms[col_name])  # noqa: S307 - trusted local config, mirrors original prototype
                    val = transform_fn(val)
                row[col_name] = val

            rows.append(row)

        return rows

    def extract_record(self, block):
        """Run every configured extraction stage against one record block."""
        name, name_idx = self.extract_name(block)
        address = self.extract_address(block, name_idx)
        rep_sections = self.extract_repeating_sections(block)
        summary = self.extract_summary_fields(block)
        data_rows = self.extract_data_rows(block)
        return name, address, rep_sections, summary, data_rows

    # ── Visual Config Verification helpers ──

    def preview_config_on_block(self, block: str) -> dict:
        """Run all extraction stages on a single block and return a structured
        summary suitable for display in the Visual Config Verification panel."""
        name, name_idx = self.extract_name(block)
        address = self.extract_address(block, name_idx)
        rep_sections = self.extract_repeating_sections(block)
        summary = self.extract_summary_fields(block)
        data_rows = self.extract_data_rows(block)

        return {
            "name": name,
            "name_line_index": name_idx,
            "address": address,
            "repeating_sections": rep_sections,
            "summary_fields": summary,
            "data_rows_count": len(data_rows),
            "data_rows_sample": data_rows[:3],
        }


# ---------------------------------------------------------------------------
# Batch folder -> single Excel workbook
# ---------------------------------------------------------------------------

class BatchPDFToExcelExporter:
    """Runs a ConfigurableExtractor over every PDF in a folder and writes one
    consolidated .xlsx workbook.

    Now also writes:
      • pdf_extractor_debug.log    – human-readable debug log (appended)
      • pdf_extractor_execution_log.csv – per-file status rows (appended)

    Both files land next to the output .xlsx.
    """

    def run(self, source_folder, output_xlsx, config, on_progress=None, abort_flag=None, log=None):
        engine = ConfigurableExtractor(config)

        # ── Set up file-based logging alongside the output workbook ──
        output_dir = os.path.dirname(os.path.abspath(output_xlsx)) or "."
        file_logger = setup_run_logger(output_dir)

        def _log(level: str, msg: str):
            """Emit to both the UI log callback and the file logger."""
            if log:
                log(level, msg)
            getattr(file_logger, level, file_logger.info)(msg)

        _log("info", f"Source folder : {source_folder}")
        _log("info", f"Output file   : {output_xlsx}")
        _log("info", f"Config keys   : {list(config.keys())}")

        pdf_paths = sorted(
            {p for p in Path(source_folder).glob("*.pdf")} |
            {p for p in Path(source_folder).glob("*.PDF")}
        )
        total = len(pdf_paths)

        if on_progress:
            on_progress({'type': 'total', 'total': total})

        if total == 0:
            msg = f"No PDF files found in: {source_folder}"
            _log("error", msg)
            raise RuntimeError(msg)

        _log("info", f"Found {total} PDF file(s) to process.")

        name_col = (config.get("name_detection") or {}).get("output_column", "record_name")
        addr_col = (config.get("address_block") or {}).get("output_column", "address")
        data_cols = (config.get("data_rows") or {}).get("columns", [])

        all_rows = []
        exec_log_rows: list[dict] = []      # ← execution log accumulator
        files_processed = 0
        records_found = 0
        run_ts = datetime.datetime.now().isoformat(timespec="seconds")

        for idx, pdf_path in enumerate(pdf_paths):
            if abort_flag and abort_flag():
                _log("warning", "Extraction aborted by user.")
                break

            if on_progress:
                on_progress({'type': 'item_start', 'idx': idx, 'student_id': pdf_path.name})

            file_ts = datetime.datetime.now().isoformat(timespec="seconds")
            file_records = 0
            file_rows_before = len(all_rows)
            file_error = ""

            try:
                _log("info", f"[{idx+1}/{total}] Processing: {pdf_path.name}")
                full_text, page_offsets = engine.read_pdf(str(pdf_path))
                records = engine.split_records(full_text, page_offsets)

                if not records:
                    _log("warning", f"{pdf_path.name}: no records matched (check record_separator).")
                    file_error = "no records matched"
                else:
                    for block, page_number in records:
                        name, address, rep_sections, summary, data_rows = engine.extract_record(block)

                        section_counts = {f"{sec}_count": len(secs) for sec, secs in rep_sections.items()}

                        row_data_list = data_rows if data_rows else (
                            [{col: 'N/A' for col in data_cols}] if data_cols else [{}]
                        )

                        for dr in row_data_list:
                            row = {
                                name_col: name,
                                addr_col: address,
                                'source_file': pdf_path.name,
                                'page_number': page_number,
                            }
                            row.update(section_counts)
                            row.update(summary)
                            row.update(dr)

                            for sec_name, sections_list in rep_sections.items():
                                for seq, sec_data in enumerate(sections_list, 1):
                                    for field_name, field_val in sec_data.items():
                                        row[f"{field_name}_{seq}"] = field_val

                            all_rows.append(row)
                            records_found += 1

                    file_records = len(records)
                    rows_added = len(all_rows) - file_rows_before
                    _log("info", f"{pdf_path.name}: {file_records} record(s), {rows_added} row(s).")

                files_processed += 1

                exec_log_rows.append({
                    "timestamp": file_ts,
                    "source_file": pdf_path.name,
                    "status": "success" if not file_error else "no_records",
                    "records_found": file_records,
                    "rows_written": len(all_rows) - file_rows_before,
                    "error_message": file_error,
                })

            except Exception as e:
                err_desc = describe_exception(e)
                _log("error", f"Failed on {pdf_path.name}: {err_desc}")
                exec_log_rows.append({
                    "timestamp": file_ts,
                    "source_file": pdf_path.name,
                    "status": "error",
                    "records_found": 0,
                    "rows_written": 0,
                    "error_message": err_desc,
                })

            if on_progress:
                on_progress({'type': 'item_done', 'completed': idx + 1})

        # ── Write execution log CSV ──
        if exec_log_rows:
            csv_path = write_execution_log_csv(output_dir, exec_log_rows)
            _log("info", f"Execution log written: {csv_path}")

        if not all_rows:
            msg = (
                "No data extracted from any PDF in the selected folder. "
                "Check the config's record_separator and field patterns against the source PDFs."
            )
            _log("error", msg)
            raise RuntimeError(msg)

        df = pd.DataFrame(all_rows)

        final_cols = [name_col, addr_col, 'source_file', 'page_number']
        for sc in df.columns:
            if sc.endswith('_count') and sc not in final_cols:
                final_cols.append(sc)
        for sf in config.get("summary_fields", []) or []:
            if sf["name"] not in final_cols:
                final_cols.append(sf["name"])
        for dc in data_cols:
            if dc not in final_cols:
                final_cols.append(dc)

        rep_cols = []
        for sec_cfg in config.get("repeating_sections", []) or []:
            sec_name = sec_cfg["name"]
            count_col = f"{sec_name}_count"
            max_count = int(df[count_col].max()) if count_col in df.columns else 0

            field_names = []
            if sec_cfg.get("next_line_value"):
                field_names.append(sec_cfg["next_line_value"]["name"])
            for mlf in sec_cfg.get("marker_line_fields", []):
                field_names.append(mlf["name"])
            for sf in sec_cfg.get("sub_fields", []):
                field_names.append(sf["name"])

            for seq in range(1, max_count + 1):
                for fn in field_names:
                    col = f"{fn}_{seq}"
                    if col in df.columns and col not in rep_cols:
                        rep_cols.append(col)

        ordered = [c for c in final_cols + rep_cols if c in df.columns]
        for c in df.columns:
            if c not in ordered:
                ordered.append(c)

        df = df[ordered]

        out_dir = os.path.dirname(output_xlsx)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        df.to_excel(output_xlsx, index=False, engine="openpyxl")

        result = {
            'files_processed': files_processed,
            'total_files': total,
            'records': records_found,
            'rows': len(all_rows),
            'output': output_xlsx,
        }
        _log("info", f"Extraction complete: {result}")
        return result

    def verify(self, output_xlsx, source_folder=None):
        """Compare rows written to an existing output workbook against the
        PDFs currently sitting in source_folder (if provided)."""
        df = pd.read_excel(output_xlsx, engine="openpyxl")
        unique_sources = sorted(set(df['source_file'])) if 'source_file' in df.columns else []

        pdf_count = 0
        missing_files = []
        if source_folder and os.path.isdir(source_folder):
            pdf_names = sorted(
                p.name for p in list(Path(source_folder).glob("*.pdf")) + list(Path(source_folder).glob("*.PDF"))
            )
            pdf_count = len(pdf_names)
            missing_files = [n for n in pdf_names if n not in unique_sources]

        return {
            'rows': len(df),
            'unique_sources': len(unique_sources),
            'pdf_count': pdf_count,
            'missing_files': missing_files,
        }


# ---------------------------------------------------------------------------
# Word/selection-based config-building helpers
# ---------------------------------------------------------------------------
# These power the merged "Setup, Config & Verify" UI, which lets a user
# drag-select single or multiple words inside a real sample PDF and tell the
# system "this text is a field label" / "this text is that field's value" /
# "this line should be excluded" / etc, instead of hand-editing JSON or
# picking whole lines. The UI always keeps the JSON config as the single
# source of truth; these helpers just translate a text selection into the
# right regex fragment, and translate the config back into a flat,
# human-readable "field map" for review/removal.

def infer_value_charclass(text: str) -> str:
    """Given a piece of user-selected text, return a regex fragment (WITHOUT
    the capturing parens) that generalizes its 'shape' so the generated
    pattern still matches the same field on other records where the value
    differs (a different date, a different name, a different number...)."""
    t = (text or '').strip()
    if not t:
        return r"\S+"
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", t):
        return r"\d{1,2}/\d{1,2}/\d{2,4}"
    if re.fullmatch(r"\d+\.\d+\s*%", t):
        return r"\d+\.?\d*\s*%"
    if re.fullmatch(r"\d+\.\d+", t):
        return r"\d+\.\d+"
    if re.fullmatch(r"\d+", t):
        return r"\d+"
    if re.fullmatch(r"[A-FW]", t):
        return r"[A-FW]"
    if re.fullmatch(r"[A-Za-z]", t):
        return r"[A-Za-z]"
    if re.fullmatch(r"[A-Za-z][A-Za-z\-'\u00c0-\u00ff]*(?:\s+[A-Za-z][A-Za-z\-'\u00c0-\u00ff]*)*", t):
        return r"[A-Za-z\u00c0-\u00ff\-'\s]+"
    if re.fullmatch(r"[A-Za-z0-9]+", t):
        return r"[A-Za-z0-9]+"
    return r"\S+"


def build_anchored_pattern(line: str, value_text: str, label_text: str = "") -> str:
    """Build a one-capture-group regex that locates *value_text* within
    *line*, anchored on either an explicit *label_text* (if it precedes the
    value on the same line) or on the literal text immediately before the
    value. Returned pattern is meant for re.search()/re.match() against a
    single cleaned line (name_detection) or the whole record block
    (summary_fields, which should be combined with flags='MULTILINE' if the
    label appears on its own line)."""
    value_text = (value_text or '').strip()
    if not value_text:
        return r"(.+)"

    idx = line.find(value_text)
    if idx == -1:
        return f"({infer_value_charclass(value_text)})"

    label_text = (label_text or '').strip()
    if label_text:
        lidx = line.find(label_text)
        if lidx != -1 and lidx < idx:
            gap = line[lidx + len(label_text):idx]
            prefix = re.escape(label_text) + re.escape(gap)
            return prefix + f"({infer_value_charclass(value_text)})"

    prefix_text = line[max(0, idx - 40):idx]
    prefix = re.escape(prefix_text)
    return prefix + f"({infer_value_charclass(value_text)})"


def build_row_pattern(line: str, columns: list[dict]) -> tuple[str, list[str]]:
    """Build a full-line anchored regex (plus ordered column names) for a
    repeating 'data row' style line, given *columns*: a list of
    {"name": ..., "start": ..., "end": ...} character-offset spans as
    selected by the user (offsets are relative to the raw PDF line, not the
    display text). Un-selected text between/around the spans becomes literal
    anchor text (escaped), with whitespace runs collapsed to '\\s+' so minor
    column-padding differences between rows don't break the match."""
    cols = sorted(columns, key=lambda c: c["start"])
    pattern_parts = ["^"]
    col_names = []
    cursor = 0

    def _literal(segment: str) -> str:
        pieces = re.split(r"(\s+)", segment)
        out = []
        for piece in pieces:
            if piece == "":
                continue
            out.append(r"\s+" if piece.isspace() else re.escape(piece))
        return "".join(out)

    for col in cols:
        start, end = col["start"], col["end"]
        if start > cursor:
            pattern_parts.append(_literal(line[cursor:start]))
        value_text = line[start:end]
        pattern_parts.append(f"({infer_value_charclass(value_text)})")
        col_names.append(col["name"])
        cursor = end

    if cursor < len(line):
        pattern_parts.append(_literal(line[cursor:]))
    pattern_parts.append("$")

    return "".join(pattern_parts), col_names


# ---------------------------------------------------------------------------
# Field-map introspection
# ---------------------------------------------------------------------------
# Flattens the JSON config into a simple table so the UI can show the
# system's current findings ("what does it think is a field, what does it
# think is a value, what will it exclude") without the user reading raw
# JSON, and lets individual entries be removed by (scope, index).

def describe_field_map(config: dict) -> list[dict]:
    """Return a list of {scope, index, label, pattern} rows describing every
    field/pattern currently configured."""
    rows = []
    config = config or {}

    sep = (config.get("record_separator") or "").strip()
    if sep:
        rows.append({"scope": "record_separator", "index": None,
                      "label": "Record Separator", "pattern": sep})

    nd = config.get("name_detection") or {}
    if nd.get("pattern"):
        rows.append({"scope": "name_detection", "index": None,
                      "label": f"Name \u2192 {nd.get('output_column', 'record_name')}",
                      "pattern": nd["pattern"]})

    ab = config.get("address_block")
    if isinstance(ab, dict) and ab.get("stop_prefixes"):
        preview = " | ".join(ab["stop_prefixes"][:4])
        if len(ab["stop_prefixes"]) > 4:
            preview += " ..."
        rows.append({"scope": "address_block", "index": None,
                      "label": f"Address \u2192 {ab.get('output_column', 'address')}",
                      "pattern": preview})

    for i, p in enumerate(config.get("exclude_patterns") or []):
        rows.append({"scope": "exclude_patterns", "index": i,
                      "label": f"Exclude line #{i + 1}", "pattern": p})

    for i, sf in enumerate(config.get("summary_fields") or []):
        rows.append({"scope": "summary_fields", "index": i,
                      "label": f"Summary \u2192 {sf.get('name')}", "pattern": sf.get("pattern", "")})

    dr = config.get("data_rows")
    if isinstance(dr, dict) and dr.get("pattern"):
        cols = ", ".join(dr.get("columns") or [])
        rows.append({"scope": "data_rows", "index": None,
                      "label": f"Data Row ({cols})", "pattern": dr["pattern"]})

    for i, sec in enumerate(config.get("repeating_sections") or []):
        rows.append({"scope": "repeating_sections", "index": i,
                      "label": f"Repeating Block \u2192 {sec.get('name')}",
                      "pattern": f"marker={sec.get('marker', '')!r}"})
        for j, mf in enumerate(sec.get("marker_line_fields") or []):
            rows.append({"scope": "repeating_marker_field", "index": (i, j),
                          "label": f"  \u21B3 marker-line field \u2192 {mf.get('name')}",
                          "pattern": mf.get("pattern", "")})
        nlv = sec.get("next_line_value")
        if isinstance(nlv, dict) and nlv.get("name"):
            rows.append({"scope": "repeating_next_line", "index": i,
                          "label": f"  \u21B3 next-line value \u2192 {nlv.get('name')}",
                          "pattern": "(first unlabeled line after marker)"})
        for j, sf in enumerate(sec.get("sub_fields") or []):
            rows.append({"scope": "repeating_subfield", "index": (i, j),
                          "label": f"  \u21B3 sub-field \u2192 {sf.get('name')}",
                          "pattern": sf.get("pattern", "")})

    return rows


def remove_field_entry(config: dict, scope: str, index=None) -> dict:
    """Remove the field-map entry identified by (scope, index) from *config*
    in place, and return it."""
    if scope == "record_separator":
        config["record_separator"] = ""
    elif scope == "name_detection":
        config["name_detection"] = {"output_column": "record_name", "pattern": "", "stop_at": ""}
    elif scope == "address_block":
        config["address_block"] = None
    elif scope == "data_rows":
        config["data_rows"] = None
    elif scope == "exclude_patterns" and index is not None:
        lst = config.get("exclude_patterns") or []
        if 0 <= index < len(lst):
            del lst[index]
        config["exclude_patterns"] = lst
    elif scope == "summary_fields" and index is not None:
        lst = config.get("summary_fields") or []
        if 0 <= index < len(lst):
            del lst[index]
        config["summary_fields"] = lst
    elif scope == "repeating_sections" and index is not None:
        lst = config.get("repeating_sections") or []
        if 0 <= index < len(lst):
            del lst[index]
        config["repeating_sections"] = lst
    elif scope == "repeating_marker_field" and isinstance(index, tuple):
        i, j = index
        secs = config.get("repeating_sections") or []
        if 0 <= i < len(secs):
            fields = secs[i].get("marker_line_fields") or []
            if 0 <= j < len(fields):
                del fields[j]
            secs[i]["marker_line_fields"] = fields
        config["repeating_sections"] = secs
    elif scope == "repeating_next_line" and index is not None:
        secs = config.get("repeating_sections") or []
        if 0 <= index < len(secs):
            secs[index]["next_line_value"] = None
        config["repeating_sections"] = secs
    elif scope == "repeating_subfield" and isinstance(index, tuple):
        i, j = index
        secs = config.get("repeating_sections") or []
        if 0 <= i < len(secs):
            fields = secs[i].get("sub_fields") or []
            if 0 <= j < len(fields):
                del fields[j]
            secs[i]["sub_fields"] = fields
        config["repeating_sections"] = secs
    return config


# ---------------------------------------------------------------------------
# In-app log readers (Debug Log + Execution Log CSV viewers)
# ---------------------------------------------------------------------------
# The batch run already writes pdf_extractor_debug.log and
# pdf_extractor_execution_log.csv next to the output workbook (see
# setup_run_logger / write_execution_log_csv above); these helpers let the
# UI surface both *inside the app* (a live tail + a parsed table) instead of
# only being reachable by opening the files externally.

def debug_log_path(output_dir: str) -> str:
    return os.path.join(output_dir or ".", "pdf_extractor_debug.log")


def execution_log_path(output_dir: str) -> str:
    return os.path.join(output_dir or ".", "pdf_extractor_execution_log.csv")


def read_debug_log_tail(output_dir: str, max_lines: int = 400) -> str:
    """Return the last *max_lines* lines of pdf_extractor_debug.log inside
    *output_dir* (or a friendly placeholder if it doesn't exist yet)."""
    path = debug_log_path(output_dir)
    if not os.path.isfile(path):
        return "(No debug log yet \u2014 run an extraction first.)"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-max_lines:])
    except OSError as e:
        return f"(Could not read debug log: {e})"


def read_execution_log_rows(output_dir: str, max_rows: int = 500) -> list[dict]:
    """Return the most recent rows of pdf_extractor_execution_log.csv inside
    *output_dir* as a list of dicts (empty list if the file doesn't exist)."""
    path = execution_log_path(output_dir)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        return rows[-max_rows:]
    except OSError:
        return []
