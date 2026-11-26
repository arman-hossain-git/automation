"""
core/data_harmonizer/validator.py
=================================
Programmatic data type and semantic integrity validator.

Ensures that mappings and transformations proposed by LLMs or heuristics
never corrupt data or violate domain constraints (e.g. currency fields must
contain numeric/monetary values, dates must be valid calendar dates).
"""

import re
import datetime
from typing import List, Any, Tuple, Optional


class IntegrityValidator:
    """Validates source column samples against expected target data types."""

    # Precompiled regex patterns
    CURRENCY_PATTERN = re.compile(r"^[\s$€£¥₹]*[-+]?[\d,]+(\.\d+)?[\s]*$|^\([$€£¥₹]?[\d,]+(\.\d+)?\)$")
    NUMERIC_PATTERN = re.compile(r"^[-+]?[\d,]+(\.\d+)?$")
    DATE_SEPARATORS = re.compile(r"[-/.]")
    EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    PHONE_PATTERN = re.compile(r"^\+?[\d\s\-\(\)\.]{7,25}$")

    @classmethod
    def test_currency(cls, val: Any) -> bool:
        if val is None:
            return True
        s = str(val).strip()
        if not s or s.lower() in ("nan", "none", "null", "n/a", "-", ""):
            return True
        return bool(cls.CURRENCY_PATTERN.match(s))

    @classmethod
    def parse_currency(cls, val: Any) -> Optional[float]:
        """Safely parses currency string or number to float, handling parenthesized negatives."""
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip()
        if not s or s.lower() in ("nan", "none", "null", "n/a", "-", ""):
            return None
        # Handle (123.45) as -123.45
        is_negative = False
        if s.startswith("(") and s.endswith(")"):
            is_negative = True
            s = s[1:-1].strip()
        elif s.startswith("-"):
            is_negative = True
            s = s[1:].strip()
        
        # Remove currency symbols, commas, spaces
        cleaned = re.sub(r"[^\d.]", "", s)
        if not cleaned:
            return None
        try:
            num = float(cleaned)
            return -num if is_negative else num
        except ValueError:
            return None

    @classmethod
    def test_date(cls, val: Any) -> bool:
        if val is None:
            return True
        s = str(val).strip()
        if not s or s.lower() in ("nan", "none", "null", "n/a", ""):
            return True
        # Try common date formats
        for fmt in (
            "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y",
            "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%Y-%m-%d %H:%M:%S"
        ):
            try:
                datetime.datetime.strptime(s, fmt)
                return True
            except ValueError:
                continue
        return False

    @classmethod
    def test_numeric(cls, val: Any) -> bool:
        if val is None:
            return True
        if isinstance(val, (int, float)):
            return True
        s = str(val).strip().replace(",", "")
        if not s or s.lower() in ("nan", "none", "null", "n/a", ""):
            return True
        try:
            float(s)
            return True
        except ValueError:
            return False

    @classmethod
    def test_email(cls, val: Any) -> bool:
        if val is None:
            return True
        s = str(val).strip()
        if not s:
            return True
        return bool(cls.EMAIL_PATTERN.match(s))

    @classmethod
    def test_phone(cls, val: Any) -> bool:
        if val is None:
            return True
        s = str(val).strip()
        if not s:
            return True
        return bool(cls.PHONE_PATTERN.match(s))

    @classmethod
    def validate_type_compatibility(
        cls, samples: List[Any], target_type: str
    ) -> Tuple[bool, float, str]:
        """
        Validates whether sample values from a source column are compatible
        with the expected target column type.

        Returns:
            (is_compatible, penalty_or_boost_score, explanation)
        """
        non_empty = [s for s in samples if s is not None and str(s).strip() not in ("", "nan", "none", "null", "n/a")]
        if not non_empty:
            # Empty column, cannot verify type, neutral compatibility
            return True, 0.0, "Empty or all-null sample"

        target_type_clean = target_type.lower()

        if any(w in target_type_clean for w in ("currency", "amount", "fee", "balance", "cost", "price", "tuition")):
            matches = sum(1 for s in non_empty if cls.test_currency(s))
            ratio = matches / len(non_empty)
            if ratio >= 0.8:
                return True, 0.2, f"{ratio:.0%} samples match currency format"
            return False, -0.6, f"Only {ratio:.0%} samples match currency format; expected monetary numbers"

        if any(w in target_type_clean for w in ("date", "dob", "birth", "deadline")):
            matches = sum(1 for s in non_empty if cls.test_date(s))
            ratio = matches / len(non_empty)
            if ratio >= 0.7:
                return True, 0.2, f"{ratio:.0%} samples match date format"
            return False, -0.6, f"Only {ratio:.0%} samples match date format; expected valid calendar dates"

        if any(w in target_type_clean for w in ("int", "float", "number", "numeric", "score", "grade_num", "count", "gpa")):
            matches = sum(1 for s in non_empty if cls.test_numeric(s))
            ratio = matches / len(non_empty)
            if ratio >= 0.8:
                return True, 0.15, f"{ratio:.0%} samples are numeric"
            return False, -0.5, f"Only {ratio:.0%} samples are numeric"

        if "email" in target_type_clean:
            matches = sum(1 for s in non_empty if cls.test_email(s))
            ratio = matches / len(non_empty)
            if ratio >= 0.8:
                return True, 0.25, f"{ratio:.0%} samples match email syntax"
            return False, -0.7, f"Samples do not match email syntax"

        if "phone" in target_type_clean:
            matches = sum(1 for s in non_empty if cls.test_phone(s))
            ratio = matches / len(non_empty)
            if ratio >= 0.8:
                return True, 0.2, f"{ratio:.0%} samples match phone format"
            return False, -0.5, f"Samples do not match phone format"

        # General string / text / categorical target
        return True, 0.0, "String / general text compatibility accepted"
