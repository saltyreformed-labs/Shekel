"""The board's fields as quill declares them (rule 14): which one stores a value per card, so no
board may hold it (:func:`stores_values`), and the one exception, GitHub's built-in Status, which
quill writes (rulings plan#1873, plan#1905, plan#1906): its name, the four options quill writes,
and how a board's fields are read for it.  The one home of each, for the tracker's
configuration (:mod:`setup_tracker`, which checks the board), the read of every card's Status
(``_queries.BOARD_STATUS``) and its writer (:mod:`_status`).

GitHub's built-in Status is the one stored field the board keeps: quill writes it from git on
every card the board holds, and puts back any other value (:mod:`_status`).  Its options are
set on the web: GitHub refuses to delete the field ("Only custom fields can be deleted.") and
silently ignored an update emptying its choices (both measured 2026-10-03).

A leaf: it imports no module of quill's, so :mod:`setup_tracker`, which :mod:`_tracker`,
:mod:`_queries` and most commands import, can import it with no cycle.  Split out of
:mod:`setup_tracker` at plan#1861's merge of plan#1811, whose change would otherwise take that
module past the 1,000 lines ``too-many-lines`` allows.
"""
from __future__ import annotations

from collections.abc import Iterable

#: A board field of one of these types STORES a value per card; every other
#: type (title, labels, parent issue, sub-issue progress, ...) shows a fact
#: the card itself holds, and so does an organization's issue field shown on the board
#: (:func:`stores_values`).
STORED_FIELD_TYPES = frozenset(
    {"TEXT", "SINGLE_SELECT", "MULTI_SELECT", "NUMBER", "DATE", "ITERATION"}
)

#: GitHub's built-in field, the one stored field GitHub will not let a board lose, and the one
#: the board keeps: quill writes it.
GITHUB_STATUS_FIELD = "Status"
#: The options Status holds, as quill writes them (rulings plan#1873, plan#1906): GitHub's
#: three, named as GitHub names them, and Dropped, which the developer adds on the web.
TODO, IN_PROGRESS, DONE, DROPPED = "Todo", "In Progress", "Done", "Dropped"
STATUS_OPTIONS = (TODO, IN_PROGRESS, DONE, DROPPED)


def stores_values(board_field: dict) -> bool:
    """Whether a board field holds a value per card: one of :data:`STORED_FIELD_TYPES`, and not
    one of the organization's issue fields (``isIssueField``), whose value the issue holds, as
    a label's (finding plan#1839: Arc, Priority, Start date and Effort read as board fields, so
    no check could pass)."""
    return (bool(board_field) and board_field["dataType"] in STORED_FIELD_TYPES
            and not board_field["isIssueField"])


def is_github_status(board_field: dict) -> bool:
    """Whether a board field is GitHub's built-in Status, which no board can delete."""
    return (board_field["name"], board_field["dataType"]) == (GITHUB_STATUS_FIELD, "SINGLE_SELECT")


def hidden(board_field: dict) -> bool:
    """Whether the plan view hides a board field: one storing a value, but Status."""
    return stores_values(board_field) and not is_github_status(board_field)


def lacking(options: Iterable[str]) -> list[str]:
    """The options of :data:`STATUS_OPTIONS` a Status field holding ``options`` (their names)
    lacks: the one reading, for :mod:`setup_tracker`'s check and ``quill sync``'s, which writes
    no card's Status while one is lacking (ruling plan#1906)."""
    held = set(options)
    return [option for option in STATUS_OPTIONS if option not in held]


def status_column(fields: list[dict]) -> dict | None:
    """GitHub's Status field among a board's or a view's ``fields``, or None."""
    return next((f for f in fields if f and is_github_status(f)), None)


def status_field_differences(project: dict) -> list[str]:
    """How a board's Status field differs (``project``: the board as :mod:`setup_tracker` reads
    it): renamed, or holding not exactly :data:`STATUS_OPTIONS`."""
    status = status_column(project["fields"]["nodes"])
    if status is None:
        return [f"no single-select field is named {GITHUB_STATUS_FIELD!r}: GitHub's built-in "
                "one was renamed, so quill can write no card's Status -- rename it back"]
    held = [option["name"] for option in status["options"]]
    if sorted(held) == sorted(STATUS_OPTIONS):
        return []
    missing = lacking(held)
    return [f"the board's {GITHUB_STATUS_FIELD} holds the options {held}, not exactly "
            f"{list(STATUS_OPTIONS)} (rulings plan#1873, plan#1906)"
            + (f", so quill writes no card's Status until it holds {missing}" if missing else "")
            + f": correct them in the board's settings, at {project['url']}/settings"]
