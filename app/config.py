"""Application configuration, driven entirely by environment variables.

Every setting has a sensible default so the app boots on Render with no
configuration at all.
"""

from __future__ import annotations

import os

# Extensions MarkItDown ships converters for (markitdown 0.1.x).
DEFAULT_ALLOWED_EXTENSIONS = frozenset(
    {
        # Documents
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".xls",
        ".epub",
        ".msg",
        # Text / data
        ".txt",
        ".text",
        ".md",
        ".markdown",
        ".csv",
        ".json",
        ".jsonl",
        ".xml",
        ".rss",
        ".atom",
        ".ipynb",
        # Web
        ".html",
        ".htm",
        ".xhtml",
        # Images (metadata, plus OCR/captioning when configured)
        ".jpg",
        ".jpeg",
        ".png",
        # Audio / video (metadata, plus transcription when available)
        ".mp3",
        ".wav",
        ".m4a",
        ".mp4",
        # Archives (converted recursively)
        ".zip",
    }
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_extensions(name: str) -> frozenset[str] | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    parsed = set()
    for item in raw.split(","):
        item = item.strip().lower()
        if not item:
            continue
        parsed.add(item if item.startswith(".") else f".{item}")
    return frozenset(parsed) or None


class Config:
    """Flask configuration object."""

    # Largest accepted request body, counting every file in it. Flask aborts
    # with 413 beyond this. Sized for a 512 MB instance: converters peak at
    # several times the file size, so this is not free headroom.
    MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", 50)
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024

    # How many files a single request may carry. Kept small so that the
    # megabyte budget above can be spent on one or two large documents.
    MAX_FILES = max(1, _env_int("MAX_FILES", 2))

    # Which uploads we accept. Override with ALLOWED_EXTENSIONS=".pdf,.docx".
    ALLOWED_EXTENSIONS = _env_extensions("ALLOWED_EXTENSIONS") or DEFAULT_ALLOWED_EXTENSIONS

    # Let MarkItDown try anything, relying on its own content sniffing.
    ALLOW_ANY_EXTENSION = _env_bool("ALLOW_ANY_EXTENSION", False)

    # Load third-party MarkItDown plugins installed in the environment.
    ENABLE_PLUGINS = _env_bool("MARKITDOWN_ENABLE_PLUGINS", False)

    # Cap for the /api/bundle zip endpoint (sum of submitted markdown, in MB).
    MAX_BUNDLE_MB = _env_int("MAX_BUNDLE_MB", 25)

    JSON_SORT_KEYS = False
