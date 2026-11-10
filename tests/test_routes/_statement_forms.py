"""What a BROWSER would submit from a statement form, read off the page itself.

Plan step ``bank_import:X-gf-2`` moved this out of
``test_statement_matches.py`` so the merchant-rule control's two surfaces could
share one reader.

**A browser submits every control it renders, at the value it renders**, and
that is the fact a hand-written payload cannot check: it is written by the same
person as the template, so the two agree about a mistake as readily as about
the truth.  This arc has paid for that twice -- a hand-picked subset shipped a
destination arm that was DEAD in a browser at plan step X-f6a-3b, found by
three adversarial reviews.

**IT IS ALL READERS NOW.**  It carried payload BUILDERS too -- ``match_item``,
``hand_match``, ``record_line``, ``rule_item`` and ``one_pass`` -- which spelled
the field names the review queue's and the workbench's forms emitted.  Plan
step ``bank_import:X-gi-2`` deleted both pages and the three test modules that
called them, leaving every one of those five with no caller, so they went in
the same commit rather than being left as a shape a later author would copy.
The Reconcile page is scraped rather than spelled
(:class:`ReconcileFormReader`), which is the stronger form of the same rule.
"""

from html.parser import HTMLParser


class RuleFormReader(HTMLParser):
    """Collect the rule form's controls and their RENDERED values.

    **A browser submits every control it renders, at the value it renders**, and
    that is the fact a hand-written payload cannot check -- it is written by the
    same person as the template, so the two agree about a mistake as readily as
    about the truth.  This reads the page instead.

    A ``<select>`` submits the option carrying ``selected``, and its FIRST
    option when none does; an ``<input>`` submits its ``value``.  Only controls
    whose name begins with ``rule`` are collected, because a page carrying this
    control carries other forms beside it and a browser posts one at a time.
    """

    def __init__(self, prefixes=("rule",)):
        super().__init__()
        self.prefixes = prefixes
        self.controls = {}
        self._select = None
        self._first = None

    def _mine(self, name):
        """Return whether *name* belongs to the form being read."""
        return any(name.startswith(prefix) for prefix in self.prefixes)

    def handle_starttag(self, tag, attrs):
        """Record an input's value, or open a select and read its options."""
        attributes = dict(attrs)
        name = attributes.get("name", "")
        if tag == "input" and self._mine(name):
            if attributes.get("type") == "checkbox" and (
                "checked" not in attributes
            ):
                # An unticked checkbox submits NOTHING, which is the whole
                # point of the default this screen rests on.
                return
            self.controls[name] = attributes.get("value", "")
        elif tag == "select" and self._mine(name):
            self._select, self._first = name, None
        elif tag == "option" and self._select is not None:
            value = attributes.get("value", "")
            if self._first is None:
                self._first = value
            if "selected" in attributes:
                self.controls[self._select] = value

    def handle_endtag(self, tag):
        """Close a select, defaulting it to its first option if none was set."""
        if tag == "select" and self._select is not None:
            self.controls.setdefault(self._select, self._first or "")
            self._select = None


def rule_form_controls(page):
    """Return what a browser would submit for the merchant-rule form."""
    reader = RuleFormReader()
    reader.feed(page)
    return reader.controls


class ReconcileFormReader(HTMLParser):
    """Collect the RECONCILE page's controls, exactly as a browser would.

    Plan step ``bank_import:X-gj-1b``.  **A browser submits every control it
    renders, at the value it renders**, and that is the fact a hand-written
    payload cannot check -- it is written by the same person as the template,
    so the two agree about a mistake as readily as about the truth.  This arc
    has paid for that twice.

    **It keeps REPEATED names**, unlike :class:`RuleFormReader`: ``ok`` is
    rendered once per OK'd card and ``rows-<line>`` once per member row, and a
    group is exactly where a multi-value defect hides.

    **An unticked checkbox and an unchecked radio contribute NOTHING**, which
    is the whole of what makes ruling **R-HS**'s *an untouched card is not
    submitted* structural rather than a default: a page rendered with no card
    OK'd posts no ``ok`` at all, so no act can be built from it.

    **A ``disabled`` control is dropped too**, which the consent box depends
    on: the MATCH pane renders it ``value=""`` and ``disabled`` in lockstep
    until the server has a figure, so a browser cannot send an empty consent.

    **It also records what a browser COULD submit, not only what it would.**
    :attr:`offerable` holds every control the page rendered -- an unticked
    checkbox and an unchecked radio included -- as the ``(name, value)`` pair
    that control would send if the owner pressed it, or as ``(name, None)``
    for a field whose value the owner supplies.  That set is what
    :func:`~tests.test_routes.test_statement_reconcile._post` refuses a
    payload against, and it exists because of a defect a green suite hid:
    every acting case appended ``("ok", str(line.id))`` by hand, and the
    ``ok-<line>`` checkbox is rendered only for a card that suggests a working
    verb -- so 31 of the developer's 248 cards had a panel button pointing at
    an element not in the document, and no test could see it because the
    tests posted a value no browser could produce.

    **The pair and not the name**: ``ok`` is ONE name shared by every card,
    keyed by its VALUE, so a check that only asked whether the page renders
    ``ok`` anywhere would pass the very payload that hid the defect.
    """

    def __init__(self):
        super().__init__()
        self.fields = []
        self.offerable = set()
        self._select = None
        self._first = None
        self._chosen = None
        self._textarea = None
        self._typed = []

    def handle_starttag(self, tag, attrs):
        """Record every control this page would submit."""
        attributes = dict(attrs)
        # **An ``<option>`` carries no name of its own**, so it is read before
        # any test on one -- a first version guarded on ``name`` up here and
        # every select therefore submitted its FIRST option, which read as a
        # template that had stopped pre-filling.  Found by running it.
        if tag == "option":
            if self._select is not None:
                value = attributes.get("value", "")
                if self._first is None:
                    self._first = value
                if "selected" in attributes:
                    self._chosen = value
            return
        name = attributes.get("name", "")
        if not name or "disabled" in attributes:
            return
        if tag == "input":
            kind = attributes.get("type", "text")
            value = attributes.get("value", "")
            # **What this control COULD send.**  A checkbox or radio sends its
            # own value or nothing, so the pair is what a browser can produce;
            # any other field carries whatever the owner types, so only the
            # name is fixed.
            self.offerable.add(
                (name, value) if kind in {"checkbox", "radio"}
                else (name, None)
            )
            if kind in {"checkbox", "radio"} and "checked" not in attributes:
                return
            self.fields.append((name, value))
        elif tag == "select":
            self.offerable.add((name, None))
            self._select, self._first, self._chosen = name, None, None
        elif tag == "textarea":
            # **A ``<textarea>`` submits its CONTENT**, the text between its
            # tags rather than a ``value`` attribute -- the empty string when
            # it renders empty, as the bank feed panel's setup-token box
            # does (plan step ``bank_import:X-f6b-2``).  The reader was
            # blind to the tag until then, so a form carrying one scraped
            # as if it were not rendered at all.
            self.offerable.add((name, None))
            self._textarea, self._typed = name, []

    def handle_data(self, data):
        """Accumulate an open textarea's content."""
        if self._textarea is not None:
            self._typed.append(data)

    def handle_endtag(self, tag):
        """Close a select or a textarea, submitting what a browser would."""
        if tag == "select" and self._select is not None:
            chosen = self._chosen
            self.fields.append(
                (self._select, self._first or "" if chosen is None else chosen),
            )
            self._select = self._first = self._chosen = None
        elif tag == "textarea" and self._textarea is not None:
            self.fields.append((self._textarea, "".join(self._typed)))
            self._textarea, self._typed = None, []


class _TriggerSubtreeReader(HTMLParser):
    """Collect the control names inside the element that carries ``hx-trigger``.

    Plan step ``bank_import:X-gj-1b``.  **Containment is a DEPTH question and
    string indices cannot answer it.**  A first version of the case that uses
    this sliced from the tag carrying ``hx-trigger`` to the next ``</div>``,
    which closes whichever nested element came first -- so the slice stopped
    long before the consent control and the assertion passed whatever the
    markup said.  Measured by mutation on 2026-08-30: moving the consent box
    back inside the trigger left the case GREEN.

    This tracks the open-element depth instead, so what it reports is the
    element's real subtree.
    """

    #: Tags that never carry an end tag, so a depth counter must not count
    #: them.  **Without this the counter only ever goes up**: ``<input>`` is
    #: the most common tag in these fragments and every one of them left the
    #: subtree looking one level deeper than it was, so the trigger element
    #: never appeared to close and everything after it read as inside.
    #: Measured by mutation 2026-08-30 -- the case using this passed with the
    #: consent box on either side of the boundary.
    _VOID = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    })

    def __init__(self):
        super().__init__()
        self.inside = set()
        self._depth = None

    def handle_starttag(self, tag, attrs):
        """Open a tag, arming on the one that carries ``hx-trigger``."""
        attributes = dict(attrs)
        if self._depth is not None:
            name = attributes.get("name")
            if name:
                self.inside.add(name)
            if tag not in self._VOID:
                self._depth += 1
        elif "hx-trigger" in attributes and tag not in self._VOID:
            self._depth = 0

    def handle_endtag(self, tag):
        """Close a tag, disarming when the trigger's own element closes."""
        if self._depth is None:
            return
        if self._depth == 0:
            self._depth = None
        else:
            self._depth -= 1


def controls_inside_the_trigger(fragment):
    """Return the control names inside the element carrying ``hx-trigger``.

    Args:
        fragment: The rendered fragment, as text.

    Returns:
        The set of ``name`` attributes in that element's subtree.

    Raises:
        AssertionError: When the fragment carries no ``hx-trigger`` at all,
            which would make every containment claim over it vacuous.
    """
    assert "hx-trigger" in fragment, (
        "this fragment carries no hx-trigger, so nothing about what its "
        "subtree contains can be graded"
    )
    reader = _TriggerSubtreeReader()
    reader.feed(fragment)
    return reader.inside


def reconcile_form_fields(page):
    """Return what a browser would submit from the Reconcile page, verbatim.

    Args:
        page: The rendered page or body, as text.

    Returns:
        A list of ``(name, value)`` pairs, repeats kept and in document order.
    """
    reader = ReconcileFormReader()
    reader.feed(page)
    return reader.fields


class _OneFormReader(ReconcileFormReader):
    """Collect only the controls inside the FORM whose action matches.

    Plan step ``bank_import:X-gj-1b``.  The Reconcile page carries two forms --
    the cards and the standing-rule offer -- and
    :func:`reconcile_form_fields` scrapes the whole document, which a browser
    never submits.  A case posting the union to one door passes for the right
    reason only by accident: it would keep passing if the offer form dropped
    its own ``csrf_token`` or ``tab``, because the cards form carries both.
    """

    def __init__(self, action, attribute="action"):
        super().__init__()
        self._action = action
        self._attribute = attribute
        self._depth = None

    def handle_starttag(self, tag, attrs):
        """Collect only while inside the form named by *action*."""
        if tag == "form":
            if self._action in dict(attrs).get(self._attribute, ""):
                self._depth = 0
            return
        if self._depth is None:
            return
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        """Leave the form, and let the base class close its selects."""
        if tag == "form":
            self._depth = None
            return
        if self._depth is not None:
            super().handle_endtag(tag)


def form_fields(page, action, *, attribute="action"):
    """Return what a browser would submit from ONE form on *page*.

    Args:
        page: The rendered page or body, as text.
        action: A substring of the form's *attribute*, naming which form.
        attribute: The form attribute that names it -- ``action`` for a
            plain form, ``hx-patch`` for the grid popover's HTMX form (plan
            step ``balance:X-bi-7b``, whose door tests post what that card
            renders).

    Returns:
        Its ``(name, value)`` pairs, repeats kept, in document order.

    Raises:
        AssertionError: When no form on the page has that attribute value,
            which would make every assertion over the result vacuous.
    """
    assert f'{attribute}="' in page and action in page, (
        f"no form on this page has a {attribute} containing {action!r}, so "
        f"a payload scraped from it would be empty"
    )
    reader = _OneFormReader(action, attribute)
    reader.feed(page)
    return reader.fields


def reconcile_offerable(page):
    """Return every control the Reconcile page rendered, pressed or not.

    The universe a browser's submission is drawn from, as
    :attr:`ReconcileFormReader.offerable` builds it: ``(name, value)`` for a
    checkbox or radio, which can only ever send its own value, and
    ``(name, None)`` for a field whose value the owner supplies.

    Args:
        page: The rendered page or body, as text.

    Returns:
        The set.
    """
    reader = ReconcileFormReader()
    reader.feed(page)
    return reader.offerable
