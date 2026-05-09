"""
Configuration settings for the balance sheet extraction system.

Model routing:
    - Names starting with "claude-" route to Anthropic (e.g., claude-sonnet-4-6)
    - All other names route to Ollama (e.g., qwen3.5, llama3)
"""

import os

# Default model for all tasks
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen3.5:397b-cloud")


class Config:
    """Global configuration for the extraction system."""

    # --- Model settings ---
    # Auto-routed by prefix: claude-* → Anthropic, everything else → Ollama
    EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", DEFAULT_MODEL)
    EVALUATION_MODEL = os.getenv("EVALUATION_MODEL", DEFAULT_MODEL)
    CAT_EVALUATION_MODEL = os.getenv("CAT_EVALUATION_MODEL", "qwen3.5:397b-cloud")

    # Per-task overrides for A/B testing (falls back to EXTRACTION_MODEL if unset)
    # e.g., RETRY_EXTRACTION_MODEL = "claude-sonnet-4-6" for higher-quality retries
    RETRY_EXTRACTION_MODEL = os.getenv("RETRY_EXTRACTION_MODEL", None)
    CAT_MODEL = os.getenv("CAT_MODEL", None)
    CAT_RETRY_MODEL = os.getenv("CAT_RETRY_MODEL", None)

    # DPI settings
    SCAN_DPI = 100       # Low DPI for VLM verification
    EXTRACT_DPI = 150    # Balanced DPI for speed + accuracy

    # Detection settings
    USE_VLM_VERIFICATION = os.getenv("USE_VLM_VERIFICATION", "false").lower() == "true"

    # Retry settings
    MAX_RETRIES = 2      # Maximum re-extraction attempts
    MAX_CAT_RETRIES = 2  # Allows 1 retry after initial attempt

    # Output settings
    OUTPUT_FORMATS = ["json", "excel"]  # Supported output formats

    # Observability settings
    ENABLE_OBSERVABILITY = os.getenv("ENABLE_OBSERVABILITY", "true").lower() == "true"
    METRICS_DIR = None  # Set at runtime
    LOGS_DIR = None     # Set at runtime
