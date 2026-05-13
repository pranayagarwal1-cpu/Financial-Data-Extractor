"""
Pydantic schemas for financial statement extraction output.

Provides structured validation for VLM-extracted JSON and helpers
to serialize clean dictionaries / JSON strings for downstream use.
"""

import json
from typing import List, Optional
from pydantic import BaseModel, Field


class StatementRow(BaseModel):
    """A single line item within a financial statement section."""

    label: str
    values: List[Optional[str]] = Field(default_factory=list)
    is_subtotal: bool = False
    indent_level: int = 0

    # Optional CoA categorization fields (added after extraction)
    coa_code: Optional[str] = None
    coa_name: Optional[str] = None
    confidence: Optional[str] = None
    reasoning: Optional[str] = None

    # ------------------------------------------------------------------
    # Dict-like interface for backward compatibility
    # ------------------------------------------------------------------

    def __setitem__(self, key: str, value):
        setattr(self, key, value)

    def __getitem__(self, key: str):
        return getattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


class StatementSection(BaseModel):
    """A named section of a financial statement (e.g. ASSETS, REVENUE)."""

    name: str
    rows: List[StatementRow] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Dict-like interface for backward compatibility
    # ------------------------------------------------------------------

    def __setitem__(self, key: str, value):
        setattr(self, key, value)

    def __getitem__(self, key: str):
        return getattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


class StatementData(BaseModel):
    """
    Validated schema for a single extracted financial statement.

    Maps directly to the JSON structure the VLM is prompted to produce.
    """

    title: str
    statement_type: str
    periods: List[str] = Field(default_factory=list)
    sections: List[StatementSection] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Dict-like interface for backward compatibility
    # ------------------------------------------------------------------

    def __setitem__(self, key: str, value):
        setattr(self, key, value)

    def __getitem__(self, key: str):
        return getattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_vlm_dict(cls, data: dict) -> "StatementData":
        """
        Build a StatementData from raw VLM JSON output.

        Tolerates minor shape deviations by normalizing nested dicts
        into proper Section / Row models.
        """
        raw_sections = data.get("sections") or []
        sections = []
        for sec in raw_sections:
            if not isinstance(sec, dict):
                continue
            raw_rows = sec.get("rows") or []
            rows = []
            for r in raw_rows:
                if not isinstance(r, dict):
                    continue
                rows.append(
                    StatementRow(
                        label=str(r.get("label", "")),
                        values=[str(v) if v is not None else None for v in r.get("values", [])],
                        is_subtotal=bool(r.get("is_subtotal", False)),
                        indent_level=int(r.get("indent_level", 0)),
                    )
                )
            sections.append(
                StatementSection(name=str(sec.get("name", "")), rows=rows)
            )

        return cls(
            title=str(data.get("title", "")),
            statement_type=str(data.get("statement_type", "")),
            periods=[str(p) for p in data.get("periods", [])],
            sections=sections,
        )

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def model_dump_clean(self) -> dict:
        """Return a plain dictionary suitable for JSON serialization."""
        return self.model_dump(mode="json")

    def model_dump_json_clean(self, indent: int = 2) -> str:
        """Return a pretty-printed JSON string."""
        return json.dumps(self.model_dump_clean(), indent=indent, ensure_ascii=False)
