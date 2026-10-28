"""
core/data_harmonizer/profiler.py
================================
Statistical and semantic data profiler.
Extracts schema structure, inferred types, datetime formats, value distribution signatures,
and normalized tokens from tabular datasets (CSV, Excel, JSON, SQLite).
"""

import os
import re
import sqlite3
import datetime
import hashlib
from typing import Dict, List, Any, Optional, Tuple, Set
from dataclasses import dataclass, field
import pandas as pd


COMMON_ABBREVIATIONS: Dict[str, str] = {
    "dob": "date of birth",
    "fname": "first name",
    "lname": "last name",
    "mname": "middle name",
    "addr": "address",
    "tel": "telephone phone",
    "mob": "mobile phone",
    "ph": "phone number",
    "phone": "phone number",
    "st": "student",
    "std": "student",
    "stu": "student",
    "id": "identifier id",
    "emp": "employee",
    "dept": "department",
    "amt": "amount",
    "bal": "balance",
    "qty": "quantity",
    "desc": "description",
    "num": "number",
    "no": "number",
    "org": "organization",
    "mgr": "manager",
    "pos": "position",
    "gpa": "grade point average grade",
    "gndr": "gender sex",
    "gen": "gender",
    "ssn": "social security number id",
    "zip": "zipcode postal code",
    "pcode": "postal code zipcode",
    "yr": "year",
    "mo": "month",
    "dt": "date",
    "curr": "currency",
}

DATE_FORMAT_CANDIDATES = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%Y-%m-%d %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
]

EMAIL_REGEX = re.compile(r"^[\w\.\+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9\-\.]+$")
PHONE_REGEX = re.compile(r"^\+?[\d\s\(\)\-\.]{7,22}$")
CURRENCY_REGEX = re.compile(r"^[\$\€\£\¥\₹]?\s*-?\d{1,3}(,\d{3})*(\.\d+)?\s*[\$\€\£\¥\₹]?$")


@dataclass
class ColumnProfile:
    name: str
    original_index: int
    inferred_type: str  # 'datetime', 'currency', 'numeric_int', 'numeric_float', 'email', 'phone', 'boolean', 'categorical', 'text'
    normalized_name: str
    tokens: Set[str]
    sample_values: List[str]
    unique_count: int
    null_count: int
    total_count: int
    uniqueness_ratio: float
    null_ratio: float
    detected_format: Optional[str] = None  # e.g., '%Y-%m-%d' for dates, or precision/casing
    value_regex_signature: str = ""
    is_candidate_key: bool = False


@dataclass
class DatasetProfile:
    source_name: str
    row_count: int
    column_count: int
    columns: Dict[str, ColumnProfile]
    column_order: List[str]
    fingerprint: str
    raw_df: Optional[pd.DataFrame] = None


class DataProfiler:
    """Profiles tabular data from files or databases for intelligent mapping."""

    @staticmethod
    def list_sheets_or_tables(source: str) -> List[str]:
        """Returns sheet names (Excel) or table names (SQLite). Returns empty list for CSV/JSON."""
        if not isinstance(source, str) or not os.path.exists(source):
            return []

        ext = os.path.splitext(source)[1].lower()
        if ext in [".xlsx", ".xls", ".xlsm"]:
            try:
                with pd.ExcelFile(source) as ef:
                    return [str(s) for s in ef.sheet_names]
            except Exception:
                return []
        elif ext in [".db", ".sqlite", ".sqlite3"]:
            try:
                conn = sqlite3.connect(source)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
                tables = [r[0] for r in cursor.fetchall()]
                conn.close()
                return tables
            except Exception:
                return []
        return []

    @staticmethod
    def load_dataset(source: Any, table_name: Optional[str] = None) -> Tuple[pd.DataFrame, str]:
        """Loads dataset into a Pandas DataFrame from file path, DataFrame, or SQLite DB.

        Supports specific sheet names for Excel via table_name.
        Returns:
            (DataFrame, source_name_str)
        """
        if isinstance(source, pd.DataFrame):
            return source.copy(), "dataframe"

        if not isinstance(source, str) or not os.path.exists(source):
            raise FileNotFoundError(f"Source dataset not found: {source}")

        base_name = os.path.basename(source)
        ext = os.path.splitext(source)[1].lower()

        if ext in [".csv", ".tsv", ".txt"]:
            # Try sniffing delimiter and encoding
            for encoding in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
                try:
                    sep = "\t" if ext == ".tsv" else None
                    df = pd.read_csv(source, sep=sep, encoding=encoding, engine="python", on_bad_lines="skip")
                    return df, base_name
                except Exception:
                    continue
            # Fallback
            df = pd.read_csv(source, encoding="utf-8", on_bad_lines="skip")
            return df, base_name

        elif ext in [".xlsx", ".xls", ".xlsm"]:
            sheet = table_name if table_name is not None else 0
            df = pd.read_excel(source, sheet_name=sheet)
            label = f"{base_name} [{table_name}]" if table_name is not None else base_name
            return df, label

        elif ext == ".json":
            try:
                df = pd.read_json(source)
            except ValueError:
                df = pd.read_json(source, lines=True)
            return df, base_name

        elif ext in [".db", ".sqlite", ".sqlite3"]:
            conn = sqlite3.connect(source)
            if not table_name:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
                tables = [r[0] for r in cursor.fetchall()]
                if not tables:
                    conn.close()
                    raise ValueError(f"No tables found in SQLite database {source}")
                table_name = tables[0]
            df = pd.read_sql_query(f'SELECT * FROM "{table_name}"', conn)
            conn.close()
            return df, f"{base_name} [{table_name}]"

        else:
            raise ValueError(f"Unsupported dataset format: {ext}")

    @classmethod
    def profile_all_sources(cls, source_path: str, max_sample_rows: int = 2000) -> List[DatasetProfile]:
        """Profiles all sheets in an Excel workbook or all tables in an SQLite DB."""
        sub_items = cls.list_sheets_or_tables(source_path)
        if len(sub_items) > 1:
            profiles = []
            for item in sub_items:
                try:
                    prof = cls.profile_dataset(source_path, table_name=item, max_sample_rows=max_sample_rows)
                    if prof.row_count > 0 and prof.column_count > 0:
                        profiles.append(prof)
                except Exception:
                    continue
            if profiles:
                return profiles

        return [cls.profile_dataset(source_path, max_sample_rows=max_sample_rows)]

    @classmethod
    def profile_dataset(cls, source: Any, table_name: Optional[str] = None, max_sample_rows: int = 2000) -> DatasetProfile:
        """Profiles a complete dataset for schema matching and transformation."""
        df, source_name = cls.load_dataset(source, table_name)
        row_count = len(df)
        col_count = len(df.columns)

        # Work on a sample for fast profiling if dataset is huge
        sample_df = df.iloc[:max_sample_rows] if row_count > max_sample_rows else df

        column_profiles: Dict[str, ColumnProfile] = {}
        column_order: List[str] = []

        for idx, col in enumerate(df.columns):
            col_str = str(col).strip()
            series = sample_df[col]
            full_series = df[col]
            profile = cls.profile_column(col_str, series, full_series, idx)
            column_profiles[col_str] = profile
            column_order.append(col_str)

        # Generate a stable fingerprint based on column names and detected types
        fp_str = "|".join(f"{c}:{column_profiles[c].inferred_type}" for c in column_order)
        fingerprint = hashlib.md5(fp_str.encode("utf-8")).hexdigest()

        return DatasetProfile(
            source_name=source_name,
            row_count=row_count,
            column_count=col_count,
            columns=column_profiles,
            column_order=column_order,
            fingerprint=fingerprint,
            raw_df=df
        )

    @classmethod
    def profile_column(cls, col_name: str, sample_series: pd.Series, full_series: pd.Series, idx: int) -> ColumnProfile:
        """Profiles a single column."""
        total_count = len(full_series)
        null_count = int(full_series.isna().sum())
        null_ratio = (null_count / total_count) if total_count > 0 else 1.0

        # Non-null non-empty string values for inspection
        non_null = sample_series.dropna().astype(str).str.strip()
        non_null = non_null[non_null != ""]
        sample_vals = non_null.head(15).tolist()

        unique_sample_count = len(non_null.unique())
        unique_full_count = int(full_series.nunique(dropna=True))
        uniqueness_ratio = (unique_full_count / total_count) if total_count > 0 else 0.0

        # Normalized name & token decomposition
        normalized_name, tokens = cls.normalize_and_tokenize(col_name)

        # Type and format inference
        inferred_type, detected_format = cls.infer_type_and_format(sample_vals, non_null)

        # High uniqueness + id-like name suggests candidate key
        is_key = (uniqueness_ratio >= 0.95 or unique_sample_count == len(non_null)) and (
            any(t in ["id", "identifier", "code", "key", "number", "no"] for t in tokens) or
            col_name.lower().endswith("_id") or
            col_name.lower() == "id"
        )

        regex_sig = cls.compute_regex_signature(sample_vals)

        return ColumnProfile(
            name=col_name,
            original_index=idx,
            inferred_type=inferred_type,
            normalized_name=normalized_name,
            tokens=tokens,
            sample_values=sample_vals,
            unique_count=unique_full_count,
            null_count=null_count,
            total_count=total_count,
            uniqueness_ratio=uniqueness_ratio,
            null_ratio=null_ratio,
            detected_format=detected_format,
            value_regex_signature=regex_sig,
            is_candidate_key=is_key
        )

    @classmethod
    def normalize_and_tokenize(cls, name: str) -> Tuple[str, Set[str]]:
        """Splits column names on underscores, hyphens, camelCase, and maps abbreviations."""
        # Convert camelCase to snake_case
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        clean = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
        clean = re.sub(r"[^a-z0-9_]+", "_", clean).strip("_")

        raw_tokens = [t for t in clean.split("_") if t]
        expanded_tokens: Set[str] = set()

        for t in raw_tokens:
            expanded_tokens.add(t)
            if t in COMMON_ABBREVIATIONS:
                for exp in COMMON_ABBREVIATIONS[t].split():
                    expanded_tokens.add(exp)

        norm_name = "_".join(raw_tokens)
        return norm_name, expanded_tokens

    @classmethod
    def infer_type_and_format(cls, sample_values: List[str], series: pd.Series) -> Tuple[str, Optional[str]]:
        """Infers the semantic data type and specific format string if applicable."""
        if not sample_values:
            return "text", None

        # Check boolean
        bool_positives = {"true", "false", "yes", "no", "y", "n", "1", "0", "t", "f"}
        if all(v.lower() in bool_positives for v in sample_values):
            return "boolean", None

        # Check datetime
        dt_format = cls.detect_datetime_format(sample_values)
        if dt_format:
            return "datetime", dt_format

        # Check email
        email_matches = sum(1 for v in sample_values if EMAIL_REGEX.match(v))
        if email_matches / len(sample_values) >= 0.7:
            return "email", None

        # Check phone
        phone_matches = sum(1 for v in sample_values if PHONE_REGEX.match(v) and len(re.sub(r"\D", "", v)) >= 7)
        if phone_matches / len(sample_values) >= 0.7:
            return "phone", None

        # Check currency
        curr_matches = sum(1 for v in sample_values if CURRENCY_REGEX.match(v) and any(c in v for c in "$€£¥₹,"))
        if curr_matches / len(sample_values) >= 0.7:
            # Check decimal places
            sample_clean = [re.sub(r"[^\d.]", "", v) for v in sample_values]
            has_decimals = any("." in v for v in sample_clean)
            return "currency", "2_decimals" if has_decimals else "integer"

        # Check numeric integer vs float
        numeric_clean = []
        is_numeric = True
        for v in sample_values:
            clean_v = v.replace(",", "")
            try:
                val = float(clean_v)
                numeric_clean.append(val)
            except ValueError:
                is_numeric = False
                break

        if is_numeric and numeric_clean:
            is_all_int = all(v.is_integer() for v in numeric_clean)
            return ("numeric_int", None) if is_all_int else ("numeric_float", None)

        # Check categorical
        if len(series) > 0:
            distinct_ratio = len(series.unique()) / len(series)
            if distinct_ratio < 0.2 and len(series.unique()) <= 30:
                return "categorical", None

        return "text", None

    @classmethod
    def detect_datetime_format(cls, sample_values: List[str]) -> Optional[str]:
        """Tries to find a strftime format that matches >=80% of sample values."""
        if not sample_values:
            return None

        for fmt in DATE_FORMAT_CANDIDATES:
            matched = 0
            for val in sample_values:
                try:
                    datetime.datetime.strptime(val, fmt)
                    matched += 1
                except (ValueError, TypeError):
                    pass
            if matched / len(sample_values) >= 0.75:
                return fmt

        # Try pandas fallback for ISO or complex strings
        pd_parsed = 0
        for val in sample_values:
            try:
                # Disallow purely numeric strings from parsing as dates (e.g. 1000)
                if val.isdigit() and len(val) < 8:
                    continue
                pd.to_datetime(val)
                pd_parsed += 1
            except Exception:
                pass

        if pd_parsed / len(sample_values) >= 0.8:
            return "iso8601_auto"

        return None

    @classmethod
    def compute_regex_signature(cls, sample_values: List[str]) -> str:
        """Generates a structural character pattern mask (e.g., DDD-DD-DDDD, AAAAA, etc.)."""
        if not sample_values:
            return ""
        v = sample_values[0]
        # Replace digits with D, letters with A
        sig = re.sub(r"\d", "D", v)
        sig = re.sub(r"[A-Za-z]", "A", sig)
        return sig
