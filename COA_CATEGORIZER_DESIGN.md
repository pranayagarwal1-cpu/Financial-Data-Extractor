# COA Categorizer Design Document

**Created:** 2026-04-14  
**Status:** Design approved, ready for implementation

---

## Objective

Add line item categorization to income statement extraction, mapping extracted P&L line items to VMG/AAHA Chart of Accounts (COA) with:
- Exact-match only (no fuzzy/LLM inference for auto-categorization)
- Full audit trail with citations
- Human-in-the-loop for ambiguous/unmatched items
- Production-grade reliability

---

## Sample P&L Analysis (0807_001_income_statement)

**Tested against COA:** 59 line items

| Confidence | Count | % |
|------------|-------|---|
| HIGH (exact/alias match) | ~35 | ~60% |
| COMPOUND (needs split) | ~6 | ~10% |
| AMBIGUOUS (multiple matches) | ~3 | ~5% |
| UNMATCHED (no match) | ~12 | ~20% |

**Expected auto-categorization rate:** ~60%  
**Expected human review rate:** ~35-40%

---

## Workflow Architecture

```
START → Orchestrator → Extractor → Evaluator → Categorizer → Save Outputs → END
                                                    │
                                                    ↓
                                            [Has Review Items?]
                                                    │
                                            ┌───────┴───────┐
                                            ↓               ↓
                                       human_review    save_outputs
                                            │
                                            ↓ (after human input)
                                       save_outputs
```

### Node Responsibilities

| Node | Responsibility | In Retry Loop? |
|------|----------------|----------------|
| Orchestrator | Detect statement pages | N/A |
| Extractor | VLM extracts JSON from images | ✅ Yes |
| Evaluator | Validate extraction quality | ✅ Yes |
| **Categorizer** | **Map line items to COA (exact match)** | ❌ No |
| Human Review | CLI/UI for manual categorization | ❌ No |
| Save Outputs | Write JSON + Excel | ❌ No |

---

## Categorizer Agent Design

### Input/Output Contract

**Input (from state):**
```python
{
    "extracted_data": {
        StatementType.INCOME_STATEMENT: {
            "title": "Income Statement",
            "periods": ["01/07/2024 to 30/06/2025"],
            "sections": [{
                "name": "REVENUE",
                "rows": [
                    {"label": "Exams / Consultations", "values": ["148,915.23"], "is_subtotal": false},
                    {"label": "Vaccinations", "values": ["256,365.82"], "is_subtotal": false},
                    ...
                ]
            }]
        }
    }
}
```

**Output (to state):**
```python
{
    "categorized_data": {
        StatementType.INCOME_STATEMENT: {
            "title": "Income Statement",
            "periods": ["01/07/2024 to 30/06/2025"],
            "sections": [{
                "name": "REVENUE",
                "rows": [
                    {
                        "label": "Vaccinations",
                        "values": ["256,365.82"],
                        "is_subtotal": false,
                        "categorization": {
                            "coa_code": "5001",
                            "coa_name": "Vaccine Revenue",
                            "coa_category": "Revenue",
                            "match_type": "exact",
                            "matched_on": "Vaccinations",
                            "citation": "VMG/AAHA COA p.108",
                            "confidence": "high"
                        }
                    }
                ]
            }]
        }
    },
    "categorization_summary": {
        "total_line_items": 59,
        "auto_categorized": 35,
        "needs_review": 24,
        "match_rate": 0.59
    },
    "review_queue": [
        {
            "label": "Surgery / Dentistry",
            "values": ["186,714.55"],
            "section": "REVENUE",
            "confidence": "compound",
            "reason": "Compound label detected",
            "candidates": [
                {"coa_code": "5500", "coa_name": "Surgery Revenue"},
                {"coa_code": "5700", "coa_name": "Dentistry Revenue"}
            ]
        },
        ...
    ]
}
```

---

## Match Confidence Levels

```python
class MatchConfidence(Enum):
    HIGH = "high"               # Single exact match - auto-approve
    AMBIGUOUS = "ambiguous"     # Multiple matches - human review
    UNMATCHED = "unmatched"     # No match - human review
    COMPOUND = "compound"       # Multiple categories in one label - human review
```

### Matching Logic

```python
def match_line_item(label: str) -> MatchResult:
    """
    Match line item to COA account using exact matching.
    
    Decision tree:
    1. Find all COA accounts where label matches name or alias (case-insensitive)
    2. If 0 matches → UNMATCHED
    3. If 2+ matches → AMBIGUOUS
    4. If 1 match AND label contains "/" or "and" → COMPOUND
    5. If 1 match AND no separator → HIGH
    """
```

---

## COA Reference Data Structure

**File:** `coa/chart_of_accounts.py`

```python
from dataclasses import dataclass
from typing import Dict, List

@dataclass
class COAAccount:
    code: str
    name: str
    category: str          # Revenue, Direct Cost, Labor, G&A, etc.
    series: str            # 5000, 6000, 7000, etc.
    description: str
    aliases: List[str]     # Pre-registered variations for exact matching
    page: int              # Citation page number

# Canonical COA - manually curated from VMG/AAHA PDF
COA_ACCOUNTS: Dict[str, COAAccount] = {
    # Revenue accounts (5000 series)
    "5001": COAAccount(
        code="5001",
        name="Vaccine Revenue",
        category="Revenue",
        series="5000",
        description="Revenue from vaccine administration",
        aliases=["Vaccinations", "Vaccine", "Vaccine Revenue"],
        page=108
    ),
    "5010": COAAccount(
        code="5010",
        name="Exam Revenue",
        category="Revenue",
        series="5000",
        description="Revenue from examinations and consultations",
        aliases=["Exams", "Consultations", "Exams / Consultations"],
        page=108
    ),
    # ... ~200 accounts total
}

# Index for O(1) lookup by name/alias
COA_NAME_INDEX: Dict[str, str] = {
    "Vaccine Revenue": "5001",
    "Vaccinations": "5001",
    "Exams / Consultations": "5010",
    # ...
}
```

---

## Files to Create/Modify

### New Files
| File | Purpose |
|------|---------|
| `coa/__init__.py` | Package init |
| `coa/chart_of_accounts.py` | COA reference data + alias index |
| `coa/matcher.py` | Exact-match logic with confidence levels |
| `agents/categorizer.py` | Categorizer node implementation |
| `agents/human_review.py` | CLI review interface (optional Phase 2) |

### Modified Files
| File | Change |
|------|--------|
| `graph/state.py` | Add `categorized_data`, `categorization_summary`, `review_queue` |
| `graph/workflow.py` | Add categorizer node + conditional edge |
| `utils/json_formatter.py` | Include categorization in JSON output |
| `utils/excel_writer.py` | Add COA Code, COA Name, Category columns |

---

## Implementation Plan

### Phase 1: Core Categorization
1. Create `coa/` module with COA reference data (bootstrap from PDF pp.106-113)
2. Implement `coa/matcher.py` with exact-match + confidence levels
3. Create `agents/categorizer.py` node
4. Update `graph/state.py` with new fields
5. Update `graph/workflow.py` to add categorizer node

### Phase 2: Output Integration
6. Update `utils/json_formatter.py` to include categorization
7. Update `utils/excel_writer.py` with COA columns
8. Test end-to-end with sample P&L

### Phase 3: Human Review (Optional)
9. Create `agents/human_review.py` CLI interface
10. Add conditional edge for review queue
11. Implement alias learning from human decisions

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Exact match only (no fuzzy) | Deterministic, auditable, per requirements |
| Alias index for variations | Handles common terminology differences |
| Block on UNMATCHED/COMPOUND/AMBIGUOUS | Human makes final call on edge cases |
| Categorization AFTER evaluator | Keeps extraction self-evaluation loop intact |
| Preserve original labels | Categorization is additive metadata, not replacement |
| Confidence levels | Transparent audit trail for why items were auto/manual |

---

## Open Questions (Resolved)

| Question | Decision |
|----------|----------|
| Where does categorizer fit in workflow? | After evaluator, before save_outputs |
| Does categorizer participate in retry? | No - extraction retries are separate |
| What match types block output? | UNMATCHED, COMPOUND, AMBIGUOUS |
| How to handle compound labels? | Flag for human review (e.g., "Surgery / Dentistry") |
| Should RAG/LLM be used? | Not for auto-categorization. Optional for human review assistance. |

---

## Sample Line Item Mappings (Reference)

| P&L Label | COA Code | COA Name | Confidence |
|-----------|----------|----------|------------|
| Vaccinations | 5001 | Vaccine Revenue | HIGH |
| Diagnostics | 5030 | Diagnostic Services Revenue | HIGH |
| Exams / Consultations | 5010 | Exam Revenue | COMPOUND |
| Surgery / Dentistry | 5500/5700 | Surgery/Dentistry Revenue | COMPOUND |
| Cremation | 5050 | Mortuary Revenue | HIGH |
| Boarding | 5825 | Boarding Revenue | HIGH |
| Food Sales | 5202 | Retail Diet Revenue | HIGH |
| Drug Sales | 5100 | Pharmacy Revenue | HIGH |
| EI Expense | — | — | UNMATCHED (Canadian-specific) |
| CPP Expense | — | — | UNMATCHED (Canadian-specific) |
| Accounting & Legal | 7765/7785 | Accounting/Legal Fees | COMPOUND |

---

## Next Steps (When Resuming)

1. Read this document to reorient
2. Begin Phase 1 implementation
3. Bootstrap COA reference data from PDF pages 106-113
4. Test categorizer against sample P&L (0807_001_income_statement)

---

## Related Files

- Sample P&L: `output/0807_001_income_statement_20260412_152949.xlsx`
- COA Reference PDF: `/Users/pranayagarwal/Downloads/VMG_AAHA-COA-Book-10-1-25.pdf`
- Workflow: `graph/workflow.py`
- State: `graph/state.py`
- Extractor: `agents/extractor.py`
- Evaluator: `agents/evaluator.py`
