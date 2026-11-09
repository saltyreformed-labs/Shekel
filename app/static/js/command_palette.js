/**
 * command_palette.js -- Ctrl+K command layer for the budget grid.
 *
 * C3 rebuild (decision 4, docs/design/grid_audit.md): a fuzzy palette
 * over the rendered grid.  The action index is derived from the grid
 * DOM itself -- every action surfaces something the server rendered
 * (a mark-paid check button, a data-can-credit stamp, an envelope
 * stamp, the anchor display) and executes through the exact same
 * controls and endpoints the on-grid buttons use.  No new mutation
 * surface, no client-side money math.
 *
 * Actions per visible window:
 *   - Mark paid  -- every projected cell (clicks its .paybtn)
 *   - Mark credit -- every credit-eligible cell: the card's Credit press
 *     where the cell says it may be made at once, else the cell's card
 *     opened, where Credit asks for a card to be added, asks which card,
 *     or names the bank line it would unmatch first
 *   - Open       -- every transaction cell (opens the action card)
 *   - Add purchase -- envelope rows (opens the card, focuses the
 *     add-purchase amount input once the entries list loads)
 *   - Go to row  -- scrolls a row into view and sets the cell cursor
 *   - Update anchor balance -- opens the inline anchor editor
 *   - Import bank statement / Review bank matches -- navigates to
 *     the statements and review pages, read off the grid's own
 *     bank door (its href and its data-review-url)
 *
 * Result rows are built with createElement/textContent, never
 * innerHTML, because labels carry user-named transactions.
 */
(function () {
    'use strict';

    var MAX_RESULTS = 8;

    var palette = null;
    var scrim = null;
    var input = null;
    var resultsEl = null;
    var countEl = null;

    var actions = [];
    var matches = [];
    var selected = 0;

    function gridTable() {
        return document.querySelector('.grid-table');
    }

    /** Period labels from the LAST thead row (the month band is first);
        each th wraps its date in a .fw-bold div, separate from the
        carry-forward button text. */
    function periodLabels() {
        var table = gridTable();
        if (!table) return [];
        var headerRow = table.querySelector('thead tr:last-child');
        if (!headerRow) return [];
        return Array.prototype.slice.call(headerRow.children, 1).map(function (th) {
            var label = th.querySelector('.fw-bold');
            return (label ? label.textContent : th.textContent).trim();
        });
    }

    /** Condensed visible amount text of a cell ("114 / 80", "1,850"). */
    function amountText(open) {
        return open.textContent.replace(/\s+/g, ' ').trim();
    }

    /** Scan the grid DOM and rebuild the action index. */
    /** Push the bank door's two destinations onto the action index.

        Read off the RENDERED control rather than plumbed in separately: the
        route already decided whether this account may have statements at
        all, and a palette entry that navigated somewhere the header does not
        link would be a second answer to that question.  Navigation, not a
        mutation -- these are the only actions here that leave the page, and
        they are why the feature is findable by name.

        Its own function because its four locals belong at a function root
        (biome ``correctness/noInnerDeclarations``), and hoisting them into
        buildActions would park them ~120 lines from their only use. */
    function pushBankDoorActions() {
        var bankDoor = document.querySelector('.grid-bank-door');
        if (!bankDoor) return;
        var bankBadge = bankDoor.querySelector('.grid-bank-badge');
        var waiting = bankBadge ? bankBadge.textContent.trim() : '';
        /* Both from DATA, never from href: the door's href is whichever act is
           live (the review screen once lines are waiting), so reading it as the
           import URL sent this action to the wrong screen the moment a badge
           appeared.  The fallback keeps the action working against a door
           rendered before data-import-url existed. */
        var importUrl = bankDoor.dataset.importUrl || bankDoor.href;
        var reviewUrl = bankDoor.dataset.reviewUrl;
        actions.push({
            kind: 'bank',
            label: 'Import bank statement',
            meta: 'account',
            run: function () { window.location.assign(importUrl); },
        });
        if (reviewUrl) {
            actions.push({
                kind: 'bank',
                label: (waiting
                    ? 'Review bank matches -- ' + waiting + ' waiting'
                    : 'Review bank matches'),
                meta: 'account',
                run: function () { window.location.assign(reviewUrl); },
            });
        }
    }

    function buildActions() {
        actions = [];
        var table = gridTable();
        if (!table) return;
        var headers = periodLabels();

        Array.prototype.forEach.call(table.querySelectorAll('tbody tr'), function (row) {
            var labelTh = row.querySelector('th.row-label');
            var cells = row.querySelectorAll('td.cell');
            if (!labelTh || !cells.length) return;
            var name = labelTh.textContent.trim();
            if (!name) return;

            var gotoCell = row.querySelector('td.cell.cur') || cells[0];
            // Capture the goto cell's wrapper id (when it has one) so the
            // runner can re-resolve the cell at run time after a pre-Enter
            // swap.  `data-cell` is the id the wrapper carries, for a
            // transaction (txn-cell-<id>) and a transfer leg
            // (xfer-leg-<transfer>-<account>) alike (balance:X-bi-6-1).
            var gotoOpen = gotoCell
                ? gotoCell.querySelector('.txn-open[data-cell]') : null;
            var gotoCellId = gotoOpen ? gotoOpen.dataset.cell : null;
            // Every row action carries the bare row name as `subject` so
            // filter() can score the query against the name alone.  The
            // verb-first labels greedily eat the leading letters of names
            // starting with g/o/t/r/w ("go to row" vs Groceries, Rent,
            // Water...), so a label-only score buries "Go to row" below
            // every mutating action for exactly those queries.
            actions.push({
                kind: 'goto',
                subject: name,
                label: 'Go to row -- ' + name,
                meta: 'grid',
                run: makeGotoRunner(gotoCell, gotoCellId),
            });

            var envelopeOpen = null;
            Array.prototype.forEach.call(cells, function (td, idx) {
                var open = td.querySelector('.txn-open[data-cell]');
                if (!open) return;
                var period = headers[idx] || '';
                var amount = amountText(open);

                var payBtn = td.querySelector('.paybtn');
                if (payBtn) {
                    actions.push({
                        kind: 'pay',
                        subject: name,
                        // Verb-first labels so the natural query ("pay
                        // gei") subsequence-matches from the start.
                        label: 'Pay -- ' + name + ' ' + amount,
                        meta: period,
                        run: makePayRunner(td, open.dataset.cell),
                    });
                }

                // Credit is a transaction-only affordance (a leg carries no
                // data-can-credit).
                if (open.dataset.canCredit) {
                    actions.push({
                        kind: 'credit',
                        subject: name,
                        label: 'Credit -- ' + name + ' ' + amount,
                        meta: period,
                        run: makeCreditRunner(td, open.dataset.cell),
                    });
                }

                actions.push({
                    kind: 'open',
                    subject: name,
                    label: 'Open -- ' + name,
                    meta: period,
                    run: makeOpenRunner(td, open.dataset.cell),
                });

                // Prefer the current period's cell for the per-row
                // add-purchase action.
                if (open.dataset.envelope
                    && (!envelopeOpen || td.classList.contains('cur'))) {
                    envelopeOpen = open;
                }
            });

            if (envelopeOpen) {
                actions.push({
                    kind: 'entry',
                    subject: name,
                    label: 'Add purchase -- ' + name,
                    meta: 'envelope',
                    run: makeAddPurchaseRunner(
                        envelopeOpen.closest('td'),
                        envelopeOpen.dataset.cell),
                });
            }
        });

        if (document.getElementById('anchor-display')) {
            actions.push({
                kind: 'anchor',
                label: 'Update anchor balance',
                meta: 'account',
                run: function () {
                    var anchor = document.getElementById('anchor-display');
                    if (!anchor) return;
                    // The anchor display sits in the page header above
                    // the grid; reveal it before opening the inline
                    // editor so the edit does not happen off-screen.
                    anchor.scrollIntoView({ block: 'center' });
                    anchor.click();
                },
            });
        }

        pushBankDoorActions();
    }

    /** Scroll a grid cell to the center of every scrollport (the grid
        wrapper and, when the page itself scrolls, the window) BEFORE
        acting on it.  buildActions indexes every rendered tbody row --
        including rows far outside the wrapper's visible region -- so
        without this, Pay/Credit swap a cell off-screen with zero visible
        feedback and Open anchors the action card to an off-screen rect.
        Center alignment also lands the cell clear of the sticky
        header/footer/label-column chrome that overlays the scrollport
        edges. */
    function revealCell(cellId, fallbackTd) {
        // Re-resolve the cell wrapper at run time (JS-14): the node captured
        // when the palette opened detaches if a swap (mark-paid, OOB entries
        // re-render) lands before Enter, so scrolling the stale node reveals
        // nothing.  The wrapper id (`data-cell`: txn-cell-<id>, or
        // xfer-leg-<transfer>-<account> for a transfer leg) survives every
        // swap shape; empty/anchor cells have no id and keep the captured
        // fallback node.
        var target = (cellId && document.getElementById(cellId))
            || fallbackTd;
        if (target) target.scrollIntoView({ block: 'center', inline: 'center' });
    }

    /** Re-resolve a cell control at RUN time from the cell's stable
        wrapper id.  The action index captures nodes when the palette
        opens; a swap landing before Enter (a mark-paid response, an
        entries OOB cell re-render) detaches them, and a click() on a
        detached node either no-ops silently (htmx guards on
        bodyContains) or never bubbles to the document-delegated
        handlers.  The wrapper id survives every swap shape -- both the
        innerHTML cell swaps and the OOB outerHTML re-render emit the
        same wrapper id. */
    function freshControl(cellId, selector) {
        var wrap = document.getElementById(cellId);
        return wrap ? wrap.querySelector(selector) : null;
    }

    function makeGotoRunner(td, cellId) {
        return function () {
            // Re-resolve the target cell when it carries an item (JS-14): the
            // node captured at palette-open detaches on a pre-Enter swap.
            // An empty goto cell has no stable id, but a mutation swap never
            // detaches an empty cell, so the captured node stays valid there.
            var wrap = cellId ? document.getElementById(cellId) : null;
            var cell = wrap ? wrap.closest('td') : td;
            revealCell(cellId, cell);
            // A bubbled click on the td (not on .txn-open) sets the
            // app.js cell cursor without opening the card.
            cell.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        };
    }

    function makePayRunner(td, cellId) {
        return function () {
            revealCell(cellId, td);
            var btn = freshControl(cellId, '.paybtn');
            if (btn) btn.click();
        };
    }

    function makeOpenRunner(td, cellId) {
        return function () {
            revealCell(cellId, td);
            var openEl = freshControl(cellId, '.txn-open[data-cell]');
            if (openEl) openEl.click();
        };
    }

    function makeCreditRunner(td, cellId) {
        return function () {
            revealCell(cellId, td);
            // Re-resolved at run time, as Pay and Open are: a swap before
            // Enter may have re-drawn the cell with other stamps, or with no
            // Credit at all.  The shared helper (app.js) presses or opens
            // the popover as the cell's stamps say (plan task plan#3114), so
            // what Credit does lives in one place (JS-19).
            var openEl = freshControl(cellId, '.txn-open[data-can-credit]');
            if (openEl) creditFromShortcut(openEl);
        };
    }

    /** Open the card on an envelope cell, then focus the add-purchase
        amount input once the lazy entries list has loaded (the add
        form is the last amount input in the popover). */
    function makeAddPurchaseRunner(td, cellId) {
        return function () {
            revealCell(cellId, td);
            var openEl = freshControl(cellId, '.txn-open[data-cell]');
            if (!openEl) return;
            openEl.click();
            var attempts = 0;
            var timer = setInterval(function () {
                attempts += 1;
                var popover = document.getElementById('txn-popover');
                var inputs = popover
                    ? popover.querySelectorAll('input[name="amount"]') : [];
                if (inputs.length) {
                    clearInterval(timer);
                    inputs[inputs.length - 1].focus();
                } else if (attempts > 30) {
                    clearInterval(timer);
                }
            }, 100);
        };
    }

    /** Subsequence fuzzy score; -1 = no match.  Consecutive runs and
        word starts score higher so "pay elec" beats scattered hits. */
    function score(query, text) {
        var q = query.toLowerCase();
        var t = text.toLowerCase();
        var ti = 0;
        var total = 0;
        var streak = 0;
        for (let qi = 0; qi < q.length; qi += 1) {
            const ch = q[qi];
            if (ch === ' ') { streak = 0; continue; }
            const found = t.indexOf(ch, ti);
            if (found === -1) return -1;
            total += 1;
            if (found === ti) { streak += 1; total += streak; }
            else { streak = 0; }
            if (found === 0 || t[found - 1] === ' ' || t[found - 1] === '-') total += 2;
            ti = found + 1;
        }
        return total;
    }

    var KIND_GLYPHS = {
        pay: '✓',
        credit: 'CC',
        open: '✎',
        goto: '⌖',
        entry: '+',
        anchor: '$',
    };

    /** Tie-break order for equal fuzzy scores.  A bare-name query ties
        every action of the matched row (they all hit via `subject`), so
        navigation must outrank the mutating actions: Enter on a typed
        row name must never silently mark a bill paid or open a card on
        it.  Verb-first queries ("pay gro") still rank Pay first because
        only its label matches, no tie. */
    var KIND_TIE_ORDER = {
        goto: 0,
        pay: 1,
        credit: 2,
        open: 3,
        entry: 4,
        anchor: 5,
    };
    // Below every known kind, so an action with an unmapped kind sorts
    // last instead of poisoning the comparator with NaN.
    var KIND_TIE_ORDER_FALLBACK = 99;

    function kindTieRank(action) {
        var rank = KIND_TIE_ORDER[action.kind];
        return rank === undefined ? KIND_TIE_ORDER_FALLBACK : rank;
    }

    function render() {
        resultsEl.textContent = '';
        if (!matches.length) {
            const empty = document.createElement('div');
            empty.className = 'cmdk-empty';
            empty.textContent = 'No matching commands in the visible window.';
            resultsEl.appendChild(empty);
            countEl.textContent = '';
            return;
        }
        matches.slice(0, MAX_RESULTS).forEach(function (action, idx) {
            var rowEl = document.createElement('div');
            rowEl.className = 'cmdk-row' + (idx === selected ? ' selected' : '');
            rowEl.setAttribute('role', 'option');
            rowEl.setAttribute('aria-selected', idx === selected ? 'true' : 'false');

            var icon = document.createElement('span');
            icon.className = 'cmdk-ic ' + action.kind;
            icon.textContent = KIND_GLYPHS[action.kind] || '·';

            var main = document.createElement('span');
            main.className = 'cmdk-main';
            main.textContent = action.label;

            var meta = document.createElement('span');
            meta.className = 'cmdk-meta';
            meta.textContent = action.meta;

            rowEl.appendChild(icon);
            rowEl.appendChild(main);
            rowEl.appendChild(meta);
            rowEl.addEventListener('click', function () { runAction(action); });
            resultsEl.appendChild(rowEl);
        });
        countEl.textContent = matches.length + ' match' + (matches.length === 1 ? '' : 'es');
    }

    function filter() {
        var query = input.value.trim();
        if (!query) {
            matches = actions.slice();
        } else {
            matches = actions
                .map(function (action) {
                    // Score the verb-first display string AND the bare
                    // row name, keeping the better hit.  Without the
                    // subject score, a name starting with g/o/t/r/w
                    // ("Rent", "Groceries"...) matches greedily inside
                    // the "Go to row" prefix, so navigation loses its
                    // streak bonuses and sinks below the row's ~12-18
                    // per-period Pay/Open entries -- out of the visible
                    // top results entirely.
                    var s = score(query, action.label + ' ' + action.meta);
                    if (action.subject) {
                        s = Math.max(s, score(query, action.subject));
                    }
                    return { action: action, s: s };
                })
                .filter(function (entry) { return entry.s >= 0; })
                .sort(function (a, b) {
                    if (b.s !== a.s) return b.s - a.s;
                    return kindTieRank(a.action) - kindTieRank(b.action);
                })
                .map(function (entry) { return entry.action; });
        }
        selected = 0;
        render();
    }

    function isOpen() {
        return palette && !palette.classList.contains('d-none');
    }

    function openPalette() {
        if (!palette || !gridTable()) return;
        buildActions();
        palette.classList.remove('d-none');
        scrim.classList.remove('d-none');
        input.value = '';
        filter();
        input.focus();
    }

    function closePalette() {
        if (!palette) return;
        palette.classList.add('d-none');
        scrim.classList.add('d-none');
        input.blur();
    }

    function runAction(action) {
        closePalette();
        action.run();
    }

    document.addEventListener('DOMContentLoaded', function () {
        palette = document.getElementById('cmdk');
        if (!palette) return;
        scrim = document.getElementById('cmdk-scrim');
        input = document.getElementById('cmdk-input');
        resultsEl = document.getElementById('cmdk-results');
        countEl = document.getElementById('cmdk-count');

        scrim.addEventListener('click', closePalette);

        var openBtn = document.getElementById('cmdk-open-btn');
        if (openBtn) openBtn.addEventListener('click', openPalette);

        input.addEventListener('input', filter);
        input.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                selected = Math.min(selected + 1,
                    Math.min(matches.length, MAX_RESULTS) - 1);
                render();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                selected = Math.max(selected - 1, 0);
                render();
            } else if (e.key === 'Enter') {
                e.preventDefault();
                if (matches[selected]) runAction(matches[selected]);
            } else if (e.key === 'Escape') {
                e.preventDefault();
                closePalette();
            }
        });
    });

    document.addEventListener('keydown', function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
            if (!palette || !gridTable()) return;
            e.preventDefault();
            if (isOpen()) closePalette();
            else openPalette();
        }
    });
})();
