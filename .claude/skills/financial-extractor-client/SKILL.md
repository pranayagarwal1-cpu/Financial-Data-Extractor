---
name: financial-extractor-client
description: Use whenever extracting or processing a financial PDF (balance sheet, income statement, cash flow) through the financial-extractor MCP server/connector, or when asked to call its extract_financial_statements tool. ALWAYS invoke scripts/mcp_client.py via Bash for this specific tool instead of reading the PDF and passing pdf_base64 directly as a tool-call argument yourself — do this even for files that look small, since the size isn't obvious until it's measured. This applies any time the user references extract_financial_statements, asks to pull balance sheet/income statement/cash flow data from a PDF via MCP, or wants a financial extraction run through this connector. The other tools on the same server (search_coa_accounts, get_review_queue, submit_categorization_correction) are small and fine to call directly — this skill only changes how extract_financial_statements is invoked.
---

# Financial extractor: use the client script, not a direct tool call

`extract_financial_statements` takes a base64-encoded PDF as one of its
arguments. If you (the model) read the file and construct that argument
yourself, you have to emit the entire encoded file as part of your own tool
call — for a 586KB PDF in this repo that measured out to roughly 573,000
tokens, and there's a second, separate problem: a base64 string that long
is genuinely at risk of getting truncated or corrupted when it passes
through your own output, which silently breaks the extraction rather than
failing loudly.

Neither problem is about *this* file being unusually large — it's inherent
to any client that builds the argument by generating it as text. The fix
isn't to judge file size and decide case-by-case; it's to never take that
path for this tool at all.

## What to do instead

Run `scripts/mcp_client.py` via Bash. It reads the file and does the
base64 encoding as plain Python code — the bytes never pass through your
own context, so there's no token cost and nothing to truncate.

```bash
python scripts/mcp_client.py --url <server-url> --token <key> extract --pdf <path> [--statements ...] [--output-dir ...]
```

`--url`/`--token` can also come from `MCP_SERVER_URL`/`MCP_API_KEY` env
vars — check what's already set or ask the user which they'd prefer before
picking one. Run `python scripts/mcp_client.py extract --help` if you need
the exact flags (statement type filtering, categorization toggle, output
directory, poll interval) rather than guessing.

The script starts the extraction, polls for completion, decodes the
returned files to disk, and prints a small JSON summary (run_id,
evaluation scores, categorization summary, output file paths) — that
summary is what should reach the conversation, not the underlying file
bytes at any point.

## Everything else on this server: call directly

`search_coa_accounts`, `get_review_queue`, and `submit_categorization_correction`
take small, plain arguments (a query string, a run_id, a handful of short
fields). There's no size problem to work around — call these as normal MCP
tool calls, the same way you would any other tool.
