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

from core.utils import sanitize_filename, describe_exception, new_run_id, find_latest_run_log
from core.session_manager import SessionManager

MODULE_ID = "pdf_extractor"

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
    "Student Transcript": {
        "report_type": "student_transcript",
        "record_separator": "Bayamon Community College",
        "output_columns": [
            "name",
            "address",
            "id",
            "dob",
            "phone",
            "program",
            "drop_date",
            "group",
            "status",
            "exp_grad_date",
            "ft_pt",
            "first_term",
            "lda",
            "grad_date",
            "session",
            "term",
            "course",
            "course_description",
            "grade",
            "units_attempted",
            "units_completed"
        ]
    },
    "BCC Trans": {
        "report_type": "student_transcript",
        "record_separator": "Bayamon Community College",
        "output_columns": [
            "name",
            "address",
            "id",
            "dob",
            "phone",
            "program",
            "drop_date",
            "group",
            "status",
            "exp_grad_date",
            "ft_pt",
            "first_term",
            "lda",
            "grad_date",
            "session",
            "term",
            "course",
            "course_description",
            "grade",
            "units_attempted",
            "units_completed"
        ]
    },
    "Student Schedule": {
        "report_type": "student_schedule",
        "record_separator": "Bayamon Community College",
        "output_columns": [
            "student_name",
            "program_description",
            "drop_date",
            "group",
            "first_term",
            "exp_grad_date",
            "ft_pt",
            "status",
            "grad_date",
            "session",
            "term",
            "course",
            "course_version",
            "course_description",
            "units",
            "fa_units",
            "instructor",
            "class_meetings_start",
            "class_meetings_end",
            "days_of_week",
            "room",
            "class_time_start",
            "class_time_end"
        ]
    },
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

def setup_run_logger(output_dir: str, run_id: str | None = None) -> logging.Logger:
    """Create / refresh a file logger that writes to a per-run
    pdf_extractor_debug_<run_id>.log inside *output_dir*, so each run gets
    its own distinct file instead of every run appending to the same one.
    Clears any existing handlers so multiple runs in the same session don't
    pile up duplicate entries."""
    run_id = run_id or new_run_id()
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, f"pdf_extractor_debug_{run_id}.log")
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


def write_execution_log_csv(output_dir: str, rows: list[dict], run_id: str | None = None) -> str:
    """Append *rows* to a per-run pdf_extractor_execution_log_<run_id>.csv
    inside *output_dir*, so each run's execution log is a distinct file
    instead of every run appending to the same one.

    Each dict in *rows* must have the keys written by BatchPDFToExcelExporter:
      source_file, status, records_found, rows_written, error_message, timestamp
    """
    run_id = run_id or new_run_id()
    log_path = os.path.join(output_dir, f"pdf_extractor_execution_log_{run_id}.csv")
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
        if self.config.get("report_type") == "student_schedule" or "term_sections" in self.config or "student_fields" in self.config:
            sse = StudentScheduleExtractor(self.config)
            return sse.split_records(full_text, page_offsets)

        if (
            self.config.get("report_type") in ("student_transcript", "transcript", "bayamon_transcript", "official_transcript")
            or "program_match" in self.config
        ):
            ste = StudentTranscriptExtractor(self.config)
            return ste.split_records(full_text, page_offsets)

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

    def _normalize_term_date(self, term_value):
        """Normalize a term value to a datetime.date using config rules."""
        import datetime as _dt

        value = (term_value or "").strip()
        pm_cfg = self.config.get("program_match") or {}
        formats = (pm_cfg.get("term_date_normalization") or {}).get("formats", [])

        for fmt in formats:
            pat = fmt.get("pattern")
            normalized = fmt.get("normalized")
            if not pat or normalized is None:
                continue
            try:
                m = re.match(pat, value)
            except re.error:
                continue
            if not m:
                continue

            result = normalized
            for i in range(1, len(m.groups()) + 1):
                result = result.replace("{" + str(i) + "}", m.group(i))

            # Support the common "20{2}" form used by the config.
            if re.search(r"20\{2\}", result):
                result = result.replace("20{2}", "20" + (m.group(2) if len(m.groups()) >= 2 else ""))
            try:
                return _dt.datetime.strptime(result, "%Y-%m-%d").date()
            except ValueError:
                pass

        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                return _dt.datetime.strptime(value, fmt).date()
            except ValueError:
                pass

        return None

    def _parse_date(self, value):
        import datetime as _dt
        value = (value or "").strip()
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                return _dt.datetime.strptime(value, fmt).date()
            except ValueError:
                pass
        return None

    def _get_context_row_data(self, block, base_row):
        """
        Build row-level context for configs using:
          - context_fields
          - program_match
          - repeating_sections[name='program']
        """
        context_cfgs = self.config.get("context_fields") or []
        if not context_cfgs:
            return [dict(base_row)]

        lines = block.split("\n")
        out_rows = []

        # Compile context patterns once.
        compiled = []
        for cfg in context_cfgs:
            try:
                compiled.append((cfg, re.compile(cfg["pattern"], re.MULTILINE)))
            except Exception:
                compiled.append((cfg, None))

        term_positions = []
        for i, line in enumerate(lines):
            clean = line.strip()
            for cfg, rgx in compiled:
                if cfg.get("name") == "term" and rgx:
                    m = rgx.search(clean)
                    if m:
                        term_positions.append((i, m.group(1).strip()))
                        break

        # If no term-aware context is configured, apply whole-block fields once.
        if not term_positions:
            ctx = {}
            for cfg, rgx in compiled:
                if not rgx:
                    continue
                value = None
                for line in lines:
                    m = rgx.search(line.strip())
                    if m:
                        value = m.group(1).strip()
                        break
                if value is not None:
                    ctx[cfg["name"]] = value
            row = dict(base_row)
            row.update(ctx)
            out_rows.append(row)
            return out_rows

        # Extract block-level context (for example ID and DOB) once.
        # These values are then copied to every course row.
        global_context = {}
        for cfg, rgx in compiled:
            if not rgx or cfg.get("name") == "term":
                continue
            value = None
            for line in lines:
                m = rgx.search(line.strip())
                if m:
                    value = m.group(1).strip()
                    break
            if value is not None:
                global_context[cfg["name"]] = value

        # Determine program sections from existing repeating_sections logic.
        rep_sections = self.extract_repeating_sections(block)
        program_sections = rep_sections.get("program", [])

        pm_cfg = self.config.get("program_match") or {}
        start_field = pm_cfg.get("range_start_field", "first_term")
        end_priority = pm_cfg.get("range_end_field_priority", ["grad_date", "exp_grad_date"])
        fallback = pm_cfg.get("range_end_fallback")

        def select_program(term_date):
            if term_date is None or not program_sections:
                return {}

            for idx, prog in enumerate(program_sections):
                start = self._parse_date(prog.get(start_field, ""))
                end = None
                for fld in end_priority:
                    end = self._parse_date(prog.get(fld, ""))
                    if end:
                        break

                if end is None and fallback == "next_program_block.first_term" and idx + 1 < len(program_sections):
                    end = self._parse_date(program_sections[idx + 1].get(start_field, ""))

                if start and term_date >= start and (end is None or term_date <= end):
                    return prog

            # Graceful fallback: last program whose start is <= term.
            candidates = []
            for prog in program_sections:
                start = self._parse_date(prog.get(start_field, ""))
                if start and term_date >= start:
                    candidates.append((start, prog))
            if candidates:
                candidates.sort(key=lambda x: x[0])
                return candidates[-1][1]

            return {}

        # Convert each term block into course rows with context.
        current_term_index = 0
        for line_idx, line in enumerate(lines):
            clean = line.strip()
            if not clean:
                continue

            # Skip until the first term marker.
            while (
                current_term_index + 1 < len(term_positions)
                and line_idx >= term_positions[current_term_index + 1][0]
            ):
                current_term_index += 1

            term_line_idx, term_value = term_positions[current_term_index]
            if line_idx <= term_line_idx:
                continue

            # Only process lines matching the actual configured data-row pattern.
            cfg = self.config.get("data_rows") or {}
            if not cfg.get("pattern"):
                continue
            try:
                m = re.match(cfg["pattern"], clean)
            except re.error:
                m = None
            if not m:
                continue

            row = dict(base_row)
            columns = cfg.get("columns", [])
            transforms = cfg.get("transforms", {})
            for idx, col_name in enumerate(columns):
                val = m.group(idx + 1)
                if col_name in transforms:
                    transform_fn = eval(transforms[col_name])
                    val = transform_fn(val)
                row[col_name] = val

            row.update(global_context)
            row["term"] = term_value

            # Find the term's "Term ... Total:" line and obtain its GPA.
            next_term_line_idx = (
                term_positions[current_term_index + 1][0]
                if current_term_index + 1 < len(term_positions)
                else len(lines)
            )
            gpa = "N/A"
            try:
                gpa_re = re.compile(
                    r"^Term\s+[^:]*Total:\s*\d+\.\d{2}\s+\d+\.\d{2}\s+(\d+\.\d{2})\s*$"
                )
                for j in range(term_line_idx + 1, next_term_line_idx):
                    gm = gpa_re.match(lines[j].strip())
                    if gm:
                        gpa = gm.group(1)
                        break
            except Exception:
                pass
            row["gpa_term"] = gpa

            # Select the program that covers this term.
            term_date = self._normalize_term_date(term_value)
            prog = select_program(term_date)

            field_map = {
                "program": "program_description",
                "status": "status",
                "exp_grad_date": "exp_grad_date",
                "ft_pt": "ft_pt",
                "first_term": "first_term",
                "session": "session",
                "grad_date": "grad_date",
                "lda": "lda",
            }
            for src_name, out_name in field_map.items():
                if src_name in prog:
                    row[out_name] = prog.get(src_name, "N/A")

            out_rows.append(row)

        return out_rows

    def extract_data_rows(self, block):
        cfg = self.config.get("data_rows")
        if not cfg:
            return []

        if cfg.get("mode") == "student_ledger":
            return self._extract_student_ledger_data_rows(block)

        if not cfg.get("pattern"):
            return []

        if self.config.get("context_fields"):
            # The context-aware path handles Term: markers and attaches
            # term/GPA/program context to each course row.
            base_rows = []
            pattern = re.compile(cfg["pattern"])
            columns = cfg["columns"]
            transforms = cfg.get("transforms", {})

            for line in block.split("\n"):
                m = pattern.match(line.strip())
                if not m:
                    continue
                row = {}
                for idx, col_name in enumerate(columns):
                    val = m.group(idx + 1)
                    if col_name in transforms:
                        transform_fn = eval(transforms[col_name])
                        val = transform_fn(val)
                    row[col_name] = val
                base_rows.append(row)

            # _get_context_row_data needs line positions, so it performs its
            # own row matching and returns the final rows.
            dummy = base_rows
            del dummy
            return self._get_context_row_data(block, {})

        pattern = re.compile(cfg["pattern"])
        columns = cfg["columns"]
        transforms = cfg.get("transforms", {})
        rows = []

        for line in block.split("\n"):
            m = pattern.match(line.strip())
            if not m:
                continue

            row = {}
            for idx, col_name in enumerate(columns):
                val = m.group(idx + 1)
                if col_name in transforms:
                    transform_fn = eval(transforms[col_name])  # noqa: S307
                    val = transform_fn(val)
                row[col_name] = val

            rows.append(row)

        return rows

    def extract_record(self, block):
        """Run every configured extraction stage against one record block."""
        if self.config.get("report_type") == "student_schedule" or "term_sections" in self.config or "student_fields" in self.config:
            sse = StudentScheduleExtractor(self.config)
            rows = sse.extract_record(block)
            name = rows[0].get("student_name", "N/A") if rows else "N/A"
            address = "N/A"
            rep_sections = {}
            summary = {k: rows[0].get(k, "") for k in (
                "program_description", "drop_date", "group", "first_term",
                "exp_grad_date", "ft_pt", "status", "grad_date", "session"
            ) if rows}
            return name, address, rep_sections, summary, rows

        if (
            self.config.get("report_type") in ("student_transcript", "transcript", "bayamon_transcript", "official_transcript")
            or "program_match" in self.config
        ):
            ste = StudentTranscriptExtractor(self.config)
            rows = ste.extract_record(block)
            name = rows[0].get("name", rows[0].get("student_name", "N/A")) if rows else "N/A"
            address = rows[0].get("address", "N/A") if rows else "N/A"
            rep_sections = {}
            summary = {k: rows[0].get(k, "") for k in (
                "id", "dob", "phone", "program", "program_description", "drop_date",
                "group", "first_term", "exp_grad_date", "ft_pt", "status",
                "lda", "grad_date", "session"
            ) if rows}
            return name, address, rep_sections, summary, rows

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
        if self.config.get("report_type") == "student_schedule" or "term_sections" in self.config or "student_fields" in self.config:
            sse = StudentScheduleExtractor(self.config)
            rows = sse.extract_record(block)
            name = rows[0].get("student_name", "N/A") if rows else "N/A"
            address = "N/A"
            rep_sections = {}
            summary = {k: rows[0].get(k, "") for k in (
                "program_description", "drop_date", "group", "first_term",
                "exp_grad_date", "ft_pt", "status", "grad_date", "session"
            ) if rows}
            return {
                "name": name,
                "name_line_index": 0,
                "address": address,
                "repeating_sections": rep_sections,
                "summary_fields": summary,
                "data_rows_count": len(rows),
                "data_rows_sample": rows[:3],
            }

        if (
            self.config.get("report_type") in ("student_transcript", "transcript", "bayamon_transcript", "official_transcript")
            or "program_match" in self.config
        ):
            ste = StudentTranscriptExtractor(self.config)
            rows = ste.extract_record(block)
            name = rows[0].get("name", rows[0].get("student_name", "N/A")) if rows else "N/A"
            address = rows[0].get("address", "N/A") if rows else "N/A"
            rep_sections = {}
            summary = {k: rows[0].get(k, "") for k in (
                "id", "dob", "phone", "program", "program_description", "drop_date",
                "group", "first_term", "exp_grad_date", "ft_pt", "status",
                "lda", "grad_date", "session"
            ) if rows}
            return {
                "name": name,
                "name_line_index": 0,
                "address": address,
                "repeating_sections": rep_sections,
                "summary_fields": summary,
                "data_rows_count": len(rows),
                "data_rows_sample": rows[:3],
            }

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
# Student Schedule extractor  (report_type == "student_schedule")
# ---------------------------------------------------------------------------

class StudentScheduleExtractor:
    """Dedicated extractor for the Bayamon Community College Student Schedule PDF.

    Handles the specific quirks of this layout:
    - Student name lives on the SAME line as the record separator.
    - Program description is split across two consecutive PDF lines.
    - Grad Date may be blank (Status: Graduated  Grad Date:  Session: On-Line).
    - Course description wraps onto the next line.
    - All student/program/term fields must be carried into every course row.
    """

    # Course row — full pattern (has dates, days, room, times)
    _COURSE_RE = re.compile(
        r"^([A-Z]{2,4}\d{3,4}[A-Z]?)\s+"           # course code
        r"\(([^)]+)\)\s+"                            # (version)
        r"(.+?)\s+"                                  # description
        r"(\d+\.\d{2})\s+"                           # units
        r"(\d+\.\d{2})\s+"                           # fa_units
        r"(.+?)\s+"                                  # instructor
        r"(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})\s+"  # date range
        r"([A-Za-z,\s]+?)\s+"                        # days of week
        r"(Online|\S+)\s+"                           # room
        r"(\d{2}:\d{2}\s+[AP]M)\s+to\s+(\d{2}:\d{2}\s+[AP]M)"  # times
        r"\s*$"
    )
    # Course row — partial fallback (missing date/days/room/times, e.g. Práctica rows)
    _COURSE_PARTIAL_RE = re.compile(
        r"^([A-Z]{2,4}\d{3,4}[A-Z]?)\s+"           # course code
        r"\(([^)]+)\)\s+"                            # (version)
        r"(.+?)\s+"                                  # description
        r"(\d+\.\d{2})\s+"                           # units
        r"(\d+\.\d{2})\s*"                           # fa_units
        r"(.*)$"                                     # remainder = instructor (may be partial)
    )
    # Term marker
    _TERM_RE = re.compile(
        r"^Term:\s*(\d{2}/\d{2}/\d{4}\s*-\s*\d{2}/\d{2}/\d{4})\s*$"
    )
    # Lines to discard entirely
    _DISCARD_RES = [
        re.compile(r"17\s+Calle\s+Maceo", re.IGNORECASE),
        re.compile(r"Bayam[o\u00f3]+n,?\s*PR\s+\d+", re.IGNORECASE),
        re.compile(r"\(787\)\s*780-4242"),
        re.compile(r"https?://bccpr\.org", re.IGNORECASE),
        re.compile(r"^Student Schedule$", re.IGNORECASE),
        re.compile(r"Books Required", re.IGNORECASE),
        re.compile(r"_{4,}"),
        re.compile(r"^(Friday|Monday|Tuesday|Wednesday|Thursday|Saturday|Sunday),.+Page\s+\d+", re.IGNORECASE),
        re.compile(r"^Course\s+Course Description\s+Units", re.IGNORECASE),
        re.compile(r"^Total Units for Term:", re.IGNORECASE),
        re.compile(r"Student Schedule\s*$", re.IGNORECASE),
        re.compile(r"\bUnited States\b", re.IGNORECASE),
        re.compile(r"^(Urb|Calle|HC|PO Box|Barrio|Bo\.|Res\.)\b", re.IGNORECASE),
    ]

    def __init__(self, config: dict):
        self.config = config or {}
        self.output_columns = config.get("output_columns", [])

    def _should_discard(self, line: str) -> bool:
        s = line.strip()
        if not s:
            return True
        for pat in self._DISCARD_RES:
            if pat.search(s):
                return True
        return False

    def read_pdf(self, pdf_path: str):
        """Return (full_text, page_offsets) like ConfigurableExtractor."""
        page_texts = []
        page_offsets = []
        current_offset = 0
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                page_offsets.append((current_offset, page_num))
                page_texts.append(text)
                current_offset += len(text) + 1
        return "\n".join(page_texts), page_offsets

    def split_records(self, full_text: str, page_offsets):
        """Split on record_separator, keeping the full line (so name suffix is preserved)."""
        separator = (self.config.get("record_separator") or "").strip()
        if not separator:
            text = full_text.strip()
            return [(text, page_offsets[0][1] if page_offsets else 1)] if text else []

        results = []
        positions = []
        search_start = 0
        while True:
            idx = full_text.find(separator, search_start)
            if idx == -1:
                break
            positions.append(idx)
            search_start = idx + len(separator)

        for k, pos in enumerate(positions):
            # Include from the separator so the name suffix is in the block
            block_start = pos
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

    def extract_record(self, block: str, source_file: str = "N/A", page_number: int = 1) -> list[dict]:
        """Parse one student block → list of output rows (one per course)."""
        raw_lines = block.splitlines()

        # ── 1. Student name (on the separator line itself) ──────────────
        student_name = "N/A"
        separator = (self.config.get("record_separator") or "").strip()
        for line in raw_lines[:3]:
            s = line.strip()
            if s.startswith(separator):
                suffix = s[len(separator):].strip()
                if suffix:
                    student_name = suffix
                    break

        # ── 2. Filter lines for field parsing ────────────────────────────
        lines = [ln.strip() for ln in raw_lines if not self._should_discard(ln)]

        # ── 3. Program block ──────────────────────────────────────────────
        prog_desc = "N/A"
        drop_date = ""
        group = "N/A"
        first_term = "N/A"
        exp_grad_date = "N/A"
        ft_pt = "N/A"
        status = "N/A"
        grad_date = ""
        session = "N/A"

        for i, line in enumerate(lines):
            if line.startswith("Program Description:"):
                # Drop Date and Group are on the same line
                m = re.search(r"Drop Date:\s*(\d{2}/\d{2}/\d{4})", line)
                drop_date = m.group(1) if m else ""
                m = re.search(r"Group:\s*(\S+)", line)
                group = m.group(1) if m else "N/A"

                # Program description: text between "Program Description:" and "Drop Date:"
                m = re.match(r"Program Description:\s*(.+?)(?:\s+Drop Date:|\s*$)", line)
                desc1 = m.group(1).strip() if m else ""

                # The continuation is on the NEXT RAW line (which may contain
                # the URL and then the rest of the program name, e.g.
                # "https://bccpr.org  Contabilidad").
                # Use raw_lines so the URL-discard filter hasn't removed it yet.
                desc2 = ""
                for raw_i, raw_line in enumerate(raw_lines):
                    if "Program Description:" in raw_line:
                        if raw_i + 1 < len(raw_lines):
                            next_raw = raw_lines[raw_i + 1].strip()
                            # Skip pure address/header lines
                            if not re.match(
                                r"^(First Term:|Status:|Term:|Program Description:|17\s+Calle)",
                                next_raw
                            ):
                                # Strip any URL prefix, keep the remainder
                                cleaned = re.sub(r"https?://\S+", "", next_raw).strip()
                                if cleaned:
                                    desc2 = cleaned
                        break

                prog_desc = " ".join(filter(None, [desc1, desc2])).strip() or "N/A"
                break

        for line in lines:
            if "First Term:" in line:
                m = re.search(r"First Term:\s*(\d{2}/\d{2}/\d{4})", line)
                if m:
                    first_term = m.group(1)
                m = re.search(r"Exp Grad Date:\s*(\d{2}/\d{2}/\d{4})", line)
                if m:
                    exp_grad_date = m.group(1)
                m = re.search(r"FT/PT:\s*(Full Time|Part Time)", line)
                if m:
                    ft_pt = m.group(1)

            if line.startswith("Status:"):
                m = re.search(r"Status:\s*(\S+(?:\s+\S+)?)(?=\s+Grad Date:|\s+Session:|$)", line)
                if m:
                    status = m.group(1).strip()
                m = re.search(r"(?<!Exp )Grad Date:\s*(\d{2}/\d{2}/\d{4})", line)
                if m:
                    grad_date = m.group(1)
                m = re.search(r"Session:\s*(\S.*?)$", line)
                if m:
                    session = m.group(1).strip()

        # ── 4. Student-level context dict ─────────────────────────────────
        student_ctx = {
            "student_name": student_name,
            "program_description": prog_desc,
            "drop_date": drop_date,
            "group": group,
            "first_term": first_term,
            "exp_grad_date": exp_grad_date,
            "ft_pt": ft_pt,
            "status": status,
            "grad_date": grad_date,
            "session": session,
        }

        # ── 5. Course rows with term context ──────────────────────────────
        output_rows = []
        current_term = "N/A"
        pending_course: dict | None = None

        def _flush(course: dict | None):
            if course is not None:
                row = dict(student_ctx)
                row["term"] = current_term
                row.update(course)
                row["source_file"] = source_file
                row["page_number"] = page_number
                output_rows.append(row)

        for line in lines:
            # Term marker
            m = self._TERM_RE.match(line)
            if m:
                _flush(pending_course)
                pending_course = None
                current_term = m.group(1).strip()
                continue

            # Course data row — full pattern first
            m = self._COURSE_RE.match(line)
            if m:
                _flush(pending_course)
                pending_course = {
                    "course": m.group(1).strip(),
                    "course_version": m.group(2).strip(),
                    "course_description": m.group(3).strip(),
                    "units": m.group(4).strip(),
                    "fa_units": m.group(5).strip(),
                    "instructor": m.group(6).strip(),
                    "class_meetings_start": m.group(7).strip(),
                    "class_meetings_end": m.group(8).strip(),
                    "days_of_week": m.group(9).strip(),
                    "room": m.group(10).strip(),
                    "class_time_start": m.group(11).strip(),
                    "class_time_end": m.group(12).strip(),
                    "_partial": False,
                }
                continue

            # Course data row — partial fallback (no date/room/times in the PDF)
            m = self._COURSE_PARTIAL_RE.match(line)
            if m and not self._TERM_RE.match(line):
                _flush(pending_course)
                pending_course = {
                    "course": m.group(1).strip(),
                    "course_version": m.group(2).strip(),
                    "course_description": m.group(3).strip(),
                    "units": m.group(4).strip(),
                    "fa_units": m.group(5).strip(),
                    "instructor": m.group(6).strip(),
                    "class_meetings_start": "",
                    "class_meetings_end": "",
                    "days_of_week": "",
                    "room": "",
                    "class_time_start": "",
                    "class_time_end": "",
                    "_partial": True,
                }
                continue

            # Continuation line — may extend course_description OR instructor
            if (
                pending_course is not None
                and line
                and not self._TERM_RE.match(line)
                and not re.match(r"^Total Units for Term:", line, re.IGNORECASE)
                and not re.match(r"^Course\s+Course Description", line, re.IGNORECASE)
                and not self._COURSE_RE.match(line)
                and not self._COURSE_PARTIAL_RE.match(line)
                and re.match(r"^[A-Za-z\u00C0-\u00FF\s,\-]+$", line)
            ):
                if pending_course.get("_partial"):
                    # Partial row (no dates at all): continuation is always instructor
                    existing = pending_course["instructor"]
                    pending_course["instructor"] = (
                        (existing + " " + line).strip() if existing else line
                    )
                elif pending_course.get("class_meetings_start"):
                    # Full row (has dates). Decide based on how many words the
                    # instructor captured on the main line already has.
                    # Spanish names have 2-3 parts; if we got < 3, the continuation
                    # line's LAST WORD is the missing surname, and anything before
                    # it belongs to the course description.
                    instructor_words = len(
                        (pending_course.get("instructor") or "").split()
                    )
                    if instructor_words >= 3:
                        # Instructor is complete — all of continuation is description
                        pending_course["course_description"] = (
                            pending_course["course_description"] + " " + line
                        ).strip()
                    else:
                        # Instructor incomplete — split: all-but-last → description,
                        # last word → instructor
                        parts = line.split()
                        desc_part = " ".join(parts[:-1])
                        inst_part = parts[-1]
                        if desc_part:
                            pending_course["course_description"] = (
                                pending_course["course_description"] + " " + desc_part
                            ).strip()
                        existing = pending_course["instructor"]
                        pending_course["instructor"] = (
                            (existing + " " + inst_part).strip() if existing else inst_part
                        )
                else:
                    # No dates yet: continuation is course description overflow
                    pending_course["course_description"] = (
                        pending_course["course_description"] + " " + line
                    ).strip()

        _flush(pending_course)

        # If no courses, emit one context-only row so the student still appears
        if not output_rows:
            row = dict(student_ctx)
            row["term"] = current_term
            row["source_file"] = source_file
            row["page_number"] = page_number
            output_rows.append(row)

        for r in output_rows:
            r.pop("_partial", None)

        return output_rows

    def _run_batch(self, source_folder, output_xlsx, config,
                   on_progress=None, abort_flag=None, log=None,
                   session_inputs=None):
        """Internal batch runner used by BatchPDFToExcelExporter."""
        output_dir = os.path.dirname(os.path.abspath(output_xlsx)) or "."
        run_id = new_run_id()
        file_logger = setup_run_logger(output_dir, run_id)

        def _log(level: str, msg: str):
            if log:
                log(level, msg)
            getattr(file_logger, level, file_logger.info)(msg)

        _log("info", f"[student_schedule] Source: {source_folder}")
        _log("info", f"[student_schedule] Output: {output_xlsx}")

        pdf_paths = sorted(
            {p for p in Path(source_folder).glob("*.pdf")} |
            {p for p in Path(source_folder).glob("*.PDF")}
        )
        total = len(pdf_paths)

        if on_progress:
            on_progress({"type": "total", "total": total})

        if total == 0:
            msg = f"No PDF files found in: {source_folder}"
            _log("error", msg)
            raise RuntimeError(msg)

        _session_inputs = session_inputs or {
            "source_folder": source_folder,
            "output_xlsx": output_xlsx,
        }
        session_data = SessionManager.start_session(MODULE_ID, _session_inputs, total_items=total)
        completed_files = set(session_data.get("completed_item_ids", []))

        all_rows = []
        exec_log_rows = []
        files_processed = 0
        records_found = 0

        for idx, pdf_path in enumerate(pdf_paths):
            if abort_flag and abort_flag():
                _log("warning", "Extraction aborted by user.")
                break

            if pdf_path.name in completed_files:
                _log("info", f"[SESSION] Skipping {pdf_path.name} — already processed.")
                if on_progress:
                    on_progress({"type": "item_done", "completed": idx + 1})
                continue

            if on_progress:
                on_progress({"type": "item_start", "idx": idx, "student_id": pdf_path.name})

            file_ts = datetime.datetime.now().isoformat(timespec="seconds")
            file_rows_before = len(all_rows)
            file_error = ""
            file_records = 0

            try:
                _log("info", f"[{idx+1}/{total}] Processing: {pdf_path.name}")
                full_text, page_offsets = self.read_pdf(str(pdf_path))
                records = self.split_records(full_text, page_offsets)

                if not records:
                    _log("warning", f"{pdf_path.name}: no records found.")
                    file_error = "no records matched"
                else:
                    for block, page_number in records:
                        rows = self.extract_record(block, pdf_path.name, page_number)
                        all_rows.extend(rows)
                        records_found += len(rows)
                    file_records = len(records)
                    _log("info", f"{pdf_path.name}: {file_records} record(s), {len(all_rows)-file_rows_before} row(s).")

                files_processed += 1
                exec_log_rows.append({
                    "timestamp": file_ts,
                    "source_file": pdf_path.name,
                    "status": "success" if not file_error else "no_records",
                    "records_found": file_records,
                    "rows_written": len(all_rows) - file_rows_before,
                    "error_message": file_error,
                })
                SessionManager.update_item_status(MODULE_ID, pdf_path.name, success=True)

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
                SessionManager.update_item_status(MODULE_ID, pdf_path.name, success=False)

            if on_progress:
                on_progress({"type": "item_done", "completed": idx + 1})
        else:
            SessionManager.clear_session(MODULE_ID)
            _log("info", "[SESSION] All PDFs processed — session cleared.")

        if exec_log_rows:
            csv_path = write_execution_log_csv(output_dir, exec_log_rows, run_id)
            _log("info", f"Execution log written: {csv_path}")

        if not all_rows:
            msg = "No data extracted. Check record_separator and patterns."
            _log("error", msg)
            raise RuntimeError(msg)

        df = pd.DataFrame(all_rows)

        # Order columns per config's output_columns list
        output_cols = self.output_columns or []
        for col in output_cols:
            if col not in df.columns:
                df[col] = ""
        ordered = [c for c in output_cols if c in df.columns]
        df = df[ordered]

        out_dir = os.path.dirname(output_xlsx)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        df.to_excel(output_xlsx, index=False, engine="openpyxl")

        result = {
            "files_processed": files_processed,
            "total_files": total,
            "records": records_found,
            "rows": len(all_rows),
            "output": output_xlsx,
        }
        _log("info", f"Extraction complete: {result}")
        return result


# ---------------------------------------------------------------------------
# Student Transcript extractor  (report_type in ("student_transcript", "transcript"))
# ---------------------------------------------------------------------------

class StudentTranscriptExtractor:
    """Dedicated extractor for Bayamon Community College Official Student Transcripts.

    Handles:
    - Student Name from separator line ("Bayamon Community College Name").
    - Personal fields: ID, DOB, Phone, Address embedded in the header.
    - Multiple Program Blocks: Program Description, Drop Date, Group, First Term,
      Exp Grad Date, FT/PT, Status, LDA, Grad Date, Session.
    - Term-to-Program Date matching: Maps each Term (e.g. 2014-08-04, O1022, O0326)
      to the appropriate Program Block based on First Term dates.
    - Multiline course descriptions.
    - Output columns normalization (support both 'name' / 'student_name',
      'program' / 'program_description', 'grade' / 'frade', etc.).
    """

    _DISCARD_BODY = [
        re.compile(r'Attendance Summary', re.IGNORECASE),
        re.compile(r'Total Required Hours.*', re.IGNORECASE),
        re.compile(r'Total Attended Hours.*', re.IGNORECASE),
        re.compile(r'Total Transfer Hours.*', re.IGNORECASE),
        re.compile(r'Total Hours Remaining.*', re.IGNORECASE),
        re.compile(r'Total Scheduled Hours.*', re.IGNORECASE),
        re.compile(r'Attendance Percentage.*', re.IGNORECASE),
        re.compile(r'Not Official unless signed.*', re.IGNORECASE),
        re.compile(r'_{5,}'),
        re.compile(r'Registrar', re.IGNORECASE),
        re.compile(r'^(Friday|Monday|Tuesday|Wednesday|Thursday|Saturday|Sunday),.*Page \d+', re.IGNORECASE),
        re.compile(r'Powered by TCPDF.*', re.IGNORECASE),
        re.compile(r'^Course\s+Course Description\s+Grade', re.IGNORECASE),
        re.compile(r'^Units\s+Units', re.IGNORECASE),
        re.compile(r'^Term.*Total:', re.IGNORECASE),
        re.compile(r'^Cumulative Total:', re.IGNORECASE),
        re.compile(r'Student Transcript\s*$', re.IGNORECASE),
    ]

    _COURSE_RE = re.compile(
        r'^([A-Z]{2,4}\d{3,4}[A-Z]?)\s+(.+?)\s+([A-Z0-9+\-]+|-)\s+(\d+\.\d{2})\s+(\d+\.\d{2})\s*$'
    )
    _INVERTED_COURSE_RE = re.compile(
        r'^(.+?)\s+([A-Z0-9+\-]+|-)\s+(\d+\.\d{2})\s+(\d+\.\d{2})\s*$'
    )
    _CODE_ONLY_RE = re.compile(r'^([A-Z]{2,4}\d{3,4}[A-Z]?)$')
    _TERM_RE = re.compile(r'^Term:\s*([A-Za-z0-9-]+)\s*$')

    def __init__(self, config: dict):
        self.config = config or {}
        self.output_columns = config.get("output_columns", [])

    def read_pdf(self, pdf_path: str):
        page_texts = []
        page_offsets = []
        current_offset = 0
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                page_offsets.append((current_offset, page_num))
                page_texts.append(text)
                current_offset += len(text) + 1
        return "\n".join(page_texts), page_offsets

    def split_records(self, full_text: str, page_offsets):
        separator = (self.config.get("record_separator") or "Bayamon Community College").strip()
        if not separator:
            text = full_text.strip()
            return [(text, page_offsets[0][1] if page_offsets else 1)] if text else []

        results = []
        positions = []
        search_start = 0
        while True:
            idx = full_text.find(separator, search_start)
            if idx == -1:
                break
            positions.append(idx)
            search_start = idx + len(separator)

        for k, pos in enumerate(positions):
            block_start = pos
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

    def _should_skip_body(self, line: str) -> bool:
        s = line.strip()
        if not s:
            return True
        for pat in self._DISCARD_BODY:
            if pat.search(s):
                return True
        return False

    @staticmethod
    def _term_month_year(term_str: str) -> tuple[int, int]:
        m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', term_str)
        if m:
            return (int(m.group(1)), int(m.group(2)))
        m = re.match(r'^[A-Za-z](\d{2})(\d{2})$', term_str)
        if m:
            month = int(m.group(1))
            year = 2000 + int(m.group(2))
            return (year, month)
        m = re.match(r'^(\d{2})/(\d{2})/(\d{4})$', term_str)
        if m:
            return (int(m.group(3)), int(m.group(1)))
        return (0, 0)

    @staticmethod
    def _date_month_year(date_str: str) -> tuple[int, int]:
        m = re.match(r'^(\d{2})/(\d{2})/(\d{4})$', date_str)
        if m:
            return (int(m.group(3)), int(m.group(1)))
        return (0, 0)

    def extract_record(self, block: str, source_file: str = "N/A", page_number: int = 1) -> list[dict]:
        raw_lines = block.splitlines()

        # 1. Student Name
        student_name = "N/A"
        separator = (self.config.get("record_separator") or "Bayamon Community College").strip()
        for line in raw_lines[:5]:
            s = line.strip()
            if s.startswith(separator):
                suffix = s[len(separator):].strip()
                if suffix:
                    student_name = suffix
                    break

        # 2. Personal fields (ID, DOB, Phone, Address)
        student_id = ""
        dob = ""
        phone = ""
        addr_parts = []

        for line in raw_lines[:15]:
            m = re.search(r'\bID:\s*([A-Za-z0-9]+)', line)
            if m:
                student_id = m.group(1).strip()
            m = re.search(r'\bDOB:\s*(\d{2}/\d{2}/\d{4})', line)
            if m:
                dob = m.group(1).strip()
            m = re.search(r'\bPhone:\s*(.*)$', line)
            if m:
                phone = m.group(1).strip()

        for line in raw_lines[1:10]:
            s = line.strip()
            if "Program Description:" in s or s.startswith("Program") or "Official Student Transcript" in s:
                break
            if s.startswith("17 Calle Maceo") or s.startswith("https://"):
                continue
            s = re.sub(r'^Bayam[oó\u00f3\u00c3\u00b3]+n,?\s*PR\s+\d+\s*', '', s, flags=re.IGNORECASE)
            s = re.sub(r'^\(787\)\s*780-4242\s*', '', s)
            s = re.sub(r'\s*ID:\s*[A-Za-z0-9]+', '', s)
            s = re.sub(r'\s*DOB:\s*\d{2}/\d{2}/\d{4}', '', s)
            s = re.sub(r'\s*Phone:.*', '', s)
            s = s.strip()
            if s and s != "17 Calle Maceo":
                addr_parts.append(s)

        address = " ".join(addr_parts)

        # 3. Header lines vs Body lines
        header_lines = []
        body_lines = []
        in_body = False
        for line in raw_lines:
            if "Official Student Transcript" in line:
                in_body = True
                continue
            if in_body:
                body_lines.append(line)
            else:
                header_lines.append(line)

        # Parse Program Blocks
        prog_indices = [i for i, ln in enumerate(header_lines) if "Program Description:" in ln]
        prog_blocks = []

        status_pats = [
            "Cancelled Contract", "Dropped Exp", "Withdrawal/Transfer",
            "Re-Admission/Transfer", "Active/Transfer In", "Active/Transfer",
            "Transfer Out", "Graduated", "Active", "Dropped", "Withdrawal", "Cancelled"
        ]

        for k, idx in enumerate(prog_indices):
            end_idx = prog_indices[k + 1] if k + 1 < len(prog_indices) else len(header_lines)
            blk_lines = header_lines[idx:end_idx]
            full_str = "\n".join(blk_lines)

            m = re.search(r'Drop Date:\s*(\d{2}/\d{2}/\d{4})', full_str)
            drop_date = m.group(1) if m else ""

            m = re.search(r'\bGroup:\s*([A-Za-z0-9]+)', full_str)
            group = m.group(1) if m else ""
            if group.lower() in ("first", "exp", "status", "ft/pt", "asistente", "program"):
                group = ""

            m = re.search(r'First Term:\s*(\d{2}/\d{2}/\d{4})', full_str)
            first_term = m.group(1) if m else ""

            m = re.search(r'LDA:\s*(\d{2}/\d{2}/\d{4})', full_str)
            lda = m.group(1) if m else ""

            m = re.search(r'Session:\s*([A-Za-z\-]+)', full_str)
            session = m.group(1) if m else ""

            ft_pt = ""
            m_ft = re.search(r'FT/PT:\s*(Full Time|Part Time|Half Time|Less than Half(?:\s+Time)?)', full_str, re.IGNORECASE)
            if m_ft:
                ft_pt = m_ft.group(1).strip()

            exp_grad_date = ""
            m_exp = re.search(r'Exp Grad Date:\s*(\d{2}/\d{2}/\d{4})', full_str)
            if m_exp:
                exp_grad_date = m_exp.group(1)
            else:
                for i, line in enumerate(blk_lines):
                    if "Exp Grad Date:" in line and i + 1 < len(blk_lines):
                        m_d = re.search(r'\b(\d{2}/\d{2}/\d{4})\b', blk_lines[i + 1])
                        if m_d:
                            exp_grad_date = m_d.group(1)
                            break

            grad_date = ""
            m_grad = re.search(r'(?<!Exp )Grad Date:\s*(\d{2}/\d{2}/\d{4})', full_str)
            if m_grad:
                grad_date = m_grad.group(1)

            # Robust status extraction
            status = ""
            cleaned_block = re.sub(r'(Grad Date:|Session:|Exp Grad Date:|FT/PT:|First Term:|LDA:|Group:|Drop Date:).*', ' ', full_str)
            for sp in status_pats:
                if re.search(r'\bStatus:\s*' + re.escape(sp), cleaned_block, re.IGNORECASE):
                    status = sp
                    break

            if not status:
                for i, line in enumerate(blk_lines):
                    if "Status:" in line:
                        next_lines = " ".join([blk_lines[j].strip() for j in range(i, min(i + 3, len(blk_lines)))])
                        cleaned_multiline = re.sub(r'(Exp\s+Grad|Grad\s+Date|FT/PT|Session|First\s+Term|LDA|Group|Drop\s+Date|https?://\S+|bccpr\.org).*', ' ', next_lines)
                        cleaned_multiline = re.sub(r'\s+', ' ', cleaned_multiline)
                        for sp in status_pats:
                            sp_regex = re.sub(r'\s+', r'\\s*', re.escape(sp))
                            if re.search(r'Status:\s*' + sp_regex, cleaned_multiline, re.IGNORECASE):
                                status = sp
                                break
                        if not status:
                            if re.search(r'Status:\s*Re-?\s*Admission/Transfer', cleaned_multiline, re.IGNORECASE):
                                status = "Re-Admission/Transfer"
                            elif re.search(r'Status:\s*Active/Transfer\s*In', cleaned_multiline, re.IGNORECASE):
                                status = "Active/Transfer In"
                            elif re.search(r'Status:\s*Cancelled\s*Contract', cleaned_multiline, re.IGNORECASE):
                                status = "Cancelled Contract"
                            elif re.search(r'Status:\s*Withdrawal/Transfer', cleaned_multiline, re.IGNORECASE):
                                status = "Withdrawal/Transfer"
                        break

            if not status:
                for sp in status_pats:
                    if re.search(r'\b' + re.escape(sp) + r'\b', full_str, re.IGNORECASE):
                        status = sp
                        break

            # Clean Program Description
            desc_lines = []
            for line in blk_lines:
                l_clean = line
                if "Program Description:" in l_clean:
                    l_clean = re.sub(r'Program Description:.*?(?=$|\n)', '', l_clean).strip()
                l_clean = re.sub(r'https?://\S+|bccpr\.org', '', l_clean).strip()
                
                if re.search(r'^(Status:|First Term:|Drop Date:|Group:|Exp Grad Date:|FT/PT:|LDA:|Grad Date:|Session:)', l_clean, re.IGNORECASE):
                    continue
                if re.search(r'^(Withdrawal|Dropped|Graduated|Active|Cancelled|Re-Admission|Admission|Transfer|\d{2}/\d{2}/\d{4})', l_clean, re.IGNORECASE):
                    continue
                
                l_clean = re.sub(r'\b(Drop Date:|Group:|First Term:|Exp Grad Date:|FT/PT:|Status:|LDA:|Grad Date:|Session:).*', '', l_clean).strip()
                l_clean = re.sub(r'\b\d{2}/\d{2}/\d{4}\b', '', l_clean).strip()
                l_clean = re.sub(r'\b(Withdrawal/Transfer|Re-Admission/Transfer|Active/Transfer In|Active/Transfer|Transfer Out|Admission/Transfer|Dropped Exp|Cancelled Contract|Graduated|Active|Dropped|Withdrawal|Cancelled|Contract|In|Out)\b', '', l_clean, flags=re.IGNORECASE).strip()
                if l_clean:
                    desc_lines.append(l_clean)
                    
            program = " ".join(desc_lines).strip()

            end_date = grad_date or exp_grad_date or drop_date
            end_my = self._date_month_year(end_date) if end_date else (9999, 12)
            first_my = self._date_month_year(first_term)

            prog_blocks.append({
                "program": program,
                "program_description": program,
                "drop_date": drop_date,
                "group": group,
                "first_term": first_term,
                "first_term_my": first_my,
                "exp_grad_date": exp_grad_date,
                "ft_pt": ft_pt,
                "status": status,
                "lda": lda,
                "grad_date": grad_date,
                "session": session,
                "end_my": end_my,
            })

        def find_program_for_term(term_str: str) -> dict:
            if not prog_blocks:
                return {}
            t_my = self._term_month_year(term_str)
            if t_my == (0, 0):
                return prog_blocks[0]
            candidates = [b for b in prog_blocks if b["end_my"] >= t_my]
            if candidates:
                return min(candidates, key=lambda b: b["end_my"])
            return prog_blocks[-1]

        current_term = "N/A"
        current_prog = prog_blocks[0] if prog_blocks else {}
        pending_course = None
        pending_inverted = None
        output_rows = []

        def flush_course():
            nonlocal pending_course
            if pending_course:
                gr = pending_course["grade"]
                row = {
                    "name": student_name,
                    "student_name": student_name,
                    "address": address,
                    "id": student_id,
                    "dob": dob,
                    "phone": phone,
                    "program": current_prog.get("program", ""),
                    "program_description": current_prog.get("program_description", ""),
                    "drop_date": current_prog.get("drop_date", ""),
                    "group": current_prog.get("group", ""),
                    "status": current_prog.get("status", ""),
                    "exp_grad_date": current_prog.get("exp_grad_date", ""),
                    "ft_pt": current_prog.get("ft_pt", ""),
                    "first_term": current_prog.get("first_term", ""),
                    "lda": current_prog.get("lda", ""),
                    "grad_date": current_prog.get("grad_date", ""),
                    "session": current_prog.get("session", ""),
                    "term": current_term,
                    "course": pending_course["course"],
                    "course_description": pending_course["course_description"],
                    "grade": gr,
                    "frade": gr,
                    "units_attempted": pending_course["units_attempted"],
                    "units_completed": pending_course["units_completed"],
                    "source_file": source_file,
                    "page_number": page_number,
                }
                output_rows.append(row)
                pending_course = None

        for line in body_lines:
            if self._should_skip_body(line):
                continue
            line_s = line.strip()

            # Term line
            m_term = self._TERM_RE.match(line_s)
            if m_term:
                flush_course()
                current_term = m_term.group(1).strip()
                current_prog = find_program_for_term(current_term)
                continue

            # Standard course line
            m_c = self._COURSE_RE.match(line_s)
            if m_c:
                flush_course()
                pending_course = {
                    "course": m_c.group(1).strip(),
                    "course_description": m_c.group(2).strip(),
                    "grade": m_c.group(3).strip(),
                    "units_attempted": m_c.group(4).strip(),
                    "units_completed": m_c.group(5).strip(),
                }
                continue

            # Inverted line: Description Grade Att Comp (waiting for course code)
            m_inv = self._INVERTED_COURSE_RE.match(line_s)
            if m_inv and not self._CODE_ONLY_RE.match(line_s):
                flush_course()
                pending_inverted = {
                    "course_description": m_inv.group(1).strip(),
                    "grade": m_inv.group(2).strip(),
                    "units_attempted": m_inv.group(3).strip(),
                    "units_completed": m_inv.group(4).strip(),
                }
                continue

            # Course code line following inverted line
            if pending_inverted:
                m_code = self._CODE_ONLY_RE.match(line_s)
                if m_code:
                    pending_course = {
                        "course": m_code.group(1).strip(),
                        "course_description": pending_inverted["course_description"],
                        "grade": pending_inverted["grade"],
                        "units_attempted": pending_inverted["units_attempted"],
                        "units_completed": pending_inverted["units_completed"],
                    }
                    pending_inverted = None
                    flush_course()
                    continue
                else:
                    pending_inverted = None

            # Continuation line for course description
            if pending_course and re.match(r'^[A-Za-z\u00C0-\u00FF\s,\-]+$', line_s):
                pending_course["course_description"] = (
                    pending_course["course_description"] + " " + line_s
                ).strip()

        flush_course()

        if not output_rows:
            row = {
                "name": student_name,
                "student_name": student_name,
                "address": address,
                "id": student_id,
                "dob": dob,
                "phone": phone,
                "program": current_prog.get("program", ""),
                "program_description": current_prog.get("program_description", ""),
                "drop_date": current_prog.get("drop_date", ""),
                "group": current_prog.get("group", ""),
                "status": current_prog.get("status", ""),
                "exp_grad_date": current_prog.get("exp_grad_date", ""),
                "ft_pt": current_prog.get("ft_pt", ""),
                "first_term": current_prog.get("first_term", ""),
                "lda": current_prog.get("lda", ""),
                "grad_date": current_prog.get("grad_date", ""),
                "session": current_prog.get("session", ""),
                "term": current_term,
                "source_file": source_file,
                "page_number": page_number,
            }
            output_rows.append(row)

        return output_rows

    def _run_batch(self, source_folder, output_xlsx, config,
                   on_progress=None, abort_flag=None, log=None,
                   session_inputs=None):
        """Internal batch runner used by BatchPDFToExcelExporter."""
        output_dir = os.path.dirname(os.path.abspath(output_xlsx)) or "."
        run_id = new_run_id()
        file_logger = setup_run_logger(output_dir, run_id)

        def _log(level: str, msg: str):
            if log:
                log(level, msg)
            getattr(file_logger, level, file_logger.info)(msg)

        _log("info", f"[student_transcript] Source: {source_folder}")
        _log("info", f"[student_transcript] Output: {output_xlsx}")

        pdf_paths = sorted(
            {p for p in Path(source_folder).glob("*.pdf")} |
            {p for p in Path(source_folder).glob("*.PDF")}
        )
        total = len(pdf_paths)

        if on_progress:
            on_progress({"type": "total", "total": total})

        if total == 0:
            msg = f"No PDF files found in: {source_folder}"
            _log("error", msg)
            raise RuntimeError(msg)

        _session_inputs = session_inputs or {
            "source_folder": source_folder,
            "output_xlsx": output_xlsx,
        }
        session_data = SessionManager.start_session(MODULE_ID, _session_inputs, total_items=total)
        completed_files = set(session_data.get("completed_item_ids", []))

        all_rows = []
        exec_log_rows = []
        files_processed = 0
        records_found = 0

        for idx, pdf_path in enumerate(pdf_paths):
            if abort_flag and abort_flag():
                _log("warning", "Extraction aborted by user.")
                break

            if pdf_path.name in completed_files:
                _log("info", f"[SESSION] Skipping {pdf_path.name} — already processed.")
                if on_progress:
                    on_progress({"type": "item_done", "completed": idx + 1})
                continue

            if on_progress:
                on_progress({"type": "item_start", "idx": idx, "student_id": pdf_path.name})

            file_ts = datetime.datetime.now().isoformat(timespec="seconds")
            file_rows_before = len(all_rows)
            file_error = ""
            file_records = 0

            try:
                _log("info", f"[{idx+1}/{total}] Processing: {pdf_path.name}")
                full_text, page_offsets = self.read_pdf(str(pdf_path))
                records = self.split_records(full_text, page_offsets)

                if not records:
                    _log("warning", f"{pdf_path.name}: no records found.")
                    file_error = "no records matched"
                else:
                    for block, page_number in records:
                        rows = self.extract_record(block, pdf_path.name, page_number)
                        all_rows.extend(rows)
                        records_found += len(rows)
                    file_records = len(records)
                    _log("info", f"{pdf_path.name}: {file_records} record(s), {len(all_rows)-file_rows_before} row(s).")

                files_processed += 1
                exec_log_rows.append({
                    "timestamp": file_ts,
                    "source_file": pdf_path.name,
                    "status": "success" if not file_error else "no_records",
                    "records_found": file_records,
                    "rows_written": len(all_rows) - file_rows_before,
                    "error_message": file_error,
                })
                SessionManager.update_item_status(MODULE_ID, pdf_path.name, success=True)

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
                SessionManager.update_item_status(MODULE_ID, pdf_path.name, success=False)

            if on_progress:
                on_progress({"type": "item_done", "completed": idx + 1})
        else:
            SessionManager.clear_session(MODULE_ID)
            _log("info", "[SESSION] All PDFs processed — session cleared.")

        if exec_log_rows:
            csv_path = write_execution_log_csv(output_dir, exec_log_rows, run_id)
            _log("info", f"Execution log written: {csv_path}")

        if not all_rows:
            msg = "No data extracted. Check record_separator and patterns."
            _log("error", msg)
            raise RuntimeError(msg)

        df = pd.DataFrame(all_rows)

        # Order columns per config's output_columns list
        output_cols = self.output_columns or []
        col_map = {c.lower(): c for c in df.columns}
        final_cols = []
        for requested in output_cols:
            req_clean = requested.strip().lower().replace(" ", "_")
            if req_clean in col_map:
                final_cols.append(col_map[req_clean])
            elif requested in df.columns:
                final_cols.append(requested)
            else:
                df[requested] = ""
                final_cols.append(requested)

        for meta in ("source_file", "page_number"):
            if meta in df.columns and meta not in final_cols:
                final_cols.append(meta)

        df = df[final_cols]

        out_dir = os.path.dirname(output_xlsx)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        df.to_excel(output_xlsx, index=False, engine="openpyxl")

        result = {
            "files_processed": files_processed,
            "total_files": total,
            "records": records_found,
            "rows": len(all_rows),
            "output": output_xlsx,
        }
        _log("info", f"Extraction complete: {result}")
        return result


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

    def run(self, source_folder, output_xlsx, config, on_progress=None, abort_flag=None, log=None,
            session_inputs=None):
        # Route to the dedicated Student Schedule extractor when requested.
        if config.get("report_type") == "student_schedule":
            sse = StudentScheduleExtractor(config)
            return sse._run_batch(
                source_folder, output_xlsx, config,
                on_progress=on_progress, abort_flag=abort_flag, log=log,
                session_inputs=session_inputs,
            )

        # Route to the dedicated Student Transcript extractor when requested.
        if (
            config.get("report_type") in ("student_transcript", "transcript", "bayamon_transcript", "official_transcript")
            or "program_match" in config
        ):
            ste = StudentTranscriptExtractor(config)
            return ste._run_batch(
                source_folder, output_xlsx, config,
                on_progress=on_progress, abort_flag=abort_flag, log=log,
                session_inputs=session_inputs,
            )

        engine = ConfigurableExtractor(config)

        # ── Set up file-based logging alongside the output workbook ──
        output_dir = os.path.dirname(os.path.abspath(output_xlsx)) or "."
        run_id = new_run_id()
        file_logger = setup_run_logger(output_dir, run_id)

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

        # --- SESSION PERSISTENCE ---
        _session_inputs = session_inputs or {
            'source_folder': source_folder,
            'output_xlsx': output_xlsx,
        }
        session_data = SessionManager.start_session(MODULE_ID, _session_inputs, total_items=total)
        completed_files = set(session_data.get('completed_item_ids', []))
        if completed_files:
            _log('info', f"[SESSION] Resuming — {len(completed_files)} of {total} PDF(s) already processed, will be skipped.")

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

            # --- SESSION: skip already-processed PDFs ---
            if pdf_path.name in completed_files:
                _log('info', f"[SESSION] Skipping {pdf_path.name} — already processed in a prior run.")
                if on_progress:
                    on_progress({'type': 'item_done', 'completed': idx + 1})
                continue

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
                # Mark file as processed so a resume skips it next time.
                SessionManager.update_item_status(MODULE_ID, pdf_path.name, success=True)

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
                SessionManager.update_item_status(MODULE_ID, pdf_path.name, success=False)

            if on_progress:
                on_progress({'type': 'item_done', 'completed': idx + 1})
        else:
            # for/else: loop completed without a break (no abort) — clear session.
            SessionManager.clear_session(MODULE_ID)
            _log('info', "[SESSION] All PDFs processed — session cleared.")

        # ── Write execution log CSV ──
        if exec_log_rows:
            csv_path = write_execution_log_csv(output_dir, exec_log_rows, run_id)
            _log("info", f"Execution log written: {csv_path}")

        if not all_rows:
            msg = (
                "No data extracted from any PDF in the selected folder. "
                "Check the config's record_separator and field patterns against the source PDFs."
            )
            _log("error", msg)
            raise RuntimeError(msg)

        df = pd.DataFrame(all_rows)

        configured_output = config.get("output_columns") or []
        if configured_output:
            final_cols = list(configured_output)
            # Keep these metadata columns after the configured output columns.
            for meta_col in (name_col, addr_col, 'source_file', 'page_number'):
                if meta_col not in final_cols and meta_col in df.columns:
                    final_cols.append(meta_col)
        else:
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

        # Guarantee configured output columns exist even when a field was not
        # found in a particular document.
        for col in configured_output:
            if col not in df.columns:
                df[col] = "N/A"

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
    """Path to the most recent run's debug log in *output_dir* (each run now
    writes a distinct pdf_extractor_debug_<run_id>.log file). Falls back to
    the old fixed-name file if no timestamped log exists yet."""
    return find_latest_run_log(output_dir, "pdf_extractor_debug", ".log") or \
        os.path.join(output_dir or ".", "pdf_extractor_debug.log")


def execution_log_path(output_dir: str) -> str:
    """Path to the most recent run's execution log CSV in *output_dir* (each
    run now writes a distinct pdf_extractor_execution_log_<run_id>.csv file).
    Falls back to the old fixed-name file if no timestamped log exists yet."""
    return find_latest_run_log(output_dir, "pdf_extractor_execution_log", ".csv") or \
        os.path.join(output_dir or ".", "pdf_extractor_execution_log.csv")


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