"""Flask application factory for the MarkItDown web converter."""

from __future__ import annotations

import io
import logging
import os
import threading

from flask import Flask

from .config import Config

__all__ = ["create_app"]

logger = logging.getLogger(__name__)

_warmed = threading.Event()


def _warm_up() -> None:
    """Build the MarkItDown instance before the first request needs it.

    Importing MarkItDown pulls in pandas, pdfminer and an ONNX runtime, and
    constructing it loads a content-detection model. Left to the first request,
    that lands on whoever uploads first as a long unexplained wait.
    """
    if _warmed.is_set():
        return
    _warmed.set()

    def run() -> None:
        try:
            from .converter import convert_upload

            convert_upload(io.BytesIO(b"<p>warm up</p>"), filename="warmup.html")
            logger.info("MarkItDown warmed up")
        except Exception:  # noqa: BLE001 - warming is best effort
            logger.debug("Warm-up failed; the first request will pay for it",
                         exc_info=True)

    threading.Thread(target=run, name="markitdown-warmup", daemon=True).start()


def create_app(config_object: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from .routes import bp

    app.register_blueprint(bp)

    if os.environ.get("WARMUP", "true").strip().lower() not in {"0", "false", "no"}:
        _warm_up()

    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    return app
