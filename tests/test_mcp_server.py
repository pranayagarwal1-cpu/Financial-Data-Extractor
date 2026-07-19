"""Smoke tests for the MCP server tools and its bearer-auth middleware.

No live LLM calls — only the pure-logic tools (CoA search, review-queue
cache, memory-correction writes) and the ASGI auth layer are exercised here.
`extract_financial_statements` is intentionally not covered since it drives
the full extraction pipeline and would require a real model call.
"""

import asyncio

import pytest

from mcp_server.auth import BearerAuthMiddleware
from mcp_server.server import (
    _run_cache,
    get_review_queue,
    search_coa_accounts,
    submit_categorization_correction,
)
import utils.memory_manager as memory_manager


class TestSearchCoaAccounts:
    def test_finds_vaccine_revenue(self):
        results = search_coa_accounts("Vaccine")
        codes = {r["code"] for r in results}
        assert "5001" in codes

    def test_no_match_returns_empty(self):
        assert search_coa_accounts("not-a-real-account-xyz") == []


class TestGetReviewQueue:
    def test_unknown_run_id_returns_empty(self):
        assert get_review_queue("no-such-run") == []

    def test_returns_cached_queue_for_known_run(self):
        _run_cache["test-run-123"] = {"review_queue": [{"label": "Surgery / Dentistry"}]}
        try:
            assert get_review_queue("test-run-123") == [{"label": "Surgery / Dentistry"}]
        finally:
            del _run_cache["test-run-123"]


class TestSubmitCategorizationCorrection:
    def test_writes_and_loads_correction(self, tmp_path, monkeypatch):
        monkeypatch.setattr(memory_manager, "MEMORY_DIR", tmp_path)

        result = submit_categorization_correction(
            practice_id="test_practice",
            label="Boarding",
            section="REVENUE",
            wrong_code="5000",
            correct_code="5825",
            correct_name="Boarding Revenue",
        )

        assert result == {"saved": 1}
        rules = memory_manager.load_memory_rules("test_practice")
        assert any(r.correct_code == "5825" and r.label == "Boarding" for r in rules)


def _run_asgi(app, auth_header: bytes | None):
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [(b"authorization", auth_header)] if auth_header else [],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    messages = []

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    return status


async def _dummy_inner_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


class TestBearerAuthMiddleware:
    def test_rejects_missing_token(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEY", "secret123")
        app = BearerAuthMiddleware(_dummy_inner_app)
        assert _run_asgi(app, auth_header=None) == 401

    def test_rejects_wrong_token(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEY", "secret123")
        app = BearerAuthMiddleware(_dummy_inner_app)
        assert _run_asgi(app, auth_header=b"Bearer wrong") == 401

    def test_accepts_correct_token(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEY", "secret123")
        app = BearerAuthMiddleware(_dummy_inner_app)
        assert _run_asgi(app, auth_header=b"Bearer secret123") == 200

    def test_fails_closed_when_key_unset(self, monkeypatch):
        monkeypatch.delenv("MCP_API_KEY", raising=False)
        app = BearerAuthMiddleware(_dummy_inner_app)
        assert _run_asgi(app, auth_header=b"Bearer anything") == 401
