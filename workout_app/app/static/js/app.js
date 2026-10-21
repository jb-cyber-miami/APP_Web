// FitLog front-end helpers. No inline scripts: the CSP only allows this file.
(function () {
  "use strict";

  // Confirm dangerous actions (delete, revoke, ...)
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
      return;
    }
    // Show a busy label on slow forms (AI builder) and prevent double submits.
    var busy = form.getAttribute("data-busy");
    var button = form.querySelector("button[type=submit]");
    if (button) {
      if (busy) button.textContent = busy;
      setTimeout(function () { button.disabled = true; }, 0);
    }
  });

  document.addEventListener("click", function (event) {
    var el = event.target.closest("[data-copy], [data-share-url], [data-use-device-tz]");
    if (!el) return;

    if (el.hasAttribute("data-copy")) {
      var input = document.querySelector(el.getAttribute("data-copy"));
      if (!input) return;
      copy(input.value, el);
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

  function copy(value, button) {
    var done = function () {
      var old = button.textContent;
      button.textContent = "Copied!";
      setTimeout(function () { button.textContent = old; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(done, function () {});
    }
  }
})();
