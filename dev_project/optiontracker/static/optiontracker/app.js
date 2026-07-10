// OptionTracker UI behavior: theme toggle, row expand/collapse, summary
// collapse, dropdown menus, share-to-clipboard. Presentation only.
(function () {
  "use strict";

  function setTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("theme", theme);
    } catch (e) {
      /* private mode */
    }
    document.cookie = "theme=" + theme + ";path=/;max-age=31536000;samesite=lax";
  }

  // Apply persisted theme early (cookie already handled server-side; localStorage wins).
  try {
    var stored = localStorage.getItem("theme");
    if (stored) document.documentElement.setAttribute("data-theme", stored);
  } catch (e) {
    /* ignore */
  }

  document.addEventListener("click", function (event) {
    var toggle = event.target.closest("#theme-toggle");
    if (toggle) {
      var current = document.documentElement.getAttribute("data-theme") || "dark";
      setTheme(current === "dark" ? "light" : "dark");
      return;
    }

    var summaryToggle = event.target.closest(".summary-toggle, .summary-head");
    if (summaryToggle) {
      var card = event.target.closest(".summary-card");
      if (card) card.classList.toggle("collapsed");
      return;
    }

    var shareButton = event.target.closest(".share-btn");
    if (shareButton) {
      event.stopPropagation();
      var text = shareButton.getAttribute("data-share") || "";
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          shareButton.classList.add("copied");
          setTimeout(function () { shareButton.classList.remove("copied"); }, 1200);
        });
      }
      return;
    }

    // In-page dialogs (roll history, add wheel position): any button
    // carrying data-dialog opens the named <dialog>; ✕ or backdrop closes.
    var dialogButton = event.target.closest("[data-dialog]");
    if (dialogButton) {
      event.stopPropagation();
      var dialog = document.getElementById(dialogButton.getAttribute("data-dialog"));
      if (dialog && dialog.showModal) dialog.showModal();
      return;
    }
    var dialogClose = event.target.closest(".dialog-close");
    if (dialogClose) {
      var openDialog = dialogClose.closest("dialog");
      if (openDialog) openDialog.close();
      return;
    }
    if (event.target.tagName === "DIALOG") {
      // click landed on the backdrop (outside the dialog's content box)
      var rect = event.target.getBoundingClientRect();
      var inside =
        event.clientX >= rect.left && event.clientX <= rect.right &&
        event.clientY >= rect.top && event.clientY <= rect.bottom;
      if (!inside) event.target.close();
      return;
    }

    var dropdownToggle = event.target.closest(".dropdown-toggle");
    if (dropdownToggle) {
      var menu = document.getElementById(dropdownToggle.getAttribute("data-dropdown"));
      if (menu) menu.hidden = !menu.hidden;
      return;
    }

    // Click outside closes any open dropdown (clicks inside stay open).
    if (!event.target.closest(".dropdown-menu")) {
      document.querySelectorAll(".dropdown-menu:not([hidden])").forEach(function (menu) {
        menu.hidden = true;
      });
    }

    // Row expand/collapse: any click on an expandable row that is not a link/button.
    var row = event.target.closest("tr.expandable");
    if (row && !event.target.closest("a, button, input, label")) {
      var detail = row.nextElementSibling;
      if (detail && detail.classList.contains("detail-row")) {
        detail.hidden = !detail.hidden;
        row.classList.toggle("open", !detail.hidden);
      }
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Enter") return;
    var row = event.target.closest && event.target.closest("tr.expandable");
    if (row) {
      var detail = row.nextElementSibling;
      if (detail && detail.classList.contains("detail-row")) {
        detail.hidden = !detail.hidden;
        row.classList.toggle("open", !detail.hidden);
      }
    }
  });
})();
