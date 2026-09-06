(function () {
  var card = document.querySelector("[data-progress-url]");
  if (!card) return;
  var url = card.getAttribute("data-progress-url");
  var elapsedNode = card.querySelector("[data-elapsed-label]");
  var logNode = document.querySelector(".job-log");
  var seen = Number((logNode && logNode.getAttribute("data-log-count")) || "0");
  var elapsedBase = Number(card.getAttribute("data-elapsed") || "0");
  var marked = Date.now();

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function formatElapsed(seconds) {
    seconds = Math.max(0, Math.floor(seconds));
    return pad(Math.floor(seconds / 60)) + ":" + pad(seconds % 60);
  }

  function paintElapsed() {
    if (!elapsedNode) return;
    var seconds = elapsedBase + (Date.now() - marked) / 1000;
    elapsedNode.textContent = "已用 " + formatElapsed(seconds);
  }

  function apply(data) {
    elapsedBase = Number(data.elapsed || 0);
    marked = Date.now();
    (data.phases || []).forEach(function (phase) {
      var row = card.querySelector('[data-phase="' + phase.key + '"]');
      if (!row) return;
      row.setAttribute("data-state", phase.state);
      var note = row.querySelector(".progress-note");
      if (note) note.textContent = phase.note || "";
      var fill = row.querySelector(".progress-fill");
      if (fill) {
        if (phase.total > 0) {
          fill.classList.remove("is-indeterminate");
          fill.style.width = Math.min(100, (100 * phase.done) / phase.total) + "%";
        } else {
          fill.classList.add("is-indeterminate");
          fill.style.width = "";
        }
      }
      var fraction = row.querySelector(".progress-fraction");
      if (fraction) {
        fraction.textContent = phase.total > 0 ? phase.done + "/" + phase.total : "";
      }
    });
    if (logNode && data.lines && data.lines.length > seen) {
      var added = data.lines.slice(seen).join("\n");
      logNode.appendChild(
        document.createTextNode((logNode.textContent ? "\n" : "") + added)
      );
      logNode.scrollTop = logNode.scrollHeight;
      seen = data.lines.length;
    }
    paintElapsed();
  }

  var poll = setInterval(function () {
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        apply(data);
        if (data.finished) {
          clearInterval(poll);
          clearInterval(tick);
          location.reload();
        }
      })
      .catch(function () {});
  }, 1000);
  var tick = setInterval(paintElapsed, 1000);
})();
