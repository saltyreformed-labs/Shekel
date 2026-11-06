"""What a rendered recurrence form shows and posts, read off the page (plan step recurrence:R8-c).

The readers the weekday reading's route suites share: the "repeating on"
control as the server rendered it (:func:`repeats_on_control`), the token one
of its weekday options posts (:func:`weekday_token`), and a whole form as a
browser posts it with the owner's changes (:func:`form_as_posted`).

**A browser submits every control it renders, at the value it renders**, so
a case that hand-picks its payload grades a submission no browser sends: an
amount-only edit posted as ``{"amount": ...}`` alone would never
carry the start and the preselected reading that the real one does, and a
preselection the page got wrong would pass it.
"""
import json
import re
from html.parser import HTMLParser

from werkzeug.datastructures import MultiDict

from tests.test_routes._statement_forms import form_fields


class _RepeatsOn(HTMLParser):
    """The "repeating on" row of a rendered page: its container, select and options."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.container_classes = None
        self.selects = []
        self.options = []
        self.by_value = {}
        self._in_select = False
        self._option = None

    def handle_starttag(self, tag, attrs):
        """Record the container's classes, the select, and each option."""
        attributes = dict(attrs)
        if tag == "div" and attributes.get("id") == "field-repeats-on":
            self.container_classes = attributes.get("class", "").split()
        elif tag == "select" and attributes.get("id") == "repeats_on":
            self.selects.append(attributes)
            self._in_select = True
        elif tag == "option" and self._in_select:
            self._option = {
                "value": attributes["value"],
                "disabled": "disabled" in attributes,
                "selected": "selected" in attributes,
                "data": {
                    name.removeprefix("data-"): value
                    for name, value in attributes.items()
                    if name.startswith("data-")
                },
                "label": "",
            }
            self.options.append(self._option)

    def handle_data(self, data):
        """Collect an option's label."""
        if self._option is not None:
            self._option["label"] += data

    def handle_endtag(self, tag):
        """Close an option, trimming its label, or the select."""
        if tag == "option" and self._option is not None:
            self._option["label"] = " ".join(self._option["label"].split())
            self._option = None
        elif tag == "select":
            self._in_select = False


def repeats_on_control(page):
    """Return the page's ONE "repeating on" control, read as rendered.

    Args:
        page: The rendered page.

    Returns:
        The reader: ``container_classes`` (the row's classes), ``selects``
        (the one select's attributes), ``options`` in document order -- each
        ``{"value", "disabled", "selected", "data", "label"}`` -- and
        ``by_value``, the same options keyed by their value.
    """
    reader = _RepeatsOn()
    reader.feed(page)
    assert len(reader.selects) == 1, "the page renders one repeats_on select"
    reader.by_value = {option["value"]: option for option in reader.options}
    return reader


def weekday_token(page, *, counts_from_end):
    """Return the token the page's weekday option for one end of the month posts.

    The option is identified by what it reads, off its ``data-places``: in a
    31-day month the 31st is read only as the last such weekday (there is no
    fifth, ruling plan#2670), and the 1st only as the first.

    Args:
        page: The rendered page.
        counts_from_end: Which end's option: the month's last such weekday
            (``True``) or the weekday counted from its start.

    Returns:
        The option's ``value``.
    """
    place = "31-31" if counts_from_end else "31-1"
    (option,) = [
        option for option in repeats_on_control(page).options
        if place in json.loads(option["data"].get("places", "{}"))
    ]
    return option["value"]


def form_as_posted(client, page_url, action, **changes):
    """Return the form posting to *action* on *page_url* as a browser posts it, with *changes*.

    Every enabled control at its rendered value (``form_fields``) of the one
    form whose action IS *action* -- asserted to be exactly one, because
    ``form_fields`` matches a substring and ``/templates/5`` is inside
    ``/templates/5/archive`` -- then each changed control set to what the
    owner typed or chose.  A control the form does not post (a disabled one)
    cannot be changed here: a case crafting one adds it to the result itself,
    so the crafting is visible in the case.

    Args:
        client: The signed-in test client.
        page_url: The page rendering the form.
        action: The form's ``action``.
        **changes: ``{control name: new value}``; each must be a control the
            form posts.

    Returns:
        A ``MultiDict`` of the submission, in document order.
    """
    page = client.get(page_url).data.decode()
    actions = re.findall(r'<form[^>]*\saction="([^"]*)"', page)
    assert [found for found in actions if action in found] == [action], actions
    fields = form_fields(page, action)
    names = {name for name, _value in fields}
    assert set(changes) <= names, f"{set(changes) - names} are not posted"
    return MultiDict([
        (name, changes.get(name, value)) for name, value in fields
    ])
