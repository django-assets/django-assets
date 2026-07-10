// OptionTracker UI behavior: theme toggle, row expand/collapse, summary
// collapse, dropdown menus, share-card image download, one-step tutorial
// tour. Presentation only — every figure shown is copied verbatim from
// server-rendered markup.
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

    var tutorialButton = event.target.closest(".tutorial-btn");
    if (tutorialButton) {
      startTour();
      return;
    }

    var summaryToggle = event.target.closest(".summary-toggle, .summary-head");
    if (summaryToggle) {
      var card = event.target.closest(".summary-card");
      if (card) card.classList.toggle("collapsed");
      return;
    }

    // Share Position dialog: "Download Image" renders the server-built
    // card to a PNG (SVG replica -> Image -> canvas). All strings/colors
    // are read from the rendered card; no values are computed here.
    var downloadButton = event.target.closest(".share-download");
    if (downloadButton) {
      event.stopPropagation();
      downloadShareCard(downloadButton);
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

    // Account menu: choosing an entry just closes the menu (a single
    // demo account is always selected in this environment).
    var accountItem = event.target.closest(".account-menu-item");
    if (accountItem) {
      var accountMenu = accountItem.closest(".dropdown-menu");
      if (accountMenu) accountMenu.hidden = true;
      return;
    }

    // Date Range menu: the Apply button (custom range) closes the menu;
    // htmx handles the request itself.
    var applyButton = event.target.closest(".range-apply");
    if (applyButton) {
      var applyMenu = applyButton.closest(".dropdown-menu");
      if (applyMenu) applyMenu.hidden = true;
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

    // Row expand/collapse: any click on an expandable row that is not a
    // link/button (or inside an open per-row dialog).
    var row = event.target.closest("tr.expandable");
    if (row && !event.target.closest("a, button, input, label, dialog")) {
      var detail = row.nextElementSibling;
      if (detail && detail.classList.contains("detail-row")) {
        detail.hidden = !detail.hidden;
        row.classList.toggle("open", !detail.hidden);
      }
    }
  });

  // Date Range menu is single-select: picking a window closes the menu;
  // picking "Custom Date Range" reveals the date inputs + Apply instead.
  document.addEventListener("change", function (event) {
    var target = event.target;
    if (!target.matches || !target.matches('.range-menu input[name="range"]')) return;
    var menu = target.closest(".range-menu");
    var custom = menu.querySelector(".range-custom");
    if (target.value === "custom") {
      if (custom) custom.hidden = false;
    } else {
      if (custom) custom.hidden = true;
      menu.hidden = true;
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

  // ---- Share Position: render the card fragment to a PNG ----
  // Draws an SVG replica of the server-rendered card (text content and
  // computed colors are copied from the DOM), loads it into an Image via
  // a Blob URL, paints it on a canvas and triggers an <a download> click.

  function esc(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function cardText(card, selector) {
    var node = card.querySelector(selector);
    return node ? node.textContent.replace(/\s+/g, " ").trim() : "";
  }

  function buildCardSvg(card) {
    var width = 560;
    var height = 464;
    var font = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";
    var muted = "#8b93a1";
    var text = "#e7eaf0";
    var pnlNode = card.querySelector(".share-pnl-value");
    var pnlColor = pnlNode ? getComputedStyle(pnlNode).color : text;
    var statLabels = [];
    card.querySelectorAll(".share-stat-label").forEach(function (label) {
      statLabels.push(label.textContent.replace(/\s+/g, " ").trim());
    });
    var cells = [];
    card.querySelectorAll(".share-cell").forEach(function (cell) {
      cells.push({
        label: cardText(cell, ".share-k").toUpperCase(),
        value: cardText(cell, ".share-v"),
      });
    });
    var parts = [
      '<svg xmlns="http://www.w3.org/2000/svg" width="' + width + '" height="' + height + '" viewBox="0 0 ' + width + " " + height + '">',
      '<rect x="0" y="0" width="' + width + '" height="' + height + '" rx="18" fill="#14171c" stroke="#262b33"/>',
      '<text x="36" y="70" font-family="' + font + '" font-size="32" font-weight="700" fill="' + text + '">' + esc(cardText(card, ".share-symbol")) + "</text>",
      '<text x="36" y="102" font-family="' + font + '" font-size="16" font-weight="600" fill="' + text + '">' + esc(cardText(card, ".share-strategy")) + "</text>",
      '<text x="36" y="128" font-family="' + font + '" font-size="14" fill="' + muted + '">' + esc(cardText(card, ".share-terms")) + "</text>",
      // Current PnL stat box
      '<rect x="36" y="150" width="236" height="96" rx="12" fill="#1a1e25" stroke="#262b33"/>',
      '<path d="M52 182l7-7 5 5 9-9" fill="none" stroke="' + pnlColor + '" stroke-width="2" stroke-linecap="round"/>',
      '<text x="80" y="184" font-family="' + font + '" font-size="12" font-weight="600" fill="' + muted + '">' + esc(statLabels[0] || "") + "</text>",
      '<text x="52" y="220" font-family="' + font + '" font-size="19" font-weight="700" fill="' + pnlColor + '">' + esc(cardText(card, ".share-pnl-value")) + "</text>",
      // AROI stat box
      '<rect x="288" y="150" width="236" height="96" rx="12" fill="#1a1e25" stroke="#262b33"/>',
      '<circle cx="311" cy="178" r="7" fill="none" stroke="#22c55e" stroke-width="1.6"/>',
      '<circle cx="311" cy="178" r="2.6" fill="none" stroke="#22c55e" stroke-width="1.6"/>',
      '<text x="326" y="184" font-family="' + font + '" font-size="12" font-weight="600" fill="' + muted + '">' + esc(statLabels[1] || "") + "</text>",
      '<text x="304" y="216" font-family="' + font + '" font-size="19" font-weight="700" fill="#22c55e">' + esc(cardText(card, ".share-aroi-value")) + "</text>",
      '<text x="304" y="236" font-family="' + font + '" font-size="10.5" fill="' + muted + '">Annualized Return</text>',
    ];
    cells.forEach(function (cell, index) {
      var x = index % 2 === 0 ? 36 : 288;
      var y = index < 2 ? 282 : 338;
      parts.push('<text x="' + x + '" y="' + y + '" font-family="' + font + '" font-size="10.5" font-weight="600" letter-spacing="1.2" fill="' + muted + '">' + esc(cell.label) + "</text>");
      parts.push('<text x="' + x + '" y="' + (y + 22) + '" font-family="' + font + '" font-size="15" font-weight="600" fill="' + text + '">' + esc(cell.value) + "</text>");
    });
    parts.push('<line x1="36" y1="396" x2="524" y2="396" stroke="#262b33"/>');
    parts.push('<text x="280" y="428" text-anchor="middle" font-family="' + font + '" font-size="11" letter-spacing="3" fill="' + muted + '">' + esc(cardText(card, ".share-foot").toUpperCase()) + "</text>");
    parts.push("</svg>");
    return { svg: parts.join(""), width: width, height: height };
  }

  function downloadShareCard(button) {
    var dialog = button.closest("dialog");
    var card = dialog && dialog.querySelector(".share-card");
    if (!card) return;
    var built = buildCardSvg(card);
    var blob = new Blob([built.svg], { type: "image/svg+xml;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var image = new Image();
    image.onload = function () {
      var scale = 2;
      var canvas = document.createElement("canvas");
      canvas.width = built.width * scale;
      canvas.height = built.height * scale;
      var context = canvas.getContext("2d");
      context.scale(scale, scale);
      context.drawImage(image, 0, 0, built.width, built.height);
      URL.revokeObjectURL(url);
      var link = document.createElement("a");
      var symbol = button.getAttribute("data-symbol") || "position";
      link.download = symbol + "-position.png";
      link.href = canvas.toDataURL("image/png");
      document.body.appendChild(link);
      link.click();
      link.remove();
    };
    image.src = url;
  }

  // ---- Start Tutorial: one-step spotlight tour ----
  // Dims the page, spotlights the Broker Connection sidebar item (the
  // spotlight box's giant box-shadow does the dimming so the item stays
  // bright), and shows a welcome popover. Clicking the spotlight follows
  // the real link; clicking anywhere else (or the ✕) dismisses.

  function endTour() {
    document.querySelectorAll(".tour-overlay, .tour-spotlight, .tour-popover").forEach(function (node) {
      node.remove();
    });
  }

  function startTour() {
    endTour();
    var target = document.querySelector('[data-tour="broker"]');
    if (!target) return;
    var rect = target.getBoundingClientRect();

    var overlay = document.createElement("div");
    overlay.className = "tour-overlay";
    overlay.addEventListener("click", endTour);

    var spotlight = document.createElement("div");
    spotlight.className = "tour-spotlight";
    spotlight.style.top = rect.top - 6 + "px";
    spotlight.style.left = rect.left - 6 + "px";
    spotlight.style.width = rect.width + 12 + "px";
    spotlight.style.height = rect.height + 12 + "px";
    spotlight.setAttribute("role", "button");
    spotlight.setAttribute("aria-label", "Go to Broker Connection");
    spotlight.addEventListener("click", function () {
      endTour();
      window.location.href = target.href;
    });

    var popover = document.createElement("div");
    popover.className = "tour-popover";
    popover.innerHTML =
      '<button type="button" class="icon-btn tour-close" aria-label="Close tutorial">✕</button>' +
      "<h4>Welcome to OptionTracker</h4>" +
      "<p>Let's get you started. First, you'll need to connect your broker account to view your positions.</p>" +
      '<p class="tour-hint">Click the highlighted area to continue</p>';
    popover.querySelector(".tour-close").addEventListener("click", endTour);

    document.body.appendChild(overlay);
    document.body.appendChild(spotlight);
    document.body.appendChild(popover);

    var popRect = popover.getBoundingClientRect();
    var top = rect.top - popRect.height - 14;
    if (top < 8) top = rect.bottom + 14;
    var left = Math.min(Math.max(rect.left + 8, 8), window.innerWidth - popRect.width - 8);
    popover.style.top = top + "px";
    popover.style.left = left + "px";
  }
})();
