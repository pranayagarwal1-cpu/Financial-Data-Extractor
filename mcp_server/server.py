"""MCP server exposing the financial extraction pipeline as remote tools.

Run locally with:
    uvicorn mcp_server.server:app --host 127.0.0.1 --port 8765

Reachability to the outside world is handled by a Cloudflare Tunnel pointed
at that local port, not by this process — it always binds to localhost.
"""

import base64
import os
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from main import ensure_directories, process_single_pdf, parse_statement_types, TMP_DIR
from coa.chart_of_accounts import search_accounts
from utils.memory_manager import append_corrections
from mcp_server.auth import BearerAuthMiddleware

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

ensure_directories()

# DNS-rebinding protection checks the incoming Host header against this
# allowlist. Localhost is always allowed; add the public hostname(s) you're
# tunneling through (e.g. a Cloudflare Tunnel URL) via MCP_ALLOWED_HOSTS,
# comma-separated, without the scheme (e.g. "my-tunnel.trycloudflare.com").
_extra_hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
_allowed_hosts = ["127.0.0.1:8765", "localhost:8765", *_extra_hosts]
_allowed_origins = [f"https://{h}" for h in _extra_hosts] + [f"http://{h}" for h in _allowed_hosts]

mcp = FastMCP(
    "financial-extractor",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=_allowed_hosts,
        allowed_origins=_allowed_origins,
    ),
)

# In-memory cache of the last review_queue per run_id. Not persisted —
# the extraction pipeline's own metrics files only store a count, not the
# item-level detail, so this cache is what get_review_queue reads from
# until the server process restarts.
_run_cache: dict[str, dict] = {}


def _stringify_keys(d: dict) -> dict:
    return {
        (k.value if hasattr(k, "value") else str(k)): v
        for k, v in d.items()
    }


@mcp.tool()
def extract_financial_statements(
    pdf_base64: str,
    filename: str,
    statement_types: str = "all",
    enable_categorization: bool = True,
) -> dict:
    """Extract Balance Sheet / Income Statement / Cash Flow data from a base64-encoded PDF.

    Returns evaluation scores, categorization summary, the human-review queue,
    and the generated Excel/JSON output files inline as base64 (a remote
    caller has no access to this machine's filesystem).
    """
    pdf_bytes = base64.b64decode(pdf_base64)
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        return {"error": f"PDF exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit"}

    tmp_path = TMP_DIR / f"{uuid.uuid4().hex}_{Path(filename).name}"
    tmp_path.write_bytes(pdf_bytes)

    try:
        types = parse_statement_types(statement_types)
        final_state = process_single_pdf(
            str(tmp_path), types, enable_categorization=enable_categorization
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    run_id = final_state.get("run_id")
    review_queue = final_state.get("review_queue", [])
    if run_id:
        _run_cache[run_id] = {"review_queue": review_queue}

    output_files = {}
    for path_str in final_state.get("output_files", []):
        p = Path(path_str)
        if p.exists():
            output_files[p.name] = base64.b64encode(p.read_bytes()).decode("ascii")

    return {
        "run_id": run_id,
        "error_message": final_state.get("error_message"),
        "evaluation_result": _stringify_keys(final_state.get("evaluation_result", {})),
        "categorization_summary": final_state.get("categorization_summary", {}),
        "review_queue": review_queue,
        "output_files": output_files,
    }


@mcp.tool()
def search_coa_accounts(query: str) -> list[dict]:
    """Search the veterinary Chart of Accounts by name, alias, or description."""
    return [
        {
            "code": a.code,
            "name": a.name,
            "category": a.category,
            "series": a.series,
            "description": a.description,
        }
        for a in search_accounts(query)
    ]


@mcp.tool()
def get_review_queue(run_id: str) -> list[dict]:
    """Return the categorization items needing human review for a previous run_id."""
    cached = _run_cache.get(run_id)
    return cached["review_queue"] if cached else []


@mcp.tool()
def submit_categorization_correction(
    practice_id: str,
    label: str,
    section: str,
    wrong_code: str,
    correct_code: str,
    correct_name: str,
) -> dict:
    """Record a human categorization correction so future runs for this practice learn from it."""
    saved = append_corrections(
        practice_id,
        [{
            "label": label,
            "section": section,
            "wrong_code": wrong_code,
            "correct_code": correct_code,
            "correct_name": correct_name,
        }],
    )
    return {"saved": saved}


app = BearerAuthMiddleware(mcp.streamable_http_app())
