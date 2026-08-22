# MarkItDown Web

A small web app that turns an uploaded file into Markdown. Drop in a PDF, Word
document, slide deck, spreadsheet, web page, e-book or image and download a
clean `.md` file.

All of the conversion is done by
[**microsoft/markitdown**](https://github.com/microsoft/markitdown) — this
repository is the upload UI, the HTTP API and the deployment config around it.

---

## Features

- Drag-and-drop or click-to-browse uploads, several files at a time
- Live Markdown preview, one-click copy, per-file `.md` download
- `Download all (.zip)` when you convert a batch
- JSON and file-download APIs, so it works from `curl` and scripts too
- Per-file error reporting: one bad file does not fail the whole batch
- Nothing is persisted — each upload is converted in a temporary directory that
  is deleted before the response is sent
- Dark and light themes, no external assets, no tracking

## Supported formats

| Category | Extensions |
| --- | --- |
| Documents | `.pdf` `.docx` `.pptx` `.xlsx` `.xls` `.epub` `.msg` |
| Text & data | `.txt` `.md` `.markdown` `.csv` `.json` `.jsonl` `.xml` `.rss` `.atom` `.ipynb` |
| Web | `.html` `.htm` `.xhtml` |
| Images | `.jpg` `.jpeg` `.png` (metadata; OCR/captioning when configured) |
| Audio & video | `.mp3` `.wav` `.m4a` `.mp4` (metadata, plus transcription) |
| Archives | `.zip` (unpacked, every file inside converted) |

Set `ALLOW_ANY_EXTENSION=true` to accept anything and let MarkItDown identify
the format from the file's contents instead.

---

## Deploy to Render

### Option A — Blueprint (recommended)

The repository already contains [`render.yaml`](render.yaml) and a
[`Dockerfile`](Dockerfile).

1. Push this repository to GitHub.
2. In Render: **New +** → **Blueprint** → pick the repository.
3. Confirm. Render builds the Docker image and starts the service.

The Docker image installs `ffmpeg` and `exiftool`, so image metadata and audio
transcription work in addition to the document formats.

`render.yaml` does not pin a branch, so Render deploys whichever branch is the
repository default. Add `branch: main` (or whichever branch you want) under the
service to pin it.

### Option B — Native Python runtime

Create a new **Web Service** in Render, point it at this repository, and set:

- **Runtime**: `Python 3`
- **Build command**: `pip install -r requirements.txt`
- **Start command**:
  `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 180`
- **Health check path**: `/healthz`

This route skips the `ffmpeg` and `exiftool` system packages, so audio
transcription and image EXIF extraction are unavailable. Every document format
still works.

### Configuration

All settings are optional environment variables — see
[`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAX_UPLOAD_MB` | `25` | Largest accepted request body |
| `MAX_FILES` | `10` | Files per conversion request |
| `ALLOWED_EXTENSIONS` | built-in list | Comma-separated allowlist override |
| `ALLOW_ANY_EXTENSION` | `false` | Accept anything and sniff the format |
| `MARKITDOWN_ENABLE_PLUGINS` | `false` | Load installed MarkItDown plugins |
| `MAX_BUNDLE_MB` | `25` | Cap on the "Download all" zip |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `WEB_CONCURRENCY` / `WEB_THREADS` / `WEB_TIMEOUT` | `2` / `4` / `180` | gunicorn sizing |

Render's free plan has 512 MB of RAM. Large PDFs are memory-hungry, so keep
`WEB_CONCURRENCY` low and raise `WEB_TIMEOUT` rather than adding workers.

---

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

python wsgi.py            # http://127.0.0.1:5000
```

For a production-like run:

```bash
gunicorn wsgi:app --bind 0.0.0.0:5000 --threads 4 --timeout 180
```

Or with Docker:

```bash
docker build -t markitdown-web .
docker run --rm -p 10000:10000 markitdown-web
```

### Tests

```bash
pytest -q
```

The suite converts real HTML, CSV and DOCX documents through MarkItDown and
covers filename sanitisation, upload limits and every API route.

---

## HTTP API

### `POST /api/convert/file`

Converts uploads and returns the Markdown itself. One file in, one `.md` back;
several files in, a `markdown.zip` back.

```bash
curl -X POST -F "files=@report.pdf" \
  https://your-app.onrender.com/api/convert/file -o report.md

curl -X POST -F "files=@a.docx" -F "files=@b.pptx" \
  https://your-app.onrender.com/api/convert/file -o markdown.zip
```

### `POST /api/convert`

Same input, JSON output. This is what the web UI calls.

```bash
curl -X POST -F "files=@report.pdf" https://your-app.onrender.com/api/convert
```

```json
{
  "results": [
    {
      "ok": true,
      "source_filename": "report.pdf",
      "markdown_filename": "report.md",
      "markdown": "# Quarterly Report\n\nRevenue increased by 12% ...",
      "title": null,
      "duration_ms": 82,
      "bytes_out": 58,
      "warnings": []
    }
  ],
  "summary": { "total": 1, "succeeded": 1, "failed": 0 }
}
```

A file that cannot be converted comes back as
`{"ok": false, "source_filename": "...", "error": "..."}` while the rest of the
batch still succeeds.

### Other routes

| Route | Purpose |
| --- | --- |
| `GET /` | The web UI |
| `GET /healthz` | Health check for Render |
| `GET /api/limits` | Current upload limits and allowed extensions |
| `POST /api/bundle` | Zips Markdown the browser already holds (powers "Download all") |

Errors are JSON on `/api/*` routes: `400` for a bad request, `413` when the
upload exceeds `MAX_UPLOAD_MB`, `422` when nothing in the batch converted.

---

## Project layout

```
app/
  __init__.py       application factory
  config.py         environment-driven settings
  converter.py      MarkItDown wrapper, filename sanitisation
  routes.py         page + JSON/file API endpoints
  templates/        Jinja templates
  static/           stylesheet and vanilla JS (no build step, no CDN)
tests/test_app.py   end-to-end tests
wsgi.py             gunicorn entrypoint
Dockerfile          image used by Render
render.yaml         Render Blueprint
```

## Notes and limits

- Scanned PDFs with no text layer produce empty Markdown; the UI says so
  explicitly rather than handing you a blank file.
- Image OCR and LLM captioning are MarkItDown features that need an external
  model. They are not wired up here; images yield metadata only.
- Uploads are never written to a persistent location, but they do pass through
  the server's memory and a temporary directory. Do not deploy this publicly
  for confidential documents without adding authentication.

## Licence

This app is MIT licensed. MarkItDown itself is MIT licensed by Microsoft.
