"""The board's views as quill declares them: the Plan view, the open cards in the developer's
drag order (ruling ``balance:R-BAL177``), and the Inbox, the open steps and questions holding no
Priority, leaves left out, newest first (rulings plan#3229, plan#3231).  The one home of each
view's name, filter, sort and columns (:data:`DECLARED`), of what a read of a board reads of its
views (:data:`VIEWS_READ`), and of how :mod:`setup_tracker` grades them (:func:`differences`,
:func:`unread`).  ``setup_tracker``'s ``--apply`` makes and corrects them from these
declarations; its writes stay there, the one module outside quill's two doors that may send a
mutation, as the developer (``test__board_fence``).

**What the API cannot write is reported with the page to set it on.**  ``updateProjectV2View``
writes a view's name, layout, filter and visible columns and nothing else, and
``createProjectV2View`` takes a name, a layout and visible columns
(``UpdateProjectV2ViewInput``, ``CreateProjectV2ViewInput`` and
``ProjectV2ViewConfigurationInput``, introspected 2026-11-10), so a view's sort and grouping
are set on the web, and its "Show hierarchy", which no API even reads, is an ``UNREAD`` line.

**A filter is graded as its text.**  GitHub drops a clause it cannot read without a word:
``is:open no:bogusfield`` listed all 492 open items of the board's 1,227 (its
``ProjectV2.items(query:)``, measured 2026-11-10 17:15 UTC), so a filter is right only as the
text that was measured.  :data:`INBOX`'s, read through the same query that day, listed exactly
the 21 open steps and questions that an unfiltered read of every item holds with no Priority
and no parent issue, and GitHub read each clause: without ``is:open`` it listed 52, without
``no:priority`` 356, without ``no:parent-issue`` 23 (two leaves), and with ``type:step`` in
place of ``type:step,question`` 18 (three questions gone).

A leaf: it imports no module of quill's but :mod:`_board_fields` and :mod:`_fields`, so
:mod:`setup_tracker` can import it with no cycle.  Split out of :mod:`setup_tracker` at
plan#3248, whose Inbox would otherwise take that module past the 1,000 lines
``too-many-lines`` allows.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tools.quill._board_fields import GITHUB_STATUS_FIELD, hidden, status_column
from tools.quill._fields import PRIORITY_FIELD, SUGGESTED_KINDS

#: The type GitHub gives the Parent issue field, by which a view's column is found: where a
#: leaf names the step it splits, since a split step is no row of the Plan view (ruling
#: plan#1814).
PARENT_COLUMN = "PARENT_ISSUE"

#: What a read of a board reads of its views: each one's name, filter and layout, every field
#: it is sorted by, by its type and direction, whether it is grouped, and its columns, each
#: asked whether it is one of the organization's issue fields (finding plan#1839).
VIEWS_READ = """
  views(first: 20) { nodes {
    id number name filter layout
    sortByFields(first: 10) { totalCount nodes { direction
      field { ... on ProjectV2FieldCommon { name dataType } } } }
    groupByFields(first: 1) { totalCount } verticalGroupByFields(first: 1) { totalCount }
    fields(first: 50) { nodes { ... on ProjectV2FieldCommon { id name dataType isIssueField } } }
  } }
"""


def parent_column(fields: list[dict]) -> dict | None:
    """The Parent issue field (:data:`PARENT_COLUMN`) among a board's or a view's ``fields``,
    or None."""
    return next((f for f in fields if f and f["dataType"] == PARENT_COLUMN), None)


@dataclass(frozen=True)
class Column:
    """A column a view shows: its name, as a difference names it, how it is found among a
    board's or a view's fields, and why the view shows it."""

    name: str
    find: Callable[[list[dict]], dict | None]
    why: str


@dataclass(frozen=True)
class SortKey:
    """One field a view is sorted by: its type as GitHub's GraphQL names it (``dataType``) and
    its direction, ``ASC`` or ``DESC``, which a sort is matched on, and the field's name, which
    only names it."""

    data_type: str
    direction: str
    name: str = field(default="", compare=False)

    def __str__(self) -> str:
        """The key as the web's sort menu names it: the field, then its direction."""
        return f"{self.name}, {'descending' if self.direction == 'DESC' else 'ascending'}"


@dataclass(frozen=True)
class View:
    """One board view as declared here: its name and filter, the order its rows are in, as a
    difference names it, the fields it is sorted by (none: the board's drag order), and the
    columns it shows.  Every declared view is an ungrouped table showing no stored field but
    Status (:func:`_board_fields.hidden`): a board layout's columns are a stored field, and a
    grouping splits its order."""

    name: str
    filter: str
    order: str
    sort: tuple[SortKey, ...] = ()
    columns: tuple[Column, ...] = ()


#: The Plan view: the open cards, unsorted, so it shows the drag order, each leaf naming the
#: step it splits in the Parent issue column, and Status, which quill writes.  Its "Show
#: hierarchy", which nests each step's tasks under it, must be on too, but no API reads it
#: (``ProjectV2View`` and ``updateProjectV2View`` hold no such setting, introspected
#: 2026-10-26, and on 2026-11-10 a view's ``configuration`` held its visible fields alone), so
#: the check prints it as ``UNREAD`` (:func:`unread`).
PLAN = View("Plan", "is:open", "the drag order", columns=(
    Column("Parent issue", parent_column, "where a leaf names the step it splits"),
    Column(GITHUB_STATUS_FIELD, status_column,
           "where quill shows each card's progress (ruling plan#1873)"),
))

#: The Inbox (rulings plan#3229, plan#3231): the open cards that take a suggested Priority
#: (``check.takes_suggestion``) and hold no Priority, newest first, so the developer sets each
#: one's Priority from it.  A leaf, or a task, has a parent issue; a question names its step by
#: a "blocked by" link (``_state.OWNED``), never as its parent, so ``no:parent-issue`` leaves
#: out the leaves alone.  GitHub's own Created field is what it is sorted by.
INBOX = View(
    "Inbox",
    f"is:open no:{PRIORITY_FIELD.lower()} no:parent-issue type:{','.join(SUGGESTED_KINDS)}",
    "the newest-first order",
    sort=(SortKey("CREATED", "DESC", "Created"),),
)

#: Every view a board holds as declared here, in the order ``--apply`` makes them.
DECLARED = (PLAN, INBOX)


def named(project: dict, view: View) -> list[dict]:
    """The views of a board (``project``, read with :data:`VIEWS_READ`) named as ``view`` is:
    one, on a board as declared."""
    return [held for held in project["views"]["nodes"] if held["name"] == view.name]


def _keys(sort: tuple[SortKey, ...]) -> str:
    """Sort keys in words, in their order."""
    return "; ".join(map(str, sort))


def _sort_difference(view: View, held: dict, url: str) -> str | None:
    """How the board's view ``held`` is sorted otherwise than ``view`` declares, if it is: a
    view declared unsorted that is sorted hides its order; one declared sorted is sorted on the
    web, at its own page, since the API cannot sort a view."""
    sort = held["sortByFields"]
    keys = tuple(SortKey(node["field"]["dataType"], node["direction"], node["field"]["name"])
                 for node in sort["nodes"])
    if keys == view.sort and sort["totalCount"] == len(view.sort):
        return None
    if not view.sort:
        return (f"the {view.name!r} view is sorted, which hides {view.order}: remove its sort "
                f"at {url}")
    now = f"sorted by {_keys(keys)}" if keys else "unsorted"
    if unread_keys := sort["totalCount"] - len(keys):
        now += f" and {unread_keys} more the read did not hold"
    return (f"the {view.name!r} view is {now}, not sorted by {_keys(view.sort)}: the API "
            f"cannot sort a view, so sort it at {url}/views/{held['number']}")


def _view_differences(project: dict, view: View) -> list[str]:
    """Each way the board's views named as ``view`` is differ from it, or that the board has
    none."""
    found, url = named(project, view), project["url"]
    if not found:
        return [f"the board has no view named {view.name!r}"]
    wrong = []
    for held in found:
        columns = held["fields"]["nodes"]
        if held["filter"] != view.filter:
            wrong.append(f"the {view.name!r} view's filter is {held['filter']!r}")
        wrong += [
            f"the {view.name!r} view does not show the {column.name} column, {column.why}"
            for column in view.columns if column.find(columns) is None]
        if (sort := _sort_difference(view, held, url)) is not None:
            wrong.append(sort)
        if shown := [f["name"] for f in columns if hidden(f)]:
            wrong.append(f"the {view.name!r} view shows stored fields {shown}")
        if held["layout"] != "TABLE_LAYOUT":
            wrong.append(
                f"the {view.name!r} view is a {held['layout']}, whose columns are a stored "
                f"field: make it a table at {url}")
        if held["groupByFields"]["totalCount"] or held["verticalGroupByFields"]["totalCount"]:
            wrong.append(
                f"the {view.name!r} view is grouped, which splits {view.order}: remove its "
                f"grouping at {url}")
    return wrong


def differences(project: dict) -> list[str]:
    """Each way a board's views (``project``, read with :data:`VIEWS_READ`) differ from
    :data:`DECLARED`, view by view; a view of another name is no difference."""
    return [difference for view in DECLARED for difference in _view_differences(project, view)]


def unread(project: dict) -> list[str]:
    """One line per declared setting of the board's views that no API reads, naming the page
    to look at: the Plan view's "Show hierarchy" (:data:`PLAN`)."""
    return [f"the {PLAN.name!r} view's \"Show hierarchy\", which must be on so each step's "
            "tasks nest under it: no API reads it, so look at "
            f"{project['url']}/views/{held['number']}" for held in named(project, PLAN)]


def _described(view: View) -> str:
    """A declared view in words, for the check's line on a board that matches."""
    sort = f"sorted by {_keys(view.sort)}" if view.sort else "unsorted"
    shown = (f"the {' and '.join(column.name for column in view.columns)} columns and no other "
             "stored field") if view.columns else f"no stored field but {GITHUB_STATUS_FIELD}"
    return (f"the {view.name!r} view, a table filtered {view.filter!r}, {sort}, ungrouped, "
            f"showing {shown}")


def described() -> str:
    """Every declared view in words, for the check's line on a board that matches."""
    return "; ".join(map(_described, DECLARED))
