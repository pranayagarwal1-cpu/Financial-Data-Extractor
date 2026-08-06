"""Smoke tests for categorizer pure-function logic (no LLM)."""

import pytest

import agents.categorizer as categorizer_mod
from agents.categorizer import (
    extract_line_items_from_statement,
    is_section_header,
)
from config import Config


class TestIsSectionHeader:
    @pytest.mark.parametrize("label", [
        "Revenue",
        "Total Revenue",
        "Total Expenses",
        "Operating Expenses",
        "Gross Profit",
        "Net Income",
        "Cost of Goods Sold",
        "Other Income",
        "Other Expenses",
        "Veterinary Revenue",
    ])
    def test_recognizes_section_headers(self, label):
        assert is_section_header(label), f"{label!r} should be a section header"

    @pytest.mark.parametrize("label", [
        "Cremation Revenue",
        "Pharmacy Sales",
        "Staff Wages",
        "Office Supplies",
        "Veterinary Drug Costs",
        "CPP Expense",
    ])
    def test_treats_postable_items_as_non_headers(self, label):
        assert not is_section_header(label), f"{label!r} should be a postable item"

    def test_case_insensitive(self):
        assert is_section_header("REVENUE")
        assert is_section_header("Total revenue")
        assert is_section_header("  total expenses  ")


class TestExtractLineItems:
    def test_extracts_postable_rows_with_section_metadata(self):
        data = {
            "sections": [
                {
                    "name": "Revenue",
                    "rows": [
                        {"label": "Pharmacy Sales", "values": ["100", "120"]},
                        {"label": "Cremation Revenue", "values": ["50", "55"]},
                        {"label": "Total Revenue", "values": ["150", "175"], "is_subtotal": True},
                    ],
                },
                {
                    "name": "Expenses",
                    "rows": [
                        {"label": "Staff Wages", "values": ["80", "85"]},
                    ],
                },
            ]
        }
        items = extract_line_items_from_statement(data)
        assert [i["label"] for i in items] == ["Pharmacy Sales", "Cremation Revenue", "Staff Wages"]
        assert items[0]["section"] == "Revenue"
        assert items[2]["section"] == "Expenses"
        assert items[0]["values"] == ["100", "120"]

    def test_skips_subtotal_rows(self):
        data = {
            "sections": [
                {"name": "Revenue", "rows": [
                    {"label": "Total Revenue", "values": ["100"], "is_subtotal": True},
                ]},
            ]
        }
        assert extract_line_items_from_statement(data) == []

    def test_skips_empty_labels(self):
        data = {
            "sections": [
                {"name": "Revenue", "rows": [
                    {"label": "", "values": ["100"]},
                    {"label": "Pharmacy Sales", "values": ["50"]},
                ]},
            ]
        }
        items = extract_line_items_from_statement(data)
        assert len(items) == 1
        assert items[0]["label"] == "Pharmacy Sales"

    def test_handles_no_sections(self):
        assert extract_line_items_from_statement({}) == []
        assert extract_line_items_from_statement({"sections": []}) == []


class TestRagCoaContextToggle:
    """The RAG-retrieval path must be opt-in and produce a smaller prompt
    than the full ~292-account CoA dump — the whole point of adding it."""

    def _run_single_batch(self, monkeypatch, use_rag: bool) -> str:
        captured = {}

        def fake_chat(*, model, messages):
            captured["prompt"] = messages[0]["content"]
            return {"message": {"content": "[]"}}

        monkeypatch.setattr(categorizer_mod, "chat", fake_chat)
        monkeypatch.setattr(Config, "USE_RAG_COA_RETRIEVAL", use_rag)
        monkeypatch.setattr(
            "coa.retriever.retrieve_candidates",
            lambda items, k=10: {item["label"]: ["5001"] for item in items},
        )

        batch_items = [{"label": "Vaccinations", "section": "REVENUE", "values": ["100"]}]
        results, ctx_tokens = categorizer_mod._llm_match_single_batch(
            batch_items, run_id=None, is_retry=False, practice_id=None
        )
        return captured["prompt"], ctx_tokens

    def test_rag_path_produces_smaller_prompt_than_full_dump(self, monkeypatch):
        full_prompt, full_tokens = self._run_single_batch(monkeypatch, use_rag=False)
        rag_prompt, rag_tokens = self._run_single_batch(monkeypatch, use_rag=True)

        assert len(rag_prompt) < len(full_prompt)
        assert rag_tokens < full_tokens

    def test_default_config_does_not_use_rag(self):
        assert Config.USE_RAG_COA_RETRIEVAL is False
