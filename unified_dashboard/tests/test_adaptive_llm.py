"""
tests/test_adaptive_llm.py
==========================
Unit and integration tests for the Adaptive Local LLM suite:
- IntegrityValidator (Type and sanity checks preventing hallucinations)
- AdaptiveHarmonizerMemory (Persistent golden rule caching & few-shot precedents)
- OllamaEngine (Structured JSON extraction & offline fallback handling)
- SelfHealingEngine (DOM inspection & selector recovery)
"""

import os
import sys
import pandas as pd

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.data_harmonizer.validator import IntegrityValidator
from core.data_harmonizer.adaptive_memory import (
    harmonizer_memory,
    compute_schema_fingerprint,
    AdaptiveHarmonizerMemory,
)
from core.data_harmonizer.profiler import DataProfiler
from core.data_harmonizer.matcher import SchemaMatcher, ColumnMappingMatch
from core.ollama_engine import OllamaEngine
from core.self_healing import SelfHealingEngine


def test_integrity_validator_currency():
    assert IntegrityValidator.test_currency("$1,250.00") is True
    assert IntegrityValidator.test_currency("($350.50)") is True
    assert IntegrityValidator.test_currency("0") is True
    assert IntegrityValidator.test_currency("InvalidString") is False

    assert IntegrityValidator.parse_currency("$1,250.00") == 1250.0
    assert IntegrityValidator.parse_currency("($350.50)") == -350.50
    assert IntegrityValidator.parse_currency("100") == 100.0


def test_integrity_validator_dates():
    assert IntegrityValidator.test_date("2024-05-15") is True
    assert IntegrityValidator.test_date("05/15/2024") is True
    assert IntegrityValidator.test_date("May 15, 2024") is True
    assert IntegrityValidator.test_date("not-a-date") is False


def test_type_compatibility_guard():
    # Test valid currency compatibility
    samples_curr = ["$100.00", "$250.50", "($50.00)"]
    is_compat, boost, exp = IntegrityValidator.validate_type_compatibility(samples_curr, "Tuition Balance (Currency)")
    assert is_compat is True
    assert boost > 0

    # Test invalid currency mapping (text into currency)
    samples_text = ["Freshman", "Sophomore", "Senior"]
    is_compat, penalty, exp = IntegrityValidator.validate_type_compatibility(samples_text, "Tuition Balance (Currency)")
    assert is_compat is False
    assert penalty < 0


def test_adaptive_memory_fingerprint_and_caching():
    df_src = pd.DataFrame({
        "STD_ID": ["101", "102"],
        "STD_BAL": ["$100", "$200"]
    })
    df_sample = pd.DataFrame({
        "student_id": ["101", "102"],
        "balance": ["$100", "$200"]
    })

    p_src = DataProfiler.profile_dataset(df_src)
    p_sample = DataProfiler.profile_dataset(df_sample)

    fp_src = compute_schema_fingerprint(p_src)
    fp_sample = compute_schema_fingerprint(p_sample)
    assert len(fp_src) == 16
    assert len(fp_sample) == 16

    # Record user feedback
    match1 = ColumnMappingMatch(
        sample_column="student_id",
        sample_type="text",
        source_column="STD_ID",
        source_type="text",
        confidence=1.0,
        tier_reason="User Confirmed",
        transformation_rule="direct"
    )
    match2 = ColumnMappingMatch(
        sample_column="balance",
        sample_type="currency",
        source_column="STD_BAL",
        source_type="currency",
        confidence=1.0,
        tier_reason="User Confirmed",
        transformation_rule="clean_currency"
    )

    harmonizer_memory.record_user_feedback(p_src, p_sample, [match1, match2])

    # Check that exact fingerprint match returns saved mappings immediately
    saved = harmonizer_memory.get_exact_fingerprint_mapping(p_src, p_sample)
    assert saved is not None
    assert "student_id" in saved
    assert saved["student_id"]["source_column"] == "STD_ID"
    assert saved["balance"]["transformation_rule"] == "clean_currency"

    # Check few-shot precedents
    precedents = harmonizer_memory.get_few_shot_precedents()
    assert len(precedents) > 0
    assert any(p["source_column"] == "std_bal" for p in precedents)


def test_ollama_engine_fallback():
    # Connect to invalid port to test graceful exception handling
    client = OllamaEngine(host="http://127.0.0.1:99999")
    assert client.is_available() is False
    assert client.list_installed_models() == []
