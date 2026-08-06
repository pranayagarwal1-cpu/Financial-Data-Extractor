"""Pure-logic tests for CoA RAG retrieval — mocked embeddings, no live Ollama calls."""

import pytest

import coa.retriever as retriever
from coa.chart_of_accounts import COAAccount

FIXTURE_ACCOUNTS = {
    "1001": COAAccount(code="1001", name="Vaccine Revenue", category="Revenue", series="1000", description="", aliases=["Vaccinations"]),
    "1002": COAAccount(code="1002", name="Boarding Revenue", category="Revenue", series="1000", description="", aliases=["Boarding"]),
    "1003": COAAccount(code="1003", name="Office Supplies", category="Operating Expense", series="1000", description="", aliases=[]),
}

VECTORS = {
    "1001": [1.0, 0.0, 0.0],
    "1002": [0.0, 1.0, 0.0],
    "1003": [0.0, 0.0, 1.0],
}


@pytest.fixture
def isolated_retriever(tmp_path, monkeypatch):
    monkeypatch.setattr(retriever, "COA_ACCOUNTS", FIXTURE_ACCOUNTS)
    monkeypatch.setattr(retriever, "CHROMA_DIR", tmp_path / "chroma")

    account_text_to_code = {
        retriever._account_text(acc): code for code, acc in FIXTURE_ACCOUNTS.items()
    }
    query_vectors = {"Vaccinations": [1.0, 0.0, 0.0], "Boarding": [0.0, 1.0, 0.0]}

    def fake_embed_texts(texts):
        out = []
        for t in texts:
            if t in account_text_to_code:
                out.append(VECTORS[account_text_to_code[t]])
            else:
                out.append(query_vectors.get(t, [0.33, 0.33, 0.33]))
        return out

    monkeypatch.setattr(retriever, "embed_texts", fake_embed_texts)
    return retriever


class TestBuildIndex:
    def test_indexes_all_fixture_accounts(self, isolated_retriever):
        collection = isolated_retriever.build_index()
        assert collection.count() == len(FIXTURE_ACCOUNTS)

    def test_skips_rebuild_when_hash_matches(self, isolated_retriever):
        isolated_retriever.build_index()

        calls = []
        original_embed = isolated_retriever.embed_texts

        def counting_embed(texts):
            calls.append(texts)
            return original_embed(texts)

        isolated_retriever.embed_texts = counting_embed
        isolated_retriever.build_index()

        assert calls == []

    def test_force_rebuild_re_embeds(self, isolated_retriever):
        isolated_retriever.build_index()

        calls = []
        original_embed = isolated_retriever.embed_texts

        def counting_embed(texts):
            calls.append(texts)
            return original_embed(texts)

        isolated_retriever.embed_texts = counting_embed
        isolated_retriever.build_index(force_rebuild=True)

        assert len(calls) == 1


class TestRetrieveCandidates:
    def test_returns_top_match_for_exact_query(self, isolated_retriever):
        candidates = isolated_retriever.retrieve_candidates([{"label": "Vaccinations"}], k=2)
        assert candidates["Vaccinations"][0] == "1001"

    def test_empty_items_returns_empty_dict(self, isolated_retriever):
        assert isolated_retriever.retrieve_candidates([]) == {}

    def test_multiple_items_each_get_own_candidates(self, isolated_retriever):
        candidates = isolated_retriever.retrieve_candidates(
            [{"label": "Vaccinations"}, {"label": "Boarding"}], k=1
        )
        assert candidates["Vaccinations"] == ["1001"]
        assert candidates["Boarding"] == ["1002"]
