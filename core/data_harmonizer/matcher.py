"""
core/data_harmonizer/matcher.py
===============================
Multi-tier intelligent schema matcher.
Combines:
  1. Learned Memory (weights from past confirmed feedback in LearningEngine)
  2. Exact Normalized Name & Acronym Mapping
  3. Token Jaccard & Levenshtein Similarity
  4. Type & Semantic Profile Compatibility
  5. Value Regex Signature & Content Distribution
  6. Composite Column Inference (e.g. First + Last -> Full Name)
"""

import difflib
from typing import Dict, List, Any, Optional, Tuple, Set
from dataclasses import dataclass, field

from .profiler import DatasetProfile, ColumnProfile
from .validator import IntegrityValidator
from .adaptive_memory import harmonizer_memory
from core.ollama_engine import ollama_engine


@dataclass
class ColumnMappingMatch:
    sample_column: str
    sample_type: str
    source_column: Optional[str]  # None if unmapped, or "col1 + col2" if composite
    source_type: Optional[str]
    confidence: float  # 0.0 to 1.0
    tier_reason: str
    transformation_rule: str  # 'direct', 'date_reformat', 'clean_currency', 'concat', 'case_match', 'numeric_cast'
    rule_params: Dict[str, Any] = field(default_factory=dict)
    sample_raw_preview: str = ""
    sample_transformed_preview: str = ""
    is_user_overridden: bool = False


class SchemaMatcher:
    """Matches raw source dataset columns to target sample dataset columns."""

    def __init__(self, learning_engine=None):
        self.learning_engine = learning_engine

    def match_schemas(
        self,
        source_profile: DatasetProfile,
        sample_profile: DatasetProfile,
        min_confidence_threshold: float = 0.35
    ) -> List[ColumnMappingMatch]:
        """Matches all columns in sample_profile against candidate columns in source_profile."""
        matches: List[ColumnMappingMatch] = []
        assigned_sources: Set[str] = set()

        # Step 0: Check exact verified fingerprint memory
        saved_fp_matches = harmonizer_memory.get_exact_fingerprint_mapping(source_profile, sample_profile)
        if saved_fp_matches:
            for sample_col, sample_cp in sample_profile.columns.items():
                if sample_col in saved_fp_matches:
                    saved = saved_fp_matches[sample_col]
                    src_col = saved.get("source_column")
                    rule = saved.get("transformation_rule", "direct")
                    params = saved.get("rule_params", {})
                    src_cp = source_profile.columns.get(src_col) if src_col else None
                    raw_val = src_cp.sample_values[0] if (src_cp and src_cp.sample_values) else ""
                    trans_val = self._preview_transform(raw_val, rule, params, sample_cp)
                    matches.append(ColumnMappingMatch(
                        sample_column=sample_col,
                        sample_type=sample_cp.inferred_type,
                        source_column=src_col,
                        source_type=src_cp.inferred_type if src_cp else None,
                        confidence=1.0,
                        tier_reason="Golden Memory (Exact verified dataset signature)",
                        transformation_rule=rule,
                        rule_params=params,
                        sample_raw_preview=raw_val,
                        sample_transformed_preview=trans_val
                    ))
            if len(matches) == len(sample_profile.columns):
                return matches
            else:
                matches.clear()

        # Step 1: Detect potential composite columns (e.g., First Name + Last Name -> Full Name)
        composite_matches = self._detect_composite_columns(source_profile, sample_profile)

        # Step 2: Score all (sample_col, source_col) pairs
        for sample_col, sample_cp in sample_profile.columns.items():
            # Check if this sample column is already satisfied by a composite match
            if sample_col in composite_matches:
                comp_match = composite_matches[sample_col]
                matches.append(comp_match)
                if comp_match.source_column:
                    for part in comp_match.rule_params.get("columns", []):
                        assigned_sources.add(part)
                continue

            best_source: Optional[ColumnProfile] = None
            best_score = 0.0
            best_reason = "Unmapped"
            best_rule = "direct"
            best_params = {}

            for source_col, source_cp in source_profile.columns.items():
                score, reason, rule, params = self.calculate_similarity(
                    source_cp, sample_cp, is_already_assigned=(source_col in assigned_sources)
                )

                if score > best_score:
                    best_score = score
                    best_source = source_cp
                    best_reason = reason
                    best_rule = rule
                    best_params = params

            # Check threshold
            if best_source and best_score >= min_confidence_threshold:
                assigned_sources.add(best_source.name)
                # Generate sample previews
                raw_val = best_source.sample_values[0] if best_source.sample_values else ""
                transformed_val = self._preview_transform(raw_val, best_rule, best_params, sample_cp)

                matches.append(ColumnMappingMatch(
                    sample_column=sample_col,
                    sample_type=sample_cp.inferred_type,
                    source_column=best_source.name,
                    source_type=best_source.inferred_type,
                    confidence=round(best_score, 3),
                    tier_reason=best_reason,
                    transformation_rule=best_rule,
                    rule_params=best_params,
                    sample_raw_preview=raw_val,
                    sample_transformed_preview=transformed_val
                ))
            else:
                matches.append(ColumnMappingMatch(
                    sample_column=sample_col,
                    sample_type=sample_cp.inferred_type,
                    source_column=None,
                    source_type=None,
                    confidence=0.0,
                    tier_reason="No confident source match found",
                    transformation_rule="unmapped",
                    sample_raw_preview="",
                    sample_transformed_preview=""
                ))

        # Step 3: Adaptive AI Matching for low-confidence or unmapped columns
        self._enhance_with_adaptive_ollama(
            matches, assigned_sources, source_profile, sample_profile
        )

        return matches

    def _enhance_with_adaptive_ollama(
        self,
        matches: List[ColumnMappingMatch],
        assigned_sources: Set[str],
        source_profile: DatasetProfile,
        sample_profile: DatasetProfile,
    ):
        """Uses local Ollama with in-context precedents to match remaining ambiguous columns."""
        import json
        try:
            if not ollama_engine.is_available():
                return
        except Exception:
            return

        unassigned_sources = {
            col: cp for col, cp in source_profile.columns.items()
            if col not in assigned_sources
        }
        if not unassigned_sources:
            return

        unmapped_targets = [
            m for m in matches if m.confidence < 0.60 or not m.source_column
        ]
        if not unmapped_targets:
            return

        # Prepare source columns data
        source_summary = {
            col: {
                "inferred_type": cp.inferred_type,
                "samples": cp.sample_values[:3]
            }
            for col, cp in unassigned_sources.items()
        }

        # Prepare target columns data
        target_summary = {
            m.sample_column: {
                "inferred_type": m.sample_type,
                "samples": sample_profile.columns[m.sample_column].sample_values[:3] if m.sample_column in sample_profile.columns else []
            }
            for m in unmapped_targets
        }

        # Fetch few-shot precedents
        precedents = harmonizer_memory.get_few_shot_precedents(limit=10)

        system_prompt = (
            "You are an expert Data Harmonizer matching source spreadsheet columns to target standard columns.\\n"
            "Analyze column semantics, sample values, and data formats.\\n"
            "Adhere strictly to the learned precedents from previous verified runs whenever applicable.\\n"
            "Output JSON format:\\n"
            '{"mappings": [{"target_column": "Name", "source_column": "Name", "rule": "direct|clean_currency|date_reformat|case_match", "confidence": 0.95, "reasoning": "brief explanation"}]}'
        )

        user_prompt = (
            f"LEARNED PRECEDENTS FROM PREVIOUS RUNS:\\n{json.dumps(precedents, indent=2)}\\n\\n"
            f"AVAILABLE SOURCE COLUMNS:\\n{json.dumps(source_summary, indent=2)}\\n\\n"
            f"TARGET COLUMNS NEEDING MATCH:\\n{json.dumps(target_summary, indent=2)}\\n\\n"
            "Map each target column to the single most appropriate source column if a confident match exists."
        )

        try:
            res = ollama_engine.generate_json(system_prompt, user_prompt, timeout=120)
            mappings = res.get("mappings", [])
            for item in mappings:
                tgt = item.get("target_column")
                src = item.get("source_column")
                rule = item.get("rule", "direct")
                conf = float(item.get("confidence", 0.85))
                reason = item.get("reasoning", "Adaptive AI Semantic Match")

                if not src or src not in unassigned_sources:
                    continue

                for m in unmapped_targets:
                    if m.sample_column == tgt:
                        src_cp = source_profile.columns[src]
                        # Programmatic Integrity Check
                        is_compat, boost, explanation = IntegrityValidator.validate_type_compatibility(
                            src_cp.sample_values, m.sample_type
                        )
                        if not is_compat:
                            continue

                        final_conf = min(0.96, conf + boost)
                        raw_val = src_cp.sample_values[0] if src_cp.sample_values else ""
                        trans_val = self._preview_transform(raw_val, rule, {}, sample_profile.columns[tgt])

                        m.source_column = src
                        m.source_type = src_cp.inferred_type
                        m.confidence = round(final_conf, 3)
                        m.tier_reason = f"Adaptive AI ({reason})"
                        m.transformation_rule = rule
                        m.sample_raw_preview = raw_val
                        m.sample_transformed_preview = trans_val
                        assigned_sources.add(src)
                        break
        except Exception as e:
            pass

    def calculate_similarity(
        self,
        source_cp: ColumnProfile,
        sample_cp: ColumnProfile,
        is_already_assigned: bool = False
    ) -> Tuple[float, str, str, Dict[str, Any]]:
        """Calculates multi-tier matching score between a source column and sample column."""
        # ── Tier 1: Check Adaptive Learning Memory ────────────────────────────
        learned_confidence, feedback_count = self._get_learned_score(source_cp.name, sample_cp.name)
        if learned_confidence >= 0.80 and feedback_count >= 1:
            rule, params = self._infer_transformation_rule(source_cp, sample_cp)
            return (
                min(0.99, learned_confidence),
                f"Learned Memory (confirmed {feedback_count}x in past runs)",
                rule,
                params
            )

        # ── Tier 2: Exact Normalized Match ───────────────────────────────────
        if source_cp.normalized_name == sample_cp.normalized_name:
            rule, params = self._infer_transformation_rule(source_cp, sample_cp)
            return (0.98, "Exact normalized name match", rule, params)

        # ── Tier 3: Token Jaccard & Substring Similarity ─────────────────────
        common_tokens = source_cp.tokens.intersection(sample_cp.tokens)
        all_tokens = source_cp.tokens.union(sample_cp.tokens)
        token_jaccard = len(common_tokens) / max(len(all_tokens), 1)

        # Fuzzy string similarity via SequenceMatcher
        seq_ratio = difflib.SequenceMatcher(None, source_cp.normalized_name, sample_cp.normalized_name).ratio()
        name_score = max(token_jaccard, seq_ratio)

        # ── Tier 4: Type Compatibility ───────────────────────────────────────
        type_score = 0.0
        if source_cp.inferred_type == sample_cp.inferred_type:
            type_score = 1.0
        elif {source_cp.inferred_type, sample_cp.inferred_type} <= {"numeric_int", "numeric_float", "currency"}:
            type_score = 0.8
        elif sample_cp.inferred_type == "text":
            type_score = 0.5  # Text sample can accept anything converted to string
        elif source_cp.inferred_type in ["datetime", "email", "phone"] and sample_cp.inferred_type not in ["datetime", "email", "phone", "text"]:
            type_score = -0.5  # Heavy penalty for mismatching structural contact/date types

        # ── Tier 5: Value Distribution & Regex Signature ─────────────────────
        value_score = 0.0
        if source_cp.value_regex_signature and sample_cp.value_regex_signature:
            if source_cp.value_regex_signature == sample_cp.value_regex_signature:
                value_score = 1.0
            else:
                value_score = difflib.SequenceMatcher(
                    None, source_cp.value_regex_signature, sample_cp.value_regex_signature
                ).ratio()

        # Weighted aggregate
        # Weights: Name(0.50), Type(0.30), Value(0.20)
        composite_score = (0.50 * name_score) + (0.30 * type_score) + (0.20 * value_score)

        # If already assigned to another column, apply penalty to favor unassigned candidates
        if is_already_assigned:
            composite_score *= 0.85

        # Cap between 0.0 and 0.95
        composite_score = max(0.0, min(0.95, composite_score))

        rule, params = self._infer_transformation_rule(source_cp, sample_cp)
        reason = f"Fuzzy & Semantic Match (Name: {int(name_score*100)}%, Type: {int(type_score*100)}%)"

        return composite_score, reason, rule, params

    def _get_learned_score(self, source_col: str, sample_col: str) -> Tuple[float, int]:
        """Queries the persistent LearningEngine for past user confirmation weights."""
        if not self.learning_engine:
            return 0.0, 0

        try:
            return self.learning_engine.get_harmonizer_confidence(source_col, sample_col)
        except Exception:
            return 0.0, 0

    def _infer_transformation_rule(
        self, source_cp: ColumnProfile, sample_cp: ColumnProfile
    ) -> Tuple[str, Dict[str, Any]]:
        """Determines what conversion is needed between source and sample."""
        # Date reformatting
        if source_cp.inferred_type == "datetime" or sample_cp.inferred_type == "datetime":
            return "date_reformat", {
                "source_format": source_cp.detected_format,
                "target_format": sample_cp.detected_format or "%Y-%m-%d"
            }

        # Currency cleaning
        if source_cp.inferred_type == "currency" or sample_cp.inferred_type == "currency":
            return "clean_currency", {
                "target_type": sample_cp.inferred_type,
                "precision": 2 if sample_cp.detected_format == "2_decimals" else 0
            }

        # Numeric cast
        if source_cp.inferred_type in ["numeric_int", "numeric_float"] and sample_cp.inferred_type in ["numeric_int", "numeric_float"]:
            return "numeric_cast", {"target_type": sample_cp.inferred_type}

        # Text casing
        if sample_cp.sample_values:
            s_val = sample_cp.sample_values[0]
            if s_val.isupper():
                return "case_match", {"casing": "upper"}
            elif s_val.islower():
                return "case_match", {"casing": "lower"}
            elif s_val.istitle():
                return "case_match", {"casing": "title"}

        return "direct", {}

    def _detect_composite_columns(
        self, source_profile: DatasetProfile, sample_profile: DatasetProfile
    ) -> Dict[str, ColumnMappingMatch]:
        """Detects if multiple source columns can combine to satisfy a sample column (e.g., First + Last -> Full Name)."""
        composite_matches: Dict[str, ColumnMappingMatch] = {}

        source_names_lower = {c.lower(): c for c in source_profile.columns.keys()}

        for sample_col, sample_cp in sample_profile.columns.items():
            norm_target = sample_cp.normalized_name

            # Case: Full Name target, but source has separate first and last names
            if norm_target in ["full_name", "name", "student_name", "employee_name"]:
                first_key = None
                last_key = None
                for k, orig in source_names_lower.items():
                    if "first" in k or k in ["fname", "first_name"]:
                        first_key = orig
                    elif "last" in k or k in ["lname", "last_name", "surname"]:
                        last_key = orig

                if first_key and last_key:
                    # Found composite candidate
                    raw1 = source_profile.columns[first_key].sample_values[0] if source_profile.columns[first_key].sample_values else ""
                    raw2 = source_profile.columns[last_key].sample_values[0] if source_profile.columns[last_key].sample_values else ""
                    preview = f"{raw1} {raw2}".strip()

                    composite_matches[sample_col] = ColumnMappingMatch(
                        sample_column=sample_col,
                        sample_type=sample_cp.inferred_type,
                        source_column=f"{first_key} + {last_key}",
                        source_type="composite",
                        confidence=0.92,
                        tier_reason=f"Composite inferred from {first_key} and {last_key}",
                        transformation_rule="concat",
                        rule_params={"columns": [first_key, last_key], "delimiter": " "},
                        sample_raw_preview=f"{raw1}, {raw2}",
                        sample_transformed_preview=preview
                    )

        return composite_matches

    def _preview_transform(self, raw_val: str, rule: str, params: Dict[str, Any], sample_cp: ColumnProfile) -> str:
        """Lightweight preview of transformed value for the UI."""
        if not raw_val:
            return ""

        try:
            if rule == "date_reformat":
                src_fmt = params.get("source_format")
                tgt_fmt = params.get("target_format", "%Y-%m-%d")
                import datetime
                if src_fmt and src_fmt != "iso8601_auto":
                    dt = datetime.datetime.strptime(raw_val, src_fmt)
                    return dt.strftime(tgt_fmt)
                else:
                    import pandas as pd
                    dt = pd.to_datetime(raw_val)
                    return dt.strftime(tgt_fmt)

            elif rule == "clean_currency":
                import re
                clean = re.sub(r"[^\d.-]", "", raw_val)
                return clean

            elif rule == "case_match":
                casing = params.get("casing")
                if casing == "upper":
                    return raw_val.upper()
                elif casing == "lower":
                    return raw_val.lower()
                elif casing == "title":
                    return raw_val.title()

            return raw_val
        except Exception:
            return raw_val
