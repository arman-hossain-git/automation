"""
core/data_harmonizer/adaptive_memory.py
=======================================
Persistent memory manager for adaptive dataset schema matching.

Caches verified mappings, generates few-shot exemplars for Ollama,
and ensures that user confirmations become permanent, instant rules
on all future runs.
"""

import hashlib
import json
import logging
from typing import Dict, List, Any, Optional, Tuple, Set

from core.learning_system import learning_engine
from .profiler import DatasetProfile, ColumnProfile

logger = logging.getLogger(__name__)


def compute_schema_fingerprint(profile: DatasetProfile) -> str:
    """Computes a deterministic hash fingerprint from dataset column names and types."""
    cols = sorted(f"{col.strip().lower()}:{cp.inferred_type}" for col, cp in profile.columns.items())
    signature = "|".join(cols)
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]


class AdaptiveHarmonizerMemory:
    """Interface connecting SchemaMatcher to the persistent LearningEngine."""

    def __init__(self, engine=None):
        self.engine = engine or learning_engine

    def get_exact_fingerprint_mapping(
        self, source_profile: DatasetProfile, sample_profile: DatasetProfile
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        If this exact source-to-sample schema pairing was previously verified,
        returns the saved column mappings directly.
        """
        src_fp = compute_schema_fingerprint(source_profile)
        sample_fp = compute_schema_fingerprint(sample_profile)
        key = f"{src_fp}:{sample_fp}"
        
        with self.engine._lock:
            profiles = self.engine._data.get("data_harmonizer_profiles", {})
            fp_runs = profiles.get("fingerprint_runs", {})
            return fp_runs.get(key, {}).get("saved_mappings")

    def get_confirmed_column_match(self, source_col: str, sample_col: str) -> Tuple[float, int, Optional[str]]:
        """
        Checks if this specific column pair was confirmed in past runs.
        Returns: (confidence [0.0..1.0], confirmed_count, saved_transformation_rule)
        """
        s_clean = source_col.strip().lower()
        t_clean = sample_col.strip().lower()

        with self.engine._lock:
            profiles = self.engine._data.get("data_harmonizer_profiles", {})
            assocs = profiles.get("column_associations", {})
            src_entry = assocs.get(s_clean, {})
            target_entry = src_entry.get(t_clean, {})

            confirmed = target_entry.get("confirmed", 0)
            rejected = target_entry.get("rejected", 0)
            rule = target_entry.get("rule", "direct")

            if confirmed > 0:
                confidence = min(1.0, 0.85 + (0.05 * min(confirmed, 3)))
                if rejected > 0:
                    confidence *= (confirmed / (confirmed + rejected))
                return confidence, confirmed, rule
            elif rejected > 0:
                return 0.0, 0, None

        return 0.0, 0, None

    def get_few_shot_precedents(self, limit: int = 15) -> List[Dict[str, str]]:
        """
        Retrieves the top confirmed column mapping precedents from historical runs.
        Used to feed into Ollama as in-context learning examples.
        """
        precedents = []
        with self.engine._lock:
            profiles = self.engine._data.get("data_harmonizer_profiles", {})
            assocs = profiles.get("column_associations", {})
            for src_col, targets in assocs.items():
                for tgt_col, data in targets.items():
                    confirmed = data.get("confirmed", 0)
                    rejected = data.get("rejected", 0)
                    rule = data.get("rule", "direct")
                    if confirmed > rejected:
                        precedents.append({
                            "source_column": src_col,
                            "target_column": tgt_col,
                            "transformation": rule,
                            "confirmed_runs": confirmed
                        })
        # Sort by most frequently confirmed
        precedents.sort(key=lambda x: x["confirmed_runs"], reverse=True)
        return precedents[:limit]

    def record_user_feedback(
        self,
        source_profile: DatasetProfile,
        sample_profile: DatasetProfile,
        matches: List[Any],
    ):
        """
        Saves user-confirmed or adjusted mappings into persistent memory.
        Locks in the rule so the user is never asked again.
        """
        src_fp = compute_schema_fingerprint(source_profile)
        sample_fp = compute_schema_fingerprint(sample_profile)
        fp_key = f"{src_fp}:{sample_fp}"
        saved_dict = {}

        for m in matches:
            if not m.source_column:
                continue
            
            s_clean = m.source_column.strip().lower()
            t_clean = m.sample_column.strip().lower()
            rule = m.transformation_rule or "direct"

            # 1. Update column association in learning engine
            with self.engine._lock:
                profiles = self.engine._data.setdefault("data_harmonizer_profiles", {})
                assocs = profiles.setdefault("column_associations", {})
                entry = assocs.setdefault(s_clean, {}).setdefault(t_clean, {
                    "confirmed": 0, "rejected": 0, "rule": rule
                })
                entry["confirmed"] += 1
                entry["rule"] = rule

                # 2. Store composite rule if composite
                if "+" in m.source_column or m.rule_params.get("columns"):
                    comp_rules = profiles.setdefault("composite_rules", {})
                    comp_rules[t_clean] = {
                        "columns": m.rule_params.get("columns", [c.strip() for c in m.source_column.split("+")]),
                        "rule": rule,
                        "separator": m.rule_params.get("separator", " "),
                        "confirmed": comp_rules.get(t_clean, {}).get("confirmed", 0) + 1
                    }

            saved_dict[m.sample_column] = {
                "source_column": m.source_column,
                "transformation_rule": rule,
                "rule_params": m.rule_params,
                "confidence": 1.0,
            }

        # 3. Save full fingerprint run
        with self.engine._lock:
            profiles = self.engine._data.setdefault("data_harmonizer_profiles", {})
            fp_runs = profiles.setdefault("fingerprint_runs", {})
            fp_entry = fp_runs.setdefault(fp_key, {"verified_runs": 0})
            fp_entry["verified_runs"] += 1
            fp_entry["saved_mappings"] = saved_dict

        self.engine.save()
        logger.info(f"[ADAPTIVE MEMORY] Successfully recorded {len(saved_dict)} confirmed mappings.")


# Global instance
harmonizer_memory = AdaptiveHarmonizerMemory()
