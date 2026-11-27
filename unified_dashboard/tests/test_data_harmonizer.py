"""
tests/test_data_harmonizer.py
=============================
Comprehensive unit tests for the Adaptive Dataset Harmonizer engine:
- DataProfiler (type detection, format detection, normalization)
- MultiSourceCombiner (Smart Union and Smart Join)
- SchemaMatcher (token similarity, type scoring, composite detection, learned memory boost)
- DataTransformer (date reformatting, currency cleaning, casing, exports)
- Adaptive Learning Reinforcement Loop (memory storage and recall)
"""

import os
import sys
import tempfile
import sqlite3
import pandas as pd
try:
    import pytest
except ImportError:
    pytest = None

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from core.data_harmonizer.profiler import DataProfiler
from core.data_harmonizer.combiner import MultiSourceCombiner
from core.data_harmonizer.matcher import SchemaMatcher
from core.data_harmonizer.transformer import DataTransformer
from core.data_harmonizer.pool_harmonizer import (
    PoolSourceScanner,
    TemplatePoolScanner,
    PoolJoinGraph,
    PoolHarmonizerEngine,
    PoolHarmonizationResult,
)
from core.learning_system import learning_engine


def test_profiler_types():
    df = pd.DataFrame({
        "student_id": ["S101", "S102", "S103"],
        "dob": ["2001-04-12", "1999-11-23", "2000-01-05"],
        "tuition": ["$1,200.00", "$3,550.50", "$0.00"],
        "email": ["alice@school.edu", "bob@school.edu", "charlie@school.edu"],
        "status": ["Active", "Active", "Inactive"]
    })

    profile = DataProfiler.profile_dataset(df)
    assert profile.column_count == 5
    assert profile.row_count == 3
    assert profile.columns["student_id"].is_candidate_key is True
    assert profile.columns["dob"].inferred_type == "datetime"
    assert profile.columns["dob"].detected_format == "%Y-%m-%d"
    assert profile.columns["tuition"].inferred_type == "currency"
    assert profile.columns["email"].inferred_type == "email"


def test_smart_union():
    f1 = pd.DataFrame({"id": ["A1", "A2"], "name": ["Alice", "Bob"]})
    f2 = pd.DataFrame({"ID": ["B1", "B2"], "Name": ["Charlie", "Diana"]})

    p1 = DataProfiler.profile_dataset(f1)
    p2 = DataProfiler.profile_dataset(f2)

    combined_df, mode, det = MultiSourceCombiner.auto_combine([p1, p2])
    assert mode == "union"
    assert len(combined_df) == 4
    assert "id" in combined_df.columns
    assert "name" in combined_df.columns


def test_smart_join():
    t_students = pd.DataFrame({"student_id": ["S1", "S2"], "full_name": ["Alice Smith", "Bob Jones"]})
    t_scores = pd.DataFrame({"std_id": ["S1", "S2"], "score": [95, 88]})

    p1 = DataProfiler.profile_dataset(t_students)
    p2 = DataProfiler.profile_dataset(t_scores)

    combined_df, mode, det = MultiSourceCombiner.auto_combine([p1, p2])
    assert mode == "join"
    assert len(combined_df) == 2
    assert "score" in combined_df.columns
    assert "full_name" in combined_df.columns


def test_schema_matching_and_composite():
    raw_df = pd.DataFrame({
        "first_name": ["John", "Mary"],
        "last_name": ["Doe", "Public"],
        "bdate": ["1998-02-14", "2000-10-31"],
        "fee": ["$500.00", "$750.00"]
    })

    sample_df = pd.DataFrame({
        "Full Name": ["Jane Doe"],
        "Birth Date": ["01/01/2000"],
        "Fee Amount": [100.0]
    })

    p_raw = DataProfiler.profile_dataset(raw_df)
    p_sample = DataProfiler.profile_dataset(sample_df)

    matcher = SchemaMatcher(learning_engine)
    matches = matcher.match_schemas(p_raw, p_sample)

    match_dict = {m.sample_column: m for m in matches}

    # Verify composite First + Last -> Full Name
    assert "Full Name" in match_dict
    assert match_dict["Full Name"].transformation_rule == "concat"

    # Verify date reformat
    assert "Birth Date" in match_dict
    assert match_dict["Birth Date"].source_column == "bdate"
    assert match_dict["Birth Date"].transformation_rule == "date_reformat"

    # Verify transformation output
    out_df = DataTransformer.transform(raw_df, matches, p_sample.column_order)
    assert list(out_df.columns) == list(sample_df.columns)
    assert out_df["Full Name"].iloc[0] == "John Doe"
    assert out_df["Birth Date"].iloc[0] == "02/14/1998"


def test_adaptive_learning_reinforcement():
    # Record feedback
    learning_engine.record_harmonizer_feedback("user_identifier", "StudentID", confirmed=True)
    conf, hits = learning_engine.get_harmonizer_confidence("user_identifier", "StudentID")
    assert conf == 1.0
    assert hits >= 1

    # Verify that matcher utilizes this learned memory
    raw = pd.DataFrame({"user_identifier": ["U101"], "grade": [90]})
    sample = pd.DataFrame({"StudentID": ["S001"]})

    p_raw = DataProfiler.profile_dataset(raw)
    p_sample = DataProfiler.profile_dataset(sample)

    matcher = SchemaMatcher(learning_engine)
    matches = matcher.match_schemas(p_raw, p_sample)
    assert matches[0].sample_column == "StudentID"
    assert matches[0].source_column == "user_identifier"
    assert matches[0].confidence >= 0.95
    assert "Learned Memory" in matches[0].tier_reason


def test_multitab_excel_profiling_and_combination():
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
            pd.DataFrame({"id": ["S1", "S2"], "first_name": ["Alice", "Bob"]}).to_excel(
                writer, sheet_name="Students", index=False
            )
            pd.DataFrame({"std_id": ["S1", "S2"], "course": ["Math", "Physics"], "grade": ["A", "B"]}).to_excel(
                writer, sheet_name="Enrollments", index=False
            )

        sheets = DataProfiler.list_sheets_or_tables(tmp_path)
        assert sheets == ["Students", "Enrollments"]

        profiles = DataProfiler.profile_all_sources(tmp_path)
        assert len(profiles) == 2
        assert "Students" in profiles[0].source_name
        assert "Enrollments" in profiles[1].source_name

        # Join the two tabs from the single workbook
        combined_df, mode, det = MultiSourceCombiner.auto_combine(profiles)
        assert mode == "join"
        assert len(combined_df) == 2
        assert "first_name" in combined_df.columns
        assert "course" in combined_df.columns
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def test_pool_scanner_and_join_graph():
    """Tests discovering multiple files (CSV, SQLite) in a pool and building a relational join graph."""
    with tempfile.TemporaryDirectory() as pool_dir:
        # Create a CSV dataset
        csv_path = os.path.join(pool_dir, "students.csv")
        pd.DataFrame({
            "student_id": ["S101", "S102", "S103"],
            "name": ["Alice Smith", "Bob Jones", "Charlie Brown"],
            "status": ["Active", "Active", "Graduated"]
        }).to_csv(csv_path, index=False)

        # Create a SQLite database with 2 tables
        db_path = os.path.join(pool_dir, "academic.db")
        conn = sqlite3.connect(db_path)
        pd.DataFrame({
            "std_id": ["S101", "S102", "S103"],
            "gpa": [3.85, 3.20, 3.90]
        }).to_sql("gpa_records", conn, index=False)
        pd.DataFrame({
            "stu_num": ["S101", "S102", "S103"],
            "tuition_due": ["$1,200", "$450", "$0"]
        }).to_sql("billing", conn, index=False)
        conn.close()

        # Run PoolSourceScanner
        profiles = PoolSourceScanner.scan_directory(pool_dir)
        assert len(profiles) == 3
        names = [p.source_name for p in profiles]
        assert any("students.csv" in n for n in names)
        assert any("gpa_records" in n for n in names)
        assert any("billing" in n for n in names)

        # Build Join Graph
        graph = PoolJoinGraph(profiles)
        assert len(graph.edges) == 3
        # Should have found join connections across student IDs
        edges_students = [e["target"] for e in graph.edges.get("students.csv", [])]
        assert any("gpa_records" in t for t in edges_students)


def test_pool_harmonizer_engine_autonomous_routing():
    """Tests autonomous pool harmonization where two distinct templates are matched
    and transformed from different subsets of datasets in the pool without user questions.
    """
    with tempfile.TemporaryDirectory() as pool_dir, tempfile.TemporaryDirectory() as tpl_dir:
        # 1. Source Pool: students.csv and grades.csv
        pd.DataFrame({
            "student_id": ["S1", "S2"],
            "first_name": ["Alice", "Bob"],
            "last_name": ["Smith", "Jones"]
        }).to_csv(os.path.join(pool_dir, "students.csv"), index=False)

        pd.DataFrame({
            "std_id": ["S1", "S2"],
            "score": [95, 88],
            "letter_grade": ["A", "B"]
        }).to_csv(os.path.join(pool_dir, "grades.csv"), index=False)

        # 2. Template 1: State Transcript (Requires joining students + grades)
        t1_path = os.path.join(tpl_dir, "State_Report.csv")
        pd.DataFrame({
            "Full Name": ["Dummy"],
            "Score": [100],
            "Grade": ["A"]
        }).to_csv(t1_path, index=False)

        # 3. Template 2: Simple Roster (Requires only students.csv)
        t2_path = os.path.join(tpl_dir, "Roster.csv")
        pd.DataFrame({
            "Student ID": ["S0"],
            "Last Name": ["Sample"]
        }).to_csv(t2_path, index=False)

        # Scan pool and templates
        source_profiles = PoolSourceScanner.scan_directory(pool_dir)
        template_profiles = TemplatePoolScanner.scan_templates(tpl_dir)
        assert len(source_profiles) == 2
        assert len(template_profiles) == 2

        # Harmonize
        engine = PoolHarmonizerEngine(learning_engine)
        results = engine.harmonize_pool(source_profiles, template_profiles)
        assert len(results) == 2

        res_dict = {r.template_name: r for r in results}

        # Verify Template 1 (Joined)
        r1 = res_dict["State_Report.csv"]
        assert r1.status == "success"
        assert r1.completeness_score >= 0.80
        assert "Full Name" in r1.output_df.columns
        assert "Score" in r1.output_df.columns
        assert r1.output_df["Full Name"].iloc[0] == "Alice Smith"
        assert str(r1.output_df["Score"].iloc[0]) == "95"

        # Verify Template 2 (Single Source)
        r2 = res_dict["Roster.csv"]
        assert r2.status == "success"
        assert "Student ID" in r2.output_df.columns
        assert r2.output_df["Student ID"].iloc[0] == "S1"


def test_pool_export_all_with_manifest():
    """Tests exporting all pool harmonization outputs and generating the manifest CSV."""
    with tempfile.TemporaryDirectory() as out_dir:
        res = PoolHarmonizationResult(
            template_name="Scholarship_Export.xlsx",
            template_profile=DataProfiler.profile_dataset(pd.DataFrame({"ID": ["1"], "Name": ["A"]})),
            source_tables_used=["students.csv"],
            combination_mode="single",
            join_details={},
            matches=[],
            output_df=pd.DataFrame({"ID": ["101", "102"], "Name": ["John", "Mary"]}),
            completeness_score=1.0,
            matched_columns_count=2,
            total_columns_count=2,
            row_count=2,
            status="success"
        )

        manifest = PoolHarmonizerEngine.export_all([res], out_dir, file_format="xlsx")
        assert len(manifest) == 1
        assert os.path.exists(os.path.join(out_dir, "Harmonized_Scholarship_Export.xlsx"))
        assert os.path.exists(os.path.join(out_dir, "_harmonization_manifest.csv"))


if __name__ == "__main__":
    test_profiler_types()
    test_smart_union()
    test_smart_join()
    test_schema_matching_and_composite()
    test_adaptive_learning_reinforcement()
    test_multitab_excel_profiling_and_combination()
    test_pool_scanner_and_join_graph()
    test_pool_harmonizer_engine_autonomous_routing()
    test_pool_export_all_with_manifest()
    print("All Data Harmonizer tests passed successfully!")

