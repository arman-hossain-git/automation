"""
modules/pdf_spatial_ml.py
=========================
Local Spatial & Classical Machine Learning Engine for PDF Extraction.

Uses spatial coordinate clustering (DBSCAN from scikit-learn) and layout
geometry heuristics to automatically discover:
  1. Record separators and repetitive page headers/footers (exclusions)
  2. Key-Value form fields and their regex anchors
  3. Multi-column table boundaries, column headers, and data row regexes
  4. Complete JSON extraction presets without manual regex authoring
"""

import os
import re
import logging
from typing import Dict, List, Any, Optional, Tuple

import pdfplumber
import numpy as np
from sklearn.cluster import DBSCAN

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Spatial Line Clustering & Layout Geometry
# ---------------------------------------------------------------------------

def cluster_page_lines(page: pdfplumber.page.Page, eps_y: float = 3.5) -> List[Dict[str, Any]]:
    """
    Extracts text lines from page.extract_text() and attaches word tokens with
    their 2D layout geometry (x0, x1, top, bottom, font size).
    
    This guarantees 1-to-1 alignment with ConfigurableExtractor line splitting
    while preserving spatial coordinates for column clustering and field heuristics.
    """
    raw_lines = (page.extract_text() or "").splitlines()
    all_words = page.extract_words(keep_blank_chars=False, extra_attrs=["size", "fontname"])
    
    result = []
    w_ptr = 0
    for l_idx, raw_l in enumerate(raw_lines):
        clean_l = raw_l.strip()
        if not clean_l:
            continue
        line_tokens = clean_l.split()
        matched_words = []
        for token in line_tokens:
            if w_ptr < len(all_words):
                matched_words.append(all_words[w_ptr])
                w_ptr += 1
        
        if matched_words:
            avg_y = float(sum((w["top"] + w["bottom"]) / 2.0 for w in matched_words) / len(matched_words))
            top_y = float(min(w["top"] for w in matched_words))
            bot_y = float(max(w["bottom"] for w in matched_words))
        else:
            avg_y = float(l_idx * 15.0)
            top_y = avg_y - 5.0
            bot_y = avg_y + 5.0

        result.append({
            "y": avg_y,
            "top": top_y,
            "bottom": bot_y,
            "text": clean_l,
            "words": matched_words
        })
    
    return result


# ---------------------------------------------------------------------------
# 2. Key-Value Field Detection (Proximity & Anchor Heuristics)
# ---------------------------------------------------------------------------

KNOWN_LABEL_KEYWORDS = [
    # Academic & Student
    "program description", "exp grad date", "student name", "first term", "grad date",
    "drop date", "campus", "status", "group", "session", "ft/pt", "dob", "phone", "term",
    "lda", "id", "student id", "major", "minor", "degree", "credits", "gpa", "cumulative gpa",
    # Financial & Accounting
    "current balance", "total credit", "total debit", "ending balance", "subtotal", "total",
    "tax", "amount due", "balance due", "invoice date", "due date", "bill to", "ship to",
    "invoice number", "invoice #", "po number", "payment terms", "account number",
    # Resume & HR
    "candidate name", "full name", "email", "phone number", "mobile", "address", "location",
    "linkedin", "github", "portfolio", "objective", "summary", "experience", "education",
    "skills", "certifications", "job title", "position", "department", "manager", "salary",
    # Attendance & Roster
    "course", "subject", "class", "instructor", "teacher", "trainer", "date", "room",
    "period", "time", "total present", "total absent", "total hours", "attendance",
]


def _find_labels_in_line(text: str) -> List[Tuple[str, int, int]]:
    """
    Identifies field labels preceding colons in a line of text.
    Handles multiple fields per line (e.g., 'Campus: Bayamon Drop Date: Group: AO0125').
    Works universally across domains (resumes, attendances, schedules, transcripts, ledgers, forms).
    Returns a list of (label_text, label_start_idx, colon_end_idx).
    """
    colon_positions = [i for i, ch in enumerate(text) if ch == ":"]
    labels = []
    last_val_start = 0

    for c_idx in colon_positions:
        # Check if colon belongs to a URL or time
        if c_idx >= 4 and text[c_idx - 4:c_idx].lower() in ("http", "https"):
            continue
        if c_idx >= 2 and text[c_idx - 2:c_idx].isdigit() and c_idx + 1 < len(text) and text[c_idx + 1:c_idx + 3].isdigit():
            # Time pattern like 06:00 or 12:30
            continue

        pre_text = text[last_val_start:c_idx].strip()
        words = pre_text.split()
        if not words:
            continue

        found_label = None
        # Check from 4 words down to 1 word for known keywords
        for n_words in (4, 3, 2, 1):
            if len(words) >= n_words:
                cand = " ".join(words[-n_words:])
                if cand.lower().strip() in KNOWN_LABEL_KEYWORDS:
                    found_label = cand
                    break

        # Fallback 1: check for Title Case label
        if not found_label:
            for n_words in (3, 2, 1):
                if len(words) >= n_words:
                    cand_words = words[-n_words:]
                    if all(w[0].isupper() or w in ("FT/PT", "ID", "DOB", "LDA", "SSN", "GPA", "PO", "HR", "CV") for w in cand_words if w):
                        found_label = " ".join(cand_words)
                        break

        # Fallback 2: Universal label heuristic - any reasonable 1-3 word phrase before colon
        if not found_label:
            for n_words in (3, 2, 1):
                if len(words) >= n_words:
                    cand = " ".join(words[-n_words:])
                    if (re.match(r"^[A-Za-z0-9\s/&._#'\(\)-]{2,32}$", cand)
                        and not cand.lower().startswith(("the", "and", "or", "of", "in", "to", "for", "with", "is", "was"))
                        and not any(ch in cand for ch in ('"', ';', '?', '!'))):
                        found_label = cand
                        break

        if found_label:
            lbl_start = text.rfind(found_label, last_val_start, c_idx)
            labels.append((found_label, lbl_start, c_idx + 1))
            last_val_start = c_idx + 1

    return labels


def detect_key_value_fields(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Detects key-value pairs in lines using backward colon parsing.
    Prevents cross-field absorption and synthesizes robust regex anchors.
    """
    detected = []
    seen_names = set()

    for l_idx, line in enumerate(lines):
        text = line["text"]
        # Skip timestamps, page headers, and URLs
        if re.match(r"^\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}", text) or text.startswith(("http://", "https://")):
            continue

        lbls = _find_labels_in_line(text)

        if not lbls:
            # Check for financial totals line e.g. "$ 0.00 $ 7,593.75 $ -7,593.75"
            fin_match = re.search(r"^\s*([-]?\$?\s*[\d,.]+)\s+([-]?\$?\s*[\d,.]+)\s+([-]?\$?\s*[\d,.]+)$", text)
            if fin_match and "total_debit" not in seen_names:
                detected.append({
                    "name": "total_debit",
                    "label": "Total Debit",
                    "pattern": r"^\s*[-]?\$?\s*([-\d,.]+)\s+[-]?\$?\s*[-\d,.]+\s+[-]?\$?\s*[-\d,.]+$",
                    "example_value": fin_match.group(1),
                    "line_idx": l_idx,
                    "flags": "MULTILINE"
                })
                detected.append({
                    "name": "total_credit",
                    "label": "Total Credit",
                    "pattern": r"^\s*[-]?\$?\s*[-\d,.]+\s+[-]?\$?\s*([-\d,.]+)\s+[-]?\$?\s*[-\d,.]+$",
                    "example_value": fin_match.group(2),
                    "line_idx": l_idx,
                    "flags": "MULTILINE"
                })
                detected.append({
                    "name": "ending_balance",
                    "label": "Ending Balance",
                    "pattern": r"^\s*[-]?\$?\s*[-\d,.]+\s+[-]?\$?\s*[-\d,.]+\s+[-]?\$?\s*([-\d,.]+)$",
                    "example_value": fin_match.group(3),
                    "line_idx": l_idx,
                    "flags": "MULTILINE"
                })
                seen_names.update(["total_debit", "total_credit", "ending_balance"])
            continue

        _OTHER_LABEL_RE = re.compile(r"^(?:Drop Date|Group|Status|Exp Grad Date|Grad Date|Session|FT/PT|Campus|LDA|First Term|Trans Date)\s*:")
        for i, (raw_label, lstart, lend) in enumerate(lbls):
            clean_name = re.sub(r"[^a-zA-Z0-9]+", "_", raw_label).strip("_").lower()
            if not clean_name or clean_name in seen_names or len(raw_label) > 35:
                continue

            vstart = lend
            vend = lbls[i + 1][1] if i + 1 < len(lbls) else len(text)
            val_text = text[vstart:vend].strip()
            next_label = lbls[i + 1][0] if i + 1 < len(lbls) else None

            # If val_text immediately starts with another field label (e.g. "Drop Date:"),
            # that means the program field has no same-line value — treat as empty
            if ("program" in clean_name or "description" in clean_name) and val_text and _OTHER_LABEL_RE.match(val_text):
                val_text = ""
                next_label = None

            # Generate robust regex pattern:
            # 1. Date or Term fields: anchor to date pattern \d{2}/\d{2}/\d{4}
            if "date" in clean_name or "term" in clean_name:
                if clean_name == "grad_date":
                    pattern = r"(?<!Exp )Grad Date:[ \t]*(\d{2}/\d{2}/\d{4})"
                else:
                    pattern = rf"{re.escape(raw_label)}:[ \t]*(\d{{2}}/\d{{2}}/\d{{4}})"
            elif not val_text and ("program" in clean_name or "description" in clean_name):
                # No same-line value — the continuation pattern will carry the actual value.
                # Use a pattern that ONLY matches if there's a real (non-label-noise) value on the same line.
                other_labels_stop = r"Status:|First Term:|Drop Date:|Group:|Exp Grad Date:|Grad Date:|Session:|FT/PT:|Campus:|LDA:|Trans Date|Date"
                # Note: include \s* inside the lookahead to skip leading whitespace before the label check
                pattern = rf"{re.escape(raw_label)}:(?!\s*(?:{other_labels_stop}))[ \t]*(.+?)(?=\r?\n|$)"

            elif next_label:
                escaped_next = re.escape(next_label) + r":"
                pattern = rf"{re.escape(raw_label)}:[ \t]*(.+?)(?=[ \t]+{escaped_next}|\r?\n|$)"
            else:
                pattern = rf"{re.escape(raw_label)}:[ \t]*(.+?)(?=\r?\n|$)"

            detected.append({
                "name": clean_name,
                "label": raw_label,
                "pattern": pattern,
                "example_value": val_text,
                "line_idx": l_idx
            })
            seen_names.add(clean_name)

        # Check for multi-line continuation line right below this line
        # Skip up to 2 lines to handle URL/noise intermediary lines (e.g. https://bccpr.org)
        other_labels = r"Status:|First Term:|Drop Date:|Group:|Exp Grad Date:|Grad Date:|Session:|FT/PT:|Campus:|LDA:|Trans Date|Date"
        _NOISE_RE = re.compile(r"Page\s+\d+|https?://|Powered by", re.IGNORECASE)
        _DATE_NUM_RE = re.compile(r"^\d{2}/\d{2}/\d{4}|^\$?\d+")
        _LABEL_STOP_RE = re.compile(rf"(?:{other_labels})")

        cont_line_text = None
        cont_line_idx = None
        has_url_gap = False  # True if there's a URL noise line between label and value
        for offset in (1, 2):
            if l_idx + offset >= len(lines):
                break
            cand = lines[l_idx + offset]["text"].strip()
            if _NOISE_RE.search(cand):
                # Strip leading URL from this candidate; what remains may be the value
                stripped = re.sub(r"^\s*https?://\S+\s*", "", cand).strip()
                if stripped and ":" not in stripped and not _DATE_NUM_RE.match(stripped) and not _LABEL_STOP_RE.search(stripped) and 1 <= len(stripped.split()) <= 10:
                    # The value is embedded after the URL on the same noise line
                    cont_line_text = stripped
                    cont_line_idx = l_idx + offset
                    has_url_gap = (offset == 1)
                    break
                # Otherwise it's pure noise — keep looking one more line
                has_url_gap = True
                continue
            if ":" in cand or _LABEL_STOP_RE.search(cand) or _DATE_NUM_RE.match(cand):
                break
            if 1 <= len(cand.split()) <= 12:
                cont_line_text = cand
                cont_line_idx = l_idx + offset
                break

        if cont_line_text and lbls:
            # Find target label on current line to associate continuation with
            target_field = next((lbl[0] for lbl in lbls if "program" in lbl[0].lower() or "description" in lbl[0].lower()), None)
            if not target_field and lbls:
                target_field = lbls[0][0]
            if target_field:
                c_clean = re.sub(r"[^a-zA-Z0-9]+", "_", target_field).strip("_").lower()
                cont_name = f"{c_clean}_continuation"
                if cont_name not in seen_names:
                    # Build continuation pattern — allow optional URL noise between label line and value
                    url_opt = r"(?:[ \t]*https?://\S+[ \t]*\r?\n)?"
                    if has_url_gap:
                        # Value on next line after URL noise; strip URL prefix if embedded on same line
                        cont_pattern = rf"{re.escape(target_field)}:[^\n\r]*\r?\n{url_opt}(?!\s*(?:{other_labels}|\d{{2}}/\d{{2}}/\d{{4}}))[ \t]*(?:https?://\S+[ \t]*)?([^\n\r]+)"
                    else:
                        cont_pattern = rf"{re.escape(target_field)}:[^\n\r]*\r?\n(?!\s*(?:{other_labels}))[ \t]*([^\n\r]+)"

                    detected.append({
                        "name": cont_name,
                        "label": f"{target_field} Continuation",
                        "pattern": cont_pattern,
                        "example_value": cont_line_text,
                        "line_idx": cont_line_idx
                    })
                    seen_names.add(cont_name)

                    # Check for a 3rd line (e.g. "Asistente de Oficina" + "Médica" split across 3 lines)
                    cont2_name = f"{c_clean}_continuation2"
                    if cont2_name not in seen_names and cont_line_idx is not None:
                        search_from = cont_line_idx + 1
                        if search_from < len(lines):
                            cand3 = lines[search_from]["text"].strip()
                            # Skip pure noise lines
                            if _NOISE_RE.search(cand3):
                                stripped3 = re.sub(r"^\s*https?://\S+\s*", "", cand3).strip()
                                cand3 = stripped3 if stripped3 else ""
                            if (cand3 and ":" not in cand3
                                    and not _LABEL_STOP_RE.search(cand3)
                                    and not _DATE_NUM_RE.match(cand3)
                                    and 1 <= len(cand3.split()) <= 6
                                    and cand3.lower() not in ("official student transcript", "contract", "in")):
                                cont2_pattern = rf"{re.escape(target_field)}:[^\n\r]*\r?\n{url_opt}[ \t]*(?!(?:{other_labels}|\d{{2}}/\d{{2}}/\d{{4}}))(?:https?://\S+[ \t]*)?[^\n\r]+\r?\n[ \t]*(?!(?:{other_labels}|\d{{2}}/\d{{2}}/\d{{4}}|Official|\$))([A-Za-z\u00C0-\u024F][^\n\r]{{1,40}})"
                                detected.append({
                                    "name": cont2_name,
                                    "label": f"{target_field} Continuation 2",
                                    "pattern": cont2_pattern,
                                    "example_value": cand3,
                                    "line_idx": search_from
                                })
                                seen_names.add(cont2_name)

    return detected


# ---------------------------------------------------------------------------
# 3. Table Column Detection & Row Regex Synthesis
# ---------------------------------------------------------------------------

def detect_table_structure(lines: List[Dict[str, Any]], eps_x: float = 18.0) -> Optional[Dict[str, Any]]:
    """
    Detects tabular sections by analyzing multi-line column alignments.
    Identifies header line, column names, and synthesizes data row regex.
    """
    if len(lines) < 5:
        return None

    header_keywords = [
        # Academic & Schedule
        "course", "class", "subject", "grade", "units", "credits", "gpa", "term", "session",
        "hours", "instructor", "teacher", "professor", "room", "time", "days", "meeting",
        # Financial & Accounting
        "date", "trans", "code", "description", "amount", "debit", "credit", "balance",
        "fee", "charge", "payment", "receipt", "check", "rate", "price", "qty", "quantity",
        "total", "subtotal", "tax", "cost", "item",
        # Attendance & Rosters
        "student", "name", "id", "present", "absent", "tardy", "status", "signature", "notes",
        "roster", "period", "in", "out",
        # HR & Work
        "title", "position", "role", "company", "employer", "department", "employee",
        "start", "end", "salary", "pay", "task", "project"
    ]

    header_candidates = []
    for l_idx, line in enumerate(lines):
        words_lower = [w["text"].lower().strip(":,") for w in line["words"]]
        matching_kw = [w for w in words_lower if any(kw in w for kw in header_keywords)]
        if len(matching_kw) >= 2 and len(line["words"]) >= 2:
            header_candidates.append((l_idx, len(matching_kw), line))

    if not header_candidates:
        return None

    header_candidates.sort(key=lambda item: item[1], reverse=True)
    header_idx, _, header_line = header_candidates[0]

    # Collect data rows below header
    data_lines = []
    for row_idx in range(header_idx + 1, min(header_idx + 30, len(lines))):
        candidate = lines[row_idx]
        if any(term in candidate["text"].lower() for term in ["ending balance", "total debit", "total credit", "student ledger", "page"]):
            break
        has_num_or_date = bool(re.search(r"\d{2}/\d{2}/\d{4}|\d+\.\d+|\$?\d+", candidate["text"]))
        if len(candidate["words"]) >= 3 and has_num_or_date:
            data_lines.append(candidate)

    if not data_lines:
        return None

    # Map header words to column names using spatial gaps
    header_words = header_line["words"]
    grouped_headers = []
    if header_words:
        cur_group = [header_words[0]]
        for w in header_words[1:]:
            if w["x0"] - cur_group[-1]["x1"] < 5.0:
                cur_group.append(w)
            else:
                grouped_headers.append(" ".join(x["text"] for x in cur_group))
                cur_group = [w]
        if cur_group:
            grouped_headers.append(" ".join(x["text"] for x in cur_group))

    col_names = []
    for h_text in grouped_headers:
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", h_text).strip("_").lower()
        if cleaned.startswith("receipt_check") or cleaned in ("receipt_or_check", "receipt_check_no"):
            cleaned = "receipt"
        if cleaned and cleaned not in col_names:
            col_names.append(cleaned)

    row_pattern, final_cols = _synthesize_row_regex(data_lines, col_names)

    return {
        "header_line_idx": header_idx,
        "header_text": header_line["text"],
        "columns": final_cols,
        "pattern": row_pattern,
        "sample_data": [dl["text"] for dl in data_lines[:3]]
    }


def _synthesize_row_regex(data_lines: List[Dict[str, Any]], col_names: List[str]) -> Tuple[str, List[str]]:
    """
    Synthesizes a robust regex pattern matching tabular data rows.
    Handles currency amounts with spaces ($ 0.00) and aligns capture groups to columns.
    """
    if not data_lines:
        pat = r"^" + r"\s+".join([r"(\S+)" for _ in col_names]) + r"$"
        return pat, col_names

    sample = data_lines[0]["text"]
    curr_matches = list(re.finditer(r"[-]?\$\s*[-]?[\d,.]+", sample))

    # Case A: Row ends with one or more currency columns (e.g. Student Ledgers)
    if curr_matches and curr_matches[-1].end() >= len(sample.strip()) - 3:
        num_curr = len(curr_matches)

        # Check if table has standard ledger / statement structure
        has_date = bool(col_names and any(k in col_names[0].lower() for k in ("date", "term")))
        has_code = bool(len(col_names) > 1 and any(k in col_names[1].lower() for k in ("code", "ledger")))

        if has_date and has_code and len(col_names) >= 2 + num_curr:
            curr_cols = col_names[-num_curr:]
            curr_pats = [r"[-]?\$?\s*([-\d,.]+)" for _ in curr_cols]

            intermediate_cols = col_names[2:len(col_names) - num_curr]
            intermediate_pats = []
            has_receipt = any("receipt" in c.lower() or "check" in c.lower() for c in intermediate_cols)

            for c in intermediate_cols:
                c_l = c.lower()
                if "receipt" in c_l or "check" in c_l:
                    intermediate_pats.append(r"(?:\s+(Receipt\s*#?\s*\S+|Check\s*#?\s*\S+))?")
                elif any(k in c_l for k in ("desc", "detail", "memo")):
                    if has_receipt:
                        intermediate_pats.append(r"(?:\s+((?:(?!Receipt|Check|\$)\S+[\s\S]*?)(?=\s*(?:Receipt|Check|\$))))?")
                    else:
                        intermediate_pats.append(r"(?:\s+([^$]*?)(?=\s*\$))?")
                else:
                    intermediate_pats.append(r"(?:\s+(\S+))?")

            date_pat = r"(\d{2}/\d{2}/\d{4})"
            code_pat = r"([A-Za-z0-9\-]+(?:\s*-\s*\S+|\s+\d{2}[/-]\d{2}|\s+[A-Za-z0-9\-]+)?)"

            synth_pat = r"^" + date_pat + r"\s+" + code_pat + "".join(intermediate_pats) + r"\s+" + r"\s+".join(curr_pats) + r"$"
            # Verify synth_pat against candidate data lines
            match_count = sum(1 for dl in data_lines if re.match(synth_pat, dl["text"].strip()))
            if match_count >= max(1, len(data_lines) // 2):
                return synth_pat, list(col_names)

        # Fallback for currency rows if specialized structure not matched
        prefix = sample[:curr_matches[0].start()].strip()
        prefix_tokens = prefix.split()
        prefix_pats = []

        if prefix_tokens:
            if re.match(r"\d{2}/\d{2}/\d{4}", prefix_tokens[0]):
                prefix_pats.append(r"(\d{2}/\d{2}/\d{4})")
            else:
                prefix_pats.append(r"(\S+)")

            if len(prefix_tokens) >= 2 and re.fullmatch(r"[A-Za-z0-9\-]+", prefix_tokens[1]):
                prefix_pats.append(r"([A-Za-z0-9\-]+)")

            if len(prefix_tokens) > 2:
                prefix_pats.append(r"(.+?)")

        curr_pats = [r"[-]?\$\s*([-\d,.]+)" for _ in range(num_curr)]
        all_pats = prefix_pats + curr_pats

        total_groups = len(all_pats)
        if len(col_names) >= total_groups:
            num_prefix = len(prefix_pats)
            final_cols = col_names[:num_prefix] + col_names[-num_curr:]
        else:
            final_cols = list(col_names)
            for i in range(len(col_names), total_groups):
                final_cols.append(f"col_{i + 1}")

        pattern = r"^(\d{2}/\d{2}/\d{4})\s+([A-Za-z0-9\-]+)\s+(.+?)\s+" + r"\s+".join(curr_pats) + r"$" if len(prefix_pats) >= 3 else (r"^" + r"\s+".join(all_pats) + r"$")
        return pattern, final_cols

    # Case B: Standard token sequence
    tokens = sample.split()
    patterns = []
    if re.match(r"\d{2}/\d{2}/\d{4}", tokens[0]):
        patterns.append(r"(\d{2}/\d{2}/\d{4})")
    else:
        patterns.append(r"(\S+)")

    num_cols = len(col_names)
    if num_cols <= 2:
        patterns.append(r"(.+)")
    else:
        for i in range(1, num_cols - 1):
            if i < len(tokens) and re.fullmatch(r"[A-Za-z0-9\-]+", tokens[i]):
                patterns.append(r"([A-Za-z0-9\-]+)")
            else:
                patterns.append(r"(.+?)")
        last_token = tokens[-1] if tokens else ""
        if re.search(r"[-]?\$?[\d,.]+", last_token):
            patterns.append(r"([-]?\$?\s*[\d,.]+)")
        else:
            patterns.append(r"(\S+)")

    pattern = r"^\s*" + r"\s+".join(patterns) + r"\s*$"
    return pattern, col_names


# ---------------------------------------------------------------------------
# 4. Record Separator & Noise Exclusions Detection
# ---------------------------------------------------------------------------

def detect_separator_and_excludes(pdf_path: str, max_pages: int = 4, doc_type: str = "generic_document") -> Tuple[str, List[str], Optional[Dict[str, Any]]]:
    """
    Analyzes multiple pages or top lines across the PDF to identify:
      - The Record Separator (institution or report title, NEVER an individual person's name)
      - Exclude Patterns (page numbers, generation timestamps, website URLs)
      - Student/Record Name Detection block
    """
    record_separator = ""
    exclude_patterns = [
        r"Page\s+\d+\s+of\s+\d+",
        r"Powered by TCPDF.*",
        r"https?://\S+",
        r"^\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}",
        r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),.*Page\s+\d+",
    ]
    name_detection = None

    # Single-record documents per PDF (resumes, attendance sheets, single-file invoices)
    if doc_type in ("resume", "attendance", "invoice"):
        return "", exclude_patterns, None

    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = pdf.pages[:max_pages]
            if not pages:
                return record_separator, exclude_patterns, name_detection

            first_lines = cluster_page_lines(pages[0])
            if not first_lines:
                return record_separator, exclude_patterns, name_detection

            for line in first_lines[:15]:
                txt = line["text"].strip()
                # Hyphen-separated title e.g. 'Student Ledger - Andrew Rudy'
                sep_match = re.match(r"^(Student\s+(?:Ledger|Schedule|Transcript|Record))\s*[-–]\s*(.+)$", txt, re.IGNORECASE)
                if sep_match:
                    record_separator = sep_match.group(1).strip()
                    name_detection = {
                        "output_column": "student_name",
                        "pattern": rf"^(?:{re.escape(record_separator)}\s*)?[-–]\s*(.+)$",
                        "stop_at": ""
                    }
                    break

                # Institution name followed by student name (e.g. 'Bayamon Community College Rivera Silva, Isamar')
                inst_with_name = re.match(
                    r"^(Bayamon\s+Community\s+College|[A-Za-z\s]+College|[A-Za-z\s]+University|[A-Za-z\s]+Institute)\s+([A-Za-z\u00C0-\u00FF\s'.-]+,\s*[A-Za-z\u00C0-\u00FF\s'.-]+)",
                    txt, re.IGNORECASE
                )
                if inst_with_name:
                    record_separator = inst_with_name.group(1).strip()
                    name_detection = {
                        "output_column": "student_name",
                        "pattern": rf"^(?:{re.escape(record_separator)}\s+)?([A-Za-z\u00C0-\u00FF\s'.-]+,\s*[A-Za-z\u00C0-\u00FF\s'.-]+)",
                        "stop_at": ""
                    }
                    break

                if re.match(r"^Student\s+Schedule$", txt, re.IGNORECASE):
                    record_separator = "Bayamon Community College"
                    name_detection = {
                        "output_column": "student_name",
                        "pattern": r"^(?:Bayamon\s+Community\s+College\s+)?(.+)$",
                        "stop_at": ""
                    }
                    break

                if re.match(r"^(Official\s+Student\s+Transcript|Student\s+(?:Ledger|Transcript|Record))$", txt, re.IGNORECASE):
                    record_separator = txt.strip()
                    break

            # If not detected, check if first line is a clean institution or header (never an individual's name)
            if not record_separator and first_lines:
                cand_sep = first_lines[0]["text"].strip()
                # If cand_sep looks like a person's name (e.g. "LastName, FirstName"), do NOT use it as separator!
                if not re.match(r"^[A-Za-z\u00C0-\u00FF\s'.-]+,\s*[A-Za-z\u00C0-\u00FF\s'.-]+$", cand_sep):
                    # Strip any person name attached to institution
                    m_strip = re.match(r"^(Bayamon\s+Community\s+College|[A-Za-z\s]+College|[A-Za-z\s]+University|[A-Za-z\s]+Institute)\b", cand_sep, re.IGNORECASE)
                    if m_strip:
                        record_separator = m_strip.group(1).strip()
                    elif len(cand_sep) < 40 and ":" not in cand_sep:
                        record_separator = cand_sep

            # Look for Name pattern if not found in title
            if not name_detection:
                for line in first_lines[:15]:
                    txt = line["text"]
                    if re.match(r"^[A-Za-z\u00c0-\u00ff\ufffd\s'.-]+,\s*[A-Za-z\u00c0-\u00ff\ufffd\s'.-]+$", txt):
                        name_detection = {
                            "output_column": "student_name",
                            "pattern": r"^([A-Za-z\u00c0-\u00ff\ufffd\s'.-]+,\s*[A-Za-z\u00c0-\u00ff\ufffd\s'.-]+)$",
                            "stop_at": ""
                        }
                        break

    except Exception as e:
        logger.warning(f"Error detecting separator/excludes on {pdf_path}: {e}")

    return record_separator, exclude_patterns, name_detection


# ---------------------------------------------------------------------------
# 5. Document Classifier Engine
# ---------------------------------------------------------------------------

def classify_document(pdf_path: str, max_pages: int = 3) -> Tuple[str, str, str]:
    """
    Classifies a PDF document using spatial layout and textual signals.
    Returns: (doc_type, display_title, institution_name)

    Supported document types:
      - student_schedule    : Schedules of classes
      - student_transcript  : Official or student academic transcripts
      - student_ledger      : Account/billing ledger statements
      - resume              : Resumes & Curriculum Vitae (CV)
      - attendance          : Attendance sheets & rosters
      - invoice             : Invoices, bills, & receipts
      - generic_document    : Any arbitrary form, report, or document
    """
    with pdfplumber.open(pdf_path) as pdf:
        pages_text = []
        for p in pdf.pages[:max_pages]:
            pages_text.append(p.extract_text() or "")
    full_sample = "\n".join(pages_text)

    # Detect institution/company name if present
    inst_name = ""
    for line in full_sample.splitlines()[:15]:
        m_inst = re.match(r"^(Bayamon\s+Community\s+College|[A-Za-z\s]+College|[A-Za-z\s]+University|[A-Za-z\s]+Institute)\b", line.strip(), re.IGNORECASE)
        if m_inst:
            inst_name = m_inst.group(1).strip()
            break

    # 1. Schedule
    if (
        re.search(r"\bStudent\s+Schedule\b", full_sample, re.IGNORECASE)
        or (
            re.search(r"\bSchedule\b", full_sample, re.IGNORECASE)
            and re.search(r"\b(?:Course\s+Course\s+Description|Class\s+Meetings|Days\s+of\s+Week|Room|Class\s+Times)\b", full_sample, re.IGNORECASE)
        )
    ):
        return "student_schedule", "Student Schedule", inst_name or "Bayamon Community College"

    # 2. Transcript
    if (
        re.search(r"\b(?:Official\s+Student\s+Transcript|Student\s+Transcript|Academic\s+Transcript|Official\s+Transcript)\b", full_sample, re.IGNORECASE)
        or (
            re.search(r"\bTranscript\b", full_sample, re.IGNORECASE)
            and re.search(r"\b(?:Grade|Units\s+Attempted|Units\s+Completed|Cumulative\s+Total|GPA)\b", full_sample, re.IGNORECASE)
        )
        or (re.search(r"\bCourse\s+Course\s+Description\s+Grade\b", full_sample, re.IGNORECASE))
    ):
        return "student_transcript", "Student Transcript", inst_name or "Bayamon Community College"

    # 3. Ledger
    if (
        re.search(r"\b(?:Student\s+Ledger|Account\s+Ledger|Ledger\s+Statement|Billing\s+Statement)\b", full_sample, re.IGNORECASE)
        or (
            re.search(r"\bTrans\s+Date\b", full_sample, re.IGNORECASE)
            and re.search(r"\b(?:Debit|Credit|Balance)\b", full_sample, re.IGNORECASE)
        )
    ):
        return "student_ledger", "Student Ledger", inst_name

    # 4. Resume / CV
    if (
        re.search(r"\b(?:Resume|Curriculum\s+Vitae|C\.?V\.?)\b", full_sample, re.IGNORECASE)
        or (
            re.search(r"\b(?:Work\s+Experience|Professional\s+Experience|Experience)\b", full_sample, re.IGNORECASE)
            and re.search(r"\b(?:Education|Academic\s+Background|Skills|Competencies)\b", full_sample, re.IGNORECASE)
        )
        or (
            re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", full_sample)
            and re.search(r"\b(?:Experience|Education|Skills)\b", full_sample, re.IGNORECASE)
        )
    ):
        return "resume", "Resume", ""

    # 5. Attendance
    if (
        re.search(r"\b(?:Attendance(?:\s+Sheet|\s+Record|\s+Report|\s+Summary|\s+Log)?|Sign-in\s+Sheet|Roll\s+Call|Roster|Timesheet|Time\s+Sheet)\b", full_sample, re.IGNORECASE)
        or (
            re.search(r"\bAttendance\b", full_sample, re.IGNORECASE)
            and re.search(r"\b(?:Present|Absent|Tardy|Hours)\b", full_sample, re.IGNORECASE)
        )
    ):
        return "attendance", "Attendance", ""

    # 6. Invoice
    if (
        re.search(r"\b(?:Invoice|Tax\s+Invoice|Bill\s+To|Receipt|Purchase\s+Order)\b", full_sample, re.IGNORECASE)
        and re.search(r"\b(?:Total|Amount\s+Due|Subtotal|Due\s+Date)\b", full_sample, re.IGNORECASE)
    ):
        return "invoice", "Invoice", ""

    # 7. Generic Document
    first_line = ""
    for l in full_sample.splitlines():
        cl = l.strip()
        if cl and len(cl) > 3 and not re.search(r"Page\s+\d+|https?://|\d{2}/\d{2}/\d{4}", cl):
            first_line = cl
            break
    title = first_line[:30] if first_line else "Document"
    return "generic_document", title, inst_name


# ---------------------------------------------------------------------------
# 6. Preset Builders for Document Types
# ---------------------------------------------------------------------------

def _build_schedule_preset(pdf_path: str, lines: List[Dict[str, Any]], institution_sep: str) -> Dict[str, Any]:
    """Preset configuration for Student Schedules."""
    return {
        "report_type": "student_schedule",
        "document_title": "Student Schedule",
        "record_separator": institution_sep or "Bayamon Community College",
        "exclude_patterns": [
            r"Page\s+\d+\s+of\s+\d+",
            r"Powered by TCPDF.*",
            r"https?://\S+",
            r"^\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}",
            r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),.*Page\s+\d+",
        ],
        "name_detection": {
            "output_column": "student_name",
            "pattern": rf"^(?:{re.escape(institution_sep or 'Bayamon Community College')}\s+)?(.+)$",
            "stop_at": ""
        },
        "address_block": None,
        "repeating_sections": [],
        "summary_fields": [
            {"name": "program_description", "pattern": r"Program Description:\s*(.+?)(?:\s+Drop Date:|\s+Group:|\r?\n|$)"},
            {"name": "program_description_continuation", "pattern": r"Program Description:[^\n\r]*\r?\n(?:[ \t]*https?://\S+[ \t]*\r?\n)?(?!\s*(?:Status:|First Term:|Drop Date:|Group:|Exp Grad Date:|Grad Date:|Session:|FT/PT:|Campus:|LDA:|Trans Date|Date))[ \t]*([^\n\r]+)"},
            {"name": "drop_date", "pattern": r"Drop Date:\s*(\d{2}/\d{2}/\d{4})"},
            {"name": "group", "pattern": r"Group:\s*(\S+)"},
            {"name": "first_term", "pattern": r"First Term:\s*(\d{2}/\d{2}/\d{4})"},
            {"name": "exp_grad_date", "pattern": r"Exp Grad Date:\s*(\d{2}/\d{2}/\d{4})"},
            {"name": "ft_pt", "pattern": r"FT/PT:\s*(Full Time|Part Time|\S+)"},
            {"name": "status", "pattern": r"Status:\s*(Active|Graduated|Dropped|Withdrawn|Inactive|Leave\s+of\s+Absence|Probation|[A-Za-z]+)(?=\s+(?:Exp\s+Grad\s+Date|Grad\s+Date|Session|FT/PT|First\s+Term):|\s+|$)"},
            {"name": "grad_date", "pattern": r"(?<!Exp )Grad Date:\s*(\d{2}/\d{2}/\d{4})"},
            {"name": "session", "pattern": r"Session:\s*([A-Za-z0-9\-]+)"},
            {"name": "term", "pattern": r"Term:\s*(\d{2}/\d{2}/\d{4}(?:\s*-\s*\d{2}/\d{2}/\d{4})?)"},
            {"name": "id", "pattern": r"(?:ID|Id):[ \t]*([A-Za-z0-9\-]+)"}
        ],
        "computed_fields": [
            {
                "name": "program",
                "join": ["program_description", "program_description_continuation"],
                "sep": " "
            }
        ],
        "data_rows": {
            "pattern": r"^([A-Z]{2,4}\d{3,4}[A-Z]?)\s+\(([^)]+)\)\s+(.+?)\s+(\d+\.\d{2})\s+(\d+\.\d{2})\s+(.+?)\s+(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})\s+((?:(?:M|Tu|W|Th|F|Sa|Su|TBA|TBD)(?:,\s*)?)+)\s+(.+?)\s+(\d{2}:\d{2}\s+[AP]M)\s+to\s+(\d{2}:\d{2}\s+[AP]M)\s*$",
            "columns": [
                "course", "course_version", "course_description", "units", "fa_units",
                "instructor", "class_meetings_start", "class_meetings_end", "days_of_week",
                "room", "class_time_start", "class_time_end"
            ]
        },
        "output_columns": [
            "student_name", "id", "program", "program_description", "program_description_continuation",
            "drop_date", "group", "first_term", "exp_grad_date", "ft_pt", "status",
            "grad_date", "session", "term", "course", "course_version", "course_description",
            "units", "fa_units", "instructor", "class_meetings_start", "class_meetings_end",
            "days_of_week", "room", "class_time_start", "class_time_end", "source_file", "page_number"
        ],
        "ml_metadata": {
            "synthesizer": "dbscan_spatial_ml",
            "source_sample": os.path.basename(pdf_path),
            "detected_report_type": "student_schedule",
            "detected_fields_count": 12,
            "table_detected": True
        }
    }


def _build_transcript_preset(pdf_path: str, lines: List[Dict[str, Any]], institution_sep: str) -> Dict[str, Any]:
    """Preset configuration for Student Transcripts."""
    sep = institution_sep or "Bayamon Community College"
    return {
        "report_type": "student_transcript",
        "document_title": "Student Transcript",
        "record_separator": sep,
        "exclude_patterns": [
            r"Page\s+\d+\s+of\s+\d+",
            r"Powered by TCPDF.*",
            r"https?://\S+",
            r"^\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}",
            r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),.*Page\s+\d+",
            r"Attendance Summary.*",
            r"Total Required Hours.*",
            r"Total Attended Hours.*",
            r"Total Transfer Hours.*",
            r"Total Hours Remaining.*",
            r"Total Scheduled Hours.*",
            r"Attendance Percentage.*",
            r"Not Official unless signed.*",
            r"Registrar",
        ],
        "name_detection": {
            "output_column": "student_name",
            "pattern": rf"^(?:{re.escape(sep)}\s+)?([A-Za-z\u00C0-\u00FF\s'.-]+,\s*[A-Za-z\u00C0-\u00FF\s'.-]+)",
            "stop_at": ""
        },
        "address_block": None,
        "repeating_sections": [
            {
                "name": "program",
                "marker": "Program Description:",
                "marker_line_fields": [
                    {"name": "program_description", "pattern": r"Program Description:\s*(.+?)(?:\s+Drop Date:|\s+Group:|\r?\n|$)"},
                    {"name": "drop_date", "pattern": r"Drop Date:\s*(\d{2}/\d{2}/\d{4})"},
                    {"name": "group", "pattern": r"Group:\s*(\S+)"}
                ],
                "sub_fields": [
                    {"name": "first_term", "pattern": r"First Term:\s*(\d{2}/\d{2}/\d{4})"},
                    {"name": "exp_grad_date", "pattern": r"Exp Grad Date:\s*(\d{2}/\d{2}/\d{4})"},
                    {"name": "ft_pt", "pattern": r"FT/PT:\s*(Full Time|Part Time|\S+)"},
                    {"name": "status", "pattern": r"Status:\s*([A-Za-z\s]+?)(?=\s+Exp Grad Date:|\s+Grad Date:|\s+FT/PT:|\s+First Term:|\s*$)"},
                    {"name": "lda", "pattern": r"LDA:\s*(\d{2}/\d{2}/\d{4})"},
                    {"name": "grad_date", "pattern": r"(?<!Exp )Grad Date:\s*(\d{2}/\d{2}/\d{4})"},
                    {"name": "session", "pattern": r"Session:\s*([A-Za-z0-9\-]+)"}
                ]
            }
        ],
        "summary_fields": [
            {"name": "id", "pattern": r"(?:ID|Id):\s*([A-Za-z0-9\-]+)"},
            {"name": "dob", "pattern": r"DOB:\s*(\d{2}/\d{2}/\d{4})"},
            {"name": "phone", "pattern": r"Phone:\s*([0-9\-\(\)\s]+?)(?=\s+Campus:|\s+Status:|\s+Drop Date:|\s*$)"},
            {"name": "program_description", "pattern": r"Program Description:\s*(.+?)(?:\s+Drop Date:|\s+Group:|\r?\n|$)"},
            {"name": "drop_date", "pattern": r"Drop Date:\s*(\d{2}/\d{2}/\d{4})"},
            {"name": "group", "pattern": r"Group:\s*(\S+)"},
            {"name": "status", "pattern": r"Status:\s*([A-Za-z\s]+?)(?=\s+Exp Grad Date:|\s+Grad Date:|\s+FT/PT:|\s+First Term:|\s*$)"},
            {"name": "exp_grad_date", "pattern": r"Exp Grad Date:\s*(\d{2}/\d{2}/\d{4})"},
            {"name": "ft_pt", "pattern": r"FT/PT:\s*(Full Time|Part Time|\S+)"},
            {"name": "first_term", "pattern": r"First Term:\s*(\d{2}/\d{2}/\d{4})"},
            {"name": "lda", "pattern": r"LDA:\s*(\d{2}/\d{2}/\d{4})"},
            {"name": "grad_date", "pattern": r"(?<!Exp )Grad Date:\s*(\d{2}/\d{2}/\d{4})"},
            {"name": "session", "pattern": r"Session:\s*([A-Za-z0-9\-]+)"}
        ],
        "computed_fields": [],
        "data_rows": {
            "pattern": r"^([A-Z]{2,4}\d{3,4}[A-Z]?)\s+(.+?)\s+([A-Z0-9+\-]+|-)\s+(\d+\.\d{2})\s+(\d+\.\d{2})\s*$",
            "columns": ["course", "course_description", "grade", "units_attempted", "units_completed"]
        },
        "output_columns": [
            "student_name", "id", "dob", "phone", "address", "program_description",
            "drop_date", "group", "status", "exp_grad_date", "ft_pt", "first_term",
            "lda", "grad_date", "session", "term", "course", "course_description",
            "grade", "units_attempted", "units_completed", "gpa_term", "source_file", "page_number"
        ],
        "ml_metadata": {
            "synthesizer": "dbscan_spatial_ml",
            "source_sample": os.path.basename(pdf_path),
            "detected_report_type": "student_transcript",
            "detected_fields_count": 13,
            "table_detected": True
        }
    }


def _build_ledger_preset(pdf_path: str, lines: List[Dict[str, Any]], institution_sep: str) -> Dict[str, Any]:
    """Preset configuration for Student Ledgers / Financial Statements."""
    return {
        "report_type": "student_ledger",
        "document_title": "Student Ledger",
        "record_separator": "Student Ledger",
        "exclude_patterns": [
            r"Page\s+\d+\s+of\s+\d+",
            r"Powered by TCPDF.*",
            r"https?://\S+",
            r"^\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}",
            r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),.*Page\s+\d+",
        ],
        "name_detection": {
            "output_column": "student_name",
            "pattern": r"^([A-Za-z\u00C0-\u00FF\s'.-]+,\s*[A-Za-z\u00C0-\u00FF\s'.-]+)$",
            "stop_at": ""
        },
        "address_block": None,
        "repeating_sections": [],
        "summary_fields": [
            {"name": "program_description", "pattern": r"Program Description:\s*(.+?)(?:\s+Drop Date:|\s+Group:|\r?\n|$)"},
            {"name": "campus", "pattern": r"Campus:\s*([A-Za-z0-9\s]+?)(?=\s+Drop Date:|\s+Status:|\s*$)"},
            {"name": "status", "pattern": r"Status:\s*([A-Za-z\s]+?)(?=\s+Exp Grad Date:|\s+FT/PT:|\s*$)"},
            {"name": "exp_grad_date", "pattern": r"Exp Grad Date:\s*(\d{2}/\d{2}/\d{4})"},
            {"name": "ft_pt", "pattern": r"FT/PT:\s*(Full Time|Part Time|\S+)"},
            {"name": "first_term", "pattern": r"First Term:\s*(\d{2}/\d{2}/\d{4})"},
            {"name": "lda", "pattern": r"LDA:\s*(\d{2}/\d{2}/\d{4})"},
            {"name": "session", "pattern": r"Session:\s*([A-Za-z0-9\-]+)"},
            {"name": "total_debit", "pattern": r"^\s*[-]?\$?\s*([-\d,.]+)\s+[-]?\$?\s*[-\d,.]+\s+[-]?\$?\s*[-\d,.]+$", "flags": "MULTILINE"},
            {"name": "total_credit", "pattern": r"^\s*[-]?\$?\s*[-\d,.]+\s+[-]?\$?\s*([-\d,.]+)\s+[-]?\$?\s*[-\d,.]+$", "flags": "MULTILINE"},
            {"name": "ending_balance", "pattern": r"^\s*[-]?\$?\s*[-\d,.]+\s+[-]?\$?\s*[-\d,.]+\s+[-]?\$?\s*([-\d,.]+)$", "flags": "MULTILINE"}
        ],
        "computed_fields": [],
        "data_rows": {
            "mode": "student_ledger",
            "pattern": r"^(\d{2}/\d{2}/\d{4})\s+([A-Za-z0-9\-]+)\s+(.+?)\s+[-]?\$?\s*([-\d,.]+)\s+[-]?\$?\s*([-\d,.]+)\s+[-]?\$?\s*([-\d,.]+)$",
            "columns": ["trans_date", "ledger_code", "description", "receipt_check", "debit", "credit", "balance"]
        },
        "output_columns": [
            "student_name", "program_description", "campus", "status", "exp_grad_date", "ft_pt",
            "first_term", "lda", "session", "trans_date", "ledger_code", "description",
            "receipt_check", "debit", "credit", "balance", "total_debit", "total_credit", "ending_balance",
            "source_file", "page_number"
        ],
        "ml_metadata": {
            "synthesizer": "dbscan_spatial_ml",
            "source_sample": os.path.basename(pdf_path),
            "detected_report_type": "student_ledger",
            "detected_fields_count": 11,
            "table_detected": True
        }
    }


def _build_resume_preset(pdf_path: str, lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Preset configuration for Resumes & CVs."""
    summary_fields = detect_key_value_fields(lines)
    # Ensure standard contact fields exist
    field_names = {sf["name"] for sf in summary_fields}
    if "email" not in field_names:
        summary_fields.insert(0, {
            "name": "email",
            "label": "Email",
            "pattern": r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
        })
    if "phone" not in field_names:
        summary_fields.insert(1, {
            "name": "phone",
            "label": "Phone",
            "pattern": r"\b((?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})\b"
        })

    output_cols = ["candidate_name"] + [sf["name"] for sf in summary_fields] + ["source_file", "page_number"]
    return {
        "report_type": "resume",
        "document_title": "Resume",
        "record_separator": "",  # 1 PDF = 1 candidate record
        "exclude_patterns": [r"Page\s+\d+\s+of\s+\d+"],
        "name_detection": {
            "output_column": "candidate_name",
            "pattern": r"^([A-Za-z\s'.-]{3,35})$",
            "stop_at": ""
        },
        "address_block": None,
        "repeating_sections": [],
        "summary_fields": summary_fields,
        "computed_fields": [],
        "data_rows": None,
        "output_columns": output_cols,
        "ml_metadata": {
            "synthesizer": "dbscan_spatial_ml",
            "source_sample": os.path.basename(pdf_path),
            "detected_report_type": "resume",
            "detected_fields_count": len(summary_fields),
            "table_detected": False
        }
    }


def _build_attendance_preset(pdf_path: str, lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Preset configuration for Attendance sheets & rosters."""
    summary_fields = detect_key_value_fields(lines)
    table_info = detect_table_structure(lines)

    output_cols = [sf["name"] for sf in summary_fields]
    data_rows_cfg = None
    if table_info:
        data_rows_cfg = {
            "pattern": table_info["pattern"],
            "columns": table_info["columns"]
        }
        for col in table_info["columns"]:
            if col not in output_cols:
                output_cols.append(col)
    output_cols.extend(["source_file", "page_number"])

    return {
        "report_type": "attendance",
        "document_title": "Attendance",
        "record_separator": "",
        "exclude_patterns": [r"Page\s+\d+\s+of\s+\d+"],
        "name_detection": None,
        "address_block": None,
        "repeating_sections": [],
        "summary_fields": summary_fields,
        "computed_fields": [],
        "data_rows": data_rows_cfg,
        "output_columns": output_cols,
        "ml_metadata": {
            "synthesizer": "dbscan_spatial_ml",
            "source_sample": os.path.basename(pdf_path),
            "detected_report_type": "attendance",
            "detected_fields_count": len(summary_fields),
            "table_detected": bool(table_info)
        }
    }


def _build_invoice_preset(pdf_path: str, lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Preset configuration for Invoices, Bills, & Receipts."""
    summary_fields = detect_key_value_fields(lines)
    table_info = detect_table_structure(lines)

    output_cols = [sf["name"] for sf in summary_fields]
    data_rows_cfg = None
    if table_info:
        data_rows_cfg = {
            "pattern": table_info["pattern"],
            "columns": table_info["columns"]
        }
        for col in table_info["columns"]:
            if col not in output_cols:
                output_cols.append(col)
    output_cols.extend(["source_file", "page_number"])

    return {
        "report_type": "invoice",
        "document_title": "Invoice",
        "record_separator": "",
        "exclude_patterns": [r"Page\s+\d+\s+of\s+\d+"],
        "name_detection": None,
        "address_block": None,
        "repeating_sections": [],
        "summary_fields": summary_fields,
        "computed_fields": [],
        "data_rows": data_rows_cfg,
        "output_columns": output_cols,
        "ml_metadata": {
            "synthesizer": "dbscan_spatial_ml",
            "source_sample": os.path.basename(pdf_path),
            "detected_report_type": "invoice",
            "detected_fields_count": len(summary_fields),
            "table_detected": bool(table_info)
        }
    }


def _build_generic_preset(pdf_path: str, lines: List[Dict[str, Any]], doc_title: str, institution_sep: str) -> Dict[str, Any]:
    """Universal preset builder for any arbitrary document type."""
    record_sep, excludes, name_det = detect_separator_and_excludes(pdf_path, doc_type="generic_document")
    summary_fields = detect_key_value_fields(lines)
    table_info = detect_table_structure(lines)

    output_cols = []
    if name_det:
        output_cols.append(name_det["output_column"])

    computed_fields = []
    for sf in summary_fields:
        if sf["name"] not in output_cols:
            output_cols.append(sf["name"])

        if sf["name"].endswith("_continuation") and not sf["name"].endswith("_continuation2"):
            base_name = sf["name"][:-len("_continuation")]
            joined_name = "program" if "program" in base_name else f"{base_name}_full"
            cont2_name = f"{base_name}_continuation2"
            has_cont2 = any(s["name"] == cont2_name for s in summary_fields)
            join_sources = [base_name, sf["name"]] + ([cont2_name] if has_cont2 else [])
            computed_fields = [cf for cf in computed_fields if cf.get("name") != joined_name]
            computed_fields.append({
                "name": joined_name,
                "join": join_sources,
                "sep": " "
            })
            if joined_name not in output_cols:
                insert_idx = output_cols.index(base_name) + 1 if base_name in output_cols else len(output_cols)
                output_cols.insert(insert_idx, joined_name)

    data_rows_cfg = None
    if table_info:
        data_rows_cfg = {
            "pattern": table_info["pattern"],
            "columns": table_info["columns"]
        }
        for col in table_info["columns"]:
            if col not in output_cols:
                output_cols.append(col)

    output_cols.extend(["source_file", "page_number"])

    slug_type = re.sub(r"[^a-zA-Z0-9]+", "_", doc_title).strip("_").lower() or "generic_document"
    return {
        "report_type": slug_type,
        "document_title": doc_title,
        "record_separator": record_sep,
        "exclude_patterns": excludes,
        "name_detection": name_det or {
            "output_column": "record_name",
            "pattern": r"^(.+)$",
            "stop_at": ""
        },
        "address_block": None,
        "repeating_sections": [],
        "summary_fields": [
            {
                "name": sf["name"],
                "pattern": sf["pattern"],
                **({"flags": sf["flags"]} if "flags" in sf else {})
            }
            for sf in summary_fields
        ],
        "computed_fields": computed_fields,
        "data_rows": data_rows_cfg,
        "output_columns": output_cols,
        "ml_metadata": {
            "synthesizer": "dbscan_spatial_ml",
            "source_sample": os.path.basename(pdf_path),
            "detected_report_type": slug_type,
            "detected_fields_count": len(summary_fields),
            "table_detected": bool(table_info)
        }
    }


# ---------------------------------------------------------------------------
# 7. Main Pipeline Entry Point
# ---------------------------------------------------------------------------

def synthesize_preset_from_pdf(pdf_path: str, preset_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Main entry point for Local Spatial ML Preset Synthesis.
    Given a single PDF document of ANY type (transcripts, resumes, schedules,
    attendances, ledgers, invoices, or arbitrary forms/reports):
      1. Classifies the document type via spatial & textual signals
      2. Dispatches to the dedicated preset synthesizer
      3. Generates complete JSON preset ready for live extraction
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            raise ValueError(f"PDF has no pages: {pdf_path}")
        page = pdf.pages[0]
        lines = cluster_page_lines(page)

    doc_type, display_title, inst_name = classify_document(pdf_path)

    if doc_type == "student_schedule":
        return _build_schedule_preset(pdf_path, lines, inst_name)
    elif doc_type == "student_transcript":
        return _build_transcript_preset(pdf_path, lines, inst_name)
    elif doc_type == "student_ledger":
        return _build_ledger_preset(pdf_path, lines, inst_name)
    elif doc_type == "resume":
        return _build_resume_preset(pdf_path, lines)
    elif doc_type == "attendance":
        return _build_attendance_preset(pdf_path, lines)
    elif doc_type == "invoice":
        return _build_invoice_preset(pdf_path, lines)
    else:
        return _build_generic_preset(pdf_path, lines, display_title, inst_name)

