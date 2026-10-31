"""Dump what every grid cell and mobile card SAYS, for a HEAD-vs-post diff.

The regression harness for plan step **balance:X-bi-6-1** (ruling
**R-BAL87**): the grid draws a transfer as a LEG read off its parent in
``budget.transfers`` instead of as the shadow row in ``budget.transactions``
it used to load.  None of the standing harnesses can see that change:
``verify_balance_baseline`` walks the ``balance_at`` seam, which this leaf does
not touch (no fold read changed); ``verify_render_surfaces`` reads status codes
and body sizes, so it can say ``/grid`` still renders and that its body grew,
and nothing about WHICH cells it drew or what each says.  Running only those
two over this leaf would report "nothing moved" beside a byte count, which is
the free-pass shape ``docs/plans/verification.md`` standard 3 asks about.

It answers *did anything a person reads move*, never *is the answer right*.
The proof that a leg's cell is right is the suite's controls; this is the
exhaustive regression check beside them.

**It captures CONTENT and deliberately not IDENTITY.**  The change is that a
transfer's cell is keyed by ``(transfer id, account id)`` rather than by a
shadow row's id, so every wrapper id, ``data-*`` attribute and door URL of
those cells moves BY DESIGN; a harness diffing raw HTML would drown the one
question in that noise.  So for every desktop cell it records the row label,
the period column, the opener's accessible label (the item's name and the
detail string: figure, recorded-versus-estimate, status, notes), the chip's
state class, and the three markers (transfer, override, account chip) plus
the due caption; for every mobile card, the card's accessible label and its
badges; and for the Plan tab's static rows, the label and figure.  Two runs
whose JSON is byte-identical drew the same words in the same cells, whatever
the cells were called.

**Where it is blind, stated so the diff is read correctly.**  Door URLs and
``hx-vals`` are identity and are not captured -- a cell whose Mark Paid
posts to the wrong door is this file's blind spot and the route tests' job.
The far-leg rule (``credit_card:R-CC23``) is exercised only where the
database holds a transfer between two members of one cash-flow set; on the
2026-09-20 production restore there is none, so that arm is the unit
tests' alone.

**Usage** (from the repository root, against the SAME database both times)::

    DATABASE_URL=postgresql://.../shekel_xbi6 \\
        .venv/bin/python tests/manual/verify_grid_cells.py before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel_xbi6 \\
        .venv/bin/python tests/manual/verify_grid_cells.py after.json
    diff before.json after.json

It drives the FIRST user in the database, which on a production clone is the
real owner, and renders ``/grid`` for every one of their accounts as the
balance line, at the default window, at ``?show_all=1``, and over the 26
paychecks BEFORE today (where every settled leg sits).
"""

import json
import pathlib
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so the repository root is added explicitly -- the same line every
# harness in this directory carries.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these imports must FOLLOW the
# ``sys.path`` line above, which is what makes this checkout's ``app`` the one
# imported at all.
# pylint: disable=wrong-import-position
from app import create_app  # noqa: E402
from app.extensions import db, login_manager  # noqa: E402
from app.models.account import Account  # noqa: E402
from app.models.user import User  # noqa: E402
from app.utils.session_helpers import login_session_stamps  # noqa: E402
# pylint: enable=wrong-import-position


def _classes(attrs) -> set:
    """Return the CSS classes an HTMLParser attribute list names.

    Args:
        attrs: The ``(name, value)`` pairs ``handle_starttag`` receives.

    Returns:
        The set of class names, empty when the tag has no ``class``.
    """
    return set((dict(attrs).get("class") or "").split())


class _GridCells(HTMLParser):
    """Walk one ``/grid`` page and collect what each cell and card says.

    A small state machine over the page's structure: ``<tr>`` rows of the
    grid table (their ``<th class="row-label">`` label, then one ``<td
    class="cell">`` per period column), the ``.txn-chip`` inside a cell and
    its ``.txn-open`` opener, and the mobile ``.mobile-txn-card`` list items.
    Everything recorded is text a person reads or a marker they see.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells: list[dict] = []
        self.cards: list[dict] = []
        self.static_rows: list[dict] = []
        self._row_label: str | None = None
        self._in_row_label = False
        self._col = -1
        self._cell: dict | None = None
        self._depth = 0
        self._cell_depth: int | None = None
        self._chip_depth: int | None = None
        self._card: dict | None = None
        self._card_depth: int | None = None
        self._capture_text_into: tuple[dict, str] | None = None
        self._text_depth: int | None = None
        self._static: dict | None = None
        self._static_depth: int | None = None

    # ── structure ────────────────────────────────────────────────────
    def handle_starttag(self, tag, attrs):
        self._depth += 1
        classes = _classes(attrs)
        attr = dict(attrs)
        if tag == "tr":
            self._row_label = None
            self._col = -1
        if tag == "th" and "row-label" in classes:
            self._in_row_label = True
            self._row_label = ""
            return
        if tag == "td" and "cell" in classes:
            self._col += 1
            self._cell_depth = self._depth
            return
        if "txn-chip" in classes and self._cell_depth is not None:
            self._cell = {
                "row": self._row_label,
                "col": self._col,
                "state": sorted(c for c in classes if c.startswith("st-")),
                "label": None,
                "transfer": False,
                "override": False,
                "account_chip": None,
                "due": None,
                "paybtn": False,
            }
            self._chip_depth = self._depth
            return
        if self._cell is not None:
            if "txn-open" in classes:
                self._cell["label"] = attr.get("aria-label", "").strip()
            if tag == "i" and attr.get("title") == "Transfer":
                self._cell["transfer"] = True
            if tag == "i" and attr.get("title") == "Overridden":
                self._cell["override"] = True
            if "paybtn" in classes:
                self._cell["paybtn"] = True
            if "flag-chip" in classes and self._chip_depth is None:
                self._capture_text_into = (self._cell, "account_chip")
                self._text_depth = self._depth
            if "cell-caption" in classes:
                self._capture_text_into = (self._cell, "due")
                self._text_depth = self._depth
        if tag == "li" and "mobile-txn-card" in classes:
            self._card = {
                "label": attr.get("aria-label", "").strip(),
                "badges": [],
                "transfer": False,
                "account_chip": None,
            }
            self._card_depth = self._depth
            self.cards.append(self._card)
            return
        if self._card is not None:
            if "badge-done" in classes or "badge-credit" in classes:
                self._card["badges"].append(
                    "done" if "badge-done" in classes else "credit",
                )
            if tag == "i" and "bi-arrow-left-right" in classes:
                self._card["transfer"] = True
            if "flag-chip" in classes:
                self._capture_text_into = (self._card, "account_chip")
                self._text_depth = self._depth
        # The Plan tab's static rows: a list item that is neither a card nor
        # a group header, holding a label span and a figure span.
        if (tag == "li" and "list-group-item" in classes
                and "mobile-txn-card" not in classes
                and "mobile-group-header" not in classes
                and "d-flex" in classes and self._card is None):
            self._static = {"text": ""}
            self._static_depth = self._depth
            self.static_rows.append(self._static)

    def handle_endtag(self, tag):
        if self._in_row_label and tag == "th":
            self._in_row_label = False
        if self._text_depth is not None and self._depth == self._text_depth:
            self._capture_text_into = None
            self._text_depth = None
        if self._chip_depth is not None and self._depth == self._chip_depth:
            # The account chip and due caption sit AFTER the chip, inside
            # the cell wrapper, so the cell stays open until its td closes.
            self._chip_depth = None
        if self._cell_depth is not None and self._depth == self._cell_depth:
            if self._cell is not None:
                self.cells.append(self._cell)
            self._cell = None
            self._cell_depth = None
        if self._card_depth is not None and self._depth == self._card_depth:
            self._card = None
            self._card_depth = None
        if self._static_depth is not None and self._depth == self._static_depth:
            self._static["text"] = " ".join(self._static["text"].split())
            self._static = None
            self._static_depth = None
        self._depth -= 1

    def handle_data(self, data):
        if self._in_row_label:
            self._row_label = (self._row_label or "") + data
        if self._capture_text_into is not None:
            target, key = self._capture_text_into
            target[key] = ((target[key] or "") + data).strip()
        if self._static is not None:
            self._static["text"] += data

    def snapshot(self) -> dict:
        """Return the page's content, whitespace-normalised, order kept."""
        for cell in self.cells:
            cell["row"] = " ".join((cell["row"] or "").split())
        return {
            "cells": self.cells,
            "cards": self.cards,
            "static_rows": self.static_rows,
        }


def _snapshot(client, route: str) -> dict:
    """Render *route* and return what its cells and cards say.

    Args:
        client: The Flask test client, already carrying a session.
        route: The ``/grid`` view to render.

    Returns:
        The parsed snapshot, or ``{"status": code}`` for a non-200 answer.
    """
    response = client.get(route)
    if response.status_code != 200:
        return {"status": response.status_code}
    parser = _GridCells()
    parser.feed(response.get_data(as_text=True))
    return parser.snapshot()


def main(out_path):
    """Write the snapshot for every grid view to *out_path*."""
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    # The probe FORGES a session rather than posting the login form, for the
    # reason ``verify_render_surfaces`` gives: it has no password for the
    # database it is pointed at, and every route below is a GET.
    login_manager.session_protection = None
    with app.app_context():
        user = db.session.query(User).order_by(User.id).first()
        if user is None:
            raise SystemExit("no user in this database; nothing to render")
        user_id = user.id
        account_ids = [
            row.id for row in
            db.session.query(Account).filter_by(user_id=user_id)
            .order_by(Account.id).all()
        ]
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["_id"] = None
        # The stamps a sign-in writes: ``load_user`` refuses a session without
        # them (plan#2173).
        session.update(login_session_stamps(datetime.now(timezone.utc)))
    snapshot = {}
    # Three windows per account: the default (today forward), the same with
    # every row shown, and a PAST window -- a settled transfer's legs sit in
    # paychecks already paid, which the default window never draws, so a run
    # without this third view would grade no settled leg at all (the
    # 2026-09-20 restore holds 38, every one in the past).
    for account_id in account_ids:
        for suffix in ("", "&show_all=1", "&periods=26&offset=-26"):
            route = f"/grid?account_id={account_id}{suffix}"
            snapshot[route] = _snapshot(client, route)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2, sort_keys=True)
    cells = sum(len(v.get("cells", [])) for v in snapshot.values())
    cards = sum(len(v.get("cards", [])) for v in snapshot.values())
    legs = sum(
        1 for v in snapshot.values() for c in v.get("cells", []) if c["transfer"]
    )
    print(
        f"wrote {out_path}: {len(snapshot)} views, {cells} cells "
        f"({legs} transfer cells), {cards} cards"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_grid_cells.py OUT.json")
    main(sys.argv[1])
