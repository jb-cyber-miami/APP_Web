// FitLog global front-end behaviours (docs/DESIGN.md section 7, BUILD_CONTRACT section 5).
// CSP: this file is loaded with <script src defer>; no inline handlers anywhere.
// Progressive enhancement only: every page works without this file.
// DOM updates use textContent / classList / setAttribute, never innerHTML.
(function () {
  "use strict";

  var root = document.documentElement;
  root.classList.add("js");

  // ------------------------------------------------------------------ toasts
  var TOAST_MS = 4000;

  function dismissToast(toast) {
    if (!toast || toast.classList.contains("is-leaving")) return;
    toast.classList.add("is-leaving");
    setTimeout(function () {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 350);
  }

  function armToast(toast) {
    var isError = toast.classList.contains("toast-error");
    if (!isError) setTimeout(function () { dismissToast(toast); }, TOAST_MS);
  }

  function svgIcon(paths, cls) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", cls || "icon icon-sm toast-icon");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    paths.forEach(function (d) {
      var p = document.createElementNS(ns, "path");
      p.setAttribute("d", d);
      svg.appendChild(p);
    });
    return svg;
  }

  var TOAST_ICONS = {
    success: ["M5 12.5 9.6 17 19 7.5"],
    error: ["M12 7.5v5.5", "M12 16.4h.01", "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"],
    warning: ["M12 7.5v5.5", "M12 16.4h.01", "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"],
    info: ["M12 11v5.5", "M12 7.8h.01", "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"],
  };

  // Public helper (used by workout.js): show a toast built from plain text.
  function toast(message, kind) {
    kind = TOAST_ICONS[kind] ? kind : "info";
    var box = document.querySelector(".toasts");
    if (!box) {
      box = document.createElement("div");
      box.className = "toasts";
      document.body.insertBefore(box, document.body.firstChild);
    }
    var el = document.createElement("div");
    el.className = "toast toast-" + kind;
    el.setAttribute("role", kind === "error" ? "alert" : "status");
    el.setAttribute("data-toast", "");
    el.appendChild(svgIcon(TOAST_ICONS[kind]));
    var text = document.createElement("p");
    text.className = "toast-text";
    text.textContent = String(message);
    el.appendChild(text);
    var close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("data-toast-close", "");
    close.setAttribute("aria-label", "Dismiss");
    close.appendChild(svgIcon(["M6 6l12 12", "M18 6 6 18"]));
    el.appendChild(close);
    box.appendChild(el);
    armToast(el);
    return el;
  }

  window.FitLog = window.FitLog || {};
  window.FitLog.toast = toast;

  // ------------------------------------------------------------------ submit: confirm + busy
  var SKIP_BUSY = "[data-set-form], [data-rate-form]";

  function labelTarget(button) {
    return button.querySelector("span:not(.visually-hidden)") || button;
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    var message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
      return;
    }
    if (event.defaultPrevented || form.matches(SKIP_BUSY)) return;

    // Busy label on slow forms (AI builder, coach) and no double submits.
    var submitter = event.submitter || form.querySelector("button[type=submit], button:not([type])");
    var busy = (submitter && submitter.getAttribute("data-busy")) || form.getAttribute("data-busy");
    if (submitter && busy) labelTarget(submitter).textContent = busy;
    if (busy) {
      form.classList.add("is-busy");
      form.setAttribute("aria-busy", "true");
    }
    var buttons = form.querySelectorAll("button[type=submit], button:not([type])");
    setTimeout(function () {
      buttons.forEach(function (b) {
        if (!b.disabled) {
          b.disabled = true;
          b.setAttribute("data-busy-disabled", "");
        }
      });
    }, 0);
  });

  // Back/forward cache restores a page with disabled buttons: re-enable them.
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    document.querySelectorAll("[data-busy-disabled]").forEach(function (b) {
      b.disabled = false;
      b.removeAttribute("data-busy-disabled");
    });
    document.querySelectorAll("form.is-busy").forEach(function (f) {
      f.classList.remove("is-busy");
      f.removeAttribute("aria-busy");
    });
  });

  // ------------------------------------------------------------------ clicks: copy, share, tz, toasts, sheets
  function flashLabel(button, text) {
    var target = labelTarget(button);
    if (target === button && button.querySelector("svg")) {
      // Icon-only button: announce via a toast instead of replacing the icon.
      toast(text, "success");
      return;
    }
    var old = target.textContent;
    target.textContent = text;
    setTimeout(function () { target.textContent = old; }, 1500);
  }

  function copy(value, button) {
    var done = function () { flashLabel(button, "Copied!"); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(done, function () { fallbackCopy(value) && done(); });
    } else if (fallbackCopy(value)) {
      done();
    }
  }

  function fallbackCopy(value) {
    var input = document.createElement("textarea");
    input.value = value;
    input.setAttribute("readonly", "");
    input.className = "visually-hidden";
    document.body.appendChild(input);
    input.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(input);
    return ok;
  }

  document.addEventListener("click", function (event) {
    var closeBtn = event.target.closest("[data-toast-close]");
    if (closeBtn) {
      dismissToast(closeBtn.closest(".toast"));
      return;
    }

    var sheetOpen = event.target.closest("[data-sheet-open]");
    if (sheetOpen) {
      var dialog = document.querySelector(sheetOpen.getAttribute("data-sheet-open"));
      if (dialog && typeof dialog.showModal === "function") {
        event.preventDefault();
        dialog.showModal();
      }
      return;
    }
    var sheetClose = event.target.closest("[data-sheet-close]");
    if (sheetClose) {
      var dlg = sheetClose.closest("dialog");
      if (dlg) dlg.close();
      return;
    }

    // Close open overflow menus when tapping elsewhere.
    document.querySelectorAll("details.menu[open]").forEach(function (menu) {
      if (!menu.contains(event.target)) menu.removeAttribute("open");
    });

    var el = event.target.closest("[data-copy], [data-share-url], [data-use-device-tz]");
    if (!el) return;

    if (el.hasAttribute("data-copy")) {
      var input = document.querySelector(el.getAttribute("data-copy"));
      if (input) copy(input.value, el);
    }

    if (el.hasAttribute("data-share-url")) {
      var url = el.getAttribute("data-share-url");
      var text = el.getAttribute("data-share-text") || "My workout";
      // Native share sheet on phones (Messages, WhatsApp, ...); copy as a fallback.
      if (navigator.share) {
        navigator.share({ title: text, text: text, url: url }).catch(function () {});
      } else {
        copy(url, el);
      }
    }

    if (el.hasAttribute("data-use-device-tz")) {
      var field = document.querySelector("[data-detect-tz]");
      try {
        if (field) field.value = Intl.DateTimeFormat().resolvedOptions().timeZone;
      } catch (e) { /* old browser: leave as is */ }
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    document.querySelectorAll("details.menu[open]").forEach(function (menu) {
      menu.removeAttribute("open");
      var summary = menu.querySelector("summary");
      if (summary) summary.focus();
    });
  });

  // ------------------------------------------------------------------ elapsed timers
  function pad(n) { return n < 10 ? "0" + n : String(n); }

  function formatElapsed(totalSeconds) {
    var s = Math.max(0, Math.floor(totalSeconds));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    return h > 0 ? h + ":" + pad(m) + ":" + pad(sec) : m + ":" + pad(sec);
  }
  window.FitLog.formatElapsed = formatElapsed;

  function startElapsed() {
    var nodes = Array.prototype.slice.call(document.querySelectorAll("[data-elapsed-since]"))
      .map(function (el) {
        var t = Date.parse(el.getAttribute("data-elapsed-since"));
        return isNaN(t) ? null : { el: el, start: t };
      })
      .filter(Boolean);
    if (!nodes.length) return;
    var tick = function () {
      var now = Date.now();
      nodes.forEach(function (n) { n.el.textContent = formatElapsed((now - n.start) / 1000); });
    };
    tick();
    setInterval(tick, 1000);
  }

  // ------------------------------------------------------------------ steppers
  function initSteppers() {
    document.querySelectorAll("[data-stepper]").forEach(function (box) {
      var input = box.querySelector("input");
      if (!input) return;
      box.querySelectorAll(".stepper-btn").forEach(function (btn) {
        btn.hidden = false;
        btn.addEventListener("click", function () {
          var step = parseFloat(btn.getAttribute("data-step")) || 1;
          var min = input.min !== "" ? parseFloat(input.min) : -Infinity;
          var max = input.max !== "" ? parseFloat(input.max) : Infinity;
          var cur = parseFloat(input.value);
          if (isNaN(cur)) cur = step > 0 ? min : max;
          if (!isFinite(cur)) cur = 0;
          var next = Math.min(max, Math.max(min, cur + step));
          input.value = String(next);
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
        });
      });
    });
  }

  // ------------------------------------------------------------------ list filter
  function initFilters() {
    document.querySelectorAll("[data-filter]").forEach(function (input) {
      var list = document.querySelector(input.getAttribute("data-filter"));
      if (!list) return;
      var wrap = input.closest("[data-search]");
      if (wrap) wrap.hidden = false;
      var empty = list.querySelector("[data-filter-empty]");
      var rows = list.querySelectorAll("[data-name]");
      var groups = list.querySelectorAll("[data-filter-group]");
      var apply = function () {
        var q = input.value.trim().toLowerCase();
        var shown = 0;
        rows.forEach(function (row) {
          var hit = !q || row.getAttribute("data-name").indexOf(q) !== -1;
          row.hidden = !hit;
          if (hit) shown += 1;
        });
        groups.forEach(function (g) {
          g.hidden = !g.querySelector("[data-name]:not([hidden])");
        });
        if (empty) empty.hidden = shown !== 0 || rows.length === 0;
      };
      input.addEventListener("input", apply);
      if (input.value) apply();
    });
  }

  // ------------------------------------------------------------------ "Save N to my library" counter
  function initSelectCount() {
    document.querySelectorAll("[data-select-count]").forEach(function (form) {
      var out = form.querySelector("[data-select-count-value]");
      var submit = form.querySelector("button[type=submit]");
      var update = function () {
        var n = form.querySelectorAll("input[name=selected]:checked").length;
        if (out) out.textContent = String(n);
        if (submit) submit.disabled = n === 0;
      };
      form.addEventListener("change", update);
      update();
    });
  }

  // ------------------------------------------------------------------ thumbnails
  // A YouTube thumbnail that fails (offline gym Wi-Fi, removed video) becomes the same empty
  // tile the server renders without a video. No inline onerror: the CSP forbids it.
  var THUMB_ICON = ["M6.5 7v10M17.5 7v10M3.5 9.5v5M20.5 9.5v5M6.5 12h11"]; // _icons.html "library"

  function initThumbs() {
    document.querySelectorAll("img.ex-thumb").forEach(function (img) {
      function swap() {
        var tile = document.createElement("span");
        tile.className = "ex-thumb ex-thumb-empty";
        tile.setAttribute("aria-hidden", "true");
        tile.appendChild(svgIcon(THUMB_ICON, "icon icon-sm"));
        if (img.parentNode) img.parentNode.replaceChild(tile, img);
      }
      if (img.complete && img.naturalWidth === 0) {
        swap();
      } else {
        img.addEventListener("error", swap, { once: true });
      }
    });
  }

  // ------------------------------------------------------------------ boot
  function boot() {
    initThumbs();
    document.querySelectorAll(".toast").forEach(armToast);
    startElapsed();
    initSteppers();
    initFilters();
    initSelectCount();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
