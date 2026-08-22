# MarkItDown Web

A small web app that turns an uploaded file into Markdown. Drop in a PDF, Word
document, slide deck, spreadsheet, web page, e-book or image and download a
clean `.md` file.

All of the conversion is done by
[**microsoft/markitdown**](https://github.com/microsoft/markitdown) — this
repository is the upload UI, the HTTP API and the deployment config around it.

---

## Features

- Drag-and-drop or click-to-browse uploads, up to two files at a time
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
  `gunicorn wsgi:app --preload --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 300`
- **Health check path**: `/healthz`

This route skips the `ffmpeg` and `exiftool` system packages, so audio
transcription and image EXIF extraction are unavailable. Every document format
still works.

### Configuration

All settings are optional environment variables — see
[`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAX_UPLOAD_MB` | `50` | Largest accepted request body, counting every file in it |
| `MAX_FILES` | `2` | Files per conversion request |
| `ALLOWED_EXTENSIONS` | built-in list | Comma-separated allowlist override |
| `ALLOW_ANY_EXTENSION` | `false` | Accept anything and sniff the format |
| `PDF_ENGINE` | `layout` | `layout` or `markitdown` — see [PDF quality](#pdf-quality) |
| `MARKITDOWN_ENABLE_PLUGINS` | `false` | Load installed MarkItDown plugins |
| `MAX_BUNDLE_MB` | `25` | Cap on the "Download all" zip |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `WEB_CONCURRENCY` / `WEB_THREADS` / `WEB_TIMEOUT` | `1` / `2` / `300` | gunicorn sizing |

### Sizing the upload limit

`MAX_UPLOAD_MB` is a budget for the whole request, not per file — two 30 MB PDFs
exceed a 50 MB limit. The defaults (2 files, 50 MB) are chosen for Render's free
plan, which gives one instance 512 MB of RAM.

Conversion costs a multiple of the file size rather than the size itself:
pdfminer builds a layout tree per page, and the spreadsheet path loads sheets
through pandas. A 50 MB PDF can peak in the hundreds of megabytes. That is why
`WEB_CONCURRENCY` and `WEB_THREADS` are low — one large conversion can claim
most of a free instance, and serving several at once is what gets the process
OOM-killed and restarted, taking the requests that were succeeding with it.

To go higher than 50 MB, raise `MAX_UPLOAD_MB` and give the service more
memory; on a 512 MB instance a 100 MB PDF is likely to be killed mid-conversion
rather than merely slow. Watch `WEB_TIMEOUT` too: a big scanned document can
outrun it, and gunicorn then kills the worker after the user has already waited
the full timeout.

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
gunicorn wsgi:app --preload --bind 0.0.0.0:5000 --threads 2 --timeout 300
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

## Performance

Measured on a 4-core container, converting a 20-page factsheet with ruled
tables, borderless tables and two-column prose on every page:

| | Cost |
| --- | --- |
| Conversion, layout engine | ~68 ms/page |
| Conversion, `PDF_ENGINE=markitdown` | ~51 ms/page |
| Import MarkItDown (cold disk, once per container) | up to ~13 s |
| Build the MarkItDown instance (once per worker) | ~0.3 s |

Nearly all of the per-page cost is parsing the PDF's content stream, which
every engine pays; layout analysis adds about a third on table-heavy pages and
is essentially free on pages without ruled tables.

Two things are done about the startup cost. `gunicorn --preload` imports the
library once in the master process, so forked workers inherit it instead of
each paying for it, and the app warms MarkItDown in a background thread at
boot so the first upload does not have to. Neither helps if the instance is not
running at all — see below.

**If conversion feels slow in production, the instance is usually the reason.**
Render's free plan runs a web service on a 0.1 CPU share, so the per-page costs
above multiply by roughly ten, and it spins the instance down after 15 minutes
of inactivity — the next request then waits for a cold container start before
any work begins. A paid instance removes the spin-down and gives several times
the CPU, which is a far larger effect than anything in this codebase.

For a large document on a multi-core paid instance, converting page ranges in
separate processes would parallelise well. It is not implemented: it would not
help on a fractional-CPU instance, which is where the problem is usually felt.

---

## PDF quality

PDFs are converted by `app/pdf.py` rather than by MarkItDown's built-in PDF
converter, which is tuned for invoices and forms. On a document with several
unrelated tables per page, or with more than one text column — a fund
factsheet, say — the built-in converter produces three specific defects:

| Defect | Cause | Handling here |
| --- | --- | --- |
| Words fuse: `Newwayofdoingbusiness` | Words are split on a fixed 3pt gap, but 7pt display type set without space glyphs has sub-point gaps | Gaps scale with character size, so small and large type both split correctly |
| Tables grow empty columns | One column grid is built per *page* and every table is forced into it, so a 3-column table above a 4-column one gains a phantom column | Each table gets its own grid, and columns no row fills are dropped |
| Multi-column pages interleave | Words are grouped into rows across the full page width, so the left and right columns are read as single lines | Gutters are detected from the text's real extents and each column is read in turn |

The same page analysis also keeps label/value blocks (`Fund Manager  George
Thomas`) on one line — the built-in converter files every label away from its
value — and restores the blank line between paragraphs.

Set `PDF_ENGINE=markitdown` to switch back to the built-in converter. Nothing
else changes: every other format is handled by MarkItDown exactly as before,
and if the layout-aware converter ever raises, MarkItDown falls through to the
built-in one on its own.

`tests/test_pdf.py` builds a PDF for each defect with reportlab and asserts the
output is correct. Every one of those tests fails under `PDF_ENGINE=markitdown`,
so they are real regression tests rather than descriptions of current
behaviour.

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
  pdf.py            layout-aware PDF -> Markdown converter
  routes.py         page + JSON/file API endpoints
  templates/        Jinja templates
  static/           stylesheet and vanilla JS (no build step, no CDN)
tests/
  test_app.py       end-to-end tests for the routes and limits
  test_pdf.py       PDF layout regression tests
wsgi.py             gunicorn entrypoint
Dockerfile          image used by Render
render.yaml         Render Blueprint
```

## Notes and limits

- Scanned PDFs with no text layer produce empty Markdown; the UI says so
  explicitly rather than handing you a blank file. No OCR is performed.
- PDF layout analysis is geometric, not semantic. A table drawn with no ruling
  and no consistent column alignment can still come out as plain lines.
- Image OCR and LLM captioning are MarkItDown features that need an external
  model. They are not wired up here; images yield metadata only.
- Uploads are never written to a persistent location, but they do pass through
  the server's memory and a temporary directory. Do not deploy this publicly
  for confidential documents without adding authentication.

## Licence

This app is MIT licensed. MarkItDown itself is MIT licensed by Microsoft.
