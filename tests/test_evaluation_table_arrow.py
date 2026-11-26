"""Regression tests for the recent-evaluation records table.

Covers the mixed-type column that previously raised:
    pyarrow.lib.ArrowInvalid: Could not convert '—' ... tried to convert to double

The table must keep numeric scores, keep unmeasured values visible as ``—``,
and remain convertible to Arrow without dropping rows or zero-filling scores.

Run:
    python tests/test_evaluation_table_arrow.py
"""

from __future__ import annotations

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pyarrow as pa  # noqa: E402

from ui.evaluation_view import _metric_cell, _records_table  # noqa: E402


def _record(record_id: int, **overrides) -> dict:
    record = {
        "id": record_id,
        "topic": f"topic {record_id}",
        "overall": 0.9,
        "relevance": 0.8,
        "evidence_coverage": 0.7,
        "citation_quality": 0.6,
        "completeness": 0.5,
        "factual_support": 0.93789,
        "generation_mode": "llm",
        "created_at": "2026-11-25T10:00:00+00:00",
    }
    record.update(overrides)
    return record


class MetricCellTests(unittest.TestCase):
    def test_numeric_values_keep_four_decimal_precision(self):
        self.assertEqual(_metric_cell(0.93789), "0.9379")
        self.assertEqual(_metric_cell(1.0), "1.0")
        self.assertEqual(_metric_cell(0), "0")

    def test_unavailable_values_render_as_dash(self):
        self.assertEqual(_metric_cell(None), "—")
        self.assertEqual(_metric_cell(float("nan")), "—")
        self.assertEqual(_metric_cell(float("inf")), "—")
        self.assertEqual(_metric_cell("—"), "—")

    def test_zero_is_not_treated_as_missing(self):
        self.assertEqual(_metric_cell(0.0), "0.0")


class RecordsTableTests(unittest.TestCase):
    def test_mixed_metric_column_converts_to_arrow(self):
        series = [
            _record(1, factual_support=0.93789),
            _record(2, factual_support=None),
            _record(3, factual_support="—"),
        ]

        table = _records_table(series)

        # Numeric scores, missing values, and an existing dash coexist.
        self.assertEqual(
            table["Factual support"].tolist(), ["0.9379", "—", "—"]
        )
        self.assertEqual(table["Overall"].tolist(), ["0.9", "0.9", "0.9"])
        self.assertEqual(len(table), 3)

        # This is the exact conversion Streamlit performs for st.dataframe.
        arrow = pa.Table.from_pandas(table)
        self.assertEqual(
            arrow.column("Factual support").to_pylist(), ["0.9379", "—", "—"]
        )
        self.assertEqual(arrow.num_rows, 3)

    def test_every_missing_metric_still_converts_to_arrow(self):
        series = [
            _record(
                1,
                overall=None,
                relevance=None,
                evidence_coverage=None,
                citation_quality=None,
                completeness=None,
                factual_support=None,
            )
        ]

        table = _records_table(series)
        arrow = pa.Table.from_pandas(table)

        for column in (
            "Overall",
            "Relevance",
            "Evidence",
            "Citation",
            "Completeness",
            "Factual support",
        ):
            self.assertEqual(arrow.column(column).to_pylist(), ["—"])

    def test_metadata_columns_keep_their_placeholders(self):
        table = _records_table([
            _record(4, topic=None, generation_mode=None, created_at=None)
        ])
        self.assertEqual(table.loc[0, "Research Query"], "—")
        self.assertEqual(table.loc[0, "Generation"], "—")
        self.assertEqual(table.loc[0, "Executed At (UTC)"], "—")


if __name__ == "__main__":
    unittest.main(verbosity=2)
