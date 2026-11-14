"""The reader of what a rendered form posts, as a browser posts it.

Moved out of ``tests/_test_helpers.py`` (task plan#3125), which is over the
1,000-line ceiling (ruling R-IR), so the reader can grow beside the tests that
read it.  :func:`rendered_form_controls` is what a route test posts instead of
a hand-picked payload; :class:`FormControls` is the parser, which a package
that needs a browser's other rules subclasses
(``tests/test_routes/test_template_amount_history/_helpers.py``).
"""

from html.parser import HTMLParser


class FormControls(HTMLParser):
    """Every control of the ONE form posting to *action*, as a browser submits it.

    Repeated names (the stub form's one-off rows) keep every value in order;
    an ``<input>`` submits its ``value`` and a ``<select>`` its ``selected``
    option, else its first, and a select with no option submits nothing.  A
    checkbox or radio submits its ``value`` (``on`` when it states none) only
    while ``checked``, and every one, checked or not, is listed in
    :attr:`choices`, so a test can tick one the form renders and never one it
    does not (task plan#3125, the raise form's Recur box and end-year
    answer).  A control without a name submits nothing.  It reads only those
    shapes: any other control inside the form -- an input of any type but
    hidden, text, number, date, checkbox or radio, a ``<textarea>``, a named
    button, a disabled input, a disabled or multiple select, a disabled
    option or an option without a ``value`` in a named select (a browser
    posts such an option's text), or a disabled ``<fieldset>`` or
    ``<optgroup>`` -- fails the reading loudly rather than being posted as a
    browser would not post it.  A disabled fieldset fails whole, even a
    control in its first legend, which a browser does post: the safe side.
    The reader of the pay stub form's and the stub setting's route suites
    (plan step salary:S11-c-2b, its delta review's LOW-3 and LOW-4).
    """

    #: Input types a browser posts by their ``value`` alone.
    _PLAIN_INPUTS = frozenset({"hidden", "text", "number", "date"})
    #: Input types a browser posts only while ``checked``.
    _CHECKED_INPUTS = frozenset({"checkbox", "radio"})

    def __init__(self, action):
        super().__init__()
        self.action = action
        self.controls: "dict[str, list[str]]" = {}
        #: Every checkbox's and radio's value by name, checked or not.
        self.choices: "dict[str, list[str]]" = {}
        self._inside = False
        self._select = None
        self._chosen = None

    def handle_starttag(self, tag, attrs):
        """Open the form, record an input, or read a select's options."""
        attributes = dict(attrs)
        if tag == "form":
            self._inside = attributes.get("action") == self.action
        elif self._inside and tag in ("textarea", "button") and attributes.get("name"):
            raise AssertionError(f"the reader does not post a named <{tag}>")
        elif self._inside and tag in ("fieldset", "optgroup") and "disabled" in attributes:
            raise AssertionError(f"the reader does not post a disabled <{tag}>")
        elif self._inside and tag == "input" and attributes.get("name"):
            kind = attributes.get("type", "text")
            if kind in self._CHECKED_INPUTS and "disabled" not in attributes:
                self._read_checked_input(attributes)
                return
            if kind not in self._PLAIN_INPUTS or "disabled" in attributes:
                raise AssertionError(f"the reader does not post {attributes}")
            self.controls.setdefault(attributes["name"], []).append(attributes.get("value") or "")
        elif self._inside and tag == "select":
            if "disabled" in attributes or "multiple" in attributes:
                raise AssertionError(f"the reader does not post {attributes}")
            self._select, self._chosen = attributes.get("name"), None
        elif self._select is not None and tag == "option":
            if "value" not in attributes or "disabled" in attributes:
                raise AssertionError(f"the reader does not post the option {attributes}")
            if self._chosen is None or "selected" in attributes:
                self._chosen = attributes["value"]

    def _read_checked_input(self, attributes):
        """List a checkbox or radio among the choices, and post it if checked."""
        value = attributes.get("value", "on")
        self.choices.setdefault(attributes["name"], []).append(value)
        if "checked" in attributes:
            self.controls.setdefault(attributes["name"], []).append(value)

    def handle_endtag(self, tag):
        """Close a select, submitting its choice, or close the form."""
        if tag == "select" and self._select is not None:
            if self._chosen is not None:
                self.controls.setdefault(self._select, []).append(self._chosen)
            self._select = None
        elif tag == "form":
            self._inside = False


def rendered_form_controls(html, action):
    """What the form posting to *action* submits, read off the rendered *html*.

    A route test posts this rather than a hand-picked payload, so a control
    the template renders is posted as a browser posts it.

    Args:
        html: A rendered page.
        action: The form's ``action``, as the page renders it.

    Returns:
        ``{name: [value, ...]}``, a repeated name's values in order.
    """
    reader = FormControls(action)
    reader.feed(html)
    assert reader.controls, f"no form posts to {action}"
    return reader.controls


def rendered_form_choices(html, action):
    """The checkboxes and radios of the form posting to *action*, checked or not.

    The half of a form :func:`rendered_form_controls` cannot show: a browser
    posts an unticked box as nothing, so a test that ticks one asks this
    first that the form renders it, with the value it would post.

    Args:
        html: A rendered page.
        action: The form's ``action``, as the page renders it.

    Returns:
        ``{name: [value, ...]}``: each box's value, a radio group's in order.
    """
    reader = FormControls(action)
    reader.feed(html)
    assert reader.controls, f"no form posts to {action}"
    return reader.choices


class EnabledFormControls(FormControls):
    """The shared form reader, leaving out a disabled control as a browser does.

    A browser posts no disabled ``<input>`` or ``<select>``, and some edit
    forms render them: a loan's standing payment's destination (ruling
    **R-R76**) and the recurrence controls the loan locks, and a card's
    worked-out payment's destination (task plan#4046).  :class:`FormControls`
    fails on any disabled control rather than model one.  This models those
    two shapes exactly: the control is not posted, and nor are a disabled
    select's options.  A disabled ``<fieldset>`` or ``<optgroup>`` still
    fails, since a browser's rule there has an exception (a fieldset's first
    legend) these forms do not need.  Moved here from
    ``tests/test_routes/test_template_amount_history/_helpers.py`` when a
    second package came to read it (task plan#4046).
    """

    def handle_starttag(self, tag, attrs):
        """Skip a disabled input or select in the form; read the rest as the shared one does."""
        if self._inside and tag in ("input", "select") and "disabled" in dict(attrs):
            return
        super().handle_starttag(tag, attrs)


def enabled_form_controls(html, action):
    """What the form posting to *action* submits, read off *html*, disabled controls left out.

    Args:
        html: A rendered page.
        action: The form's ``action``, as the page renders it.

    Returns:
        ``{name: [value, ...]}``, a repeated name's values in order.
    """
    reader = EnabledFormControls(action)
    reader.feed(html)
    assert reader.controls, f"no form posts to {action}"
    return reader.controls


__all__ = [
    "EnabledFormControls",
    "FormControls",
    "enabled_form_controls",
    "rendered_form_choices",
    "rendered_form_controls",
]
