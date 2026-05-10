# Guardrails & Safety Architecture

**Document status:** Living document — updated as guardrails are implemented.
**Last updated:** 2026-05-09

---

## 1. Current Guardrails (As-Built)

### Retry & Recovery

| Mechanism | Location | Behavior |
|-----------|----------|----------|
| **Extraction retry cap** | `config.py:38` / `agents/orchestrator.py:181` | Max 2 re-extraction attempts. After max, saves raw data without categorization. |
| **Categorization retry cap** | `config.py:39` / `agents/orchestrator.py:211` | Max 2 re-categorization attempts. After max, saves anyway. |
| **Selective retry** | `agents/extractor.py:98-111` | On retry, only re-extracts statement types that failed evaluation. Passed types preserved. |
| **Per-page fault tolerance** | `agents/extractor.py:139-151` | Single page extraction failure returns `None`; merger continues with remaining pages. |
| **Batch retry with backoff** | `agents/categorizer.py:383-396` | Failed categorization batches retried sequentially with 5-second cooldown. |
| **Evaluator fallback** | `agents/cat_evaluator.py:412-423` | If LLM evaluation response is unparseable, falls back to heuristic-only pass/fail. |

### Quality Checks

| Mechanism | Location | Behavior |
|-----------|----------|----------|
| **Numeric precheck hard-fail** | `agents/evaluator.py:351-367` | If >50% of values are unparsable, forces evaluation `passed=False` before LLM judge is called. |
| **Programmatic equation checks** | `agents/evaluator.py:404-413` | Balance sheet (`Assets = Liabilities + Equity`), income statement (`Gross Profit = Revenue - COGS`), cash flow reconciliation. Hard-overrides LLM judge when equations don't balance. |
| **Completeness gate** | `agents/evaluator.py:62-65` | Extraction passes only if `completeness == 10` and `format_validity == 10`. |
| **Ambiguity detection** | `agents/categorizer.py:76-82` | Scans LLM reasoning text for uncertainty keywords ("could be", "unclear", etc.) and auto-flags `needs_review=True`. |
| **Confidence scoring** | `agents/categorizer.py:495-501` | Categorization matches scored `high/medium/low/unmatched`; low-confidence items surfaced in UI for human review. |
| **Learned-correction enforcement** | `agents/cat_evaluator.py:396-403` | If a previously-learned correction is ignored 2+ times, forces categorization evaluation to FAIL. |

### Input Validation

| Mechanism | Location | Behavior |
|-----------|----------|----------|
| **File type restriction** | `ui/sidebar.py:56` / `main.py:144` | UI restricts uploads to `.pdf`; CLI globs `*.pdf`. |
| **PDF path sanitization** | `utils/pdf_utils.py:7-14` | Resolves path, checks `.exists()` and `.is_file()` before passing to `pdfinfo`/`pdftoppm`. Prevents command injection. |
| **Text layer detection** | `utils/llm_detector.py:196-201` | Checks if extracted text length > 100 chars across all pages. If not, falls back to VLM-based image detection. |
| **Page number validation** | `utils/llm_detector.py:295-299` | Validates LLM-returned page numbers are within `1 <= p <= total_pages`. |
| **Pydantic structural validation** | `utils/vlm_utils.py:309-310` | `StatementData.from_vlm_dict()` validates VLM JSON output against schema before downstream propagation. |

### Observability & Cost Tracking

| Mechanism | Location | Behavior |
|-----------|----------|----------|
| **Per-run metrics** | `utils/observability.py` | Tracks duration, LLM calls, retries, token counts, cost per model, evaluation scores. |
| **Cost logging** | `utils/observability.py:25-63` | Model-specific cost rates (Anthropic priced, Ollama = $0). Per-model usage breakdown saved to `output/metrics/*.json`. |
| **Per-run budget logging** | `utils/observability.py:256-267` | Accumulates `total_prompt_tokens`, `total_completion_tokens`, `total_cost_usd` per run. |
| **Freemium cap** | `utils/freemium.py` | Hard limit of 2 free extractions per month per email address. |

### Missing / Not Implemented

| Mechanism | Status | Risk |
|-----------|--------|------|
| **LLM timeout** | ❌ None | Hung Ollama/Anthropic calls block workflow indefinitely. |
| **Circuit breaker** | ❌ None | Repeated LLM failures cascade without fast-fail or fallback. |
| **Rate limiting** | ❌ None | `ThreadPoolExecutor` spawns unlimited workers based on statement type / batch count. |
| **Cost hard cap** | ❌ None | Tracks cost but never blocks when budget exceeded. Runaway Anthropic retries could rack up charges. |
| **Quality degradation detection** | ❌ None | No cross-run tracking of evaluation scores to detect model drift. |
| **PII redaction** | ❌ None | No detection or redaction of SSNs, emails, account numbers in prompts or logs. |
| **Hallucination ground-truth check** | ✅ `agents/evaluator.py` | Subtotal values verified against source page text; anti-hallucination prompt injected on retry. |
| **PDF size / page limits** | ❌ None | No max file size, max page count, or max image dimension checks. |
| **Concurrency cap** | ❌ None | No limit on parallel workflow invocations. |
| **Data integrity escalation** | ❌ None | Equation checks fail evaluation but never hard-abort the run when diff is severe (>10%). |

---

## 2. Proposed Guardrails (Phased)

### Phase A — "Keep the System Alive" (Priority: High)

Focus: prevent runaway costs, catch bad inputs early, and detect when the LLM is producing garbage.

#### A1. Cost Guardrail (Hard Cap)

**What it does:** Enforce a per-run token and dollar budget. Warn at 80%, hard-stop at 100%.

**Where:** `utils/llm_client.py` (pre-call check), `utils/observability.py` (accumulator hook).

**Trigger:**
- Per-run tokens > `GUARDRAIL_MAX_TOKENS_PER_RUN` (default 200,000)
- Per-run cost > `GUARDRAIL_MAX_COST_PER_RUN` (default $3.00)

**Action:**
- At 80%: emit warning, switch to cheaper model for remaining calls.
- At 100%: raise `CostLimitExceededError`. Workflow saves whatever data exists with `cost_limited: true` flag.

**Config:**
```python
GUARDRAIL_MAX_TOKENS_PER_RUN = int(os.getenv("GUARDRAIL_MAX_TOKENS_PER_RUN", "200000"))
GUARDRAIL_MAX_COST_PER_RUN = float(os.getenv("GUARDRAIL_MAX_COST_PER_RUN", "3.00"))
```

---

#### A2. Quality Tracker (Degradation Detection)

**What it does:** Maintain a sliding window of evaluation scores. Detect when the model is producing consistently low-quality output.

**Where:** `agents/evaluator.py` (post-evaluation), `agents/orchestrator.py` (`should_retry()`).

**Trigger:**
- Run-level: average evaluation score < 5.0 for 2 consecutive attempts on same PDF.
- Global-level: 3 consecutive runs (any PDFs) with average score < 5.0.

**Action:**
- Run-level: switch to higher-quality retry model (`Config.RETRY_EXTRACTION_MODEL`).
- Global-level: disable retry loop, save raw data with `guardrail_flags: ["quality_degraded"]`.

**Reset:** Global tracker resets after 2 consecutive runs with average score >= 7.0.

---

#### A3. Input Validator (Fail-Fast for Bad PDFs)

**What it does:** Validate PDF health before any LLM calls are attempted.

**Where:** `agents/orchestrator.py` (top of `orchestrator_node()`).

**Trigger:**
- File size = 0 or > 100 MB
- `pdfplumber.open()` raises `PDFSyntaxError`
- Total pages = 0
- Text layer absent (`total_text_length < 100`) AND VLM fallback also returns empty

**Action:** Immediate fail-fast. Returns `{"error_message": "Malformed input: <reason>"}`. No LLM calls.

---

#### A4. Concurrency Limiter

**What it does:** Cap parallel workflow invocations and internal ThreadPoolExecutor workers.

**Where:** `main.py` (workflow-level), `agents/extractor.py`, `agents/categorizer.py`.

**Trigger:** `active_workflows >= GUARDRAIL_MAX_CONCURRENT_RUNS` (default 4).

**Action:**
- Workflow-level: queue or reject with `"System is at maximum concurrency. Please try again later."`
- Node-level: cap `ThreadPoolExecutor(max_workers)` to 3 (extractor) / 4 (categorizer).

---

### Phase B — "Keep Data Safe" (Priority: Medium)

Focus: protect sensitive data in prompts/logs, and catch fabricated values.

#### B1. PII Guardrail (Redaction)

**What it does:** Detect and redact SSNs, emails, phone numbers, bank account numbers from prompts before they leave the local machine, and from logs before they hit disk.

**Where:** `utils/llm_client.py` (pre-send sanitization), `utils/observability.py` (pre-log redaction).

**Trigger:** Regex match in prompt text or log payload.

**Action:** Replace matches with `[REDACTED-SSN]`, `[REDACTED-EMAIL]`, etc. Never blocks execution — always-on sanitizer.

**Risk assessment:** Low-medium. Only financial statement pages (not full PDF) are sent to LLMs. But account numbers, tax IDs, or phone numbers can still appear in statement footnotes.

---

#### B2. Hallucination Guardrail (Ground-Truth Check) — ✅ Implemented

**What it does:** After extraction, verify that subtotal/total values fuzzy-match text extracted from the source PDF pages.

**Where:** `agents/evaluator.py` (Layer 0, before numeric precheck). `agents/extractor.py` stores source page texts in state.

**Trigger:** > 3 subtotal values that do not appear in source page text.

**Action:**
- Forces evaluation `passed=False`.
- Injects anti-hallucination prompt on retry: `"Extract ONLY values visibly printed in the image. Do not compute or infer totals."`
- If max retries reached: save with `guardrail_flags: ["hallucination_warning"]`.

---

#### B3. Data Integrity Escalation (Hard Fail)

**What it does:** When accounting equation checks show a severe mismatch, abort the run instead of saving bad data.

**Where:** `agents/evaluator.py` (Layer 3), `agents/orchestrator.py` (`should_retry()`).

**Trigger:** After `MAX_RETRIES`, balance sheet diff > 10% of expected total.

**Action:** Route to `guardrail_sink` node instead of `save_outputs`. No output files produced. Observability run ends `success=False` with `data_quality_severity: "critical"`.

---

### Phase C — "Resilience" (Priority: Low)

Focus: handle LLM backend outages and timeouts.

#### C1. Model Circuit Breaker with Fallback

**What it does:** Fast-fail when an LLM backend is repeatedly failing, with automatic fallback to alternate backend.

**Where:** `utils/llm_client.py`.

**Trigger:** 3 consecutive exceptions from same backend (`ConnectionError`, `TimeoutError`, rate-limit 429/502/503).

**Action:**
- Ollama OPEN → fallback to Anthropic.
- Anthropic OPEN → fallback to Ollama.
- Both OPEN → raise `CircuitBreakerOpenError`, route to `guardrail_sink`.

**Reset:** Ollama 30s, Anthropic 300s. HALF_OPEN allows 1 probe call.

---

#### C2. Request Timeout

**What it does:** Inject explicit request-level timeouts on all LLM calls.

**Where:** `utils/ollama_client.py`, `utils/anthropic_client.py`.

**Action:** `ollama.Client(timeout=120)`, `Anthropic(timeout=120)`. Raises `TimeoutError` if exceeded.

---

## 3. Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-05-09 | Ollama downtime deprioritized | User runs local Ollama; primary concern is model quality degradation, not availability. |
| 2026-05-09 | Anthropic cost guardrail prioritized | Only cloud backend with metered billing; Ollama is $0. |
| 2026-05-09 | PII guardrail in Phase B (not A) | Financial statement pages are a subset of the full PDF; lower exposure than full-document upload systems. |
| 2026-05-09 | Hallucination guardrail in Phase B | Requires source page text storage in state (`page_texts`); more invasive than cost/quality checks. |
| 2026-05-09 | Circuit breaker in Phase C | User's primary failure modes are quality/cost/input, not backend outages. |
