"""
core/data_harmonizer/transformer.py
===================================
Executes data reshaping and transformation pipeline.
Applies mappings, conversions, date/currency normalizations, and outputs clean tabular
data strictly following the sample dataset schema and column order.
"""

import os
import re
import sqlite3
import logging
from typing import List, Dict, Any, Optional, cast
import pandas as pd

from .matcher import ColumnMappingMatch
from .profiler import DatasetProfile

logger = logging.getLogger(__name__)


class DataTransformer:
    """Transforms raw source DataFrames to exactly conform to the target sample schema."""

    @classmethod
    def transform(
        cls,
        source_df: pd.DataFrame,
        matches: List[ColumnMappingMatch],
        target_column_order: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """Applies transformation rules to produce a target DataFrame.

        Args:
            source_df: The combined raw input DataFrame.
            matches: The list of ColumnMappingMatch rules.
            target_column_order: Desired column order (defaults to order of matches).
        """
        output_data: Dict[str, pd.Series] = {}
        total_rows = len(source_df)

        for match in matches:
            target_col = match.sample_column
            rule = match.transformation_rule
            params = match.rule_params or {}

            # Case: Unmapped
            if not match.source_column or rule == "unmapped":
                output_data[target_col] = pd.Series([""] * total_rows, index=source_df.index)
                continue

            # Case: Composite Concat
            if rule == "concat":
                cols = params.get("columns", [])
                delimiter = params.get("delimiter", " ")
                valid_cols = [c for c in cols if c in source_df.columns]
                if valid_cols:
                    series = source_df[valid_cols[0]].fillna("").astype(str).str.strip()
                    for next_col in valid_cols[1:]:
                        series = series + delimiter + source_df[next_col].fillna("").astype(str).str.strip()
                    output_data[target_col] = series.str.strip()
                else:
                    output_data[target_col] = pd.Series([""] * total_rows, index=source_df.index)
                continue

            # Normal single column source
            src_col = match.source_column
            if src_col not in source_df.columns:
                output_data[target_col] = pd.Series([""] * total_rows, index=source_df.index)
                continue

            raw_series = source_df[src_col]

            if rule == "date_reformat":
                target_fmt = params.get("target_format", "%Y-%m-%d")
                output_data[target_col] = cls._transform_dates(raw_series, target_fmt)

            elif rule == "clean_currency":
                precision = params.get("precision", 2)
                output_data[target_col] = cls._transform_currency(raw_series, precision)

            elif rule == "numeric_cast":
                target_type = params.get("target_type", "numeric_float")
                output_data[target_col] = cls._transform_numeric(raw_series, target_type)

            elif rule == "case_match":
                casing = params.get("casing", "title")
                output_data[target_col] = cls._transform_casing(raw_series, casing)

            else:  # 'direct' or default
                # Trim strings, preserve NaN as empty or original
                output_data[target_col] = raw_series.apply(
                    lambda v: v.strip() if isinstance(v, str) else v
                )

        result_df = pd.DataFrame(output_data, index=source_df.index)

        # Ensure column ordering matches sample schema
        if target_column_order:
            ordered_cols = [c for c in target_column_order if c in result_df.columns]
            # Add any missing cols at the end
            for c in result_df.columns:
                if c not in ordered_cols:
                    ordered_cols.append(c)
            result_df = result_df[ordered_cols]

        return result_df

    @staticmethod
    def _transform_dates(series: pd.Series, target_fmt: str) -> pd.Series:
        """Converts dates to target format string, preserving blank for missing."""
        def parse_date(val):
            if pd.isna(val) or val == "" or str(val).lower() in ["nan", "none", "null"]:
                return ""
            try:
                dt = pd.to_datetime(val)
                return dt.strftime(target_fmt)
            except Exception:
                return str(val)

        return cast(pd.Series, series.apply(parse_date))

    @staticmethod
    def _transform_currency(series: pd.Series, precision: int) -> pd.Series:
        """Strips symbols and formats to clean number or decimal string."""
        def clean_curr(val):
            if pd.isna(val) or val == "":
                return ""
            try:
                s = re.sub(r"[^\d.-]", "", str(val))
                if not s:
                    return ""
                f_val = float(s)
                return f"{f_val:.{precision}f}" if precision > 0 else f"{int(round(f_val))}"
            except Exception:
                return str(val)

        return cast(pd.Series, series.apply(clean_curr))

    @staticmethod
    def _transform_numeric(series: pd.Series, target_type: str) -> pd.Series:
        """Cleans and casts numbers safely."""
        def clean_num(val):
            if pd.isna(val) or val == "":
                return ""
            try:
                s = str(val).replace(",", "").strip()
                f_val = float(s)
                if target_type == "numeric_int":
                    return str(int(round(f_val)))
                return f"{f_val:g}"
            except Exception:
                return str(val)

        return cast(pd.Series, series.apply(clean_num))

    @staticmethod
    def _transform_casing(series: pd.Series, casing: str) -> pd.Series:
        """Applies text casing."""
        def apply_case(val):
            if not isinstance(val, str) or not val:
                return val
            if casing == "upper":
                return val.upper()
            elif casing == "lower":
                return val.lower()
            elif casing == "title":
                return val.title()
            return val

        return cast(pd.Series, series.apply(apply_case))

    @classmethod
    def export_dataset(cls, df: pd.DataFrame, output_path: str, table_name: Optional[str] = None) -> str:
        """Exports the transformed DataFrame to disk."""
        ext = os.path.splitext(output_path)[1].lower()

        if ext == ".csv":
            df.to_csv(output_path, index=False, encoding="utf-8")
        elif ext in [".xlsx", ".xls"]:
            df.to_excel(output_path, index=False, engine="openpyxl")
        elif ext == ".json":
            df.to_json(output_path, orient="records", indent=2, force_ascii=False)
        elif ext in [".db", ".sqlite", ".sqlite3"]:
            conn = sqlite3.connect(output_path)
            t_name = table_name or "harmonized_output"
            df.to_sql(t_name, conn, if_exists="replace", index=False)
            conn.close()
        else:
            # Default to CSV
            df.to_csv(output_path, index=False, encoding="utf-8")

        return output_path
