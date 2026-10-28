"""
core/data_harmonizer/combiner.py
================================
Multi-source dataset combination engine.
Supports Smart Union (stacking rows across homogeneous datasets with fuzzy column alignment)
and Smart Join (relational merging across multi-table datasets via candidate key inference).
"""

import logging
from typing import List, Dict, Any, Tuple, Optional, Set
import pandas as pd

from .profiler import DatasetProfile, ColumnProfile

logger = logging.getLogger(__name__)


class MultiSourceCombiner:
    """Combines multiple input datasets or database tables via Smart Union or Smart Join."""

    @classmethod
    def auto_combine(cls, profiles: List[DatasetProfile]) -> Tuple[pd.DataFrame, str, Dict[str, Any]]:
        """Automatically decides whether to Union or Join, and executes the combination.

        Returns:
            (combined_df, mode_used, details_dict)
        """
        if not profiles:
            raise ValueError("No datasets provided to combine.")

        if len(profiles) == 1:
            raw = profiles[0].raw_df
            df = raw.copy() if raw is not None else pd.DataFrame()
            return df, "single", {"message": "Single source, no combination needed."}

        # Analyze column overlap across all pairs using normalized column names
        all_cols_sets = [{cp.normalized_name for cp in p.columns.values()} for p in profiles]
        pair_overlaps = []
        for i in range(len(profiles)):
            for j in range(i + 1, len(profiles)):
                c1, c2 = all_cols_sets[i], all_cols_sets[j]
                overlap = len(c1.intersection(c2)) / max(len(c1.union(c2)), 1)
                pair_overlaps.append(overlap)

        avg_overlap = sum(pair_overlaps) / max(len(pair_overlaps), 1)

        # High overlap (>= 50%) -> Homogeneous datasets (Union)
        if avg_overlap >= 0.50:
            df, details = cls.smart_union(profiles)
            return df, "union", details
        else:
            # Low overlap -> Relational datasets (Join)
            df, details = cls.smart_join(profiles)
            return df, "join", details

    @classmethod
    def smart_union(cls, profiles: List[DatasetProfile]) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """Harmonizes column names across files and concatenates rows."""
        dfs_to_concat = []
        canonical_map: Dict[str, str] = {}  # norm_name -> canonical_name
        aligned_col_mappings: List[Dict[str, str]] = []

        # Find canonical column names across all profiles
        for p in profiles:
            for col_name, col_prof in p.columns.items():
                norm = col_prof.normalized_name
                if norm not in canonical_map:
                    canonical_map[norm] = col_name

        total_rows_in = sum(p.row_count for p in profiles)

        for p in profiles:
            df_copy = p.raw_df.copy() if p.raw_df is not None else pd.DataFrame()
            file_rename = {}
            for col_name, col_prof in p.columns.items():
                norm = col_prof.normalized_name
                target_col = canonical_map[norm]
                file_rename[col_name] = target_col
            df_copy.rename(columns=file_rename, inplace=True)
            df_copy["_source_file"] = p.source_name
            dfs_to_concat.append(df_copy)
            aligned_col_mappings.append(file_rename)

        combined = pd.concat(dfs_to_concat, ignore_index=True, sort=False)
        details = {
            "mode": "union",
            "files_combined": [p.source_name for p in profiles],
            "total_rows_in": total_rows_in,
            "total_rows_out": len(combined),
            "canonical_columns": list(combined.columns),
        }
        return combined, details

    @classmethod
    def find_candidate_join_keys(
        cls, primary: DatasetProfile, secondary: DatasetProfile
    ) -> List[Dict[str, Any]]:
        """Finds candidate join keys between primary and secondary dataset based on

        Jaccard similarity of distinct values and column name token matches.
        """
        candidates = []

        for p_col, p_prof in primary.columns.items():
            for s_col, s_prof in secondary.columns.items():
                # Compare token similarity
                common_tokens = p_prof.tokens.intersection(s_prof.tokens)
                token_sim = len(common_tokens) / max(len(p_prof.tokens.union(s_prof.tokens)), 1)

                # Skip obvious incompatible types (e.g. text description vs float)
                if (p_prof.inferred_type == "text" and s_prof.inferred_type in ["numeric_float", "datetime"]):
                    continue

                if primary.raw_df is None or secondary.raw_df is None:
                    continue
                p_vals = set(primary.raw_df[p_col].dropna().astype(str).unique())
                s_vals = set(secondary.raw_df[s_col].dropna().astype(str).unique())

                if not p_vals or not s_vals:
                    continue

                intersection_size = len(p_vals.intersection(s_vals))
                jaccard = intersection_size / max(len(p_vals.union(s_vals)), 1)
                overlap_ratio = intersection_size / min(len(p_vals), len(s_vals))

                # Weight score
                is_key_bonus = 0.3 if (p_prof.is_candidate_key or s_prof.is_candidate_key) else 0.0
                score = (0.5 * overlap_ratio) + (0.3 * token_sim) + is_key_bonus

                if score >= 0.3 or overlap_ratio >= 0.4:
                    candidates.append({
                        "primary_col": p_col,
                        "secondary_col": s_col,
                        "secondary_source": secondary.source_name,
                        "overlap_count": intersection_size,
                        "overlap_ratio": overlap_ratio,
                        "token_similarity": token_sim,
                        "score": round(score, 3),
                    })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

    @classmethod
    def smart_join(
        cls,
        profiles: List[DatasetProfile],
        explicit_joins: Optional[List[Tuple[str, str]]] = None
    ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """Relational merge: Primary dataset (profiles[0]) merged with secondary datasets."""
        primary = profiles[0]
        base_df = primary.raw_df.copy() if primary.raw_df is not None else pd.DataFrame()
        join_plan = []

        for sec_idx, secondary in enumerate(profiles[1:], start=1):
            sec_df = secondary.raw_df.copy() if secondary.raw_df is not None else pd.DataFrame()

            # Identify join key
            p_key = None
            s_key = None

            if explicit_joins and len(explicit_joins) >= sec_idx:
                p_key, s_key = explicit_joins[sec_idx - 1]
            else:
                candidates = cls.find_candidate_join_keys(primary, secondary)
                if candidates:
                    best = candidates[0]
                    p_key = best["primary_col"]
                    s_key = best["secondary_col"]

            if not p_key or not s_key:
                # Fallback: check if identical column name exists
                common_names = set(primary.columns.keys()).intersection(set(secondary.columns.keys()))
                if common_names:
                    p_key = s_key = list(common_names)[0]

            if p_key and s_key:
                # Disambiguate overlapping column names before joining
                rename_map = {}
                for c in sec_df.columns:
                    if c != s_key and c in base_df.columns:
                        rename_map[c] = f"{secondary.source_name}_{c}"
                if rename_map:
                    sec_df.rename(columns=rename_map, inplace=True)

                # Convert join columns to string for safe merge
                base_df[p_key] = base_df[p_key].astype(str).str.strip()
                sec_df[s_key] = sec_df[s_key].astype(str).str.strip()

                base_df = pd.merge(
                    base_df,
                    sec_df,
                    left_on=p_key,
                    right_on=s_key,
                    how="left",
                    suffixes=("", f"_{secondary.source_name}")
                )
                join_plan.append({
                    "secondary_file": secondary.source_name,
                    "primary_key": p_key,
                    "secondary_key": s_key,
                    "status": "merged"
                })
            else:
                join_plan.append({
                    "secondary_file": secondary.source_name,
                    "status": "skipped_no_key"
                })

        details = {
            "mode": "join",
            "primary_source": primary.source_name,
            "join_plan": join_plan,
            "total_rows_out": len(base_df),
            "columns": list(base_df.columns),
        }
        return base_df, details
