"""Tests for categorization metrics computation."""

import pytest

from utils.cat_metrics import (
    CategorizationMetrics,
    compute_categorization_metrics,
    compute_delta_metrics,
    _account_series,
    _is_valid_for_section,
    _section_type,
)


class TestAccountSeries:
    @pytest.mark.parametrize("code,expected", [
        ("5000", "5xxx"),
        ("5100", "5xxx"),
        ("6050", "6xxx"),
        ("7200", "7xxx"),
        ("8050", "8xxx"),
        ("9010", "9xxx"),
        ("1234", "balance_sheet"),
        ("2100", "balance_sheet"),
        ("3999", "balance_sheet"),
        ("", None),
        (None, None),
        ("abc", None),
    ])
    def test_account_series(self, code, expected):
        assert _account_series(code) == expected


class TestSectionType:
    @pytest.mark.parametrize("name,expected", [
        ("Revenue", "revenue"),
        ("Total Revenue", "revenue"),
        ("Operating Income", "revenue"),
        ("Cost of Goods Sold", "cogs"),
        ("Cost of Revenue", "cogs"),
        ("Operating Expenses", "opex"),
        ("General and Administrative", "opex"),
        ("Other Income", "other"),
        ("Interest Expense", "other"),
        ("Random Section", "unknown"),
    ])
    def test_section_type(self, name, expected):
        assert _section_type(name) == expected


class TestIsValidForSection:
    @pytest.mark.parametrize("section_type,series,expected", [
        ("revenue", "5xxx", True),
        ("revenue", "6xxx", False),
        ("cogs", "6xxx", True),
        ("cogs", "7xxx", False),
        ("opex", "7xxx", True),
        ("opex", "8xxx", True),
        ("opex", "6xxx", False),
        ("other", "8xxx", True),
        ("other", "9xxx", True),
        ("other", "5xxx", False),
    ])
    def test_validity(self, section_type, series, expected):
        assert _is_valid_for_section(section_type, series) == expected


class TestComputeCategorizationMetrics:
    def test_empty_data(self):
        metrics = compute_categorization_metrics({})
        assert metrics.total_items == 0
        assert metrics.coverage_rate == 0.0
        assert metrics.postable_items == 0

    def test_basic_counts(self):
        data = {
            "sections": [
                {
                    "name": "Revenue",
                    "rows": [
                        {
                            "label": "Pharmacy Sales",
                            "values": ["100"],
                            "categorization": {
                                "coa_code": "5100",
                                "confidence": "high",
                                "match_type": "exact",
                                "needs_review": False,
                            },
                        },
                        {
                            "label": "Cremation Revenue",
                            "values": ["50"],
                            "categorization": {
                                "coa_code": "5050",
                                "confidence": "medium",
                                "match_type": "llm",
                                "needs_review": False,
                            },
                        },
                    ],
                },
                {
                    "name": "Expenses",
                    "rows": [
                        {
                            "label": "Staff Wages",
                            "values": ["80"],
                            "categorization": {
                                "coa_code": "7100",
                                "confidence": "high",
                                "match_type": "token",
                                "needs_review": False,
                            },
                        },
                        {
                            "label": "Unknown Item",
                            "values": ["10"],
                            "categorization": {
                                "coa_code": None,
                                "confidence": "unmatched",
                                "match_type": "unmatched",
                                "needs_review": True,
                            },
                        },
                    ],
                },
            ]
        }
        metrics = compute_categorization_metrics(data)
        assert metrics.total_items == 4
        assert metrics.postable_items == 4
        assert metrics.categorized_items == 3
        assert metrics.uncategorized_items == 1
        assert metrics.high_confidence_items == 2
        assert metrics.medium_confidence_items == 1
        assert metrics.low_confidence_items == 0
        assert metrics.unmatched_items == 1
        assert metrics.needs_review_items == 1
        assert metrics.coverage_rate == 0.75
        assert metrics.high_conf_rate == 2 / 3
        assert metrics.review_rate == 0.25
        assert metrics.exact_matches == 1
        assert metrics.llm_matches == 1
        assert metrics.token_matches == 1
        assert metrics.unmatched_matches == 1

    def test_section_headers_excluded(self):
        data = {
            "sections": [
                {
                    "name": "Revenue",
                    "rows": [
                        {
                            "label": "Revenue",
                            "values": [],
                            "line_type": "section_header",
                        },
                        {
                            "label": "Pharmacy Sales",
                            "values": ["100"],
                            "categorization": {
                                "coa_code": "5100",
                                "confidence": "high",
                                "match_type": "exact",
                                "needs_review": False,
                            },
                        },
                    ],
                },
            ]
        }
        metrics = compute_categorization_metrics(data)
        assert metrics.section_headers == 1
        assert metrics.postable_items == 1
        assert metrics.total_items == 2
        assert metrics.coverage_rate == 1.0

    def test_subtotals_excluded(self):
        data = {
            "sections": [
                {
                    "name": "Revenue",
                    "rows": [
                        {
                            "label": "Total Revenue",
                            "values": ["150"],
                            "is_subtotal": True,
                            "categorization": {
                                "coa_code": "5100",
                                "confidence": "high",
                            },
                        },
                        {
                            "label": "Pharmacy Sales",
                            "values": ["100"],
                            "categorization": {
                                "coa_code": "5100",
                                "confidence": "high",
                                "needs_review": False,
                            },
                        },
                    ],
                },
            ]
        }
        metrics = compute_categorization_metrics(data)
        assert metrics.total_items == 1
        assert metrics.postable_items == 1
        assert metrics.categorized_items == 1

    def test_section_sanity_violations(self):
        data = {
            "sections": [
                {
                    "name": "Revenue",
                    "rows": [
                        {
                            "label": "Bad Revenue",
                            "values": ["100"],
                            "categorization": {
                                "coa_code": "7100",
                                "confidence": "high",
                                "match_type": "llm",
                                "needs_review": False,
                            },
                        },
                    ],
                },
            ]
        }
        metrics = compute_categorization_metrics(data)
        assert len(metrics.section_violations) == 1
        assert metrics.section_violations[0]["assigned_code"] == "7100"
        assert metrics.section_violations[0]["expected_for_section"] == "revenue"
        assert metrics.revenue_sanity == 0.0  # categorized / items = 0/1 because violation

    def test_balance_sheet_codes_flagged(self):
        data = {
            "sections": [
                {
                    "name": "Expenses",
                    "rows": [
                        {
                            "label": "Cash",
                            "values": ["100"],
                            "categorization": {
                                "coa_code": "1100",
                                "confidence": "low",
                                "match_type": "llm",
                                "needs_review": False,
                            },
                        },
                    ],
                },
            ]
        }
        metrics = compute_categorization_metrics(data)
        assert metrics.balance_sheet_codes_used == ["1100"]

    def test_split_items(self):
        data = {
            "sections": [
                {
                    "name": "Expenses",
                    "rows": [
                        {
                            "label": "Accounting & Legal",
                            "values": ["100"],
                            "categorization": {
                                "coa_code": "7765",
                                "confidence": "high",
                                "match_type": "llm_split",
                                "needs_review": True,
                                "is_split": True,
                            },
                        },
                    ],
                },
            ]
        }
        metrics = compute_categorization_metrics(data)
        assert metrics.split_items == 1
        assert metrics.llm_split_matches == 1

    def test_batch_metadata(self):
        data = {"sections": []}
        metrics = compute_categorization_metrics(
            data,
            batch_count=2,
            batch_failure_count=1,
            llm_duration_ms=1500.5,
            memory_rules_applied=3,
        )
        assert metrics.batch_count == 2
        assert metrics.batch_failure_count == 1
        assert metrics.llm_duration_ms == 1500.5
        assert metrics.memory_rules_applied == 3

    def test_sanity_rates_with_empty_sections(self):
        data = {"sections": []}
        metrics = compute_categorization_metrics(data)
        assert metrics.revenue_sanity == 1.0
        assert metrics.cogs_sanity == 1.0
        assert metrics.opex_sanity == 1.0
        assert metrics.other_sanity == 1.0
        assert metrics.overall_sanity == 0.0  # no postable items

    def test_to_dict_roundtrip(self):
        data = {
            "sections": [
                {
                    "name": "Revenue",
                    "rows": [
                        {
                            "label": "Sales",
                            "values": ["100"],
                            "categorization": {
                                "coa_code": "5000",
                                "confidence": "high",
                                "match_type": "exact",
                                "needs_review": False,
                            },
                        },
                    ],
                },
            ]
        }
        metrics = compute_categorization_metrics(data)
        d = metrics.to_dict()
        assert d["total_items"] == 1
        assert d["coverage_rate"] == 1.0
        assert d["high_conf_rate"] == 1.0
        assert isinstance(d["section_violations"], list)


class TestDeltaMetrics:
    def test_delta_computes_changes(self):
        prev = compute_categorization_metrics({
            "sections": [
                {
                    "name": "Revenue",
                    "rows": [
                        {
                            "label": "Sales",
                            "values": ["100"],
                            "categorization": {
                                "coa_code": "5000",
                                "confidence": "high",
                                "match_type": "exact",
                                "needs_review": False,
                            },
                        },
                    ],
                },
            ]
        })
        curr = compute_categorization_metrics({
            "sections": [
                {
                    "name": "Revenue",
                    "rows": [
                        {
                            "label": "Sales",
                            "values": ["100"],
                            "categorization": {
                                "coa_code": "5000",
                                "confidence": "high",
                                "match_type": "exact",
                                "needs_review": False,
                            },
                        },
                        {
                            "label": "Services",
                            "values": ["200"],
                            "categorization": {
                                "coa_code": "5100",
                                "confidence": "medium",
                                "match_type": "llm",
                                "needs_review": False,
                            },
                        },
                    ],
                },
            ]
        })
        delta = compute_delta_metrics(prev, curr)
        assert delta["coverage_rate"]["absolute"] == 0.0  # both fully covered
        assert delta["categorized_items"]["absolute"] == 1
        assert delta["categorized_items"]["relative"] == 1.0

    def test_delta_with_zero_previous(self):
        prev = compute_categorization_metrics({})
        curr = compute_categorization_metrics({
            "sections": [
                {
                    "name": "Revenue",
                    "rows": [
                        {
                            "label": "Sales",
                            "values": ["100"],
                            "categorization": {
                                "coa_code": "5000",
                                "confidence": "high",
                                "match_type": "exact",
                                "needs_review": False,
                            },
                        },
                    ],
                },
            ]
        })
        delta = compute_delta_metrics(prev, curr)
        assert delta["coverage_rate"]["previous"] == 0.0
        assert delta["coverage_rate"]["current"] == 1.0
        assert delta["categorized_items"]["relative"] is None  # prev == 0