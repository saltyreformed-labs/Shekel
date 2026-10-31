"""Read the reconcile's pages as the owner reads them, for the reconcile rehearsal.

Task plan#2452 moved these here from ``reconcile_rehearsal.py`` when that file
passed pylint's 1,000-line ceiling: they are the rehearsal's readers of what a
page SHOWS, and they parse HTML and nothing else -- no request, no database, no
run -- so they are one seam apart from the acts that drive the doors.  Each
reads one thing a browser renders: where a Reconcile card's MATCH pane stands
and where it posts (:class:`PaneSpan`), what the pane lists (:class:`PaneRows`),
what presses the card's OK (:class:`OkPresses`), and how the grid names a row
(:class:`GridRows`).

A script beside this one imports it by name (``from reconcile_pages import
PaneRows``), because Python puts a script's own folder first on ``sys.path``.
"""

from html.parser import HTMLParser

from app.services.statement_match import Verb


class PaneSpan(HTMLParser):
    """Find where one card's MATCH pane stands in a page, and where it posts.

    **The pane is what htmx swaps, start tag to end tag**: the card's one
    element of class ``rec-match``, which is first the placeholder the page
    renders (``verb_pane`` in ``accounts/_statement_reconcile_macros.html``)
    and then each pane that replaces it (``accounts/_statement_reconcile_match
    .html``), every one swapped ``outerHTML``.  The first ``hx-post`` inside it
    is where each sends: the placeholder's own, then the pane's
    ``rec-match-picks``.

    Attributes:
        line_id: The bank line the card is about.
        span: ``(start, end)`` of the pane in the page's text, ``end`` ``None``
            until its end tag is read; ``None`` while no pane is found.
        fetch: The URL the pane posts to, or ``None``.
    """

    def __init__(self, line_id: int, html: str):
        super().__init__(convert_charrefs=True)
        self.line_id = line_id
        self.span: "tuple[int, int | None] | None" = None
        self.fetch: "str | None" = None
        self._html = html
        # Lines counted as the parser counts them, at each "\n" only
        # (``_markupbase.ParserBase.updatepos``): ``splitlines`` also breaks at
        # "\r", "\f", " " and others, which a row's name can carry, and
        # would put every offset after one a line early.
        self._lines = [0]
        for line in html.split("\n")[:-1]:
            self._lines.append(self._lines[-1] + len(line) + 1)
        self._in_card = False
        self._divs = 0
        self.feed(html)
        self.close()

    def _at(self) -> int:
        """Return where the tag being read starts, in the page's text."""
        line, column = self.getpos()
        return self._lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        """Enter the card, start its pane, count a div inside it, or take where it posts."""
        attr = dict(attrs)
        if tag == "details":
            self._in_card = attr.get("id") == f"rec-card-{self.line_id}"
        elif tag == "div" and self._divs:
            self._divs += 1
        elif tag == "div" and self._in_card and self.span is None \
                and "rec-match" in (attr.get("class") or "").split():
            self.span, self._divs = (self._at(), None), 1
        if tag == "div" and self._divs and self.fetch is None and attr.get("hx-post"):
            self.fetch = attr["hx-post"]

    def handle_endtag(self, tag):
        """Leave the card, or close a div of the pane and, with its last, the pane."""
        if tag == "details":
            self._in_card = False
        elif tag == "div" and self._divs:
            self._divs -= 1
            if not self._divs:
                self.span = (self.span[0], self._html.index(">", self._at()) + 1)


class PaneRows(HTMLParser):
    """Read one MATCH pane as the owner reads it: the rows it lists, and whether it adds up.

    **The sheet names a row in the pane's own words**, because the owner finds
    it by reading the pane: its name, the days printed under it and its figure
    (the ``pick`` macro's ``rec-row-name``, each ``rec-row-day`` and
    ``rec-row-figure``).  A row another shares words with is one the owner
    cannot tell apart, so :meth:`words_of` refuses it.

    Attributes:
        line_id: The bank line the card is about.
        rows: Each row the pane lists, in its order:
            ``{"token", "checked", "name", "days", "figure"}``.
        adds_up: Whether the pane carries the figure it was priced at as a
            hidden field: its arm for ticked rows that come to the bank's
            figure, which leaves nothing to choose.  The placeholder carries a
            proposal's figure hidden too, unpriced, so only a loaded pane's
            counts.
    """

    #: The ``pick`` macro's spans, and the field each one's words go to.
    _WORDS = {"rec-row-name": "name", "rec-row-day": "days", "rec-row-figure": "figure"}

    def __init__(self, line_id: int, pane: str):
        super().__init__(convert_charrefs=True)
        self.line_id = line_id
        self.rows: "list[dict]" = []
        self.adds_up = False
        self._waiting: "bool | None" = None
        self._spans = 0
        self._words: "tuple[str, int, list[str]] | None" = None
        self.feed(pane)
        self.close()

    def handle_starttag(self, tag, attrs):
        """Note what the pane is, record a row's box or a hidden figure, or start words."""
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        if tag == "div" and self._waiting is None:
            self._waiting = "rec-match-waiting" in classes
        elif tag == "input" and attr.get("name") == f"rows-{self.line_id}" \
                and attr.get("type") == "checkbox":
            self.rows.append({"token": attr["value"], "checked": "checked" in attr,
                              "name": "", "days": [], "figure": ""})
        elif tag == "input" and attr.get("name") == f"consent-{self.line_id}" \
                and attr.get("type") == "hidden" and not self._waiting:
            self.adds_up = True
        elif tag == "span":
            self._spans += 1
            field = next((self._WORDS[name] for name in classes if name in self._WORDS), None)
            if field and self.rows and self._words is None:
                self._words = (field, self._spans, [])

    def handle_data(self, data):
        """Keep the words of a row's span."""
        if self._words is not None:
            self._words[2].append(data)

    def handle_endtag(self, tag):
        """Close a row's span, keeping its words."""
        if tag != "span" or not self._spans:
            return
        if self._words is not None and self._words[1] == self._spans:
            field, _, text = self._words
            words = " ".join("".join(text).split())
            if field == "days":
                self.rows[-1]["days"].append(words)
            else:
                self.rows[-1][field] = words
            self._words = None
        self._spans -= 1

    def words_of(self, row: dict) -> str:
        """Return *row* as the sheet names it: its name, its days, its figure, as printed.

        Raises:
            AssertionError: When another row the pane lists prints the same words.
        """
        def words(each: dict) -> str:
            return f"\"{each['name']}\", {', '.join(each['days'])}, {each['figure']}"
        alike = [each["token"] for each in self.rows if words(each) == words(row)]
        assert alike == [row["token"]], \
            f"the MATCH pane of line {self.line_id} prints {words(row)} for {alike}"
        return words(row)


class OkPresses(HTMLParser):
    """Read what presses one card's OK, off the card as rendered.

    **Never assumed**: the summary's own OK (``rec-ok-btn``) is rendered only
    where the card offers one (``offers_ok``), and every Transfers card has
    none, because its sentence opens on TRANSFER, a verb with no door; the
    opened card's foot button named after the MATCH verb (``rec-cta``) is on
    every card a match is open on.  Both are labels for the card's one hidden
    OK checkbox (``accounts/_statement_reconcile_macros.html``), so a second
    press of either takes the OK back.

    Attributes:
        line_id: The bank line the card is about.
        summary: The summary's OK button's words, or ``None``.
        panel: The opened card's MATCH button's words, or ``None``.
    """

    def __init__(self, line_id: int, html: str):
        super().__init__(convert_charrefs=True)
        self.line_id = line_id
        self.summary: "str | None" = None
        self.panel: "str | None" = None
        self._label: "tuple[str, list[str]] | None" = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        """Enter a label for the card's OK box: the summary's, or the MATCH button."""
        attr = dict(attrs)
        if tag != "label" or attr.get("for") != f"ok-{self.line_id}":
            return
        classes = (attr.get("class") or "").split()
        if "rec-ok-btn" in classes:
            self._label = ("summary", [])
        elif "rec-cta" in classes and attr.get("data-verb") == Verb.MATCH.value:
            self._label = ("panel", [])

    def handle_data(self, data):
        """Keep the words of the label being read."""
        if self._label is not None:
            self._label[1].append(data)

    def handle_endtag(self, tag):
        """Close the label being read, keeping its words."""
        if tag == "label" and self._label is not None:
            setattr(self, self._label[0], " ".join("".join(self._label[1]).split()))
            self._label = None

    def words(self) -> str:
        """Return what the owner presses, once, to OK this card, as the sheet says it.

        Raises:
            AssertionError: When the opened card renders no MATCH button for its OK.
        """
        assert self.panel, f"the card of line {self.line_id} renders no MATCH button"
        if self.summary:
            return (f"{self.summary} on the card's summary line, once, NOT also "
                    f"{self.panel} at the foot of the opened card: both press one box, "
                    "so a second press takes the OK back")
        return (f"{self.panel} at the foot of the opened card, once: this card's summary "
                "line shows no OK, the button shows no pressed state, and a second press "
                "takes it back; the Apply button then reads \"Apply 1 of the cards you OK'd\"")


class GridRows(HTMLParser):
    """Read the grid as the owner reads it: each row's label, and the headers above it.

    **The grid labels a row its own way** (``grid_view_service``'s row keys):
    a recurring row by its template's CURRENT name and any other by its own,
    with "Transfer to ", "Transfer from " and "CC Payback: " stripped.  So a
    payback row reads as what it pays back: measured 2026-10-31, "Groceries",
    "Gas" and "Kayla's Spending Money" each head a row in the Family or Auto
    section and another in the Credit Card section on nine of the sixteen
    pages Checking's acts are found on.  **A label is told apart only with the
    headers above it** (``grid/grid.html``): its group's
    (``group-header-row``), which an "Income" or "Expenses" group does not
    print, and, where the PAGE holds two rows or more of its category, the
    category's (``item-header-row``).  So the headers are a fact of the page
    and not of the row.  The row's ``title`` names its group and category
    always, which is how a row is placed under a header or past its run.

    Attributes:
        rows: Each row in page order: ``{"label", "group", "item", "cells"}``,
            its label, the group and category headers above it on screen (or
            ``None``), and the ``txn-cell-`` ids of the cells in it.
    """

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.rows: "list[dict]" = []
        self._group: "str | None" = None
        self._item: "str | None" = None
        self._header: "tuple[str, list[str]] | None" = None
        self._label: "tuple[str, list[str]] | None" = None
        self._in_row = False
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        """Start a section, a header row, a row's label, or record a cell in the row."""
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        if tag == "tbody":
            self._group = self._item = None
        elif tag == "tr":
            kind = next((name for name in ("group-header-row", "item-header-row")
                         if name in classes), None)
            self._header = (kind, []) if kind else None
            self._label, self._in_row = None, False
        elif tag == "th" and attr.get("scope") == "row" and "row-label" in classes:
            self._label = (attr.get("title") or "", [])
        elif tag == "div" and self._in_row and (attr.get("id") or "").startswith("txn-cell-"):
            self.rows[-1]["cells"].append(attr["id"])

    def handle_data(self, data):
        """Keep a header's or a row label's words."""
        for held in (self._header, self._label):
            if held is not None:
                held[1].append(data)

    def handle_endtag(self, tag):
        """Close a header row, keeping its words; a row's label, keeping the row; or a row."""
        if tag == "tr" and self._header is not None:
            words = " ".join("".join(self._header[1]).split())
            if self._header[0] == "group-header-row":
                self._group, self._item = words, None
            else:
                self._item = words
            self._header = None
        elif tag == "tr":
            self._in_row = False
        elif tag == "th" and self._label is not None:
            self._keep_row()
            self._label, self._in_row = None, True

    def _keep_row(self) -> None:
        """Keep the row whose label just closed, with the headers above it on screen.

        The last group header printed is this row's only when its ``title``
        opens with it: an unprinted group's rows sit under the one before.
        The last category header is this row's only while its ``title`` ends
        with it: a category run ends at the first row of another.
        """
        title, text = self._label
        group = self._group if self._group is not None \
            and title.startswith(f"{self._group}: ") else None
        if self._item is not None and not title.endswith(f": {self._item}"):
            self._item = None
        self.rows.append({"label": " ".join("".join(text).split()), "group": group,
                          "item": self._item, "cells": []})

    def words_of(self, txn_id: int) -> str:
        """Return the row holding row *txn_id*'s cell, as FIND names it.

        **Every row the words could name is counted, not only rows printing
        the same words**: a row named with no section (an "Income" row) is
        also every row of its label in a section, and the owner reading the
        words could take either.

        Raises:
            AssertionError: When no row, or more than one, holds the cell, or
                when the words name another row on the page too.
        """
        cell = f"txn-cell-{txn_id}"
        mine = [row for row in self.rows if cell in row["cells"]]
        assert len(mine) == 1, f"{len(mine)} grid rows hold the cell of row {txn_id}"
        row = mine[0]
        words = f"\"{row['label']}\""
        if row["group"] is not None:
            words += f" in the \"{row['group']}\" section"
        if row["item"] is not None:
            words += f", under \"{row['item']}\""
        named = [each for each in self.rows if each["label"] == row["label"]
                 and row["group"] in (None, each["group"]) and row["item"] in (None, each["item"])]
        assert len(named) == 1, f"{len(named)} grid rows are {words}"
        return words
