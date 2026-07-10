// OptionTracker UI behavior: theme toggle, row expand/collapse, summary
// collapse, dropdown menus, share-card image download, the embedded
// TradingView chart panel, and the multi-step tutorial tour. Presentation
// only — every figure shown is copied verbatim from server-rendered markup.
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
      showTourStep(0);
      return;
    }

    // TradingView panel: toolbar chip toggles the embedded panel; the
    // panel's own buttons expand to fullscreen / close; watchlist entries
    // switch the charted symbol (all client-side, no server round-trip).
    var tvToggle = event.target.closest("#tv-toggle");
    if (tvToggle) {
      var panel = document.getElementById("tv-panel");
      if (panel) {
        if (panel.hidden) openTvPanel();
        else closeTvPanel();
      }
      return;
    }
    var tvClose = event.target.closest(".tv-close");
    if (tvClose) {
      closeTvPanel();
      return;
    }
    var tvExpand = event.target.closest(".tv-expand");
    if (tvExpand) {
      var expandPanel = document.getElementById("tv-panel");
      if (expandPanel) expandPanel.classList.toggle("fullscreen");
      return;
    }
    var tvItem = event.target.closest(".tv-watch-item");
    if (tvItem) {
      selectTvSymbol(tvItem.textContent.trim());
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
    parts.push('<text x="280" y="428" text-anchor="middle" font-family="' + font + '" font-size="11" letter-spacing="3" fill="' + muted + '">' + esc(cardText(card, ".share-foot")) + "</text>");
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

  // ---- TradingView panel: official embed widgets, client-side only ----
  // The chart is TradingView's Advanced Real-Time Chart embed (their
  // standard external-embedding script fed a JSON config). The watchlist
  // is a clone-side list of each position's underlying; clicking an entry
  // re-embeds the chart with that symbol. If the widget script cannot
  // load (offline dev) the panel shows a placeholder note instead.

  var TV_EMBED_SRC = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
  var tvActiveSymbol = "";

  function tvSymbols() {
    var seen = {};
    var symbols = [];
    document.querySelectorAll("#positions-table .sym-link").forEach(function (link) {
      var code = link.textContent.trim();
      if (code && !seen[code]) {
        seen[code] = true;
        symbols.push(code);
      }
    });
    return symbols;
  }

  function renderTvWatchlist(symbols) {
    var list = document.querySelector("#tv-watchlist .tv-watchlist-items");
    if (!list) return;
    list.innerHTML = "";
    symbols.forEach(function (code) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "tv-watch-item" + (code === tvActiveSymbol ? " active" : "");
      item.textContent = code;
      list.appendChild(item);
    });
  }

  function embedTvChart(symbol) {
    var host = document.getElementById("tv-chart");
    if (!host) return;
    host.innerHTML = "";
    var container = document.createElement("div");
    container.className = "tradingview-widget-container";
    var widget = document.createElement("div");
    widget.className = "tradingview-widget-container__widget";
    container.appendChild(widget);
    var script = document.createElement("script");
    script.type = "text/javascript";
    script.src = TV_EMBED_SRC;
    script.async = true;
    script.text = JSON.stringify({
      autosize: true,
      symbol: symbol,
      interval: "D",
      timezone: "Etc/UTC",
      theme: "dark",
      style: "1",
      locale: "en",
      allow_symbol_change: true,
      support_host: "https://www.tradingview.com",
    });
    script.onerror = function () {
      host.innerHTML =
        '<div class="tv-placeholder"><strong>' + esc(symbol) + "</strong>" +
        "<span>TradingView chart unavailable — the embed script could not load " +
        "(offline). This frame hosts the official Advanced Chart widget when " +
        "a network connection is available.</span></div>";
    };
    container.appendChild(script);
    host.appendChild(container);
  }

  function selectTvSymbol(symbol) {
    if (!symbol || symbol === tvActiveSymbol) return;
    tvActiveSymbol = symbol;
    document.querySelectorAll(".tv-watch-item").forEach(function (item) {
      item.classList.toggle("active", item.textContent.trim() === symbol);
    });
    embedTvChart(symbol);
  }

  function openTvPanel() {
    var panel = document.getElementById("tv-panel");
    if (!panel) return;
    var symbols = tvSymbols();
    if (!tvActiveSymbol || symbols.indexOf(tvActiveSymbol) === -1) {
      tvActiveSymbol = symbols[0] || "";
    }
    renderTvWatchlist(symbols);
    panel.hidden = false;
    embedTvChart(tvActiveSymbol || "AAPL");
  }

  function closeTvPanel() {
    var panel = document.getElementById("tv-panel");
    if (!panel) return;
    panel.hidden = true;
    panel.classList.remove("fullscreen");
    var host = document.getElementById("tv-chart");
    if (host) host.innerHTML = "";
  }

  // Filters/sort swap the table via htmx: refresh the watchlist so it
  // keeps mirroring the visible positions while the panel is open.
  document.addEventListener("htmx:afterSwap", function (event) {
    if (!event.target || event.target.id !== "positions-table") return;
    var panel = document.getElementById("tv-panel");
    if (panel && !panel.hidden) renderTvWatchlist(tvSymbols());
  });

  // ---- Start Tutorial: multi-step spotlight tour ----
  // Dims the page (the spotlight box's giant box-shadow does the dimming
  // so the target stays bright) and walks a fixed set of steps with
  // Next/Previous. Steps live on two pages; the current step index is
  // kept in sessionStorage so the tour survives the step-1 -> step-2
  // navigation (and back). ✕ or a click outside dismisses anywhere.

  var TOUR_KEY = "optiontracker-tour-step";
  var TOUR_STEPS = [
    {
      page: "positions",
      target: '[data-tour="broker"]',
      title: "Welcome to OptionTracker",
      body: "Let's get you started. First, you'll need to connect your broker account to view your positions.",
      hint: "Click the highlighted area to continue",
      clickThrough: true,
    },
    {
      page: "broker",
      target: ".broker-tile",
      title: "Connecting Your Broker",
      body: "Click on a broker card to start connecting your account. This will open the connection flow.",
      next: true,
    },
    {
      page: "positions",
      target: ".summary-card",
      title: "Account Summary",
      body: "Your total value, options and equity positions at a glance.",
      previous: true,
      next: true,
    },
    {
      page: "positions",
      target: "#positions-table",
      title: "Live Positions Overview",
      body: "Your open option strategies with live prices and greeks. Click a row to see per-leg details.",
      previous: true,
      next: true,
    },
    {
      page: "positions",
      target: "#tv-toggle",
      title: "TradingView",
      body: "Open an embedded chart panel for your symbols.",
      previous: true,
      next: true,
    },
    {
      page: "positions",
      target: "#strategy-dropdown",
      title: "Filters",
      body: "Narrow positions by strategy or symbol.",
      previous: true,
      next: true,
    },
  ];

  function tourPage() {
    return window.location.pathname.indexOf("/broker") !== -1 ? "broker" : "positions";
  }

  function tourPageUrl(page) {
    var link = document.querySelector(page === "broker" ? '[data-tour="broker"]' : '[data-tour="positions"]');
    return link ? link.href : null;
  }

  function clearTourStep() {
    try {
      sessionStorage.removeItem(TOUR_KEY);
    } catch (e) {
      /* ignore */
    }
  }

  function saveTourStep(index) {
    try {
      sessionStorage.setItem(TOUR_KEY, String(index));
    } catch (e) {
      /* ignore */
    }
  }

  function endTour() {
    clearTourStep();
    document.querySelectorAll(".tour-overlay, .tour-spotlight, .tour-popover").forEach(function (node) {
      node.remove();
    });
  }

  function removeTourNodes() {
    document.querySelectorAll(".tour-overlay, .tour-spotlight, .tour-popover").forEach(function (node) {
      node.remove();
    });
  }

  function showTourStep(index) {
    removeTourNodes();
    if (index < 0 || index >= TOUR_STEPS.length) {
      clearTourStep();
      return;
    }
    var step = TOUR_STEPS[index];
    saveTourStep(index);
    if (step.page !== tourPage()) {
      var url = tourPageUrl(step.page);
      if (url) window.location.href = url;
      return;
    }
    var target = document.querySelector(step.target);
    if (!target) {
      clearTourStep();
      return;
    }
    target.scrollIntoView({ block: "center", inline: "nearest" });
    var rect = target.getBoundingClientRect();
    // Clamp tall targets (the positions table) to the viewport so the
    // spotlight ring stays fully visible.
    var boxTop = Math.max(rect.top, 8);
    var boxBottom = Math.min(rect.bottom, window.innerHeight - 8);

    var overlay = document.createElement("div");
    overlay.className = "tour-overlay";
    overlay.addEventListener("click", endTour);

    var spotlight = document.createElement("div");
    spotlight.className = "tour-spotlight";
    spotlight.style.top = boxTop - 6 + "px";
    spotlight.style.left = rect.left - 6 + "px";
    spotlight.style.width = rect.width + 12 + "px";
    spotlight.style.height = boxBottom - boxTop + 12 + "px";
    if (step.clickThrough) {
      spotlight.classList.add("clickable");
      spotlight.setAttribute("role", "button");
      spotlight.setAttribute("aria-label", "Go to " + step.title);
      spotlight.addEventListener("click", function () {
        removeTourNodes();
        showTourStep(index + 1);
      });
    } else {
      spotlight.style.pointerEvents = "none";
    }

    var popover = document.createElement("div");
    popover.className = "tour-popover";
    var html =
      '<button type="button" class="icon-btn tour-close" aria-label="Close tutorial">✕</button>' +
      "<h4>" + esc(step.title) + "</h4>" +
      "<p>" + esc(step.body) + "</p>";
    if (step.hint) html += '<p class="tour-hint">' + esc(step.hint) + "</p>";
    if (step.previous || step.next) {
      html += '<div class="tour-nav">';
      if (step.previous) html += '<button type="button" class="btn tour-prev">‹ Previous</button>';
      if (step.next) html += '<button type="button" class="btn btn-primary tour-next">Next ›</button>';
      html += "</div>";
    }
    popover.innerHTML = html;
    popover.querySelector(".tour-close").addEventListener("click", endTour);
    var prevButton = popover.querySelector(".tour-prev");
    if (prevButton) prevButton.addEventListener("click", function () { showTourStep(index - 1); });
    var nextButton = popover.querySelector(".tour-next");
    if (nextButton) {
      nextButton.addEventListener("click", function () {
        if (index + 1 >= TOUR_STEPS.length) endTour();
        else showTourStep(index + 1);
      });
    }

    document.body.appendChild(overlay);
    document.body.appendChild(spotlight);
    document.body.appendChild(popover);

    var popRect = popover.getBoundingClientRect();
    var top = boxTop - popRect.height - 14;
    if (top < 8) top = Math.min(boxBottom + 14, window.innerHeight - popRect.height - 8);
    var left = Math.min(Math.max(rect.left + 8, 8), window.innerWidth - popRect.width - 8);
    popover.style.top = top + "px";
    popover.style.left = left + "px";
  }

  // Resume a tour in progress after the cross-page navigation.
  (function () {
    var saved = null;
    try {
      saved = sessionStorage.getItem(TOUR_KEY);
    } catch (e) {
      /* ignore */
    }
    if (saved === null) return;
    var index = parseInt(saved, 10);
    if (isNaN(index)) {
      clearTourStep();
      return;
    }
    if (TOUR_STEPS[index] && TOUR_STEPS[index].page === tourPage()) {
      showTourStep(index);
    }
  })();
})();
