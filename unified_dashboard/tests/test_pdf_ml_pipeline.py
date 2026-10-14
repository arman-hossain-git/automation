"""
tests/test_pdf_ml_pipeline.py
=============================
Automated test suite for Local Spatial ML & Active Learning System:
  1. Tests DBSCAN spatial clustering and preset auto-synthesis.
  2. Tests extraction execution with the synthesized preset.
  3. Tests active learning rule tuning & regex anchor adjustment.
  4. Tests learning_data.json confidence persistence.
"""

import os
import sys
import unittest

# Ensure project root is in path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from modules.pdf_spatial_ml import synthesize_preset_from_pdf, cluster_page_lines
from modules.pdf_rule_tuner import tune_field_in_preset, add_exclude_rule_to_preset, record_verification_feedback
from modules.pdf_extractor import ConfigurableExtractor
from core.learning_system import learning_engine


class TestPdfSpatialMLPipeline(unittest.TestCase):

    def setUp(self):
        # Locate sample PDF in parent or test dirs
        parent_dir = os.path.dirname(PROJECT_ROOT)
        sample_candidates = [
            os.path.join(parent_dir, "pd to data", "Student Ledger - Andrew Rudy.pdf"),
            os.path.join(parent_dir, "Student Ledger - Andrew Rudy.pdf"),
            os.path.join(parent_dir, "Student Schedule.pdf"),
        ]
        self.sample_pdf = None
        for p in sample_candidates:
            if os.path.isfile(p):
                self.sample_pdf = p
                break

    def test_01_spatial_preset_synthesis(self):
        if not self.sample_pdf:
            self.skipTest("No sample PDF found for test")

        preset = synthesize_preset_from_pdf(self.sample_pdf)
        self.assertIsInstance(preset, dict)
        self.assertIn("record_separator", preset)
        self.assertIn("summary_fields", preset)
        self.assertIn("output_columns", preset)
        self.assertIn("name_detection", preset)

        # Verify summary fields were discovered
        self.assertGreater(len(preset["summary_fields"]), 0, "DBSCAN should discover at least one key-value field")
        print(f"\n[Test 1] Discovered {len(preset['summary_fields'])} fields from {os.path.basename(self.sample_pdf)}")

    def test_02_extraction_with_synthesized_preset(self):
        if not self.sample_pdf:
            self.skipTest("No sample PDF found for test")

        preset = synthesize_preset_from_pdf(self.sample_pdf)
        extractor = ConfigurableExtractor(preset)
        full_text, offsets = extractor.read_pdf(self.sample_pdf)
        records = extractor.split_records(full_text, offsets)
        self.assertGreater(len(records), 0, "Extractor should split at least 1 record")

        name, addr, rep, summary, rows = extractor.extract_record(records[0][0])
        print(f"[Test 2] Extracted record: Name={name}, Summary keys={list(summary.keys())}")
        self.assertIsInstance(summary, dict)

    def test_03_active_learning_rule_tuning(self):
        preset = {
            "record_separator": "Student Record",
            "summary_fields": [
                {"name": "campus", "pattern": r"Campus:\s*([A-Za-z]+)"}
            ],
            "exclude_patterns": []
        }

        # Simulated line where previous pattern failed or was too loose
        sample_line = "Campus: Bayamon    Term: Fall 2026"
        updated_preset, msg = tune_field_in_preset(preset, "campus", "Bayamon", sample_line)

        # Verify new pattern compiles and extracts accurately
        new_pattern = updated_preset["summary_fields"][0]["pattern"]
        import re
        m = re.search(new_pattern, sample_line)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "Bayamon")
        print(f"[Test 3] Rule tuning updated regex to: {new_pattern}")

    def test_04_noise_exclusion_learning(self):
        preset = {"exclude_patterns": []}
        noise = "Powered by TCPDF (www.tcpdf.org)"
        updated_preset, msg = add_exclude_rule_to_preset(preset, noise)
        self.assertGreater(len(updated_preset["exclude_patterns"]), 0)
        print(f"[Test 4] Exclude rule added: {updated_preset['exclude_patterns'][0]}")

    def test_05_confidence_tracking(self):
        preset_name = "Unit_Test_Preset"
    def test_06_multiline_rule_tuning(self):
        preset = {
            "record_separator": "Student Record",
            "summary_fields": [
                {"name": "program_description", "pattern": r"Program Description:\s*([A-Za-z\s]+)"}
            ],
            "computed_fields": [],
            "output_columns": ["program_description"]
        }
        sample_line = "Program Description: Asistente de Drop Date: Group: AO0125"
        all_lines = [
            "Program Description: Asistente de Drop Date: Group: AO0125",
            "Contabilidad",
            "First Term: 01/21/2025"
        ]

        # User corrects field to multi-line value spanning lines
        updated_preset, msg = tune_field_in_preset(
            preset, "program_description", "Asistente de Contabilidad", sample_line, all_lines=all_lines
        )
        self.assertIn("computed_fields", updated_preset)
        self.assertEqual(len(updated_preset["computed_fields"]), 1)
        self.assertEqual(updated_preset["computed_fields"][0]["name"], "program")
        self.assertIn("program_description_continuation", [sf["name"] for sf in updated_preset["summary_fields"]])

        # Verify extraction with the tuned preset on multi-line text block
        block = "\n".join(all_lines)
        ext = ConfigurableExtractor(updated_preset)
        fields = ext.extract_summary_fields(block)
        self.assertEqual(fields.get("program"), "Asistente de Contabilidad")
        print(f"[Test 6] Multi-line rule tuning verified: {fields.get('program')}")

    def test_07_multiline_preset_synthesis(self):
        parent_dir = os.path.dirname(PROJECT_ROOT)
        ledger_pdf = os.path.join(parent_dir, "Student_Ledger_27460.pdf")
        if not os.path.isfile(ledger_pdf):
            self.skipTest("Student_Ledger_27460.pdf not found")

        preset = synthesize_preset_from_pdf(ledger_pdf)
        self.assertIn("computed_fields", preset)
        self.assertIn("program", preset.get("output_columns", []))

        ext = ConfigurableExtractor(preset)
        full_text, offsets = ext.read_pdf(ledger_pdf)
        records = ext.split_records(full_text, offsets)
        self.assertGreater(len(records), 0)

        name, addr, rep, summary, rows = ext.extract_record(records[0][0])
        self.assertEqual(summary.get("program"), "Asistente de Contabilidad")
        print(f"[Test 7] Auto-detected multi-line field: program='{summary.get('program')}'")

    def test_08_row_field_rule_tuning(self):
        preset = {
            "record_separator": "Student Ledger",
            "data_rows": {
                "pattern": r"^(\S+)\s+(\S+)\s+(.+?)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)$",
                "columns": ["trans_date", "ledger_code", "description", "debit", "credit", "balance"]
            },
            "output_columns": ["trans_date", "ledger_code", "description", "debit", "credit", "balance"]
        }

        # Simulated line where user points the true value of description
        sample_line = "08/15/2024  TUIT  Tuition - Fall 2024  $ 1,500.00  $ 0.00  $ 1,500.00"
        updated_preset, msg = tune_field_in_preset(
            preset, "row_description", "Tuition - Fall 2024", sample_line
        )

        self.assertIn("data_rows", updated_preset)
        pat = updated_preset["data_rows"]["pattern"]
        import re
        m = re.match(pat, sample_line.strip())
        self.assertIsNotNone(m, f"Pattern {pat} should match sample line")

        # Test extraction using ConfigurableExtractor
        ext = ConfigurableExtractor(updated_preset)
        rows = ext.extract_data_rows(sample_line)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("description"), "Tuition - Fall 2024")
        self.assertEqual(rows[0].get("trans_date"), "08/15/2024")
        self.assertEqual(rows[0].get("ledger_code"), "TUIT")
        self.assertEqual(rows[0].get("debit"), "1,500.00")
        print(f"[Test 8] Row field tuning verified: {rows[0]}")

    def test_09_row_line_preset_tuning(self):
        from modules.pdf_rule_tuner import tune_data_row_line_in_preset
        preset = {
            "record_separator": "Student Ledger",
            "data_rows": {
                "columns": ["trans_date", "ledger_code", "description", "debit", "credit", "balance"]
            }
        }
        sample_line = "09/01/2024  FEES  Library Technology Fee  $ 150.00  $ 0.00  $ 1,650.00"
        updated_preset, msg = tune_data_row_line_in_preset(preset, sample_line)

        ext = ConfigurableExtractor(updated_preset)
        rows = ext.extract_data_rows(sample_line)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("description"), "Library Technology Fee")
        self.assertEqual(rows[0].get("debit"), "150.00")
        print(f"[Test 9] Row line tuning verified: {rows[0]}")

    def test_10_row_verification_feedback(self):
        preset_name = "Unit_Test_Row_Preset"
        record_verification_feedback(preset_name, "row_description", is_correct=True)
        record_verification_feedback(preset_name, "row_debit", is_correct=True)

        profile = learning_engine._data.get("pdf_extractor_profiles", {}).get(preset_name, {})
        conf_map = profile.get("field_confidence", {})
        self.assertIn("row_description", conf_map)
        self.assertGreaterEqual(conf_map["row_description"], 0.8)
        print(f"[Test 10] Row feedback tracking verified: {conf_map}")


if __name__ == "__main__":
    unittest.main()

