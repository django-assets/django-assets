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
      selectTvSymbol(tvItem.getAttribute("data-symbol") || tvItem.textContent.trim());
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

    // Roll Selection finder: the header checkbox selects/deselects every
    // candidate row; clicking a row toggles its own selection (both are
    // client-side only; demo-inert).
    var selectAll = event.target.closest(".roll-select-all");
    if (selectAll) {
      var selectAllBody = selectAll.closest(".roll-finder-body");
      if (selectAllBody) {
        selectAllBody.querySelectorAll("tr.roll-candidate").forEach(function (row) {
          setRollCandidateSelected(row, selectAll.checked);
        });
        updateRollTotals(selectAllBody);
      }
      return;
    }
    var candidateRow = event.target.closest("tr.roll-candidate");
    if (candidateRow) {
      toggleRollCandidate(candidateRow);
      return;
    }

    // Month-view calendar cells: open the month-detail dialog and load
    // the month's fragment (all figures server-rendered by the library).
    var monthCell = event.target.closest("[data-month-url]");
    if (monthCell) {
      openMonthDetail(monthCell.getAttribute("data-month-url"), false);
      return;
    }

    // In-page dialogs (roll finder, share position): any button carrying
    // data-dialog opens the named <dialog>; ✕ or backdrop closes. Roll
    // finder dialogs lazy-load their candidates on this first open (pure
    // ledger data, but fetched on click to keep the rows light).
    var dialogButton = event.target.closest("[data-dialog]");
    if (dialogButton) {
      event.stopPropagation();
      var dialog = document.getElementById(dialogButton.getAttribute("data-dialog"));
      if (dialog && dialog.showModal) {
        dialog.showModal();
        loadRollFinder(dialog);
      }
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
    var candidate = event.target.closest && event.target.closest("tr.roll-candidate");
    if (candidate) {
      toggleRollCandidate(candidate);
      return;
    }
    var monthCell = event.target.closest && event.target.closest("[data-month-url]");
    if (monthCell) {
      openMonthDetail(monthCell.getAttribute("data-month-url"), false);
      return;
    }
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

  // ---- Roll Selection finder ----
  // The dialog carries data-roll-url; its body fragment is fetched via
  // htmx only when the dialog is first opened (kept lazy so the positions
  // rows stay light). Row selection and the two running "with selection"
  // totals are pure presentation: sums of the fragment's data-premium /
  // data-pnl attributes on top of the body's data-current-* base values
  // (all finished library figures). Save Selection is demo-inert (just
  // closes the dialog).

  function loadRollFinder(dialog) {
    if (!dialog || !dialog.hasAttribute("data-roll-url") || dialog.dataset.rollLoaded) return;
    var body = dialog.querySelector(".roll-finder-body");
    if (!body || !window.htmx) return;
    dialog.dataset.rollLoaded = "1";
    htmx.ajax("GET", dialog.getAttribute("data-roll-url"), { target: body, swap: "outerHTML" });
  }

  function formatMoneyJs(value) {
    var sign = value < 0 ? "-" : "";
    return (
      sign +
      "$" +
      Math.abs(value).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    );
  }

  function setRollCandidateSelected(row, selected) {
    row.classList.toggle("selected", selected);
    row.setAttribute("aria-checked", selected ? "true" : "false");
    var box = row.querySelector('input[type="checkbox"]');
    if (box) box.checked = selected;
  }

  function toggleRollCandidate(row) {
    setRollCandidateSelected(row, !row.classList.contains("selected"));
    updateRollTotals(row.closest(".roll-finder-body"));
  }

  function updateRollTotals(body) {
    if (!body) return;
    var rows = body.querySelectorAll("tr.roll-candidate");
    var picked = body.querySelectorAll("tr.roll-candidate.selected");
    var selectAll = body.querySelector(".roll-select-all");
    if (selectAll) {
      selectAll.checked = rows.length > 0 && picked.length === rows.length;
      selectAll.indeterminate = picked.length > 0 && picked.length < rows.length;
    }
    var premiumNode = body.querySelector(".roll-total-premium");
    var pnlNode = body.querySelector(".roll-total-pnl");
    if (!premiumNode || !pnlNode) return;
    if (!picked.length) {
      premiumNode.textContent = "-";
      pnlNode.textContent = "-";
      pnlNode.className = "roll-total-pnl";
      return;
    }
    var premium = parseFloat(body.getAttribute("data-current-premium")) || 0;
    var pnl = parseFloat(body.getAttribute("data-current-pnl")) || 0;
    picked.forEach(function (row) {
      premium += parseFloat(row.getAttribute("data-premium")) || 0;
      pnl += parseFloat(row.getAttribute("data-pnl")) || 0;
    });
    premiumNode.textContent = formatMoneyJs(premium);
    pnlNode.textContent = formatMoneyJs(pnl);
    pnlNode.className = "roll-total-pnl " + (pnl > 0 ? "pos" : pnl < 0 ? "neg" : "");
  }

  // ---- Calendar month-detail dialog ----
  // Month-view cells carry data-month-url; opening the dialog HTMX-loads
  // that month's fragment. During the tour the dialog opens non-modally
  // (dialog.show()) so the tour chrome stays above it.

  function openMonthDetail(url, tour) {
    var dialog = document.getElementById("month-detail-dialog");
    if (!dialog || !url) return;
    if (tour) {
      if (!dialog.open) dialog.show();
      dialog.classList.add("tour-open");
    } else if (dialog.showModal && !dialog.open) {
      dialog.showModal();
    }
    var body = dialog.querySelector(".month-detail-body");
    if (body && window.htmx) {
      body.classList.remove("loaded");
      htmx.ajax("GET", url, { target: body, swap: "outerHTML" });
    }
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
    // Table-ish chrome: Symbol / Last / Chg% columns. Prices are not
    // fetched client-side, so the quote cells stay as placeholder dashes.
    var head = document.createElement("div");
    head.className = "tv-watch-head";
    head.innerHTML = "<span>Symbol</span><span>Last</span><span>Chg%</span>";
    list.appendChild(head);
    symbols.forEach(function (code) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "tv-watch-item" + (code === tvActiveSymbol ? " active" : "");
      item.setAttribute("data-symbol", code);
      item.innerHTML =
        '<span class="tv-watch-sym">' + esc(code) + "</span>" +
        '<span class="tv-watch-quote">–</span><span class="tv-watch-quote">–</span>';
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
      item.classList.toggle("active", item.getAttribute("data-symbol") === symbol);
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
  // Mirrors the reference app's guided tour: 23 steps across every page of
  // the app, organized in titled groups with a segmented progress bar.
  // Click-through steps show a pulsing ORANGE ring and advance when the
  // highlighted element is clicked (the click also performs the element's
  // real action: navigation, opening the TradingView panel, expanding a
  // row, opening the roll dialog...). Next-driven steps show a BLUE ring
  // with Previous/Next buttons. The current step index is kept in
  // sessionStorage so the tour survives cross-page navigations; ✕ or a
  // click on the dim overlay dismisses anywhere and does not resume.

  var TOUR_KEY = "optiontracker-tour-step";

  // --- step-state helpers (idempotent; used by ensure() when a step is
  // shown, so Previous / reload rebuild the exact UI state a step needs).

  // The tour's roll steps ride the OSCR covered-call row (the reference
  // tour names OSCR explicitly); fall back to any OSCR row, then to the
  // first expandable row so the tour still works on reseeded data.
  function tourRollRow() {
    var rows = document.querySelectorAll("#positions-table tr.expandable");
    var oscr = null;
    for (var i = 0; i < rows.length; i++) {
      var link = rows[i].querySelector(".sym-link");
      if (link && link.textContent.trim() === "OSCR") {
        if (rows[i].textContent.indexOf("Covered Call") !== -1) return rows[i];
        if (!oscr) oscr = rows[i];
      }
    }
    return oscr || rows[0] || null;
  }

  function tourDetailRow() {
    var row = tourRollRow();
    var detail = row && row.nextElementSibling;
    return detail && detail.classList.contains("detail-row") ? detail : null;
  }

  function tourRollDialog() {
    var detail = tourDetailRow();
    return detail ? detail.querySelector('dialog[id^="roll-dialog"]') : null;
  }

  function setTourRowExpanded(expanded) {
    var row = tourRollRow();
    var detail = tourDetailRow();
    if (!row || !detail) return;
    detail.hidden = !expanded;
    row.classList.toggle("open", expanded);
  }

  function setTourRollDialogOpen(open) {
    var dialog = tourRollDialog();
    if (!dialog) return;
    if (open) {
      // Non-modal on purpose: showModal() would paint the dialog in the
      // browser top layer, above the tour spotlight and popover.
      if (!dialog.open) dialog.show();
      dialog.classList.add("tour-open");
      loadRollFinder(dialog);
    } else {
      dialog.classList.remove("tour-open");
      if (dialog.open) dialog.close();
    }
  }

  function setTourTvOpen(open) {
    var panel = document.getElementById("tv-panel");
    if (!panel) return;
    if (open && panel.hidden) openTvPanel();
    if (!open && !panel.hidden) closeTvPanel();
  }

  function positionsState(tv, row, dialog) {
    return function () {
      setTourRollDialogOpen(dialog);
      setTourRowExpanded(row);
      setTourTvOpen(tv);
    };
  }

  // Calendar steps: the tour walks the MONTH view (like the reference),
  // so make sure the Jan–Dec grid is loaded before ringing a month cell.
  // Issues the same swap the view <select> would (htmx.ajax works even
  // right after load, before htmx has bound the select's own listener);
  // a flag on #calendar-body stops repeat requests — the swap replaces
  // the element, so a fresh body (grid included) starts clean.
  function ensureMonthView() {
    if (document.querySelector("#calendar-body .month-grid")) return;
    var body = document.getElementById("calendar-body");
    if (!body || body.dataset.tourMonthLoading || !window.htmx) return;
    body.dataset.tourMonthLoading = "1";
    var select = body.querySelector('select[name="view"]');
    if (select) select.value = "month";
    var params = new URLSearchParams(window.location.search);
    params.set("view", "month");
    htmx.ajax("GET", window.location.pathname + "?" + params.toString(), {
      target: body,
      swap: "outerHTML",
    });
  }

  // The month cell the tour rings: the first month card with trades.
  function tourMonthCell() {
    var cards = document.querySelectorAll("#calendar-body .month-card");
    for (var i = 0; i < cards.length; i++) {
      if (cards[i].querySelector(".month-card-trades")) return cards[i];
    }
    return cards[0] || null;
  }

  function setTourMonthDialogOpen(open) {
    var dialog = document.getElementById("month-detail-dialog");
    if (!dialog) return;
    if (open) {
      dialog.classList.add("tour-open");
      if (!dialog.open) {
        var cell = tourMonthCell();
        if (cell) openMonthDetail(cell.getAttribute("data-month-url"), true);
      }
    } else {
      dialog.classList.remove("tour-open");
      if (dialog.open) dialog.close();
    }
  }

  // --- the 23 steps, mirroring the reference tour ---
  // group: {i, n} renders an n-segment progress bar with the first i
  // segments filled. clickThrough steps advance by clicking the ring.

  var TOUR_STEPS = [
    {
      page: "positions",
      target: '[data-tour="broker"]',
      title: "Welcome to OptionTracker",
      body: "Let's get you started. First, you'll need to connect your broker account to view your positions.",
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
      page: "broker",
      target: ".summary-card",
      title: "Account Summary",
      body: "This is your Account Summary. It shows total account value, options and equity positions, margin, PnL, and cash in one place. You can expand or collapse it and use it as a quick reference while you trade.",
      next: true,
    },
    {
      page: "broker",
      target: '[data-tour="positions"]',
      title: "Live Positions Overview",
      body: "Click on 'Option Positions' in the sidebar to view your live option positions.",
      clickThrough: true,
      group: { i: 1, n: 2 },
    },
    {
      page: "positions",
      target: "#positions-split .page-card",
      title: "Live Positions Overview",
      body: "This section shows all your active option positions. Use the symbol and strategy filters to narrow the list. Click any row to expand and see detailed leg information, and sort by any column to organize your view.",
      previous: true,
      next: true,
      group: { i: 2, n: 2 },
      ensure: positionsState(false, false, false),
    },
    {
      page: "positions",
      target: "#tv-toggle",
      title: "TradingView",
      body: "Click here to open TradingView charts for your positions.",
      clickThrough: true,
      group: { i: 1, n: 2 },
      ensure: positionsState(false, false, false),
      action: function () {
        setTourTvOpen(true);
      },
    },
    {
      page: "positions",
      target: "#tv-panel",
      title: "TradingView",
      body: "This panel displays interactive TradingView charts for your positions. You can switch between different symbols using the watchlist, analyze price movements, and use all TradingView charting tools. The panel can be expanded to full screen or closed when you're done.",
      previous: true,
      next: true,
      group: { i: 2, n: 2 },
      ensure: positionsState(true, false, false),
    },
    {
      page: "positions",
      target: function () {
        return tourRollRow();
      },
      title: "Roll Candidates",
      body: "Click on the OSCR row to expand it and reveal position details and roll options.",
      clickThrough: true,
      group: { i: 1, n: 4 },
      ensure: positionsState(false, false, false),
      action: function () {
        setTourRowExpanded(true);
      },
    },
    {
      page: "positions",
      target: function () {
        var detail = tourDetailRow();
        return detail ? detail.querySelector(".roll-btn") : null;
      },
      title: "Roll Candidates",
      body: "Click the Roll Selection button to find optimal roll strategies for this position.",
      clickThrough: true,
      group: { i: 2, n: 4 },
      ensure: positionsState(false, true, false),
      action: function () {
        setTourRollDialogOpen(true);
      },
    },
    {
      page: "positions",
      target: function () {
        var dialog = tourRollDialog();
        if (!dialog) return null;
        var row = dialog.querySelector("tr.roll-candidate");
        if (row) return row;
        // Loaded but no candidates (no linkable prior closed trades):
        // ring the dialog instead.
        return dialog.querySelector(".roll-finder-body.loaded") ? dialog : null;
      },
      title: "Roll Candidates",
      body: "Click on the row to select a roll candidate. You can select multiple candidates to combine them into a roll strategy.",
      clickThrough: true,
      group: { i: 3, n: 4 },
      ensure: positionsState(false, true, true),
      action: function (target) {
        if (target && target.classList && target.classList.contains("roll-candidate")) {
          toggleRollCandidate(target);
        }
      },
    },
    {
      page: "positions",
      target: function () {
        var dialog = tourRollDialog();
        if (!dialog) return null;
        var save = dialog.querySelector(".roll-save");
        if (save) return save;
        return dialog.querySelector(".roll-finder-body.loaded") ? dialog : null;
      },
      title: "Roll Candidates",
      body: "Click the Save Selection button to save your roll candidate choices. This will link the selected candidates to your current position.",
      next: true,
      group: { i: 4, n: 4 },
      ensure: positionsState(false, true, true),
    },
    {
      page: "wheel",
      target: "main .page-card",
      title: "Wheel Strategy Campaigns",
      body: "Wheel Campaigns shows shares tied to active Wheel strategies. You can review the campaign, adjusted cost basis, collected premium, and customize which transactions belong to each wheel.",
      next: true,
      group: { i: 1, n: 2 },
    },
    {
      page: "wheel",
      target: '.sidenav-subitem[href*="equities"]',
      title: "Equity Positions Overview",
      body: "The Equity Positions tab shows your full stock and ETF holdings, including shares that are not part of a Wheel Campaign.",
      previous: true,
      next: true,
      group: { i: 2, n: 2 },
    },
    {
      page: "wheel",
      target: 'a.sidenav-item[href*="analytics"]',
      title: "Analytics Overview",
      body: "Click on 'Analytics' in the sidebar to view your analytics dashboard.",
      clickThrough: true,
      group: { i: 1, n: 2 },
    },
    {
      page: "analytics",
      target: "#analytics-body",
      title: "Analytics Overview",
      body: "Track your performance with detailed analytics and PnL charts.",
      previous: true,
      next: true,
      group: { i: 2, n: 2 },
    },
    {
      page: "analytics",
      target: '.sidenav-subitem[href*="flow"]',
      title: "PnL Flow",
      body: "Click PnL Flow to see how finalized options PnL moves from symbols through call/put or strategy type into gains and losses.",
      clickThrough: true,
      group: { i: 1, n: 2 },
    },
    {
      page: "pnl_flow",
      target: "#flow-body",
      title: "PnL Flow",
      body: "This chart shows PnL flow by symbol, call or put, strategy, and date. Use the filters to focus on a smaller set of finalized trades.",
      previous: true,
      next: true,
      group: { i: 2, n: 2 },
    },
    {
      page: "pnl_flow",
      target: 'a.sidenav-item[href*="calendar"]',
      title: "Calendar View",
      body: "Click the Calendar item to view your PnL organized by date. This calendar view helps you see which time periods had the most profit or loss.",
      clickThrough: true,
      group: { i: 1, n: 4 },
    },
    {
      page: "calendar",
      target: "#calendar-body",
      title: "Calendar View",
      body: "This calendar view shows your PnL by week or month. You can see which time periods had the most profit or loss.",
      previous: true,
      next: true,
      group: { i: 2, n: 4 },
      ensure: function () {
        setTourMonthDialogOpen(false);
      },
    },
    {
      page: "calendar",
      target: function () {
        return tourMonthCell();
      },
      title: "Calendar View",
      body: "Click on the June cell to view a detailed summary of that month, including all trades and their PnL breakdown.",
      clickThrough: true,
      previous: true,
      group: { i: 3, n: 4 },
      ensure: function () {
        setTourMonthDialogOpen(false);
        ensureMonthView();
      },
      action: function (target) {
        var cell = target && target.getAttribute && target.getAttribute("data-month-url") ? target : tourMonthCell();
        if (cell) openMonthDetail(cell.getAttribute("data-month-url"), true);
      },
    },
    {
      page: "calendar",
      target: function () {
        var dialog = document.getElementById("month-detail-dialog");
        if (!dialog || !dialog.open) return null;
        return dialog.querySelector(".month-detail-body.loaded") ? dialog : null;
      },
      title: "Calendar View",
      body: "This dialog shows a detailed breakdown of the selected time period. You can see total PnL, win ratio, trading days, a daily PnL chart, and all individual trades.",
      previous: true,
      next: true,
      group: { i: 4, n: 4 },
      ensure: function () {
        ensureMonthView();
        setTourMonthDialogOpen(true);
      },
    },
    {
      page: "calendar",
      target: 'a.sidenav-item[href*="history"]',
      title: "History Overview",
      body: "Click on 'History' in the sidebar to view your historical trades database.",
      clickThrough: true,
      group: { i: 1, n: 2 },
      ensure: function () {
        setTourMonthDialogOpen(false);
      },
    },
    {
      page: "history",
      target: "main .page-card",
      title: "History Overview",
      body: "This is your historical trades database. You can search by symbol or option strategy to review past trades, including strike price, P&L, and fees.",
      previous: true,
      finish: true,
      group: { i: 2, n: 2 },
    },
  ];

  var TOUR_PAGE_PATHS = {
    positions: "",
    broker: "broker/",
    wheel: "wheel/",
    equities: "equities/",
    analytics: "analytics/",
    pnl_flow: "analytics/flow/",
    calendar: "calendar/",
    history: "history/",
  };

  function tourPage() {
    var path = window.location.pathname;
    if (path.indexOf("/broker") !== -1) return "broker";
    if (path.indexOf("/wheel") !== -1) return "wheel";
    if (path.indexOf("/equities") !== -1) return "equities";
    if (path.indexOf("/analytics/flow") !== -1) return "pnl_flow";
    if (path.indexOf("/analytics") !== -1) return "analytics";
    if (path.indexOf("/calendar") !== -1) return "calendar";
    if (path.indexOf("/history") !== -1) return "history";
    return "positions";
  }

  function tourPageUrl(page) {
    var base = document.querySelector('[data-tour="positions"]');
    if (!base || TOUR_PAGE_PATHS[page] === undefined) return null;
    return base.href + TOUR_PAGE_PATHS[page];
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

  function removeTourNodes() {
    document.querySelectorAll(".tour-overlay, .tour-spotlight, .tour-popover").forEach(function (node) {
      node.remove();
    });
  }

  function endTour() {
    clearTourStep();
    removeTourNodes();
    document.querySelectorAll("dialog.tour-open").forEach(function (dialog) {
      dialog.classList.remove("tour-open");
      if (dialog.open) dialog.close();
    });
  }

  function resolveTourTarget(step) {
    if (typeof step.target === "function") return step.target();
    return document.querySelector(step.target);
  }

  // Advance past a click-through step: run its action, then either show
  // the next step in place or persist the index and follow the navigation.
  function advanceTour(index, target) {
    var step = TOUR_STEPS[index];
    var next = TOUR_STEPS[index + 1];
    if (step.action) step.action(target);
    if (!next) {
      endTour();
      return;
    }
    if (next.page !== tourPage()) {
      removeTourNodes();
      saveTourStep(index + 1);
      var url = target && target.tagName === "A" && target.href ? target.href : tourPageUrl(next.page);
      if (url) window.location.href = url;
      else endTour();
      return;
    }
    showTourStep(index + 1);
  }

  var TOUR_CHEV_LEFT =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>';
  var TOUR_CHEV_RIGHT =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>';

  function showTourStep(index, attempt) {
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
    if (step.ensure) step.ensure();
    var target = resolveTourTarget(step);
    if (!target) {
      // Async fragments (the roll finder, the month grid, the month
      // dialog) may still be loading: poll before giving up (generous
      // budget — a cold quote cache can stall a swap for many seconds),
      // aborting if the tour was closed meanwhile.
      var tries = attempt || 0;
      if (tries < 120) {
        setTimeout(function () {
          var saved = null;
          try {
            saved = sessionStorage.getItem(TOUR_KEY);
          } catch (e) {
            /* ignore */
          }
          if (saved === String(index)) showTourStep(index, tries + 1);
        }, 250);
        return;
      }
      clearTourStep();
      return;
    }
    target.scrollIntoView({ block: "center", inline: "nearest" });
    var rect = target.getBoundingClientRect();
    // Clamp tall targets (the positions table, history list) to the
    // viewport so the spotlight ring stays fully visible.
    var boxTop = Math.max(rect.top, 8);
    var boxBottom = Math.min(rect.bottom, window.innerHeight - 8);

    var overlay = document.createElement("div");
    overlay.className = "tour-overlay";
    overlay.addEventListener("click", endTour);

    var spotlight = document.createElement("div");
    spotlight.className = "tour-spotlight";
    spotlight.style.top = boxTop + "px";
    spotlight.style.left = rect.left + "px";
    spotlight.style.width = rect.width + "px";
    spotlight.style.height = boxBottom - boxTop + "px";
    if (step.clickThrough) {
      spotlight.classList.add("clickable");
      spotlight.setAttribute("role", "button");
      spotlight.setAttribute("aria-label", "Click to continue tutorial");
      spotlight.addEventListener("click", function () {
        advanceTour(index, target);
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
    if (step.group) {
      html += '<div class="tour-progress">';
      for (var s = 0; s < step.group.n; s++) {
        html += '<span class="seg' + (s < step.group.i ? " fill" : "") + '"></span>';
      }
      html += "</div>";
    }
    html += '<div class="tour-foot"><div class="tour-foot-btns">';
    if (step.previous) {
      html += '<button type="button" class="tour-btn tour-prev">' + TOUR_CHEV_LEFT + "Previous</button>";
    }
    html += "</div>";
    if (step.clickThrough) {
      html += '<div class="tour-hint">Click the highlighted area to continue</div>';
    } else if (step.finish) {
      html += '<button type="button" class="tour-btn tour-next">Finish</button>';
    } else if (step.next) {
      html += '<button type="button" class="tour-btn tour-next">Next' + TOUR_CHEV_RIGHT + "</button>";
    }
    html += "</div>";
    popover.innerHTML = html;
    popover.querySelector(".tour-close").addEventListener("click", endTour);
    var prevButton = popover.querySelector(".tour-prev");
    if (prevButton) {
      prevButton.addEventListener("click", function () {
        showTourStep(index - 1);
      });
    }
    var nextButton = popover.querySelector(".tour-next");
    if (nextButton) {
      nextButton.addEventListener("click", function () {
        advanceTour(index, null);
      });
    }

    document.body.appendChild(overlay);
    document.body.appendChild(spotlight);
    document.body.appendChild(popover);

    // Placement, mirroring the reference: centered below the target, then
    // above; sidebar targets get the popover on their right instead.
    var popRect = popover.getBoundingClientRect();
    var popW = popRect.width;
    var popH = popRect.height;
    var top = null;
    var left = null;
    if (rect.left < 320 && rect.left + rect.width + 16 + popW < window.innerWidth - 8) {
      left = rect.left + rect.width + 16;
      top = (boxTop + boxBottom) / 2 - popH / 2;
    } else if (boxBottom + 16 + popH < window.innerHeight - 8) {
      top = boxBottom + 16;
      left = rect.left + rect.width / 2 - popW / 2;
    } else if (boxTop - 16 - popH >= 8) {
      top = boxTop - 16 - popH;
      left = rect.left + rect.width / 2 - popW / 2;
    } else {
      top = window.innerHeight - popH - 16;
      left = rect.left + rect.width / 2 - popW / 2;
    }
    popover.style.top = Math.min(Math.max(top, 8), window.innerHeight - popH - 8) + "px";
    popover.style.left = Math.min(Math.max(left, 8), window.innerWidth - popW - 8) + "px";
  }

  // Resume a tour in progress after a cross-page navigation.
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
