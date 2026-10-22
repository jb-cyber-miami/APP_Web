// FitLog active workout (session.html in focus mode only). BUILD_CONTRACT section 5.
// - form[data-set-form]: check / un-check a set with fetch; falls back to a normal POST.
// - form[data-rate-form]: Easy / Good / Hard saved on change.
// - [data-rest-timer]: rest countdown started when a set is checked.
// Server JSON is only ever written with textContent / setAttribute / classList.
(function () {
  "use strict";

  var toast = (window.FitLog && window.FitLog.toast) || function () {};
  var RING_C = 125.66; // 2 * PI * r (r = 20) in session.html
  var STORE_KEY = "fitlog-rest:" + location.pathname;

  // ------------------------------------------------------------------ fetch helper
  function post(form) {
    var data = new FormData(form);
    var token = form.querySelector("input[name=csrf_token]");
    var headers = { "X-Requested-With": "fetch", Accept: "application/json" };
    if (token) headers["X-CSRFToken"] = token.value;
    return fetch(form.action, {
      method: "POST",
      body: data,
      headers: headers,
      credentials: "same-origin",
      redirect: "manual",
    }).then(function (res) {
      var type = res.headers.get("Content-Type") || "";
      if (type.indexOf("application/json") === -1) throw new Error("not-json");
      return res.json().then(function (json) {
        if (!json || typeof json !== "object") throw new Error("bad-json");
        return json;
      });
    });
  }

  function sameOrigin(url) {
    try {
      return new URL(url, location.href).origin === location.origin;
    } catch (e) {
      return false;
    }
  }

  // ------------------------------------------------------------------ progress + current markers
  function updateProgress(json) {
    // The header counts SETS (2/18), so it moves on every check-off.
    var doneN = typeof json.sets_done === "number" ? json.sets_done : null;
    var totalN = typeof json.sets_total === "number" ? json.sets_total : null;
    var done = document.querySelector("[data-progress-done]");
    var total = document.querySelector("[data-progress-total]");
    if (done && doneN !== null) done.textContent = String(doneN);
    if (total && totalN !== null) total.textContent = String(totalN);
    var bar = document.querySelector("[data-progress-bar]");
    if (bar && doneN !== null && totalN > 0) {
      var pct = Math.max(0, Math.min(100, Math.round((doneN * 100) / totalN)));
      bar.setAttribute("width", String(pct));
    }
    var finish = document.querySelector("form[data-finish]");
    if (finish && typeof json.unlogged_sets === "number") {
      var n = json.unlogged_sets;
      if (n > 0) {
        finish.setAttribute("data-confirm", n + " set" + (n === 1 ? "" : "s") + " not logged. Finish anyway?");
      } else {
        finish.removeAttribute("data-confirm");
      }
    }
  }

  function updateCard(card, json) {
    if (!card) return;
    card.querySelectorAll("[data-entry-count]").forEach(function (count) {
      count.textContent = json.entry_done + "/" + json.entry_planned;
    });
    card.classList.toggle("is-complete", json.entry_done >= json.entry_planned);
    card.querySelectorAll("[data-swap]").forEach(function (swap) {
      swap.hidden = json.entry_done > 0;
    });
  }

  function refreshCurrent() {
    var cards = Array.prototype.slice.call(document.querySelectorAll(".ex-card[data-tracked='true']"));
    var currentCard = null;
    cards.forEach(function (card) {
      var rows = card.querySelectorAll("[data-set-row]");
      var firstOpen = null;
      rows.forEach(function (row) {
        row.classList.remove("is-current");
        if (!firstOpen && !row.classList.contains("is-done")) firstOpen = row;
      });
      if (!currentCard && firstOpen) {
        currentCard = card;
        firstOpen.classList.add("is-current");
      }
      card.classList.remove("is-current");
    });
    if (currentCard) currentCard.classList.add("is-current");
    return currentCard;
  }

  function nextSetLabel() {
    var row = document.querySelector("[data-set-row].is-current");
    if (!row) return "";
    var btn = row.querySelector("[data-set-number]");
    var card = row.closest(".ex-card");
    var name = card ? card.querySelector(".ex-name") : null;
    var label = btn ? "Set " + btn.getAttribute("data-set-number") : "Next set";
    return name ? label + " · " + name.textContent.trim() : label;
  }

  // Non-current cards are collapsed (<details data-ex-body>). When an exercise is finished,
  // open the next one and bring it into view.
  function advanceTo(card, fromCard) {
    if (!card || card === fromCard) return;
    var body = card.querySelector("details[data-ex-body]");
    if (body) body.open = true;
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    card.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
  }

  // ------------------------------------------------------------------ set check-off
  function applySetState(form, json) {
    var row = form.closest("[data-set-row]");
    var check = form.querySelector(".set-check");
    if (row) row.classList.toggle("is-done", !!json.is_done);
    if (check) check.setAttribute("aria-pressed", json.is_done ? "true" : "false");
    if (json.action && sameOrigin(json.action)) form.setAttribute("action", json.action);

    var rate = row ? row.querySelector("form[data-rate-form]") : null;
    if (rate) {
      if (json.is_done && json.rate_action && sameOrigin(json.rate_action)) {
        rate.setAttribute("action", json.rate_action);
        rate.hidden = false;
      } else {
        rate.hidden = true;
        rate.setAttribute("action", "");
        rate.querySelectorAll("input[type=radio]").forEach(function (r) { r.checked = false; });
      }
    }
    updateCard(form.closest(".ex-card"), json);
    updateProgress(json);
    refreshCurrent();
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) return;

    if (form.hasAttribute("data-set-form")) {
      event.preventDefault();
      if (form.getAttribute("data-pending")) return;
      var row = form.closest("[data-set-row]");
      var wasDone = row && row.classList.contains("is-done");
      form.setAttribute("data-pending", "1");
      if (row) row.classList.add("is-pending");
      post(form)
        .then(function (json) {
          if (json.ok) {
            applySetState(form, json);
            if (json.is_done && !wasDone && json.entry_done >= json.entry_planned) {
              advanceTo(document.querySelector(".ex-card.is-current"), form.closest(".ex-card"));
            }
            if (json.is_done && !wasDone) {
              if (json.unlogged_sets === 0) {
                timer.stop();
                toast("All sets done. Tap Finish when you're ready.", "success");
              } else if (json.rest_seconds > 0) {
                timer.start(json.rest_seconds, nextSetLabel());
              }
            }
          } else {
            toast(json.error || "That set couldn't be saved.", "error");
          }
        })
        .catch(function () {
          // Network error, session expired, CSRF failure...: fall back to a normal POST.
          form.submit();
        })
        .then(function () {
          form.removeAttribute("data-pending");
          if (row) row.classList.remove("is-pending");
        });
      return;
    }

    if (form.hasAttribute("data-rate-form")) {
      event.preventDefault();
      if (!form.getAttribute("action")) return;
      post(form)
        .then(function (json) {
          if (!json.ok) toast(json.error || "Rating not saved.", "error");
        })
        .catch(function () { form.submit(); });
    }
  });

  document.addEventListener("change", function (event) {
    var input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== "radio") return;
    var form = input.closest("form[data-rate-form]");
    if (!form || !form.getAttribute("action")) return;
    if (typeof form.requestSubmit === "function") {
      form.requestSubmit();
    } else {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    }
  });

  // ------------------------------------------------------------------ rest timer
  var timer = (function () {
    var el = document.querySelector("[data-rest-timer]");
    var timeEl = el && el.querySelector("[data-rest-time]");
    var labelEl = el && el.querySelector("[data-rest-label]");
    var ringEl = el && el.querySelector("[data-rest-ring]");
    var liveEl = el && el.querySelector("[data-rest-live]");
    var endAt = 0;
    var total = 0;
    var next = "";
    var interval = null;
    var hideTimeout = null;
    var announced = {};

    function fmt(s) {
      var m = Math.floor(s / 60);
      var sec = s % 60;
      return m + ":" + (sec < 10 ? "0" : "") + sec;
    }

    function save() {
      try {
        if (endAt) {
          sessionStorage.setItem(STORE_KEY, JSON.stringify({ endAt: endAt, total: total, next: next }));
        } else {
          sessionStorage.removeItem(STORE_KEY);
        }
      } catch (e) { /* storage disabled: timer still works for this page */ }
    }

    function announce(text) {
      if (liveEl) liveEl.textContent = text;
    }

    function render() {
      if (!el) return;
      var remaining = Math.max(0, Math.ceil((endAt - Date.now()) / 1000));
      if (timeEl) timeEl.textContent = fmt(remaining);
      if (ringEl && total > 0) {
        var offset = RING_C * (1 - remaining / total);
        ringEl.setAttribute("stroke-dashoffset", offset.toFixed(2));
      }
      if ((remaining === 30 || remaining === 10) && !announced[remaining]) {
        announced[remaining] = true;
        announce(remaining + " seconds of rest left");
      }
      if (remaining <= 0) finish();
    }

    function finish() {
      clearInterval(interval);
      interval = null;
      endAt = 0;
      save();
      el.classList.add("is-done");
      if (labelEl) labelEl.textContent = "Go";
      if (timeEl) timeEl.textContent = next || "Next set";
      if (ringEl) ringEl.setAttribute("stroke-dashoffset", "0");
      announce("Rest over. " + (next || "Next set") + ".");
      if (navigator.vibrate) {
        try { navigator.vibrate(200); } catch (e) { /* not allowed */ }
      }
      clearTimeout(hideTimeout);
      hideTimeout = setTimeout(stop, 3000);
    }

    function show() {
      el.hidden = false;
      el.classList.remove("is-done");
      document.body.classList.add("has-timer");
      if (labelEl) labelEl.textContent = "Rest";
    }

    function run() {
      clearInterval(interval);
      clearTimeout(hideTimeout);
      show();
      render();
      interval = setInterval(render, 250);
    }

    function start(seconds, nextLabel) {
      if (!el) return;
      seconds = Math.max(1, Math.min(600, Math.round(Number(seconds) || 0)));
      total = seconds;
      endAt = Date.now() + seconds * 1000;
      next = nextLabel || "";
      announced = {};
      save();
      run();
    }

    function adjust(delta) {
      if (!el || !endAt) return;
      var remaining = Math.max(0, (endAt - Date.now()) / 1000);
      var updated = Math.max(1, Math.min(600, remaining + delta));
      endAt = Date.now() + updated * 1000;
      total = Math.max(total, Math.ceil(updated));
      announced = {};
      save();
      render();
    }

    function stop() {
      clearInterval(interval);
      clearTimeout(hideTimeout);
      interval = null;
      endAt = 0;
      save();
      if (!el) return;
      el.hidden = true;
      el.classList.remove("is-done");
      document.body.classList.remove("has-timer");
    }

    function restore() {
      if (!el) return;
      var state = null;
      try { state = JSON.parse(sessionStorage.getItem(STORE_KEY) || "null"); } catch (e) { state = null; }
      if (!state || typeof state.endAt !== "number" || state.endAt <= Date.now()) {
        try { sessionStorage.removeItem(STORE_KEY); } catch (e) { /* ignore */ }
        return;
      }
      endAt = state.endAt;
      total = Math.max(1, Math.min(600, Number(state.total) || 60));
      next = typeof state.next === "string" ? state.next.slice(0, 120) : "";
      run();
    }

    if (el) {
      el.addEventListener("click", function (event) {
        var adj = event.target.closest("[data-rest-adjust]");
        if (adj) {
          adjust(parseInt(adj.getAttribute("data-rest-adjust"), 10) || 0);
          return;
        }
        if (event.target.closest("[data-rest-skip]")) stop();
      });
    }

    return { start: start, stop: stop, restore: restore };
  })();

  function boot() {
    refreshCurrent();
    timer.restore();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
