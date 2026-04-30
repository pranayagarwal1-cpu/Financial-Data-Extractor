import json
from typing import Any


def format_json_output(data: dict, indent: int = 2, include_categorization: bool = True) -> str:
    """
    Format extracted financial statement data as a pretty-printed JSON string.

    Preserves categorization metadata if present (CoA mappings, confidence, reasoning).

    Args:
        data: Dict with extracted financial statement data
        indent: Number of spaces for indentation
        include_categorization: Whether to include categorization metadata

    Returns:
        Formatted JSON string
    """
    return json.dumps(data, indent=indent)


def validate_json_structure(data: dict) -> tuple[bool, list[str]]:
    """
    Validate that the extracted data has the expected structure.

    Args:
        data: Dict to validate

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors = []

    # Check required top-level keys
    required_keys = ["title", "periods", "sections"]
    for key in required_keys:
        if key not in data:
            errors.append(f"Missing required key: {key}")

    if errors:
        return False, errors

    # Validate periods
    if not isinstance(data["periods"], list) or len(data["periods"]) == 0:
        errors.append("Periods must be a non-empty list")

    # Validate sections
    if not isinstance(data["sections"], list) or len(data["sections"]) == 0:
        errors.append("Sections must be a non-empty list")
    else:
        for i, section in enumerate(data["sections"]):
            if "name" not in section:
                errors.append(f"Section {i} missing 'name' field")
            if "rows" not in section:
                errors.append(f"Section {i} missing 'rows' field")
            elif not isinstance(section["rows"], list):
                errors.append(f"Section {i} 'rows' must be a list")
            else:
                for j, row in enumerate(section["rows"]):
                    if "label" not in row:
                        errors.append(f"Section {i}, row {j} missing 'label'")
                    if "values" not in row:
                        errors.append(f"Section {i}, row {j} missing 'values'")

    return len(errors) == 0, errors
