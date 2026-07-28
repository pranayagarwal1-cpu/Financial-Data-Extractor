"""Smoke tests for the MCP server tools and its bearer-auth middleware.

No live LLM calls — the pure-logic tools (CoA search, review-queue cache,
memory-correction writes), the ASGI auth layer, and the async extraction
task lifecycle (with process_single_pdf mocked out) are exercised here.
"""

import asyncio
import base64
import time
import uuid
from pathlib import Path

import httpx
import pytest

from mcp_server.auth import BearerAuthMiddleware
import mcp_server.server as server_mod
from mcp_server.server import (
    _run_cache,
    extract_financial_statements,
    get_extraction_status,
    get_review_queue,
    search_coa_accounts,
    submit_categorization_correction,
)
import utils.memory_manager as memory_manager

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _stage_upload(tmp_path, content: bytes = b"%PDF-fake") -> str:
    """Directly register a fake upload (bypassing the real /upload HTTP route) —
    for tests that only care about the extraction lifecycle, not the upload
    mechanism itself. TestUploadEndpoint below tests /upload directly."""
    upload_id = uuid.uuid4().hex[:12]
    staged_path = tmp_path / f"{upload_id}_test.pdf"
    staged_path.write_bytes(content)
    with server_mod._uploads_lock:
        server_mod._uploads[upload_id] = staged_path
    return upload_id


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


def _wait_for_terminal_status(task_id: str, timeout: float = 5.0) -> dict:
    """Poll get_extraction_status until it leaves 'running', bounded so a bug can't hang the suite."""
    deadline = time.time() + timeout
    status = get_extraction_status(task_id)
    while status["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
        status = get_extraction_status(task_id)
    return status


class TestExtractFinancialStatementsAsync:
    def test_returns_immediately_with_task_id(self, tmp_path, monkeypatch):
        def slow_process(*args, **kwargs):
            time.sleep(2)
            return {"run_id": "abc", "output_files": [], "review_queue": []}

        monkeypatch.setattr(server_mod, "process_single_pdf", slow_process)
        monkeypatch.setattr(server_mod, "TMP_DIR", tmp_path)

        upload_id = _stage_upload(tmp_path)
        start = time.time()
        result = extract_financial_statements(upload_id=upload_id)
        elapsed = time.time() - start

        assert result["status"] == "started"
        assert "task_id" in result
        assert elapsed < 1.0, "extract_financial_statements should not block on the pipeline"

    def test_completes_and_status_reflects_result(self, tmp_path, monkeypatch):
        def fake_process(pdf_path, types, enable_categorization=True):
            return {
                "run_id": "run-xyz",
                "output_files": [],
                "review_queue": [],
                "evaluation_result": {},
                "categorization_summary": {"total_line_items": 5},
            }

        monkeypatch.setattr(server_mod, "process_single_pdf", fake_process)
        monkeypatch.setattr(server_mod, "TMP_DIR", tmp_path)

        upload_id = _stage_upload(tmp_path)
        result = extract_financial_statements(upload_id=upload_id)

        status = _wait_for_terminal_status(result["task_id"])
        assert status["status"] == "completed"
        assert status["run_id"] == "run-xyz"
        assert status["categorization_summary"] == {"total_line_items": 5}

    def test_failure_is_captured_not_raised(self, tmp_path, monkeypatch):
        def failing_process(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(server_mod, "process_single_pdf", failing_process)
        monkeypatch.setattr(server_mod, "TMP_DIR", tmp_path)

        upload_id = _stage_upload(tmp_path)
        result = extract_financial_statements(upload_id=upload_id)

        status = _wait_for_terminal_status(result["task_id"])
        assert status["status"] == "failed"
        assert "boom" in status["error"]

    def test_cleans_up_temp_file(self, tmp_path, monkeypatch):
        def fake_process(pdf_path, types, enable_categorization=True):
            assert Path(pdf_path).exists()
            return {"run_id": "r1", "output_files": [], "review_queue": []}

        monkeypatch.setattr(server_mod, "process_single_pdf", fake_process)
        monkeypatch.setattr(server_mod, "TMP_DIR", tmp_path)

        upload_id = _stage_upload(tmp_path)
        result = extract_financial_statements(upload_id=upload_id)
        _wait_for_terminal_status(result["task_id"])

        assert list(tmp_path.iterdir()) == []


class TestGetExtractionStatus:
    def test_unknown_task_id_returns_unknown(self):
        assert get_extraction_status("no-such-task") == {"status": "unknown"}


class TestExtractFinancialStatementsUnknownUpload:
    def test_unknown_upload_id_returns_error(self):
        result = extract_financial_statements(upload_id="no-such-upload")
        assert "error" in result

    def test_upload_id_is_single_use(self, tmp_path, monkeypatch):
        def fake_process(pdf_path, types, enable_categorization=True):
            return {"run_id": "r1", "output_files": [], "review_queue": []}

        monkeypatch.setattr(server_mod, "process_single_pdf", fake_process)
        monkeypatch.setattr(server_mod, "TMP_DIR", tmp_path)

        upload_id = _stage_upload(tmp_path)
        first = extract_financial_statements(upload_id=upload_id)
        assert "task_id" in first
        _wait_for_terminal_status(first["task_id"])

        second = extract_financial_statements(upload_id=upload_id)
        assert "error" in second


class TestUploadEndpoint:
    """Exercises the real /upload Starlette route end-to-end via ASGI —
    not an MCP tool call, so this is tested as a plain HTTP request."""

    async def _post(self, content: bytes, token: str | None, filename: str = "test.pdf"):
        transport = httpx.ASGITransport(app=server_mod.app)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            files = {"file": (filename, content, "application/pdf")}
            return await client.post("/upload", headers=headers, files=files)

    def _also_unlock_outer_middleware(self, monkeypatch, key: str):
        """server_mod.app, in non-OAuth mode, is BearerAuthMiddleware(...) — it read
        MCP_API_KEY once at import time, before any test's monkeypatch.setenv could
        run, so it needs patching directly too for a request to get past both layers."""
        if isinstance(server_mod.app, BearerAuthMiddleware):
            monkeypatch.setattr(server_mod.app, "api_key", key)

    async def test_requires_auth(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEY", "secret123")
        resp = await self._post(b"%PDF-fake", token=None)
        assert resp.status_code == 401

    async def test_rejects_wrong_token(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEY", "secret123")
        resp = await self._post(b"%PDF-fake", token="wrong")
        assert resp.status_code == 401

    async def test_accepts_valid_upload(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MCP_API_KEY", "secret123")
        self._also_unlock_outer_middleware(monkeypatch, "secret123")
        monkeypatch.setattr(server_mod, "TMP_DIR", tmp_path)

        resp = await self._post(b"%PDF-fake-content", token="secret123")

        assert resp.status_code == 200
        data = resp.json()
        assert "upload_id" in data
        with server_mod._uploads_lock:
            staged = server_mod._uploads.pop(data["upload_id"], None)
        assert staged is not None and staged.read_bytes() == b"%PDF-fake-content"

    async def test_rejects_oversized_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MCP_API_KEY", "secret123")
        self._also_unlock_outer_middleware(monkeypatch, "secret123")
        monkeypatch.setattr(server_mod, "TMP_DIR", tmp_path)
        monkeypatch.setattr(server_mod, "MAX_UPLOAD_BYTES", 10)

        resp = await self._post(b"this is definitely more than 10 bytes", token="secret123")

        assert resp.status_code == 413


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
