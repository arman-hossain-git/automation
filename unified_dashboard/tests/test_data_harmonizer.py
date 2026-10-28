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
import pytest

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from core.data_harmonizer.profiler import DataProfiler
from core.data_harmonizer.combiner import MultiSourceCombiner
from core.data_harmonizer.matcher import SchemaMatcher
from core.data_harmonizer.transformer import DataTransformer
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

