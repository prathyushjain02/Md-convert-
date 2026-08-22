"""HTTP routes for the MarkItDown web converter."""

from __future__ import annotations

import io
import logging
import zipfile
from collections import Counter

from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import RequestEntityTooLarge

from .converter import (
    ConversionError,
    convert_upload,
    file_extension,
    markdown_filename_for,
    safe_stem,
)

logger = logging.getLogger(__name__)

bp = Blueprint("main", __name__)


def _limits() -> dict:
    cfg = current_app.config
    return {
        "max_upload_mb": cfg["MAX_UPLOAD_MB"],
        "max_files": cfg["MAX_FILES"],
        "allow_any_extension": cfg["ALLOW_ANY_EXTENSION"],
        "allowed_extensions": sorted(cfg["ALLOWED_EXTENSIONS"]),
    }


def _collect_uploads() -> list[FileStorage]:
    """Pull uploaded files out of the request under any of the usual field names."""
    files: list[FileStorage] = []
    for key in ("files", "files[]", "file"):
        files.extend(request.files.getlist(key))
    if not files:
        # Be forgiving about the field name when called from curl or a script.
        for key in request.files:
            files.extend(request.files.getlist(key))
    return [f for f in files if f and f.filename]


def _reject_reason(upload: FileStorage) -> str | None:
    cfg = current_app.config
    if cfg["ALLOW_ANY_EXTENSION"]:
        return None
    extension = file_extension(upload.filename)
    if not extension:
        return "File has no extension, so the format cannot be determined."
    if extension not in cfg["ALLOWED_EXTENSIONS"]:
        return f"Files of type {extension} are not supported."
    return None


def _convert_uploads(uploads: list[FileStorage]) -> list[dict]:
    """Convert every upload, recording per-file success or failure."""
    enable_plugins = current_app.config["ENABLE_PLUGINS"]
    used_names: Counter[str] = Counter()
    results: list[dict] = []

    for upload in uploads:
        reason = _reject_reason(upload)
        if reason:
            results.append(
                {"ok": False, "source_filename": upload.filename, "error": reason}
            )
            continue
        try:
            result = convert_upload(
                upload.stream,
                filename=upload.filename,
                mimetype=upload.mimetype,
                enable_plugins=enable_plugins,
            )
        except ConversionError as exc:
            results.append(
                {"ok": False, "source_filename": upload.filename, "error": str(exc)}
            )
            continue

        # Two uploads can reduce to the same .md name; keep them distinct.
        name = result.markdown_filename
        used_names[name] += 1
        if used_names[name] > 1:
            stem = safe_stem(name)
            name = f"{stem}-{used_names[name]}.md"
            result.markdown_filename = name

        results.append(result.to_dict())

    return results


def _zip_bytes(entries: list[tuple[str, str]]) -> io.BytesIO:
    """Build an in-memory zip from (filename, markdown) pairs."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, markdown in entries:
            archive.writestr(name, markdown)
    buffer.seek(0)
    return buffer


@bp.get("/")
def index():
    return render_template("index.html", limits=_limits())


@bp.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@bp.get("/api/limits")
def limits():
    return jsonify(_limits())


@bp.post("/api/convert")
def api_convert():
    """Convert uploads and return the Markdown as JSON (used by the web UI)."""
    uploads = _collect_uploads()
    if not uploads:
        return jsonify({"error": "No file was uploaded."}), 400

    max_files = current_app.config["MAX_FILES"]
    if len(uploads) > max_files:
        return (
            jsonify({"error": f"Too many files: the limit is {max_files} per request."}),
            400,
        )

    results = _convert_uploads(uploads)
    succeeded = sum(1 for r in results if r["ok"])
    return jsonify(
        {
            "results": results,
            "summary": {
                "total": len(results),
                "succeeded": succeeded,
                "failed": len(results) - succeeded,
            },
        }
    )


@bp.post("/api/convert/file")
def api_convert_file():
    """Convert uploads and return the Markdown itself.

    One file in, one `.md` back; several files in, a `.zip` back. This is the
    endpoint to use from curl or a script.
    """
    uploads = _collect_uploads()
    if not uploads:
        return jsonify({"error": "No file was uploaded."}), 400

    max_files = current_app.config["MAX_FILES"]
    if len(uploads) > max_files:
        return (
            jsonify({"error": f"Too many files: the limit is {max_files} per request."}),
            400,
        )

    results = _convert_uploads(uploads)
    failures = [r for r in results if not r["ok"]]
    successes = [r for r in results if r["ok"]]

    if not successes:
        error = failures[0]["error"] if failures else "Conversion failed."
        return jsonify({"error": error, "results": results}), 422

    if len(successes) == 1 and not failures:
        only = successes[0]
        payload = io.BytesIO(only["markdown"].encode("utf-8"))
        return send_file(
            payload,
            mimetype="text/markdown; charset=utf-8",
            as_attachment=True,
            download_name=only["markdown_filename"],
        )

    entries = [(r["markdown_filename"], r["markdown"]) for r in successes]
    if failures:
        report = "\n".join(f"- {r['source_filename']}: {r['error']}" for r in failures)
        entries.append(("_failed.txt", f"These files could not be converted:\n{report}\n"))

    return send_file(
        _zip_bytes(entries),
        mimetype="application/zip",
        as_attachment=True,
        download_name="markdown.zip",
    )


@bp.post("/api/bundle")
def api_bundle():
    """Zip up Markdown the browser already holds, so 'Download all' is one file.

    Expects `{"files": [{"filename": "a.md", "markdown": "..."}]}`.
    """
    payload = request.get_json(silent=True) or {}
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        return jsonify({"error": "Expected a non-empty 'files' array."}), 400

    max_files = current_app.config["MAX_FILES"]
    if len(files) > max_files:
        return jsonify({"error": f"Too many files: the limit is {max_files}."}), 400

    max_bytes = current_app.config["MAX_BUNDLE_MB"] * 1024 * 1024
    total = 0
    entries: list[tuple[str, str]] = []
    used_names: Counter[str] = Counter()

    for item in files:
        if not isinstance(item, dict):
            return jsonify({"error": "Each entry must be an object."}), 400
        markdown = item.get("markdown")
        if not isinstance(markdown, str):
            return jsonify({"error": "Each entry needs a 'markdown' string."}), 400
        total += len(markdown.encode("utf-8"))
        if total > max_bytes:
            return (
                jsonify(
                    {"error": f"Bundle is larger than the {current_app.config['MAX_BUNDLE_MB']} MB limit."}
                ),
                413,
            )
        name = markdown_filename_for(str(item.get("filename") or "document.md"))
        used_names[name] += 1
        if used_names[name] > 1:
            name = f"{safe_stem(name)}-{used_names[name]}.md"
        entries.append((name, markdown))

    return send_file(
        _zip_bytes(entries),
        mimetype="application/zip",
        as_attachment=True,
        download_name="markdown.zip",
    )


@bp.app_errorhandler(RequestEntityTooLarge)
def handle_too_large(_exc):
    limit = current_app.config["MAX_UPLOAD_MB"]
    message = f"Upload is too large. The limit is {limit} MB per request."
    if request.path.startswith("/api/"):
        return jsonify({"error": message}), 413
    return render_template("error.html", message=message), 413


@bp.app_errorhandler(404)
def handle_not_found(_exc):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found."}), 404
    return render_template("error.html", message="Page not found."), 404


@bp.app_errorhandler(500)
def handle_server_error(exc):
    logger.exception("Unhandled server error", exc_info=exc)
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error."}), 500
    return render_template("error.html", message="Something went wrong."), 500
