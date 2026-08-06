#!/usr/bin/env python3
"""
MCP client — calls the financial-extractor MCP server directly, without
routing file bytes through an LLM's context window.

Why this exists: when an LLM-driven MCP client (e.g. Claude Code) calls
extract_financial_statements, the model itself has to read the PDF and emit
the base64-encoded bytes as part of the tool call it generates — for a
100KB+ file that's 100K+ characters of pure token cost, plus real risk of
truncation/corruption on a string that long. This script sidesteps that
two ways: it does the upload as a plain HTTP multipart POST (not an MCP
tool call — see mcp_server/server.py's /upload route for why that matters),
and does the base64-decoding of results in a plain Python process, so file
bytes never pass through a model's token stream in either direction.

Usage:
    python scripts/mcp_client.py extract --pdf input/report.pdf
    python scripts/mcp_client.py extract --pdf input/report.pdf --statements income_statement --output-dir output/
    python scripts/mcp_client.py search "Vaccine"
    python scripts/mcp_client.py review-queue <run_id>
    python scripts/mcp_client.py submit-correction --practice-id foo --label Boarding --section REVENUE --wrong-code 5000 --correct-code 5825 --correct-name "Boarding Revenue"

Config (env vars, or pass --url/--token explicitly):
    MCP_SERVER_URL   e.g. https://your-tunnel.trycloudflare.com/mcp
    MCP_API_KEY      the bearer token the server expects
"""

import argparse
import asyncio
import base64
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DEFAULT_TIMEOUT = timedelta(seconds=300)


def _upload_url(mcp_url: str) -> str:
    """Derive the /upload endpoint's URL from the MCP server's URL (same scheme+host)."""
    parts = urlsplit(mcp_url)
    return urlunsplit((parts.scheme, parts.netloc, "/upload", "", ""))


async def _upload_file(url: str, token: str, pdf_path: Path) -> str:
    """POST the file as plain multipart/form-data (not an MCP tool call) and return its upload_id."""
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        with open(pdf_path, "rb") as f:
            files = {"file": (pdf_path.name, f, "application/pdf")}
            response = await client.post(_upload_url(url), headers=headers, files=files, timeout=120)
    response.raise_for_status()
    data = response.json()
    if "upload_id" not in data:
        raise RuntimeError(f"Upload failed: {data}")
    return data["upload_id"]


def _resolve(url: str | None, token: str | None) -> tuple[str, str]:
    url = url or os.environ.get("MCP_SERVER_URL")
    token = token or os.environ.get("MCP_API_KEY")
    if not url or not token:
        print("Error: server URL and API key required (via --url/--token or MCP_SERVER_URL/MCP_API_KEY env vars)", file=sys.stderr)
        sys.exit(1)
    return url, token


async def _call_tool(url: str, token: str, tool_name: str, arguments: dict, timeout: timedelta = DEFAULT_TIMEOUT) -> dict:
    """Call an MCP tool and return its result as a plain dict."""
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments, read_timeout_seconds=timeout)

    if result.structuredContent is not None:
        data = result.structuredContent
        return data.get("result", data) if isinstance(data, dict) else data

    # Fall back to parsing the text content blocks (tools with a bare `dict`
    # return type don't always get structuredContent populated).
    texts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return {"raw": texts[0]}
    # Multiple text blocks (e.g. a list of accounts) -> parse each as JSON.
    parsed = []
    for t in texts:
        try:
            parsed.append(json.loads(t))
        except json.JSONDecodeError:
            parsed.append(t)
    return {"result": parsed}


async def _extract_and_poll(url, token, pdf_path, statement_types, enable_categorization, poll_interval, max_wait):
    """Start an extraction and poll get_extraction_status until it's done.

    Each poll is its own short-lived MCP call, so no single request sits
    open long enough to hit a proxy/tunnel timeout, no matter how long the
    overall extraction takes.
    """
    upload_id = await _upload_file(url, token, pdf_path)
    print(f"Uploaded (upload_id={upload_id}), starting extraction...", file=sys.stderr)

    start_result = await _call_tool(url, token, "extract_financial_statements", {
        "upload_id": upload_id,
        "statement_types": statement_types,
        "enable_categorization": enable_categorization,
    })

    if start_result.get("error") or "task_id" not in start_result:
        return start_result

    task_id = start_result["task_id"]
    print(f"Extraction started (task_id={task_id}), polling every {poll_interval}s...", file=sys.stderr)

    waited = 0
    while waited < max_wait:
        await asyncio.sleep(poll_interval)
        waited += poll_interval
        status = await _call_tool(url, token, "get_extraction_status", {"task_id": task_id})
        if status.get("status") == "running":
            print(f"  ... still running ({waited}s elapsed)", file=sys.stderr)
            continue
        return status

    return {"status": "failed", "error": f"Timed out waiting for task {task_id} after {max_wait}s"}


def cmd_extract(args):
    url, token = _resolve(args.url, args.token)

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"Error: file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    result = asyncio.run(_extract_and_poll(
        url, token, pdf_path, args.statements, not args.no_categorization,
        args.poll_interval, args.max_wait,
    ))

    if result.get("status") == "failed" or result.get("error"):
        print(f"Error: {result.get('error') or result}", file=sys.stderr)
        sys.exit(1)

    if result.get("status") != "completed":
        print(f"Unexpected result: {json.dumps(result, indent=2)}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for name, b64content in result.get("output_files", {}).items():
        out_path = output_dir / name
        out_path.write_bytes(base64.b64decode(b64content))
        written.append(str(out_path))

    summary = {
        "run_id": result.get("run_id"),
        "error_message": result.get("error_message"),
        "categorization_summary": result.get("categorization_summary"),
        "review_queue_count": len(result.get("review_queue", [])),
        "output_files_written": written,
    }
    print(json.dumps(summary, indent=2))


def cmd_search(args):
    url, token = _resolve(args.url, args.token)
    result = asyncio.run(_call_tool(url, token, "search_coa_accounts", {"query": args.query}))
    print(json.dumps(result, indent=2))


def cmd_review_queue(args):
    url, token = _resolve(args.url, args.token)
    result = asyncio.run(_call_tool(url, token, "get_review_queue", {"run_id": args.run_id}))
    print(json.dumps(result, indent=2))


def cmd_submit_correction(args):
    url, token = _resolve(args.url, args.token)
    result = asyncio.run(_call_tool(url, token, "submit_categorization_correction", {
        "practice_id": args.practice_id,
        "label": args.label,
        "section": args.section,
        "wrong_code": args.wrong_code,
        "correct_code": args.correct_code,
        "correct_name": args.correct_name,
    }))
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description="MCP client for the financial-extractor server")
    parser.add_argument("--url", help="MCP server URL (default: $MCP_SERVER_URL)")
    parser.add_argument("--token", help="Bearer token (default: $MCP_API_KEY)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_extract = subparsers.add_parser("extract", help="Extract financial statements from a PDF")
    p_extract.add_argument("--pdf", required=True, help="Path to the local PDF file")
    p_extract.add_argument("--statements", default="all", help="balance_sheet,income_statement,cash_flow or all")
    p_extract.add_argument("--no-categorization", action="store_true", help="Skip CoA categorization")
    p_extract.add_argument("--output-dir", default="output", help="Where to write returned Excel/JSON files")
    p_extract.add_argument("--poll-interval", type=int, default=5, help="Seconds between status checks")
    p_extract.add_argument("--max-wait", type=int, default=1200, help="Give up after this many seconds")
    p_extract.set_defaults(func=cmd_extract)

    p_search = subparsers.add_parser("search", help="Search the Chart of Accounts")
    p_search.add_argument("query")
    p_search.set_defaults(func=cmd_search)

    p_review = subparsers.add_parser("review-queue", help="Get the review queue for a run_id")
    p_review.add_argument("run_id")
    p_review.set_defaults(func=cmd_review_queue)

    p_correction = subparsers.add_parser("submit-correction", help="Submit a human categorization correction")
    p_correction.add_argument("--practice-id", required=True)
    p_correction.add_argument("--label", required=True)
    p_correction.add_argument("--section", required=True)
    p_correction.add_argument("--wrong-code", required=True)
    p_correction.add_argument("--correct-code", required=True)
    p_correction.add_argument("--correct-name", required=True)
    p_correction.set_defaults(func=cmd_submit_correction)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
