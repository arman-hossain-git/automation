"""
modules/pdf_rule_tuner.py
=========================
Active Learning & Self-Tuning Engine for PDF Extractor.

Enables human-in-the-loop tuning:
  1. Compares extracted values against user-verified ground truth.
  2. Synthesizes tighter regex anchors and lookaheads when a field is marked incorrect.
  3. Records noise exclusions for stray lines.
  4. Persists field-level confidence scores and tuning history into learning_data.json.
"""

import os
import re
import json
import datetime
import logging
from typing import Dict, Any, Tuple, Optional, List

from core.learning_system import learning_engine

logger = logging.getLogger(__name__)


def tune_row_field_in_preset(
    preset_config: Dict[str, Any],
    field_name: str,
    expected_value: str,
    sample_line_text: str,
    all_lines: Optional[List[str]] = None
) -> Tuple[Dict[str, Any], str]:
    """
    Auto-tunes the regex pattern for tabular data rows (`data_rows`) in `preset_config`
    when a user points or corrects a specific row column.
    
    Returns:
        (updated_preset_dict, status_message)
    """
    expected_clean = expected_value.strip()
    line_clean = sample_line_text.strip()
    col_name = field_name[4:] if field_name.startswith("row_") else field_name

    if not expected_clean:
        return preset_config, "Empty expected value; no changes made."
    if not line_clean:
        return preset_config, "Empty sample line; cannot synthesize row pattern."

    SCHEDULE_SUMMARY_FIELDS = {
        "student_name", "status", "grad_date", "session", "ft_pt",
        "program_description", "drop_date", "group", "first_term",
        "exp_grad_date", "term"
    }

    if preset_config.get("report_type") == "student_schedule" and col_name in SCHEDULE_SUMMARY_FIELDS:
        if "data_rows" in preset_config and "columns" in preset_config["data_rows"]:
            preset_config["data_rows"]["columns"] = [
                c for c in preset_config["data_rows"]["columns"] if c not in SCHEDULE_SUMMARY_FIELDS
            ]
        clean_name = field_name[4:] if field_name.startswith("row_") else field_name
        return tune_field_in_preset(preset_config, clean_name, expected_value, sample_line_text, all_lines)

    data_rows = preset_config.setdefault("data_rows", {})
    existing_cols = list(data_rows.get("columns") or [])

    # If receipt column was omitted during initial extraction but exists in header, include it
    if "receipt" not in existing_cols and "receipt_check" not in existing_cols:
        if any("receipt" in l.lower() or "check" in l.lower() for l in (all_lines or [])):
            if "description" in existing_cols:
                d_idx = existing_cols.index("description")
                existing_cols.insert(d_idx + 1, "receipt")
            else:
                existing_cols.append("receipt")

    if col_name not in existing_cols:
        existing_cols.append(col_name)

    col_idx = existing_cols.index(col_name)
    num_cols = len(existing_cols)

    # Locate expected_clean inside line_clean
    val_start = line_clean.find(expected_clean)
    if val_start == -1:
        # Check without currency symbols or stripped words
        clean_unquoted = expected_clean.lstrip("$ ").strip()
        if clean_unquoted and clean_unquoted in line_clean:
            val_start = line_clean.find(clean_unquoted)
            val_end = val_start + len(clean_unquoted)
        else:
            val_start = -1
            val_end = -1
    else:
        val_end = val_start + len(expected_clean)

    def _infer_token_pattern(tok: str, c_name: str = "") -> str:
        s = tok.strip()
        c_lower = c_name.lower()
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", s):
            return r"(\d{2}/\d{2}/\d{4})"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return r"(\d{4}-\d{2}-\d{2})"
        if any(k in c_lower for k in ("debit", "credit", "balance", "amount", "fee", "cost", "total", "rate", "gpa", "units")):
            return r"[-]?\$?\s*([-\d,.]+)"
        if re.fullmatch(r"[-]?\$?\s*[\d,.]+", s):
            return r"[-]?\$?\s*([-\d,.]+)"
        if re.fullmatch(r"\(([^)]+)\)", s):
            return r"\(([^)]+)\)"
        if re.fullmatch(r"[A-Za-z0-9\-_/]+", s):
            return r"([A-Za-z0-9\-_/]+)"
        return r"(\S+)"

    if val_start != -1:
        prefix_str = line_clean[:val_start].strip()
        suffix_str = line_clean[val_end:].strip()

        # Prefix columns (0 .. col_idx - 1)
        num_prefix = col_idx
        prefix_tokens = prefix_str.split()
        prefix_pats = []
        if num_prefix > 0:
            if len(prefix_tokens) >= num_prefix:
                for p_i in range(num_prefix):
                    c_n = existing_cols[p_i]
                    tok = prefix_tokens[p_i]
                    prefix_pats.append(_infer_token_pattern(tok, c_n))
            else:
                prefix_pats = [r"(\S+)" for _ in range(num_prefix)]

        # Target column pattern
        c_lower = col_name.lower()
        if any(k in c_lower for k in ("code", "ledger")):
            target_pat = rf"({re.escape(expected_clean)}|[A-Za-z0-9\-]+(?:\s*-\s*\S+|\s+\d{{2}}[/-]\d{{2}}|\s+[A-Za-z0-9\-]+)?)"
        elif re.fullmatch(r"\d{2}/\d{2}/\d{4}", expected_clean):
            target_pat = r"(\d{2}/\d{2}/\d{4})"
        elif any(k in c_lower for k in ("debit", "credit", "balance", "amount", "fee", "cost", "total")):
            target_pat = r"[-]?\$?\s*([-\d,.]+)"
        elif re.fullmatch(r"[A-Za-z0-9\-_/]+", expected_clean):
            target_pat = r"([A-Za-z0-9\-_/]+)"
        elif re.fullmatch(r"\(([^)]+)\)", expected_clean):
            target_pat = r"\(([^)]+)\)"
        else:
            # Multi-word text / description
            target_pat = rf"({re.escape(expected_clean)}|.+?)"

        # Suffix columns (col_idx + 1 .. num_cols - 1)
        suffix_cols = existing_cols[col_idx + 1:]
        num_suffix = len(suffix_cols)
        curr_matches = list(re.finditer(r"[-]?\$\s*[-]?[\d,.]+|[-]?[\d,.]+", suffix_str))

        is_curr_col = lambda c: any(k in c.lower() for k in ("debit", "credit", "balance", "amount", "fee", "cost", "total"))
        trailing_curr_cols = []
        for c in reversed(suffix_cols):
            if is_curr_col(c):
                trailing_curr_cols.insert(0, c)
            else:
                break
        intermediate_cols = suffix_cols[:len(suffix_cols) - len(trailing_curr_cols)]

        if trailing_curr_cols and len(curr_matches) >= len(trailing_curr_cols):
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

            curr_pats = [r"[-]?\$?\s*([-\d,.]+)" for _ in trailing_curr_cols]
            new_pattern = r"^" + r"\s+".join(prefix_pats + [target_pat]) + "".join(intermediate_pats) + r"\s+" + r"\s+".join(curr_pats) + r"$"
        elif num_suffix > 0:
            suffix_tokens = suffix_str.split()
            suffix_pats = []
            if len(suffix_tokens) >= num_suffix:
                for s_i in range(num_suffix):
                    actual_idx = col_idx + 1 + s_i
                    c_n = existing_cols[actual_idx]
                    tok = suffix_tokens[s_i]
                    suffix_pats.append(_infer_token_pattern(tok, c_n))
            else:
                suffix_pats = [r"(\S+)" for _ in range(num_suffix)]

            pattern_parts = prefix_pats + [target_pat] + suffix_pats
            new_pattern = r"^" + r"\s+".join(pattern_parts) + r"$"
        else:
            new_pattern = r"^" + r"\s+".join(prefix_pats + [target_pat]) + r"$"

        if not re.match(new_pattern, line_clean):
            flex_pattern = r"^\s*" + new_pattern.lstrip("^").rstrip("$") + r"\s*$"
            if re.match(flex_pattern, line_clean):
                new_pattern = flex_pattern
    else:
        # Fallback: synthesize pattern directly from line tokens
        tokens = line_clean.split()
        patterns = []
        for i, col in enumerate(existing_cols):
            tok = tokens[i] if i < len(tokens) else ""
            patterns.append(_infer_token_pattern(tok, col))
        new_pattern = r"^\s*" + r"\s+".join(patterns) + r"\s*$"

    data_rows["pattern"] = new_pattern
    data_rows["columns"] = existing_cols

    # Ensure output_columns contains row columns
    out_cols = preset_config.setdefault("output_columns", [])
    for c in existing_cols:
        if c not in out_cols:
            out_cols.append(c)

    return preset_config, f"Row column '{col_name}' auto-tuned. Data row pattern updated to:\n{new_pattern}"


def tune_data_row_line_in_preset(
    preset_config: Dict[str, Any],
    sample_line_text: str,
    columns: Optional[List[str]] = None
) -> Tuple[Dict[str, Any], str]:
    """
    Auto-tunes or synthesizes data_rows in preset_config from a highlighted sample row line.
    """
    line_clean = sample_line_text.strip()
    if not line_clean:
        return preset_config, "Empty line; cannot synthesize row pattern."

    data_rows = preset_config.setdefault("data_rows", {})
    existing_cols = columns or list(data_rows.get("columns") or [])

    tokens = line_clean.split()
    if not existing_cols:
        existing_cols = [f"col_{i + 1}" for i in range(len(tokens))]

    curr_matches = list(re.finditer(r"[-]?\$\s*[-]?[\d,.]+", line_clean))
    if curr_matches and curr_matches[-1].end() >= len(line_clean) - 3:
        num_curr = len(curr_matches)
        prefix = line_clean[:curr_matches[0].start()].strip()
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
        if len(existing_cols) >= total_groups:
            num_prefix = len(prefix_pats)
            final_cols = existing_cols[:num_prefix] + existing_cols[-num_curr:]
        else:
            final_cols = list(existing_cols)
            for i in range(len(existing_cols), total_groups):
                final_cols.append(f"col_{i + 1}")
        pattern = r"^(\d{2}/\d{2}/\d{4})\s+([A-Za-z0-9\-]+)\s+(.+?)\s+" + r"\s+".join(curr_pats) + r"$" if len(prefix_pats) >= 3 else (r"^\s*" + r"\s+".join(all_pats) + r"\s*$")
    else:
        patterns = []
        for i, col in enumerate(existing_cols):
            tok = tokens[i] if i < len(tokens) else ""
            if re.fullmatch(r"\d{2}/\d{2}/\d{4}", tok):
                patterns.append(r"(\d{2}/\d{2}/\d{4})")
            elif re.fullmatch(r"[-]?\$?\s*[\d,.]+", tok):
                patterns.append(r"[-]?\$?\s*([-\d,.]+)")
            elif re.fullmatch(r"[A-Za-z0-9\-]+", tok):
                patterns.append(r"([A-Za-z0-9\-]+)")
            else:
                patterns.append(r"(.+?)" if i < len(existing_cols) - 1 else r"(.+)")
        pattern = r"^\s*" + r"\s+".join(patterns) + r"\s*$"
        final_cols = existing_cols

    data_rows["pattern"] = pattern
    data_rows["columns"] = final_cols

    out_cols = preset_config.setdefault("output_columns", [])
    for c in final_cols:
        if c not in out_cols:
            out_cols.append(c)

    return preset_config, f"Synthesized data_rows from line with {len(final_cols)} columns:\n{pattern}"


def tune_field_in_preset(
    preset_config: Dict[str, Any],
    field_name: str,
    expected_value: str,
    sample_line_text: str,
    all_lines: Optional[List[str]] = None
) -> Tuple[Dict[str, Any], str]:
    """
    Auto-tunes the regex pattern for `field_name` in `preset_config` so that
    it accurately extracts `expected_value`. Supports single-line, multi-line
    continuation values, and table row columns.
    
    Returns:
        (updated_preset_dict, status_message)
    """
    expected_clean = expected_value.strip()
    if not expected_clean:
        # User explicitly set this field to blank/empty — clear its extraction pattern
        # so subsequent extractions return '' instead of the previous matched value.
        cleared = False
        if "summary_fields" in preset_config:
            for sf in preset_config["summary_fields"]:
                if sf.get("name") == field_name:
                    sf["pattern"] = r"(?!)"   # never-matching sentinel → always returns ''
                    cleared = True
                    break
        if not cleared:
            if preset_config.get("name_detection", {}).get("output_column") == field_name:
                preset_config["name_detection"]["pattern"] = r"(?!)"
                cleared = True
        if not cleared:
            # Field not found yet — add a sentinel entry
            preset_config.setdefault("summary_fields", []).append(
                {"name": field_name, "pattern": r"(?!)"}
            )
            if field_name not in preset_config.get("output_columns", []):
                preset_config.setdefault("output_columns", []).append(field_name)
        return preset_config, f"Field '{field_name}' cleared — will now extract as empty."

    all_lines = all_lines or []

    SCHEDULE_SUMMARY_FIELDS = {
        "student_name", "status", "grad_date", "session", "ft_pt",
        "program_description", "drop_date", "group", "first_term",
        "exp_grad_date", "term"
    }
    is_schedule_summary = (
        preset_config.get("report_type") == "student_schedule"
        and (field_name in SCHEDULE_SUMMARY_FIELDS or field_name.replace("row_", "") in SCHEDULE_SUMMARY_FIELDS)
    )

    # Check if this field belongs to table rows
    is_row_field = not is_schedule_summary and (
        field_name.startswith("row_")
        or (
            "data_rows" in preset_config
            and isinstance(preset_config["data_rows"], dict)
            and field_name in preset_config["data_rows"].get("columns", [])
        )
    )
    if is_row_field:
        return tune_row_field_in_preset(
            preset_config, field_name, expected_value, sample_line_text, all_lines
        )

    # 1. Multi-Line / Wrapped Value Detection:
    # Check if expected_clean is split into Part 1 (on sample_line_text) and Part 2 (on a subsequent line)
    words = expected_clean.split()
    part1, part2 = None, None
    if len(words) >= 2 and expected_clean not in sample_line_text:
        for split_idx in range(len(words) - 1, 0, -1):
            cand1 = " ".join(words[:split_idx])
            if cand1 in sample_line_text:
                part1 = cand1
                part2 = " ".join(words[split_idx:])
                break

    if part1 and part2:
        # Multi-line continuation: configure base field + continuation + computed_field
        base_name = field_name if not field_name.endswith("_continuation") else field_name.replace("_continuation", "")
        cont_name = f"{base_name}_continuation"
        joined_name = "program" if "program" in base_name else (base_name if not base_name.endswith("_description") else base_name.replace("_description", ""))

        val_start = sample_line_text.find(part1)
        prefix_text = sample_line_text[:val_start].rstrip()
        label_match = re.search(r"([A-Za-z0-9\s/]+?:)\s*$", prefix_text)
        anchor_label = label_match.group(1).strip() if label_match else f"{base_name.replace('_', ' ').title()}:"

        next_label_match = re.search(r"^([A-Za-z0-9\s/]+?:)", sample_line_text[val_start + len(part1):].lstrip())
        if next_label_match:
            primary_pattern = rf"{re.escape(anchor_label)}[ \t]*(.+?)(?=[ \t]+{re.escape(next_label_match.group(1).strip())}|\r?\n|$)"
        else:
            primary_pattern = rf"{re.escape(anchor_label)}[ \t]*(.+?)(?=\r?\n|$)"

        other_labels = r"Status:|First Term:|Drop Date:|Group:|Exp Grad Date:|Grad Date:|Session:|FT/PT:|Campus:|LDA:|Trans Date|Date"
        url_opt = r"(?:[ \t]*https?://\S+[ \t]*\r?\n)?"
        cont_pattern = rf"{re.escape(anchor_label)}[^\n\r]*\r?\n{url_opt}(?!\s*(?:{other_labels}))[ \t]*(?:https?://\S+[ \t]*)?([^\n\r]+)"


        # Update or insert summary fields
        s_fields = preset_config.setdefault("summary_fields", [])
        updated_base, updated_cont = False, False
        for sf in s_fields:
            if sf.get("name") == base_name:
                sf["pattern"] = primary_pattern
                updated_base = True
            elif sf.get("name") == cont_name:
                sf["pattern"] = cont_pattern
                updated_cont = True

        if not updated_base:
            s_fields.append({"name": base_name, "pattern": primary_pattern})
        if not updated_cont:
            s_fields.append({"name": cont_name, "pattern": cont_pattern})

        # Add or update computed_fields
        computed = preset_config.setdefault("computed_fields", [])
        cf_entry = next((cf for cf in computed if cf.get("name") == joined_name), None)
        if not cf_entry:
            computed.append({
                "name": joined_name,
                "join": [base_name, cont_name],
                "sep": " "
            })
        else:
            cf_entry["join"] = [base_name, cont_name]
            cf_entry["sep"] = " "

        out_cols = preset_config.setdefault("output_columns", [])
        for col in (base_name, cont_name, joined_name):
            if col not in out_cols:
                out_cols.append(col)

        return preset_config, f"Configured multi-line join for '{joined_name}' ({part1} + {part2})."

    # 2. Single Line Tuning
    if expected_clean in sample_line_text:
        val_start = sample_line_text.find(expected_clean)
        val_end = val_start + len(expected_clean)
        prefix_text = sample_line_text[:val_start].rstrip()
        suffix_text = sample_line_text[val_end:].lstrip()

        label_match = re.search(r"([A-Za-z0-9\s/]+?:)\s*$", prefix_text)
        if label_match:
            anchor_label = label_match.group(1).strip()
            prefix_pattern = re.escape(anchor_label) + r"[ \t]*"
        elif prefix_text:
            last_word = prefix_text.split()[-1]
            prefix_pattern = re.escape(last_word) + r"[ \t]+"
        else:
            prefix_pattern = r"^"

        next_label_match = re.search(r"^([A-Za-z0-9\s/]+?:)", suffix_text)
        if next_label_match:
            next_label = next_label_match.group(1).strip()
            suffix_pattern = rf"(?=[ \t]+{re.escape(next_label)}|\r?\n|$)"
        elif suffix_text:
            next_word = suffix_text.split()[0]
            suffix_pattern = rf"(?=[ \t]+{re.escape(next_word)}|\r?\n|$)"
        else:
            suffix_pattern = r"(?=\r?\n|$)"

        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", expected_clean):
            val_pat = r"(\d{2}/\d{2}/\d{4})"
        elif re.fullmatch(r"\$?\s*[-]?[\d,.]+", expected_clean):
            val_pat = r"([-]?\$?[\d,.]+)"
        elif re.fullmatch(r"[A-Za-z0-9\-]+", expected_clean):
            val_pat = r"([A-Za-z0-9\-]+)"
        else:
            val_pat = r"(.+?)"

        new_pattern = f"{prefix_pattern}{val_pat}{suffix_pattern}"
    else:
        # 3. Resilient fallback: anchor by label on sample_line_text
        label_match = re.search(r"([A-Za-z0-9\s/]+?:)", sample_line_text)
        if label_match:
            anchor_label = label_match.group(1).strip()
            new_pattern = rf"{re.escape(anchor_label)}[ \t]*(.+?)(?=[ \t]+[A-Z][A-Za-z0-9/]+:|\r?\n|$)"
        else:
            new_pattern = rf"({re.escape(expected_clean)})"

    # Update preset config
    updated = False
    if "summary_fields" in preset_config:
        for sf in preset_config["summary_fields"]:
            if sf.get("name") == field_name:
                sf["pattern"] = new_pattern
                updated = True
                break

    if not updated and preset_config.get("name_detection", {}).get("output_column") == field_name:
        preset_config["name_detection"]["pattern"] = new_pattern
        updated = True

    if not updated:
        preset_config.setdefault("summary_fields", []).append({
            "name": field_name,
            "pattern": new_pattern
        })
        if field_name not in preset_config.get("output_columns", []):
            preset_config.setdefault("output_columns", []).append(field_name)

    return preset_config, f"Field '{field_name}' pattern updated to: {new_pattern}"


def add_exclude_rule_to_preset(preset_config: Dict[str, Any], noise_line: str) -> Tuple[Dict[str, Any], str]:
    """
    Adds an exclusion pattern for a recurring noise line (e.g. headers, footers).
    """
    clean_line = noise_line.strip()
    if not clean_line:
        return preset_config, "Empty noise text; nothing added."

    # Generalize dates, numbers or URLs in noise text
    pat = re.escape(clean_line)
    # If it contains Page numbers: Page X of Y
    if re.search(r"page\s+\d+", clean_line, re.IGNORECASE):
        pat = r"(?i)Page\s+\d+(\s+of\s+\d+)?"
    elif re.search(r"powered by", clean_line, re.IGNORECASE):
        pat = r"(?i)Powered by.*"
    elif re.search(r"https?://", clean_line):
        pat = r"https?://\S+"

    excludes = preset_config.setdefault("exclude_patterns", [])
    if pat not in excludes:
        excludes.append(pat)
        return preset_config, f"Added exclusion pattern: {pat}"
    return preset_config, f"Exclusion pattern already exists: {pat}"


def record_verification_feedback(
    preset_name: str,
    field_name: str,
    is_correct: bool,
    old_value: str = "",
    new_value: str = ""
):
    """
    Records field verification in learning_data.json.
    Increases confidence when correct; records tuning action when updated.
    """
    norm_preset = preset_name.strip() if preset_name else ""
    if not norm_preset or norm_preset == "(unsaved / custom)":
        norm_preset = "default"

    data = learning_engine._data
    profiles = data.setdefault("pdf_extractor_profiles", {})
    profile = profiles.setdefault(norm_preset, {
        "verified_runs": 0,
        "field_confidence": {},
        "tuning_history": []
    })

    conf_map = profile.setdefault("field_confidence", {})
    current_conf = conf_map.get(field_name, 0.8)

    if is_correct:
        # Increase confidence towards 1.0
        conf_map[field_name] = round(min(1.0, current_conf + 0.05), 3)
    else:
        # Decrease confidence and log tuning
        conf_map[field_name] = round(max(0.2, current_conf - 0.15), 3)
        profile.setdefault("tuning_history", []).append({
            "field": field_name,
            "old_value": old_value,
            "new_value": new_value,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

    profile["verified_runs"] = profile.get("verified_runs", 0) + 1
    learning_engine.save()
