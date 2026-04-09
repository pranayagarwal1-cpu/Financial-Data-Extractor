# Financial Statement Extractor

**For financial analysts, investment teams, and FP&A leaders** — Automate the extraction of Balance Sheets, Income Statements, and Cash Flow data from PDF annual reports into structured Excel and JSON formats.

**Value Proposition:** Reduce manual data extraction time by 90%+ while maintaining audit-ready accuracy. Transform unstructured PDFs into warehouse-ready data for BI dashboards, financial models, and AI analytics.

---


## The Problem

Financial analysts spend hours manually copying data from PDF annual reports into Excel. This process is:
- **Time-consuming** — 30-60 minutes per report for basic statements
- **Error-prone** — Manual copy-paste mistakes
- **Hard to scale** — Processing 50+ companies becomes a multi-day effort
- **Not reproducible** — Different analysts extract differently

## The Solution

A multi-agent AI system that:
1. **Identifies** financial statement pages in any PDF (handles varied formats, international documents)
2. **Extracts** all line items with period values using vision LLMs
3. **Validates** extraction quality automatically (LLM-as-Judge with accounting rules)
4. **Retries** on failure — no manual review needed for most documents
5. **Outputs** structured Excel and JSON ready for data warehouses

---

## Use Cases

| User Segment | Use Case |
|--------------|----------|
| **Investment Analysts** | Extract comparables from 50+ company annual reports for valuation models |
| **FP&A Teams** | Pull competitor financials for benchmarking and strategic planning |
| **Credit Analysts** | Process borrower financials for covenant monitoring and risk assessment |
| **Quant Researchers** | Build structured datasets from historical filings for backtesting |
| **Data Engineers** | Pipeline PDFs → structured JSON → data warehouse (Snowflake, BigQuery, Databricks) |

---

## Features

- **Multi-Statement Extraction**: Balance Sheet, Income Statement, Cash Flow Statement
- **Multi-Agent Architecture**: Orchestrator → Extractor → Evaluator → Retry loop
- **LLM-as-Judge**: Automatic quality scoring against accounting rules (e.g., Assets = Liabilities + Equity)
- **Dual Output**: Excel for analysts, JSON for data pipelines
- **Web UI**: Streamlit interface for upload/download with metrics dashboard
- **Observability**: Track extraction time, LLM costs, success rates per run

---

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Orchestrator│────▶│  Extractor  │────▶│  Evaluator  │
│  (Page ID)  │     │  (VLM API)  │     │ (LLM-Judge) │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
                    ┌──────────────────────────┘
                    │
            ┌───────▼────────┐
            │  Retry (max 2) │
            └───────┬────────┘
                    │ (pass)
                    ▼
            ┌───────────────┐
            │ Save Outputs  │
            │ (JSON + Excel)│
            └───────────────┘
```

---

## Quick Start

### Prerequisites

- Python 3.12+
- Ollama with a vision-capable model (e.g., `qwen3.5:397b-cloud`)
- System dependencies: `poppler-utils` (for PDF rasterization)

### Install

```bash
# macOS
brew install poppler

# Linux
sudo apt-get install poppler-utils

# Python dependencies
pip install -r requirements.txt
```

### Usage

```bash
# CLI - Single PDF
python main.py --pdf input/annual-report.pdf

# CLI - Batch process folder
python main.py --folder input/

# CLI - Extract specific statements
python main.py --pdf input/report.pdf --statements balance_sheet,income_statement

# Web UI
streamlit run frontend.py
```

---

## Output

Files saved to `output/`:

| Format | Use Case |
|--------|----------|
| `*_balance_sheet.xlsx` | Analyst review, financial modeling |
| `*_balance_sheet.json` | Data warehouse ingestion, API consumption |
| `output/metrics/*.json` | Audit trail, cost tracking |
| `output/logs/*.jsonl` | Compliance, debugging |

---

## Evaluation Criteria

The Evaluator scores each extraction against accounting rules:

| Criterion | What It Checks | Pass Threshold |
|-----------|----------------|----------------|
| **Completeness** | All required sections present (Assets, Liabilities, Equity) | 10/10 |
| **Data Integrity** | Subtotals reconcile (Assets = Liabilities + Equity) | 10/10 |
| **Period Consistency** | Same periods across all rows | 10/10 |
| **Format Validity** | Valid JSON structure | 10/10 |
| **Missing Values** | <20% null/empty values | 8/10 |

**Pass Decision:** Average score ≥ 7, completeness = 10, format_validity = 10

Failed extractions automatically retry (max 2 attempts) before flagging for manual review.

---

## Data Pipeline Integration

```
PDF Upload → Extraction → JSON → Data Warehouse → BI/AI Tools
                                      │
                                      ├──► Snowflake / BigQuery / Databricks
                                      ├──► PowerBI / Tableau / Looker
                                      └──► Python notebooks (pandas, polars)
```

JSON schema for warehouse ingestion:

```json
{
  "title": "Consolidated Balance Sheet",
  "statement_type": "balance_sheet",
  "periods": ["2025", "2024"],
  "sections": [
    {
      "name": "ASSETS",
      "rows": [
        {"label": "Cash and cash equivalents", "values": ["1,234", "1,100"], "is_subtotal": false}
      ]
    }
  ]
}
```

---

## Configuration

Environment variables (optional):

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_MODEL` | `qwen3.5:397b-cloud` | Default model for all tasks |
| `EXTRACTION_MODEL` | Same as above | Model for page detection & extraction |
| `EVALUATION_MODEL` | Same as above | Model for LLM-as-Judge evaluation |
| `ENABLE_OBSERVABILITY` | `true` | Enable metrics logging |

Copy `.env.example` to `.env` to customize.

---

## Project Structure

```
.
├── agents/
│   ├── orchestrator.py   # LLM-based page detection
│   ├── extractor.py      # VLM data extraction
│   └── evaluator.py      # LLM-as-Judge quality scoring
├── graph/
│   ├── state.py          # Workflow state definition
│   └── workflow.py       # LangGraph multi-agent pipeline
├── utils/
│   ├── vlm_utils.py      # Vision LLM prompts
│   ├── llm_detector.py   # Text-based page detection
│   ├── pdf_utils.py      # PDF rasterization
│   ├── excel_writer.py   # Excel output formatting
│   ├── json_formatter.py # JSON serialization
│   └── observability.py  # Metrics & logging
├── config.py             # Global configuration
├── main.py               # CLI entry point
├── frontend.py           # Streamlit web UI
└── requirements.txt      # Python dependencies
```

---

## Metrics & Cost Tracking

The observability module tracks:

- **Extraction duration** — Per-node timing (Orchestrator, Extractor, Evaluator)
- **LLM calls** — Count and duration for cost estimation
- **Retry count** — Quality failures requiring re-extraction
- **Success/failure** — Run-level outcomes
- **Evaluation scores** — Per-statement quality metrics

View metrics via the Streamlit UI or query `output/metrics/*.json` directly.

---

## Limitations

- **PDF quality matters** — Scanned documents with poor OCR may have lower accuracy
- **Table complexity** — Highly nested or multi-currency tables may need manual review
- **Model dependency** — Results vary by vision LLM capability (tested with Qwen3.5)

---

## License

MIT

---

**Built for** financial teams who need to process hundreds of annual reports without hiring an army of analysts.
