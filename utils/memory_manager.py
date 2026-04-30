"""
Memory Manager — Load, save, and manage per-practice learned corrections.

File layout:
    memory/
    ├── _default.md          # Universal rules (promoted after 3+ practices agree)
    ├── 0807_001.md          # Practice-specific overrides
    └── ...

Usage:
    load_memory_rules("0807_001")   -> list of CorrectionRule
    append_corrections("0807_001", [...])  -> saves to file
    maybe_promote_to_default(rule) -> bool
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional
from collections import defaultdict

BASE_DIR = Path(__file__).parent.parent
MEMORY_DIR = BASE_DIR / "memory"
DEFAULT_MEMORY_PATH = MEMORY_DIR / "_default.md"


@dataclass
class CorrectionRule:
    """A single learned correction."""
    label: str
    section: str
    wrong_code: str
    correct_code: str
    correct_name: str
    count: int = 1


def _parse_memory_file(path: Path) -> List[CorrectionRule]:
    """Parse a markdown memory file into CorrectionRule objects."""
    rules = []
    if not path.exists():
        return rules

    content = path.read_text(encoding="utf-8")
    # Find the markdown table
    # Skip header rows, parse data rows
    lines = content.splitlines()
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|---"):
            in_table = True
            continue
        if in_table and stripped.startswith("|") and "Label" not in stripped:
            parts = [p.strip() for p in stripped.split("|")]
            parts = [p for p in parts if p]  # Remove empty from leading/trailing |
            if len(parts) >= 5:
                try:
                    count = int(parts[5]) if len(parts) > 5 else 1
                except ValueError:
                    count = 1
                rules.append(CorrectionRule(
                    label=parts[0],
                    section=parts[1],
                    wrong_code=parts[2],
                    correct_code=parts[3],
                    correct_name=parts[4],
                    count=count
                ))
    return rules


def load_memory_rules(practice_id: str) -> List[CorrectionRule]:
    """
    Load learned corrections for a practice.

    Loads _default.md first, then merges practice-specific overrides.
    Practice-specific rules take precedence (overwrite default by label+section key).

    Args:
        practice_id: Usually the PDF filename stem (e.g., "0807_001")

    Returns:
        List of CorrectionRule objects applicable to this practice
    """
    rules_by_key: Dict[str, CorrectionRule] = {}

    # 1. Load defaults
    for rule in _parse_memory_file(DEFAULT_MEMORY_PATH):
        key = f"{rule.label.lower()}::{rule.section.lower()}"
        rules_by_key[key] = rule

    # 2. Load practice-specific overrides
    practice_path = MEMORY_DIR / f"{practice_id}.md"
    for rule in _parse_memory_file(practice_path):
        key = f"{rule.label.lower()}::{rule.section.lower()}"
        rules_by_key[key] = rule

    return list(rules_by_key.values())


def build_memory_prompt(practice_id: str) -> str:
    """
    Build a prompt snippet with learned corrections for injection into categorizer.

    Returns:
        Markdown-formatted rules block, or empty string if no memory exists.
    """
    rules = load_memory_rules(practice_id)
    if not rules:
        return ""

    lines = ["\n## PRIOR CORRECTIONS (learned from previous runs)\n"]

    for rule in rules:
        if rule.count >= 2:
            lines.append(
                f'- "{rule.label}" in "{rule.section}" → {rule.correct_code} {rule.correct_name}'
                f' (NEVER {rule.wrong_code})'
            )
        else:
            lines.append(
                f'- "{rule.label}" in "{rule.section}" → {rule.correct_code} {rule.correct_name}'
                f' (previously mis-mapped to {rule.wrong_code})'
            )

    lines.append("")
    return "\n".join(lines)


def _serialize_rules(rules: List[CorrectionRule]) -> str:
    """Serialize rules to markdown table format."""
    lines = [
        "| Label | Section | Wrong Code | Correct Code | Correct Name | Count |",
        "|---|---|---|---|---|---|",
    ]
    for rule in rules:
        lines.append(
            f"| {rule.label} | {rule.section} | {rule.wrong_code} | {rule.correct_code} | {rule.correct_name} | {rule.count} |"
        )
    return "\n".join(lines) + "\n"


def _write_memory_file(path: Path, rules: List[CorrectionRule]) -> None:
    """Write or overwrite a memory file with rules."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    now_str = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    header = f"""---
practice_id: {path.stem}
last_updated: {now_str}
total_corrections: {len(rules)}
---

"""
    content = header + _serialize_rules(rules)
    path.write_text(content, encoding="utf-8")


def append_corrections(practice_id: str, corrections: List[dict]) -> int:
    """
    Append new corrections to a practice memory file.

    Args:
        practice_id: PDF filename stem
        corrections: List of dicts with keys:
            label, section, wrong_code, correct_code, correct_name

    Returns:
        Number of corrections actually saved (new or updated)
    """
    practice_path = MEMORY_DIR / f"{practice_id}.md"

    # Load existing
    existing = _parse_memory_file(practice_path)
    existing_by_key: Dict[str, CorrectionRule] = {
        f"{r.label.lower()}::{r.section.lower()}": r for r in existing
    }

    new_count = 0
    for corr in corrections:
        key = f"{corr['label'].lower()}::{corr['section'].lower()}"
        if key in existing_by_key:
            # Same correction already exists — increment count
            existing_by_key[key].count += 1
            existing_by_key[key].correct_code = corr["correct_code"]
            existing_by_key[key].correct_name = corr["correct_name"]
        else:
            existing_by_key[key] = CorrectionRule(
                label=corr["label"],
                section=corr["section"],
                wrong_code=corr["wrong_code"],
                correct_code=corr["correct_code"],
                correct_name=corr["correct_name"],
                count=1
            )
            new_count += 1

    _write_memory_file(practice_path, list(existing_by_key.values()))
    return new_count


def get_default_rules() -> List[CorrectionRule]:
    """Load the universal _default.md rules."""
    return _parse_memory_file(DEFAULT_MEMORY_PATH)


def maybe_promote_to_default(rule: CorrectionRule, practice_ids_seen: List[str]) -> bool:
    """
    Check if a rule should be promoted to _default.md.

    A rule is promoted when 3+ different practices have independently
    made the same correction (same label + section + correct_code).

    Args:
        rule: The correction rule to evaluate
        practice_ids_seen: List of all practice IDs that have this correction

    Returns:
        True if rule should be promoted to default
    """
    return len(set(practice_ids_seen)) >= 3 and rule.count >= 3
