"""
core/data_harmonizer/profiler.py
================================
Statistical and semantic data profiler.
Extracts schema structure, inferred types, datetime formats, value distribution signatures,
and normalized tokens from tabular datasets (CSV, Excel, JSON, SQLite).
"""

import csv
import io
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
    source_path: Optional[str] = None
    table_name: Optional[str] = None
    is_sampled: bool = False


class DataProfiler:
    """Profiles tabular data from files or databases for intelligent mapping."""

    @staticmethod
    def _read_text_file(source: str) -> str:
        """Reads a text file trying common encodings."""
        for encoding in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
            try:
                with open(source, "r", encoding=encoding, errors="replace") as fh:
                    return fh.read()
            except Exception:
                continue
        raise ValueError(f"Could not read file: {source}")

    @staticmethod
    def parse_sql_dump(
        source: str,
        target_tables: Optional[Set[str]] = None,
        max_rows_per_table: Optional[int] = 5000,
        max_file_read_mb: int = 100
    ) -> Dict[str, pd.DataFrame]:
        """Parses a plain-text .sql dump file and returns a dict of {table_name: DataFrame}.

        Args:
            source: Path to the .sql file.
            target_tables: Optional set/iterable of table names to parse. If provided,
                INSERT statements for other tables are skipped without parsing VALUES.
            max_rows_per_table: Maximum rows to sample per table for profiling (prevents OOM on huge dumps).
            max_file_read_mb: Max megabytes to read if dump is massive.
        """
        target_set = set(target_tables) if target_tables is not None else None
        
        IDENT = r'(?:[`"\[]?\w+[`"\]]?\.)?[`"\[]?(\w+)[`"\]]?'
        CREATE_HEAD_RE = re.compile(
            r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?' + IDENT + r'\s*\(',
            re.IGNORECASE
        )
        COL_DEF_RE = re.compile(
            r'^\s*[`"\[]?(\w+)[`"\]]?\s+\w',
            re.IGNORECASE
        )
        INSERT_HEAD_RE = re.compile(
            r'INSERT\s+(?:IGNORE\s+)?INTO\s+' + IDENT + r'\s*(?:\(([^)]+)\))?\s*VALUES\s*',
            re.IGNORECASE
        )

        # For huge dumps (> max_file_read_mb), stream line-by-line keeping all CREATE TABLE
        # statements and a bounded number of INSERT chunks per table so later tables are never cut off.
        file_size_mb = os.path.getsize(source) / (1024 * 1024) if os.path.exists(source) else 0
        if file_size_mb > max_file_read_mb:
            chunks: List[str] = []
            insert_lines_per_table: Dict[str, int] = {}
            in_create = False
            cur_insert_table: Optional[str] = None
            keep_cur_insert = False
            for encoding in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
                try:
                    with open(source, "r", encoding=encoding, errors="replace") as fh:
                        for line in fh:
                            if not in_create and cur_insert_table is None:
                                cm = CREATE_HEAD_RE.search(line)
                                if cm:
                                    tname = cm.group(1)
                                    if target_set is None or tname in target_set:
                                        chunks.append(line)
                                        if ";" not in line:
                                            in_create = True
                                    continue
                                im = INSERT_HEAD_RE.search(line)
                                if im:
                                    tname = im.group(1)
                                    if (target_set is None or tname in target_set) and insert_lines_per_table.get(tname, 0) < 50:
                                        insert_lines_per_table[tname] = insert_lines_per_table.get(tname, 0) + 1
                                        chunks.append(line)
                                        if not line.rstrip().endswith(";"):
                                            cur_insert_table = tname
                                            keep_cur_insert = True
                                    elif not line.rstrip().endswith(";"):
                                        cur_insert_table = tname
                                        keep_cur_insert = False
                                    continue
                            elif in_create:
                                chunks.append(line)
                                if ";" in line:
                                    in_create = False
                            elif cur_insert_table is not None:
                                if keep_cur_insert:
                                    chunks.append(line)
                                if line.rstrip().endswith(";"):
                                    cur_insert_table = None
                                    keep_cur_insert = False
                    break
                except Exception:
                    chunks.clear()
                    continue
            text = "".join(chunks)
        else:
            text = DataProfiler._read_text_file(source)

        # --- Parse CREATE TABLE definitions (fast paren-matching, no DOTALL regex) ---
        table_columns: Dict[str, List[str]] = {}
        n = len(text)
        for m in CREATE_HEAD_RE.finditer(text):
            tname = m.group(1)
            if target_set is not None and tname not in target_set:
                continue
            # Find matching closing paren
            pos = m.end()
            depth = 1
            while pos < n and depth > 0:
                ch = text[pos]
                if ch == "'" or ch == '"':
                    q = ch
                    pos += 1
                    while pos < n:
                        nxt = text.find(q, pos)
                        if nxt == -1:
                            pos = n
                            break
                        if text[nxt - 1] == '\\':
                            pos = nxt + 1
                            continue
                        pos = nxt + 1
                        break
                    continue
                elif ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                pos += 1
            body = text[m.end():pos - 1] if depth == 0 else ""
            cols = []
            for line in body.split('\n'):
                line = line.strip().rstrip(',')
                if not line or line.startswith('--'):
                    continue
                upper = line.upper().lstrip()
                if any(upper.startswith(kw) for kw in (
                    'PRIMARY', 'UNIQUE', 'KEY', 'INDEX', 'CONSTRAINT',
                    'FOREIGN', 'CHECK', 'FULLTEXT', 'SPATIAL'
                )):
                    continue
                cm = COL_DEF_RE.match(line)
                if cm:
                    cols.append(cm.group(1))
            if cols:
                table_columns[tname] = cols

        def _parse_values_from(start_pos: int) -> List[List[Optional[str]]]:
            """Parses (...),(...); starting right after VALUES using fast str.find for strings."""
            rows: List[List[Optional[str]]] = []
            i = start_pos
            while i < n:
                ch = text[i]
                if ch in ' \t\r\n,':
                    i += 1
                    continue
                if ch == ';':
                    break
                if ch != '(':
                    break
                # Parse one row tuple (...)
                i += 1
                row: List[Optional[str]] = []
                while i < n:
                    # Skip leading whitespace
                    while i < n and text[i] in ' \t\r\n':
                        i += 1
                    if i >= n:
                        break
                    ch = text[i]
                    if ch == "'" or ch == '"':
                        q = ch
                        i += 1
                        start = i
                        has_escape = False
                        while i < n:
                            nxt = text.find(q, i)
                            if nxt == -1:
                                i = n
                                break
                            # Count preceding backslashes
                            bs = 0
                            k = nxt - 1
                            while k >= start and text[k] == '\\':
                                bs += 1
                                k -= 1
                            if bs % 2 == 1:
                                has_escape = True
                                i = nxt + 1
                                continue
                            # Handle doubled quote '' or ""
                            if nxt + 1 < n and text[nxt + 1] == q:
                                has_escape = True
                                i = nxt + 2
                                continue
                            val = text[start:nxt]
                            if has_escape:
                                val = (
                                    val.replace("\\'", "'")
                                       .replace('\\"', '"')
                                       .replace("''", "'")
                                       .replace('\\n', '\n')
                                       .replace('\\t', '\t')
                                       .replace('\\\\', '\\')
                                )
                            row.append(val)
                            i = nxt + 1
                            break
                        # Advance to next comma or closing paren
                        while i < n and text[i] in ' \t\r\n':
                            i += 1
                        if i < n and text[i] == ',':
                            i += 1
                            continue
                        elif i < n and text[i] == ')':
                            i += 1
                            break
                    elif ch == ')':
                        # Empty or trailing
                        i += 1
                        break
                    else:
                        # Unquoted token (number, NULL, function call, etc.)
                        start = i
                        depth = 0
                        while i < n:
                            c2 = text[i]
                            if c2 == '(':
                                depth += 1
                            elif c2 == ')':
                                if depth == 0:
                                    break
                                depth -= 1
                            elif c2 == ',' and depth == 0:
                                break
                            i += 1
                        tok = text[start:i].strip()
                        row.append(None if tok.upper() == 'NULL' or tok == '' else tok)
                        if i < n and text[i] == ',':
                            i += 1
                            continue
                        elif i < n and text[i] == ')':
                            i += 1
                            break
                rows.append(row)
            return rows

        # --- Parse INSERT INTO statements ---
        table_rows: Dict[str, List[List[Optional[str]]]] = {}
        table_col_map: Dict[str, List[str]] = {}

        for m in INSERT_HEAD_RE.finditer(text):
            tname = m.group(1)
            if target_set is not None and tname not in target_set:
                continue

            # Early skip if table has already reached sample quota
            if max_rows_per_table and tname in table_rows and len(table_rows[tname]) >= max_rows_per_table:
                continue

            col_part = m.group(2)
            if col_part:
                cols = [c.strip().strip('`"[]') for c in col_part.split(',')]
            elif tname in table_columns:
                cols = table_columns[tname]
            else:
                cols = None

            parsed_rows = _parse_values_from(m.end())
            if max_rows_per_table:
                curr_len = len(table_rows.get(tname, []))
                parsed_rows = parsed_rows[:max(0, max_rows_per_table - curr_len)]

            if tname not in table_rows:
                table_rows[tname] = []
                table_col_map[tname] = cols or []
            if not table_col_map[tname] and cols:
                table_col_map[tname] = cols
            table_rows[tname].extend(parsed_rows)

        # --- Build DataFrames ---
        result: Dict[str, pd.DataFrame] = {}
        for tname, rows in table_rows.items():
            cols = table_col_map.get(tname) or []
            max_width = max((len(r) for r in rows), default=0)
            if not cols:
                cols = [f"col_{i+1}" for i in range(max_width)]
            if rows and any(len(r) != len(cols) for r in rows):
                n_cols = len(cols)
                rows = [
                    r + [None] * (n_cols - len(r)) if len(r) < n_cols else r[:n_cols]
                    for r in rows
                ]
            result[tname] = pd.DataFrame(rows, columns=cols)

        # Include empty tables from CREATE TABLE if they had no INSERT rows
        for tname, cols in table_columns.items():
            if tname not in result:
                result[tname] = pd.DataFrame(columns=cols)

        return result

    @staticmethod
    def list_sheets_or_tables(source: str) -> List[str]:
        """Returns sheet names (Excel) or table names (SQLite/.sql dump). Returns empty list for CSV/JSON."""
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
        elif ext == ".sql":
            try:
                pattern = re.compile(
                    r'\b(?:CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?|INSERT\s+(?:IGNORE\s+)?INTO\s+)(?:[`"\[]?\w+[`"\]]?\.)?[`"\[]?(\w+)[`"\]]?',
                    re.IGNORECASE
                )
                seen: Dict[str, None] = {}
                for encoding in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
                    try:
                        with open(source, "r", encoding=encoding, errors="replace") as fh:
                            for line in fh:
                                if "CREATE" in line.upper() or "INSERT" in line.upper():
                                    for m in pattern.finditer(line):
                                        seen[m.group(1)] = None
                        break
                    except Exception:
                        continue
                return list(seen.keys())
            except Exception:
                return []
        return []

    @staticmethod
    def load_dataset(source: Any, table_name: Optional[str] = None, max_sample_rows: Optional[int] = None) -> Tuple[pd.DataFrame, str]:
        """Loads dataset into a Pandas DataFrame from file path, DataFrame, or SQLite DB.

        Supports specific sheet names for Excel via table_name, and row sampling for large DBs.
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
                    df = pd.read_csv(source, sep=sep, encoding=encoding, engine="python", on_bad_lines="skip", nrows=max_sample_rows)
                    return df, base_name
                except Exception:
                    continue
            # Fallback
            df = pd.read_csv(source, encoding="utf-8", on_bad_lines="skip", nrows=max_sample_rows)
            return df, base_name

        elif ext in [".xlsx", ".xls", ".xlsm"]:
            sheet = table_name if table_name is not None else 0
            df = pd.read_excel(source, sheet_name=sheet, nrows=max_sample_rows)
            label = f"{base_name} [{table_name}]" if table_name is not None else base_name
            return df, label

        elif ext == ".json":
            try:
                df = pd.read_json(source)
                if max_sample_rows and len(df) > max_sample_rows:
                    df = df.head(max_sample_rows)
            except ValueError:
                df = pd.read_json(source, lines=True, nrows=max_sample_rows)
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
            limit_clause = f" LIMIT {max_sample_rows}" if max_sample_rows else ""
            df = pd.read_sql_query(f'SELECT * FROM "{table_name}"{limit_clause}', conn)
            conn.close()
            return df, f"{base_name} [{table_name}]"

        elif ext == ".sql":
            target_set = {table_name} if table_name else None
            tables = DataProfiler.parse_sql_dump(
                source, target_tables=target_set, max_rows_per_table=max_sample_rows or 5000
            )
            if not tables:
                raise ValueError(f"No tables or INSERT data found in SQL dump: {source}")
            if table_name:
                if table_name not in tables:
                    raise ValueError(f"Table '{table_name}' not found in SQL dump. Available: {list(tables.keys())}")
                df = tables[table_name]
            else:
                table_name = next(iter(tables))
                df = tables[table_name]
            return df, f"{base_name} [{table_name}]"

        else:
            raise ValueError(f"Unsupported dataset format: {ext}")

    @classmethod
    def profile_selected_sources(
        cls,
        source_path: str,
        table_names: List[str],
        max_sample_rows: int = 2000,
    ) -> List[DatasetProfile]:
        """Batch-profiles selected sheets/tables from a multi-table file in a single open/parse pass."""
        if not table_names:
            return []

        base_name = os.path.basename(source_path)
        ext = os.path.splitext(source_path)[1].lower()
        profiles: List[DatasetProfile] = []

        if ext == ".sql":
            tables = cls.parse_sql_dump(source_path, target_tables=set(table_names))
            for tname in table_names:
                if tname in tables:
                    profiles.append(
                        cls._profile_dataframe(tables[tname], f"{base_name} [{tname}]", max_sample_rows)
                    )
            return profiles

        if ext in [".db", ".sqlite", ".sqlite3"]:
            conn = sqlite3.connect(source_path)
            try:
                for tname in table_names:
                    df = pd.read_sql_query(f'SELECT * FROM "{tname}"', conn)
                    profiles.append(
                        cls._profile_dataframe(df, f"{base_name} [{tname}]", max_sample_rows)
                    )
            finally:
                conn.close()
            return profiles

        if ext in [".xlsx", ".xls", ".xlsm"]:
            with pd.ExcelFile(source_path) as ef:
                for tname in table_names:
                    df = ef.parse(tname)
                    profiles.append(
                        cls._profile_dataframe(df, f"{base_name} [{tname}]", max_sample_rows)
                    )
            return profiles

        for tname in table_names:
            profiles.append(cls.profile_dataset(source_path, table_name=tname, max_sample_rows=max_sample_rows))
        return profiles

    @classmethod
    def profile_all_sources(cls, source_path: str, max_sample_rows: int = 2000) -> List[DatasetProfile]:
        """Profiles all sheets in an Excel workbook or all tables in an SQLite/.sql DB."""
        sub_items = cls.list_sheets_or_tables(source_path)
        if len(sub_items) > 1:
            try:
                profs = cls.profile_selected_sources(source_path, sub_items, max_sample_rows=max_sample_rows)
                valid = [p for p in profs if p.row_count > 0 and p.column_count > 0]
                if valid:
                    return valid
            except Exception:
                pass

        return [cls.profile_dataset(source_path, max_sample_rows=max_sample_rows)]

    @classmethod
    def profile_dataset(
        cls,
        source: Any,
        table_name: Optional[str] = None,
        max_sample_rows: int = 2000,
        source_name: Optional[str] = None
    ) -> DatasetProfile:
        """Profiles a complete dataset for schema matching and transformation."""
        df, inferred_name = cls.load_dataset(source, table_name)
        final_name = source_name if source_name is not None else inferred_name
        return cls._profile_dataframe(df, final_name, max_sample_rows=max_sample_rows)

    @classmethod
    def _profile_dataframe(
        cls,
        df: pd.DataFrame,
        source_name: str,
        max_sample_rows: int = 2000,
    ) -> DatasetProfile:
        """Profiles an already-loaded DataFrame."""
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
