/* MarkItDown web converter — upload queue, conversion, downloads. */
(function () {
  "use strict";

  var limits = window.APP_LIMITS || {
    max_files: 10,
    max_upload_mb: 25,
    allowed_extensions: [],
    allow_any_extension: false,
  };

  var form = document.getElementById("upload-form");
  var dropzone = document.getElementById("dropzone");
  var fileInput = document.getElementById("file-input");
  var queueEl = document.getElementById("queue");
  var resultsEl = document.getElementById("results");
  var convertBtn = document.getElementById("convert-btn");
  var clearBtn = document.getElementById("clear-btn");
  var errorEl = document.getElementById("form-error");

  var queue = [];
  var lastResults = [];
  var busy = false;

  /* ---------- helpers ---------- */

  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function extensionOf(name) {
    var dot = name.lastIndexOf(".");
    return dot === -1 ? "" : name.slice(dot).toLowerCase();
  }

  function showError(message) {
    if (!message) {
      errorEl.hidden = true;
      errorEl.textContent = "";
      return;
    }
    errorEl.textContent = message;
    errorEl.hidden = false;
  }

  function keyFor(file) {
    return file.name + "::" + file.size + "::" + file.lastModified;
  }

  function saveBlob(blob, filename) {
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 1000);
  }

  /* ---------- queue ---------- */

  function addFiles(fileList) {
    var incoming = Array.prototype.slice.call(fileList || []);
    if (!incoming.length) return;

    var problems = [];
    var maxBytes = limits.max_upload_mb * 1024 * 1024;

    incoming.forEach(function (file) {
      if (queue.some(function (queued) { return keyFor(queued) === keyFor(file); })) {
        return;
      }
      if (queue.length >= limits.max_files) {
        problems.push("Only " + limits.max_files + " files can be converted at a time.");
        return;
      }
      if (file.size > maxBytes) {
        problems.push(file.name + " is larger than " + limits.max_upload_mb + " MB.");
        return;
      }
      if (
        !limits.allow_any_extension &&
        limits.allowed_extensions.length &&
        limits.allowed_extensions.indexOf(extensionOf(file.name)) === -1
      ) {
        problems.push(file.name + " is not a supported file type.");
        return;
      }
      queue.push(file);
    });

    showError(problems.length ? problems[0] : "");
    renderQueue();
  }

  function removeFile(index) {
    queue.splice(index, 1);
    showError("");
    renderQueue();
  }

  function renderQueue() {
    queueEl.textContent = "";

    queue.forEach(function (file, index) {
      var item = document.createElement("li");
      item.className = "queue-item";

      var name = document.createElement("span");
      name.className = "queue-name";
      name.textContent = file.name;

      var size = document.createElement("span");
      size.className = "queue-size";
      size.textContent = formatBytes(file.size);

      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "queue-remove";
      remove.setAttribute("aria-label", "Remove " + file.name);
      remove.textContent = "×";
      remove.addEventListener("click", function () {
        removeFile(index);
      });

      item.appendChild(name);
      item.appendChild(size);
      item.appendChild(remove);
      queueEl.appendChild(item);
    });

    convertBtn.disabled = busy || queue.length === 0;
    clearBtn.hidden = queue.length === 0 && lastResults.length === 0;
  }

  function setBusy(state) {
    busy = state;
    convertBtn.disabled = state || queue.length === 0;
    convertBtn.textContent = "";
    if (state) {
      var spinner = document.createElement("span");
      spinner.className = "spinner";
      convertBtn.appendChild(spinner);
      convertBtn.appendChild(document.createTextNode("Converting…"));
    } else {
      convertBtn.textContent = "Convert to Markdown";
    }
  }

  /* ---------- results ---------- */

  function buildSummary(summary) {
    var wrap = document.createElement("div");
    wrap.className = "results-summary";

    var text = document.createElement("span");
    var parts = [summary.succeeded + " converted"];
    if (summary.failed) parts.push(summary.failed + " failed");
    text.textContent = parts.join(" · ");
    wrap.appendChild(text);

    var successes = lastResults.filter(function (r) { return r.ok; });
    if (successes.length > 1) {
      var zipBtn = document.createElement("button");
      zipBtn.type = "button";
      zipBtn.className = "button button-small";
      zipBtn.textContent = "Download all (.zip)";
      zipBtn.addEventListener("click", function () {
        downloadZip(zipBtn, successes);
      });
      wrap.appendChild(zipBtn);
    }

    return wrap;
  }

  function buildCard(result) {
    var card = document.createElement("article");
    card.className = "result-card" + (result.ok ? "" : " is-error");

    var head = document.createElement("div");
    head.className = "result-head";

    var dot = document.createElement("span");
    dot.className = "status-dot" + (result.ok ? "" : " is-error");
    head.appendChild(dot);

    var title = document.createElement("div");
    title.className = "result-title";

    var name = document.createElement("span");
    name.className = "result-name";
    name.textContent = result.source_filename;
    title.appendChild(name);

    var meta = document.createElement("span");
    meta.className = "result-meta";
    meta.textContent = result.ok
      ? result.markdown_filename +
        " · " +
        formatBytes(result.bytes_out) +
        " · " +
        result.duration_ms +
        " ms"
      : "Not converted";
    title.appendChild(meta);
    head.appendChild(title);

    if (result.ok) {
      var actions = document.createElement("div");
      actions.className = "result-actions";

      var copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.className = "button button-small";
      copyBtn.textContent = "Copy";
      copyBtn.addEventListener("click", function () {
        copyMarkdown(copyBtn, result.markdown);
      });
      actions.appendChild(copyBtn);

      var downloadBtn = document.createElement("button");
      downloadBtn.type = "button";
      downloadBtn.className = "button button-small button-primary";
      downloadBtn.textContent = "Download .md";
      downloadBtn.addEventListener("click", function () {
        saveBlob(
          new Blob([result.markdown], { type: "text/markdown;charset=utf-8" }),
          result.markdown_filename
        );
      });
      actions.appendChild(downloadBtn);

      head.appendChild(actions);
    }

    card.appendChild(head);

    if (!result.ok) {
      var error = document.createElement("p");
      error.className = "result-error";
      error.textContent = result.error;
      card.appendChild(error);
      return card;
    }

    (result.warnings || []).forEach(function (message) {
      var warning = document.createElement("p");
      warning.className = "result-warning";
      warning.textContent = message;
      card.appendChild(warning);
    });

    if (result.markdown) {
      var preview = document.createElement("pre");
      preview.className = "result-preview";
      var text = result.markdown;
      var truncated = text.length > 20000;
      preview.textContent = truncated
        ? text.slice(0, 20000) + "\n\n… preview truncated — download the file for the rest."
        : text;
      card.appendChild(preview);
    }

    return card;
  }

  function renderResults(payload) {
    resultsEl.textContent = "";
    lastResults = payload.results || [];
    if (!lastResults.length) return;
    resultsEl.appendChild(buildSummary(payload.summary));
    lastResults.forEach(function (result) {
      resultsEl.appendChild(buildCard(result));
    });
  }

  function copyMarkdown(button, markdown) {
    var original = button.textContent;
    var done = function (label) {
      button.textContent = label;
      setTimeout(function () {
        button.textContent = original;
      }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(markdown).then(
        function () { done("Copied"); },
        function () { done("Copy failed"); }
      );
    } else {
      done("Copy failed");
    }
  }

  function downloadZip(button, successes) {
    var original = button.textContent;
    button.disabled = true;
    button.textContent = "Zipping…";

    fetch("/api/bundle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        files: successes.map(function (r) {
          return { filename: r.markdown_filename, markdown: r.markdown };
        }),
      }),
    })
      .then(function (response) {
        if (!response.ok) throw new Error("Bundle failed");
        return response.blob();
      })
      .then(function (blob) {
        saveBlob(blob, "markdown.zip");
      })
      .catch(function () {
        showError("Could not build the zip. Download the files individually instead.");
      })
      .finally(function () {
        button.disabled = false;
        button.textContent = original;
      });
  }

  /* ---------- events ---------- */

  fileInput.addEventListener("change", function () {
    addFiles(fileInput.files);
    fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach(function (type) {
    dropzone.addEventListener(type, function (event) {
      event.preventDefault();
      dropzone.classList.add("is-dragging");
    });
  });

  ["dragleave", "drop"].forEach(function (type) {
    dropzone.addEventListener(type, function (event) {
      event.preventDefault();
      if (type === "dragleave" && dropzone.contains(event.relatedTarget)) return;
      dropzone.classList.remove("is-dragging");
    });
  });

  dropzone.addEventListener("drop", function (event) {
    if (event.dataTransfer && event.dataTransfer.files) {
      addFiles(event.dataTransfer.files);
    }
  });

  dropzone.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });

  // The whole page accepts a drop, so a near-miss doesn't navigate away.
  window.addEventListener("dragover", function (event) {
    event.preventDefault();
  });
  window.addEventListener("drop", function (event) {
    event.preventDefault();
  });

  clearBtn.addEventListener("click", function () {
    queue = [];
    lastResults = [];
    resultsEl.textContent = "";
    showError("");
    renderQueue();
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (busy || !queue.length) return;

    showError("");
    setBusy(true);

    var data = new FormData();
    queue.forEach(function (file) {
      data.append("files", file, file.name);
    });

    fetch("/api/convert", { method: "POST", body: data })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            throw new Error(
              response.status === 413
                ? "Upload is too large. The limit is " + limits.max_upload_mb + " MB."
                : "The server returned an unexpected response."
            );
          })
          .then(function (payload) {
            if (!response.ok) {
              throw new Error(payload.error || "Conversion failed.");
            }
            return payload;
          });
      })
      .then(function (payload) {
        renderResults(payload);
        renderQueue();
      })
      .catch(function (err) {
        showError(err.message || "Conversion failed.");
      })
      .finally(function () {
        setBusy(false);
        renderQueue();
      });
  });

  renderQueue();
})();
