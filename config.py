"""
Configuration settings for the balance sheet extraction system.
"""

import os

# Default model for all tasks
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen3.5:397b-cloud")


class Config:
    """Global configuration for the extraction system."""

    # Model settings - all default to the same model for simplicity
    EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", DEFAULT_MODEL)
    EVALUATION_MODEL = os.getenv("EVALUATION_MODEL", DEFAULT_MODEL)

    # DPI settings
    SCAN_DPI = 100       # Low DPI for VLM verification
    EXTRACT_DPI = 150    # Balanced DPI for speed + accuracy

    # Detection settings
    USE_VLM_VERIFICATION = os.getenv("USE_VLM_VERIFICATION", "false").lower() == "true"

    # Retry settings
    MAX_RETRIES = 2      # Maximum re-extraction attempts

    # Output settings
    OUTPUT_FORMATS = ["json", "excel"]  # Supported output formats

    # Observability settings
    ENABLE_OBSERVABILITY = os.getenv("ENABLE_OBSERVABILITY", "true").lower() == "true"
    METRICS_DIR = None  # Set at runtime
    LOGS_DIR = None     # Set at runtime
