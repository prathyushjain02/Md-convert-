"""Thin wrapper around Microsoft's MarkItDown library.

Everything file-format specific lives in MarkItDown itself
(https://github.com/microsoft/markitdown); this module only handles turning an
uploaded file into a `.md` payload plus the metadata the UI needs.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import BinaryIO

from markitdown import (
    FileConversionException,
    MarkItDown,
    MissingDependencyException,
    StreamInfo,
    UnsupportedFormatException,
)

logger = logging.getLogger(__name__)

_local = threading.local()

# Characters that are unsafe in a download filename on any common platform.
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class ConversionError(Exception):
    """Raised when a file cannot be converted to Markdown."""


@dataclass
class ConversionResult:
    """A successful conversion of one uploaded file."""

    source_filename: str
    markdown_filename: str
    markdown: str
    title: str | None = None
    duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def bytes_out(self) -> int:
        return len(self.markdown.encode("utf-8"))

    def to_dict(self) -> dict:
        return {
            "ok": True,
            "source_filename": self.source_filename,
            "markdown_filename": self.markdown_filename,
            "markdown": self.markdown,
            "title": self.title,
            "duration_ms": self.duration_ms,
            "bytes_out": self.bytes_out,
            "warnings": self.warnings,
        }


def get_markitdown(*, enable_plugins: bool = False) -> MarkItDown:
    """Return a per-thread MarkItDown instance.

    Building one is not free (it registers every converter and warms up
    content sniffing), and the library makes no thread-safety promises, so we
    keep exactly one instance per worker thread.
    """
    instance = getattr(_local, "markitdown", None)
    if instance is None or getattr(_local, "plugins", None) != enable_plugins:
        instance = MarkItDown(enable_plugins=enable_plugins)
        _local.markitdown = instance
        _local.plugins = enable_plugins
    return instance


def safe_stem(filename: str, fallback: str = "document") -> str:
    """Reduce an arbitrary upload name to a safe filename stem."""
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    stem = _UNSAFE_FILENAME_CHARS.sub("-", stem).strip("-._")
    if not stem:
        stem = fallback
    return stem[:100]


def markdown_filename_for(filename: str) -> str:
    """`quarterly report.pdf` -> `quarterly-report.md`."""
    return f"{safe_stem(filename)}.md"


def file_extension(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def convert_upload(
    stream: BinaryIO,
    *,
    filename: str,
    mimetype: str | None = None,
    enable_plugins: bool = False,
) -> ConversionResult:
    """Convert an uploaded file to Markdown.

    The upload is spooled to a private temporary file first. MarkItDown's
    content sniffing (magika) insists on a real buffered binary stream, which
    Werkzeug's upload object is not, and some converters shell out to tools
    that want a path on disk. The temporary file is removed before returning,
    whatever the outcome.
    """
    extension = file_extension(filename)
    basename = os.path.basename(filename or "") or "upload"
    started = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="markitdown-") as tmpdir:
        # Keep the original name: some converters (zip, msg) echo the path they
        # were handed into the Markdown they produce.
        tmp_path = os.path.join(tmpdir, f"{safe_stem(basename)}{extension}")
        try:
            stream.seek(0)
        except (OSError, ValueError):  # non-seekable uploads are read as-is
            pass
        with open(tmp_path, "wb") as handle:
            shutil.copyfileobj(stream, handle, length=1024 * 1024)

        stream_info = StreamInfo(
            filename=basename,
            extension=extension or None,
            mimetype=(mimetype or None),
            local_path=tmp_path,
        )

        with open(tmp_path, "rb") as handle:
            try:
                result = get_markitdown(enable_plugins=enable_plugins).convert_stream(
                    handle, stream_info=stream_info
                )
            except UnsupportedFormatException as exc:
                raise ConversionError(
                    "MarkItDown has no converter for this file"
                    f"{f' ({extension})' if extension else ''}."
                ) from exc
            except MissingDependencyException as exc:
                raise ConversionError(
                    "This format needs an optional dependency that is not installed "
                    f"on the server: {exc}"
                ) from exc
            except FileConversionException as exc:
                raise ConversionError(f"MarkItDown could not read this file: {exc}") from exc
            except Exception as exc:  # noqa: BLE001 - one bad file must not fail the request
                logger.exception("Unexpected failure converting %s", basename)
                raise ConversionError(
                    f"Conversion failed: {exc.__class__.__name__}: {exc}"
                ) from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    markdown = (result.markdown or "").strip()
    # Never let the server's temporary path show up in the output.
    markdown = markdown.replace(tmp_path, basename).replace(tmpdir, "")

    warnings: list[str] = []
    if not markdown:
        warnings.append(
            "MarkItDown produced no text for this file. It may be empty, or a scanned "
            "document with no embedded text layer."
        )

    return ConversionResult(
        source_filename=basename,
        markdown_filename=markdown_filename_for(filename),
        markdown=markdown,
        title=getattr(result, "title", None),
        duration_ms=duration_ms,
        warnings=warnings,
    )
