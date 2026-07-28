"""MCP server exposing the financial extraction pipeline as remote tools.

Run locally with:
    uvicorn mcp_server.server:app --host 127.0.0.1 --port 8765

Reachability to the outside world is handled by a Cloudflare Tunnel pointed
at that local port, not by this process — it always binds to localhost.
"""

import base64
import os
import threading
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

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

# OAuth is opt-in: set OAUTH_CLIENT_ID (+ OAUTH_CLIENT_SECRET, MCP_PUBLIC_URL)
# to let clients that only support OAuth (e.g. Claude Desktop's Connectors UI)
# authenticate via a real authorization-code + PKCE flow instead of the
# static MCP_API_KEY. The static key keeps working unchanged either way —
# SimpleOAuthProvider.load_access_token() accepts both.
_oauth_client_id = os.environ.get("OAUTH_CLIENT_ID")
_oauth_provider = None  # set below when OAuth is enabled; reused by /upload's auth check

if _oauth_client_id:
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
    from mcp_server.oauth_provider import SimpleOAuthProvider

    _public_url = os.environ.get("MCP_PUBLIC_URL")
    if not _public_url:
        raise RuntimeError(
            "OAUTH_CLIENT_ID is set but MCP_PUBLIC_URL is not — OAuth requires "
            "the server's own public URL (e.g. the current Cloudflare Tunnel URL)."
        )

    _oauth_provider = SimpleOAuthProvider()

    mcp = FastMCP(
        "financial-extractor",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=_allowed_hosts,
            allowed_origins=_allowed_origins,
        ),
        auth_server_provider=_oauth_provider,
        auth=AuthSettings(
            issuer_url=_public_url,
            resource_server_url=f"{_public_url}/mcp",
            client_registration_options=ClientRegistrationOptions(enabled=False),
        ),
    )
else:
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

# Background-task status/results, keyed by a task_id handed out immediately
# when extract_financial_statements is called. A Cloudflare tunnel (and most
# HTTP proxies) will drop a connection that sits open for the several minutes
# a full extraction can take, so the tool returns right away and the caller
# polls get_extraction_status instead of blocking on one long request.
_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()

# Files staged via the plain HTTP /upload endpoint, keyed by upload_id.
# extract_financial_statements consumes one of these instead of taking file
# bytes as a tool argument — see the /upload route below for why.
_uploads: dict[str, Path] = {}
_uploads_lock = threading.Lock()


def _stringify_keys(d: dict) -> dict:
    return {
        (k.value if hasattr(k, "value") else str(k)): v
        for k, v in d.items()
    }


async def _is_authorized(request: Request) -> bool:
    """Same auth rules as the rest of the server, applied manually here since
    @mcp.custom_route() explicitly does not apply auth on its own (routes
    registered that way are meant for things like health checks that must
    stay public)."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    token = auth_header[len("Bearer "):]

    static_key = os.environ.get("MCP_API_KEY")
    if static_key and token == static_key:
        return True

    if _oauth_provider is not None:
        return await _oauth_provider.load_access_token(token) is not None

    return False


@mcp.custom_route("/upload", methods=["POST"])
async def upload_file(request: Request) -> JSONResponse:
    """Plain HTTP file upload — deliberately NOT an MCP tool.

    An MCP tool call requires the calling LLM to generate the argument
    values itself, so a "file content" tool argument would always cost
    tokens proportional to file size no matter what the argument was named.
    A real multipart upload sidesteps that entirely: only an actual HTTP
    client (scripts/mcp_client.py, curl, a browser) can perform one — an
    LLM has no way to "generate" a multipart request body as tool-call
    output. extract_financial_statements takes the resulting upload_id
    instead of file content, so the expensive path isn't available to any
    client, not just ones that happen to know to avoid it.
    """
    if not await _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    form = await request.form(max_part_size=MAX_UPLOAD_BYTES + 1024)
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"error": "missing 'file' field in multipart form"}, status_code=400)

    contents = await upload.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"error": f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit"}, status_code=413
        )

    upload_id = uuid.uuid4().hex[:12]
    filename = Path(upload.filename or "upload.pdf").name  # .name strips any path components
    staged_path = TMP_DIR / f"{upload_id}_{filename}"
    staged_path.write_bytes(contents)

    with _uploads_lock:
        _uploads[upload_id] = staged_path

    return JSONResponse({"upload_id": upload_id})


def _run_extraction(task_id: str, tmp_path: Path, statement_types: str, enable_categorization: bool) -> None:
    """Runs the actual pipeline on a background thread and stores the result under task_id."""
    try:
        types = parse_statement_types(statement_types)
        final_state = process_single_pdf(
            str(tmp_path), types, enable_categorization=enable_categorization
        )

        run_id = final_state.get("run_id")
        review_queue = final_state.get("review_queue", [])
        if run_id:
            _run_cache[run_id] = {"review_queue": review_queue}

        output_files = {}
        for path_str in final_state.get("output_files", []):
            p = Path(path_str)
            if p.exists():
                output_files[p.name] = base64.b64encode(p.read_bytes()).decode("ascii")

        result = {
            "run_id": run_id,
            "error_message": final_state.get("error_message"),
            "evaluation_result": _stringify_keys(final_state.get("evaluation_result", {})),
            "categorization_summary": final_state.get("categorization_summary", {}),
            "review_queue": review_queue,
            "output_files": output_files,
        }
        with _tasks_lock:
            _tasks[task_id] = {"status": "completed", **result}
    except Exception as e:
        with _tasks_lock:
            _tasks[task_id] = {"status": "failed", "error": str(e)}
    finally:
        tmp_path.unlink(missing_ok=True)


@mcp.tool()
def extract_financial_statements(
    upload_id: str,
    statement_types: str = "all",
    enable_categorization: bool = True,
) -> dict:
    """Start extracting Balance Sheet / Income Statement / Cash Flow data from a previously uploaded PDF.

    upload_id comes from POST /upload (a plain multipart file upload, not an
    MCP tool call) — call that first with the PDF's bytes, then pass the
    upload_id it returns here. Returns immediately with a task_id — the
    extraction runs in the background (it can take several minutes) and
    does not block this call. Poll get_extraction_status(task_id) for
    progress and the final result (evaluation scores, categorization
    summary, review queue, and the generated Excel/JSON files inline as
    base64).
    """
    with _uploads_lock:
        tmp_path = _uploads.pop(upload_id, None)

    if tmp_path is None or not tmp_path.exists():
        return {"error": f"unknown or already-consumed upload_id: {upload_id}"}

    task_id = uuid.uuid4().hex[:12]

    with _tasks_lock:
        _tasks[task_id] = {"status": "running"}

    thread = threading.Thread(
        target=_run_extraction,
        args=(task_id, tmp_path, statement_types, enable_categorization),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id, "status": "started"}


@mcp.tool()
def get_extraction_status(task_id: str) -> dict:
    """Poll for the status/result of an extraction started by extract_financial_statements.

    Returns {"status": "running"}, {"status": "completed", ...full result...},
    {"status": "failed", "error": ...}, or {"status": "unknown"} for an
    unrecognized task_id (e.g. after a server restart — task status is
    in-memory only, not persisted).
    """
    with _tasks_lock:
        return _tasks.get(task_id, {"status": "unknown"})


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


# When OAuth is enabled, the mcp SDK wraps /mcp with its own RequireAuthMiddleware
# (backed by SimpleOAuthProvider.load_access_token) and adds /authorize, /token,
# and /.well-known/* routes that must stay reachable without our static-key
# check — so BearerAuthMiddleware only applies in the non-OAuth, static-key-only
# configuration.
app = mcp.streamable_http_app() if _oauth_client_id else BearerAuthMiddleware(mcp.streamable_http_app())
