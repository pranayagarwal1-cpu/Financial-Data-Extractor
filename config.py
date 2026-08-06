"""
Configuration settings for the balance sheet extraction system.

Model routing:
    - Names starting with "claude-" route to Anthropic (e.g., claude-sonnet-4-6)
    - All other names route to Ollama (e.g., qwen3.5, llama3)
"""

import os

# Default model for all tasks
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemma4:31b-cloud")


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

    # Parallel extraction worker caps
    MAX_PARALLEL_PAGES_LOCAL = 3   # Conservative for local Ollama (VRAM-bound)
    MAX_PARALLEL_PAGES_CLOUD = 10  # Higher for cloud API backends

    # Retry policy: score-based with hard-fail check overrides (Phase 3)
    RETRY_MIN_OVERALL_SCORE = 6.0
    RETRY_HARD_FAIL_CHECKS = ["B2", "A1", "C1", "C2", "C3"]
    NEVER_RETRY_CHECKS = ["C7", "A3"]  # structural issues retry won't fix

    # When True, saves output even if evaluation fails (useful for manual review)
    FORCE_SAVE_ON_FAILURE = os.getenv("FORCE_SAVE_ON_FAILURE", "true").lower() == "true"

    # Output settings
    OUTPUT_FORMATS = ["json", "excel"]  # Supported output formats

    # Guardrail settings
    GUARDRAIL_MAX_TOKENS_PER_RUN = int(os.getenv("GUARDRAIL_MAX_TOKENS_PER_RUN", "200000"))
    GUARDRAIL_MAX_COST_PER_RUN = float(os.getenv("GUARDRAIL_MAX_COST_PER_RUN", "3.00"))
    GUARDRAIL_MAX_CONCURRENT_RUNS = int(os.getenv("GUARDRAIL_MAX_CONCURRENT_RUNS", "4"))
    GUARDRAIL_QUALITY_SCORE_THRESHOLD = float(os.getenv("GUARDRAIL_QUALITY_SCORE_THRESHOLD", "5.0"))
    DISABLE_GUARDRAILS = os.getenv("DISABLE_GUARDRAILS", "false").lower() == "true"

    # Orientation correction for scanned / inverted PDF pages
    AUTO_CORRECT_ORIENTATION = os.getenv("AUTO_CORRECT_ORIENTATION", "true").lower() == "true"

    # Phase 6 — Deep content checks toggle (disable when latency is critical)
    ENABLE_DEEP_CONTENT_CHECKS = os.getenv("ENABLE_DEEP_CONTENT_CHECKS", "true").lower() == "true"

    # Observability settings
    ENABLE_OBSERVABILITY = os.getenv("ENABLE_OBSERVABILITY", "true").lower() == "true"
    METRICS_DIR = None  # Set at runtime
    LOGS_DIR = None     # Set at runtime

    # RAG-assisted CoA retrieval — off by default; when enabled, the categorizer
    # embeds line items and retrieves top-k candidate accounts instead of
    # dumping the entire CoA into every categorization prompt.
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
    USE_RAG_COA_RETRIEVAL = os.getenv("USE_RAG_COA_RETRIEVAL", "false").lower() == "true"
