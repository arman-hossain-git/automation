"""
core/data_harmonizer/pool_harmonizer.py
======================================
Autonomous Pool-to-Pool Data Harmonization Engine.

Scans an entire directory of mixed source datasets and relational databases (CSV, XLSX,
SQLite, JSON, SQL dumps), catalogs them into an offline relational graph, and automatically
matches, joins, unions, and transforms data into outputs for every sample template
found in a templates directory with zero or minimal user intervention.
"""

import os
import glob
import logging
from typing import List, Dict, Any, Optional, Tuple, Set, Callable
from dataclasses import dataclass, field
import pandas as pd

from .profiler import DataProfiler, DatasetProfile, ColumnProfile
from .combiner import MultiSourceCombiner
from .matcher import SchemaMatcher, ColumnMappingMatch
from .transformer import DataTransformer
from .adaptive_memory import harmonizer_memory

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".csv", ".tsv", ".txt", ".xlsx", ".xls", ".xlsm",
    ".json", ".db", ".sqlite", ".sqlite3", ".sql"
}


IGNORE_DIR_NAMES = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".vscode", ".idea",
    ".pytest_cache", "site-packages", "AppData", "Windows"
}

IGNORE_FILE_SUFFIXES = {
    ".db-wal", ".db-shm", ".db-journal", ".tmp", ".log"
}


@dataclass
class PoolHarmonizationResult:
    """Outcome of harmonizing the source pool against a single target sample template."""
    template_name: str
    template_profile: DatasetProfile
    source_tables_used: List[str]
    combination_mode: str  # 'single', 'union', 'join', 'no_match'
    join_details: Dict[str, Any]
    matches: List[ColumnMappingMatch]
    output_df: pd.DataFrame
    completeness_score: float  # 0.0 to 1.0
    matched_columns_count: int
    total_columns_count: int
    row_count: int
    status: str  # 'success', 'partial', 'empty', 'error'
    message: str = ""
    source_file_paths: List[str] = field(default_factory=list)


class PoolSourceScanner:
    """Discovers, loads, and profiles all tabular files and database tables in a directory."""

    @classmethod
    def scan_directory(
        cls,
        directory_path: str,
        recursive: bool = True,
        max_rows_profile: Optional[int] = 5000,
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> List[DatasetProfile]:
        """Scans a directory for all supported files and database tables.

        Returns a list of DatasetProfiles (one per flat file, Excel sheet, or SQLite table).
        """
        if not os.path.isdir(directory_path):
            raise FileNotFoundError(f"Source pool directory not found: {directory_path}")

        # Gather files while filtering out system and virtualenv directories
        candidate_files: List[str] = []
        for root, dirs, files in os.walk(directory_path):
            # Prune ignored directories in-place
            dirs[:] = [d for d in dirs if d not in IGNORE_DIR_NAMES and not d.startswith(".")]
            for f in files:
                base = f
                if base.startswith("~$") or base.startswith("."):
                    continue
                lower_f = f.lower()
                if any(lower_f.endswith(suf) for suf in IGNORE_FILE_SUFFIXES):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    candidate_files.append(os.path.join(root, f))
            if not recursive:
                break

        profiles: List[DatasetProfile] = []
        total_files = len(candidate_files)

        for idx, file_path in enumerate(candidate_files, start=1):
            base_name = os.path.basename(file_path)
            if progress_callback:
                progress_callback(f"Scanning: {base_name}", idx, total_files)

            ext = os.path.splitext(file_path)[1].lower()
            try:
                # 1. Plain Text SQL Dump (.sql): Parse once with row cap to avoid freeze
                if ext == ".sql":
                    tables = DataProfiler.parse_sql_dump(file_path, max_rows_per_table=max_rows_profile)
                    for tname, df in tables.items():
                        if df is not None and len(df.columns) > 0:
                            label = f"{base_name} [{tname}]"
                            p = DataProfiler.profile_dataset(df, source_name=label, max_sample_rows=max_rows_profile or 5000)
                            p.source_path = file_path
                            p.table_name = tname
                            p.is_sampled = bool(max_rows_profile and len(df) >= max_rows_profile)
                            profiles.append(p)
                    continue

                # 2. SQLite Database (.db, .sqlite, .sqlite3): Fast sample querying
                if ext in [".db", ".sqlite", ".sqlite3"]:
                    sub_items = DataProfiler.list_sheets_or_tables(file_path)
                    if sub_items:
                        import sqlite3
                        conn = sqlite3.connect(file_path)
                        for item_name in sub_items:
                            try:
                                cur = conn.cursor()
                                cur.execute(f'SELECT count(*) FROM "{item_name}"')
                                row_cnt = cur.fetchone()[0]
                                limit_clause = f' LIMIT {max_rows_profile}' if (max_rows_profile and row_cnt > max_rows_profile) else ''
                                df = pd.read_sql_query(f'SELECT * FROM "{item_name}"{limit_clause}', conn)
                                if df is not None and len(df.columns) > 0:
                                    label = f"{base_name} [{item_name}]"
                                    p = DataProfiler.profile_dataset(df, source_name=label, max_sample_rows=max_rows_profile or 5000)
                                    p.row_count = row_cnt  # preserve accurate total row count
                                    p.source_path = file_path
                                    p.table_name = item_name
                                    p.is_sampled = bool(max_rows_profile and row_cnt > len(df))
                                    profiles.append(p)
                            except Exception as sub_err:
                                logger.warning(f"Could not load table {item_name} from {base_name}: {sub_err}")
                        conn.close()
                    continue

                # 3. Excel Multi-Tab (.xlsx, .xls, .xlsm)
                if ext in [".xlsx", ".xls", ".xlsm"]:
                    sub_items = DataProfiler.list_sheets_or_tables(file_path)
                    if sub_items:
                        for item_name in sub_items:
                            try:
                                df, label = DataProfiler.load_dataset(file_path, table_name=item_name, max_sample_rows=max_rows_profile)
                                if df is not None and not df.empty:
                                    p = DataProfiler.profile_dataset(df, source_name=label, max_sample_rows=max_rows_profile or 5000)
                                    p.source_path = file_path
                                    p.table_name = item_name
                                    p.is_sampled = bool(max_rows_profile and len(df) >= max_rows_profile)
                                    profiles.append(p)
                            except Exception as sub_err:
                                logger.warning(f"Could not load sheet {item_name} from {base_name}: {sub_err}")
                        continue

                # 4. Flat formats (CSV, TSV, JSON, etc.)
                df, label = DataProfiler.load_dataset(file_path, max_sample_rows=max_rows_profile)
                if df is not None and not df.empty:
                    p = DataProfiler.profile_dataset(df, source_name=label, max_sample_rows=max_rows_profile or 5000)
                    p.source_path = file_path
                    p.table_name = None
                    p.is_sampled = bool(max_rows_profile and len(df) >= max_rows_profile)
                    profiles.append(p)

            except Exception as err:
                logger.warning(f"Error scanning file {file_path}: {err}")

        logger.info(f"Pool scanner discovered {len(profiles)} tables/datasets across {total_files} files.")
        return profiles


class TemplatePoolScanner:
    """Scans and profiles target sample template files in a directory."""

    @classmethod
    def scan_templates(
        cls,
        template_dir: str,
        recursive: bool = False,
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> List[DatasetProfile]:
        """Loads and profiles all target sample templates in the folder."""
        if not os.path.isdir(template_dir):
            raise FileNotFoundError(f"Template directory not found: {template_dir}")

        files: List[str] = []
        pattern = os.path.join(template_dir, "**", "*") if recursive else os.path.join(template_dir, "*")
        for path in glob.glob(pattern, recursive=recursive):
            if os.path.isfile(path):
                base = os.path.basename(path)
                if base.startswith("~$") or base.startswith("."):
                    continue
                ext = os.path.splitext(path)[1].lower()
                if ext in [".csv", ".tsv", ".xlsx", ".xls", ".xlsm", ".json"]:
                    files.append(path)

        template_profiles: List[DatasetProfile] = []
        total = len(files)

        for idx, file_path in enumerate(files, start=1):
            base_name = os.path.basename(file_path)
            if progress_callback:
                progress_callback(f"Analyzing Template: {base_name}", idx, total)

            ext = os.path.splitext(file_path)[1].lower()
            try:
                if ext in [".xlsx", ".xls", ".xlsm"]:
                    sheets = DataProfiler.list_sheets_or_tables(file_path)
                    for s in sheets:
                        df, label = DataProfiler.load_dataset(file_path, table_name=s)
                        if df is not None:
                            tp = DataProfiler.profile_dataset(df, source_name=label)
                            template_profiles.append(tp)
                else:
                    df, label = DataProfiler.load_dataset(file_path)
                    if df is not None:
                        tp = DataProfiler.profile_dataset(df, source_name=label)
                        template_profiles.append(tp)
            except Exception as err:
                logger.warning(f"Failed to profile template {file_path}: {err}")

        return template_profiles


class PoolJoinGraph:
    """Discovers and caches relational join connections between all datasets in the pool."""

    def __init__(self, profiles: List[DatasetProfile]):
        self.profiles = {p.source_name: p for p in profiles}
        # Adjacency map: source_name -> List of join edges
        self.edges: Dict[str, List[Dict[str, Any]]] = {p.source_name: [] for p in profiles}
        self._build_graph()

    def _build_graph(self):
        """Precomputes join relationships across all source tables."""
        p_list = list(self.profiles.values())
        for i in range(len(p_list)):
            for j in range(i + 1, len(p_list)):
                p1, p2 = p_list[i], p_list[j]
                candidates = MultiSourceCombiner.find_candidate_join_keys(p1, p2)
                if candidates:
                    best = candidates[0]
                    if best.get("score", 0) >= 0.35:
                        edge_1to2 = {
                            "target": p2.source_name,
                            "primary_key": best["primary_col"],
                            "secondary_key": best["secondary_col"],
                            "score": best["score"]
                        }
                        edge_2to1 = {
                            "target": p1.source_name,
                            "primary_key": best["secondary_col"],
                            "secondary_key": best["primary_col"],
                            "score": best["score"]
                        }
                        self.edges[p1.source_name].append(edge_1to2)
                        self.edges[p2.source_name].append(edge_2to1)

    def find_join_plan(self, primary_table: str, required_tables: List[str]) -> List[Tuple[str, str, str]]:
        """Finds join sequence: List of (source_a, key_a, source_b, key_b) connecting primary to required tables."""
        plan: List[Tuple[str, str, str]] = []
        connected = {primary_table}

        for req in required_tables:
            if req in connected:
                continue
            # Look for direct edge from any already connected table
            found = False
            for conn_table in list(connected):
                for edge in self.edges.get(conn_table, []):
                    if edge["target"] == req:
                        plan.append((conn_table, edge["primary_key"], req, edge["secondary_key"]))
                        connected.add(req)
                        found = True
                        break
                if found:
                    break

            # Fallback: check candidate keys on demand
            if not found and primary_table in self.profiles and req in self.profiles:
                cands = MultiSourceCombiner.find_candidate_join_keys(
                    self.profiles[primary_table], self.profiles[req]
                )
                if cands:
                    best = cands[0]
                    plan.append((primary_table, best["primary_col"], req, best["secondary_col"]))
                    connected.add(req)

        return plan


class PoolHarmonizerEngine:
    """Autonomous Engine that orchestrates pool-to-pool dataset matching, joining, and transformation."""

    def __init__(self, learning_engine=None):
        self.learning_engine = learning_engine
        self.matcher = SchemaMatcher(learning_engine=learning_engine)

    def harmonize_pool(
        self,
        source_profiles: List[DatasetProfile],
        template_profiles: List[DatasetProfile],
        min_confidence: float = 0.35,
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> List[PoolHarmonizationResult]:
        """Harmonizes the pool of sources against every template in template_profiles."""
        if not source_profiles:
            raise ValueError("No source datasets provided in the pool.")
        if not template_profiles:
            raise ValueError("No target sample templates provided.")

        # Prefer populated tables when harmonizing so empty schema-only tables don't shadow real data
        populated_sources = [sp for sp in source_profiles if sp.row_count > 0]
        active_sources = populated_sources if populated_sources else source_profiles

        results: List[PoolHarmonizationResult] = []
        join_graph = PoolJoinGraph(active_sources)
        total_templates = len(template_profiles)
        full_df_cache: Dict[Tuple[str, Optional[str]], pd.DataFrame] = {}

        for idx, template in enumerate(template_profiles, start=1):
            if progress_callback:
                progress_callback(f"Matching Template: {template.source_name}", idx, total_templates)

            result = self._harmonize_single_template(
                template=template,
                source_profiles=active_sources,
                join_graph=join_graph,
                min_confidence=min_confidence,
                full_df_cache=full_df_cache
            )
            results.append(result)

        return results

    @staticmethod
    def _ensure_full_profile(
        sp: DatasetProfile,
        cache: Optional[Dict[Tuple[str, Optional[str]], pd.DataFrame]] = None,
    ) -> DatasetProfile:
        """Reloads all rows for a sampled DatasetProfile before final combination/transform."""
        if not sp.is_sampled or not sp.source_path or not os.path.exists(sp.source_path):
            return sp

        cache_key = (sp.source_path, sp.table_name)
        if cache is not None and cache_key in cache:
            full_df = cache[cache_key]
        else:
            ext = os.path.splitext(sp.source_path)[1].lower()
            full_df = None
            try:
                if ext == ".sql" and sp.table_name:
                    tables = DataProfiler.parse_sql_dump(
                        sp.source_path,
                        target_tables={sp.table_name},
                        max_rows_per_table=None,
                    )
                    full_df = tables.get(sp.table_name)
                else:
                    full_df, _ = DataProfiler.load_dataset(
                        sp.source_path,
                        table_name=sp.table_name,
                        max_sample_rows=None,
                    )
            except Exception as e:
                logger.warning(f"Could not reload full rows for {sp.source_name}: {e}")
                return sp

            if full_df is None or full_df.empty:
                return sp
            if cache is not None:
                cache[cache_key] = full_df

        from copy import copy
        full_sp = copy(sp)
        full_sp.raw_df = full_df
        full_sp.row_count = len(full_df)
        full_sp.is_sampled = False
        return full_sp

    def _harmonize_single_template(
        self,
        template: DatasetProfile,
        source_profiles: List[DatasetProfile],
        join_graph: PoolJoinGraph,
        min_confidence: float = 0.35,
        full_df_cache: Optional[Dict[Tuple[str, Optional[str]], pd.DataFrame]] = None,
    ) -> PoolHarmonizationResult:
        """Autonomously matches the best combination of source tables for a given template."""
        template_cols = list(template.columns.keys())
        total_cols = len(template_cols)

        if total_cols == 0:
            return PoolHarmonizationResult(
                template_name=template.source_name,
                template_profile=template,
                source_tables_used=[],
                combination_mode="empty",
                join_details={},
                matches=[],
                output_df=pd.DataFrame(),
                completeness_score=0.0,
                matched_columns_count=0,
                total_columns_count=0,
                status="empty",
                message="Template contains no columns."
            )

        # ── Step 1: Score column coverage for each source table ──
        # Find which source tables cover which template columns
        # table_name -> {template_col: (score, source_col, match_info)}
        table_coverage: Dict[str, Dict[str, Tuple[float, str, Any]]] = {}

        for sp in source_profiles:
            table_coverage[sp.source_name] = {}
            # 1. Check composite columns (e.g. First Name + Last Name -> Full Name)
            composite_matches = self.matcher._detect_composite_columns(sp, template)
            for t_col, comp_m in composite_matches.items():
                if comp_m.confidence >= min_confidence:
                    table_coverage[sp.source_name][t_col] = (
                        comp_m.confidence,
                        comp_m.source_column,
                        (comp_m.transformation_rule, comp_m.rule_params)
                    )

            # 2. Quick match preview for each template col against sp
            for t_col, t_cp in template.columns.items():
                if t_col in table_coverage[sp.source_name]:
                    continue
                best_score = 0.0
                best_src_col = None
                best_rule = "direct"
                best_params = {}

                for s_col, s_cp in sp.columns.items():
                    score, reason, rule, params = self.matcher.calculate_similarity(s_cp, t_cp)
                    if score > best_score:
                        best_score = score
                        best_src_col = s_col
                        best_rule = rule
                        best_params = params

                if best_score >= min_confidence and best_src_col:
                    table_coverage[sp.source_name][t_col] = (best_score, best_src_col, (best_rule, best_params))

        # Rank tables by number of matched columns and total score
        table_scores = []
        for t_name, covered in table_coverage.items():
            count = len(covered)
            avg_score = sum(item[0] for item in covered.values()) / max(count, 1)
            table_scores.append((t_name, count, avg_score))

        table_scores.sort(key=lambda x: (x[1], x[2]), reverse=True)

        if not table_scores or table_scores[0][1] == 0:
            # No confident match found anywhere in the pool
            empty_matches = [
                ColumnMappingMatch(
                    sample_column=c,
                    sample_type=template.columns[c].inferred_type,
                    source_column=None,
                    source_type=None,
                    confidence=0.0,
                    tier_reason="No confident source column in pool",
                    transformation_rule="unmapped"
                )
                for c in template_cols
            ]
            return PoolHarmonizationResult(
                template_name=template.source_name,
                template_profile=template,
                source_tables_used=[],
                combination_mode="no_match",
                join_details={},
                matches=empty_matches,
                output_df=pd.DataFrame(columns=template_cols),
                completeness_score=0.0,
                matched_columns_count=0,
                total_columns_count=total_cols,
                status="no_match",
                message="No matching columns found in any pool dataset."
            )

        best_primary_name = table_scores[0][0]
        primary_profile = next(p for p in source_profiles if p.source_name == best_primary_name)
        primary_covered_cols = set(table_coverage[best_primary_name].keys())

        # ── Step 2: Check for Homogeneous Tables (Union Candidate) ──
        # Check if there are other tables in the pool that share nearly identical schema
        # with primary and match the template (e.g., month_1.csv, month_2.csv)
        union_candidates = [primary_profile]
        for sp in source_profiles:
            if sp.source_name == best_primary_name:
                continue
            # Overlap between sp and primary
            sp_covered = set(table_coverage[sp.source_name].keys())
            overlap = len(primary_covered_cols.intersection(sp_covered)) / max(len(primary_covered_cols), 1)
            # Check row schema similarity
            if overlap >= 0.70 and len(sp_covered) >= len(primary_covered_cols) * 0.70:
                union_candidates.append(sp)

        # ── Step 3: Determine Combination Strategy (Single, Union, or Join) ──
        combined_df: Optional[pd.DataFrame] = None
        mode_used = "single"
        join_details: Dict[str, Any] = {}
        tables_used: List[str] = [best_primary_name]

        if len(union_candidates) > 1:
            # Auto-Union across homogeneous pool datasets
            mode_used = "union"
            full_union = [self._ensure_full_profile(p, full_df_cache) for p in union_candidates]
            combined_df, join_details = MultiSourceCombiner.smart_union(full_union)
            tables_used = [p.source_name for p in union_candidates]
        else:
            # Check if columns are missing from primary that exist in secondary tables (Join candidates)
            uncovered_cols = set(template_cols) - primary_covered_cols
            secondary_tables_to_join: List[DatasetProfile] = []

            if uncovered_cols:
                # Find secondary tables that can fill the uncovered columns
                remaining = set(uncovered_cols)
                for t_name, count, _ in table_scores[1:]:
                    if not remaining:
                        break
                    cov = table_coverage[t_name]
                    can_fill = set(cov.keys()).intersection(remaining)
                    if can_fill:
                        sec_prof = next(p for p in source_profiles if p.source_name == t_name)
                        secondary_tables_to_join.append(sec_prof)
                        remaining -= can_fill

            if secondary_tables_to_join:
                # Relational Auto-Join
                mode_used = "join"
                all_to_join = [self._ensure_full_profile(p, full_df_cache) for p in ([primary_profile] + secondary_tables_to_join)]
                combined_df, join_details = MultiSourceCombiner.smart_join(all_to_join)
                tables_used = [p.source_name for p in all_to_join]
            else:
                # Single Table Source
                mode_used = "single"
                full_primary = self._ensure_full_profile(primary_profile, full_df_cache)
                combined_df = full_primary.raw_df.copy() if full_primary.raw_df is not None else pd.DataFrame()
                tables_used = [best_primary_name]

        if combined_df is None or combined_df.empty:
            combined_df = pd.DataFrame()

        # ── Step 4: Profile the combined dataset & execute schema matching ──
        working_profile = DataProfiler.profile_dataset(combined_df, source_name=f"Pool_{mode_used.title()}")
        matches = self.matcher.match_schemas(
            source_profile=working_profile,
            sample_profile=template,
            min_confidence_threshold=min_confidence
        )

        # ── Step 5: Execute Data Transformation ──
        try:
            output_df = DataTransformer.transform(
                source_df=combined_df,
                matches=matches,
                target_column_order=template.column_order,
                exclude_blank=False
            )
        except Exception as trans_err:
            logger.error(f"Transformation failed for template {template.source_name}: {trans_err}")
            output_df = pd.DataFrame(columns=template.column_order)

        # ── Step 6: Compute Quality & Completeness ──
        matched_count = sum(1 for m in matches if m.source_column and m.transformation_rule != "unmapped")
        completeness = round(matched_count / max(total_cols, 1), 3)

        status = "success" if completeness >= 0.80 else ("partial" if completeness > 0.0 else "no_match")
        msg = f"Harmonized {len(output_df):,} rows using {len(tables_used)} source table(s) [{mode_used.upper()}]."

        return PoolHarmonizationResult(
            template_name=template.source_name,
            template_profile=template,
            source_tables_used=tables_used,
            combination_mode=mode_used,
            join_details=join_details,
            matches=matches,
            output_df=output_df,
            completeness_score=completeness,
            matched_columns_count=matched_count,
            total_columns_count=total_cols,
            row_count=len(output_df),
            status=status,
            message=msg
        )

    @classmethod
    def export_all(
        cls,
        results: List[PoolHarmonizationResult],
        output_directory: str,
        file_format: str = "xlsx"
    ) -> List[Dict[str, Any]]:
        """Exports all harmonized results to output files and writes a manifest report."""
        os.makedirs(output_directory, exist_ok=True)
        manifest: List[Dict[str, Any]] = []

        for res in results:
            clean_name = os.path.splitext(res.template_name)[0]
            # Replace invalid path characters
            for ch in [':', '*', '?', '"', '<', '>', '|', '[', ']']:
                clean_name = clean_name.replace(ch, "_")

            filename = f"Harmonized_{clean_name}.{file_format.lower()}"
            out_path = os.path.join(output_directory, filename)

            try:
                if file_format.lower() == "xlsx":
                    res.output_df.to_excel(out_path, index=False)
                elif file_format.lower() == "csv":
                    res.output_df.to_csv(out_path, index=False, encoding="utf-8-sig")
                elif file_format.lower() == "json":
                    res.output_df.to_json(out_path, orient="records", indent=2)

                manifest.append({
                    "template": res.template_name,
                    "output_file": filename,
                    "full_path": out_path,
                    "rows": res.row_count,
                    "columns": res.total_columns_count,
                    "completeness": f"{res.completeness_score * 100:.1f}%",
                    "sources_used": ", ".join(res.source_tables_used),
                    "mode": res.combination_mode,
                    "status": "Exported"
                })
            except Exception as e:
                logger.error(f"Failed to export {filename}: {e}")
                manifest.append({
                    "template": res.template_name,
                    "output_file": filename,
                    "error": str(e),
                    "status": "Failed"
                })

        # Write manifest CSV
        manifest_df = pd.DataFrame(manifest)
        manifest_path = os.path.join(output_directory, "_harmonization_manifest.csv")
        try:
            manifest_df.to_csv(manifest_path, index=False, encoding="utf-8-sig")
        except Exception:
            pass

        return manifest
