"""Set up the private tracker Shekel's plan lives in, and report where it differs from this file.

The plan moves out of ``docs/plans/`` into a private repository's Issues
(ruling ``balance:R-BAL170``): each step, finding, ruling and question is one
issue -- a "card" -- and one Project board holds the open ones in the order
the developer drags them.  This module is the one home of WHERE that tracker
lives (:class:`Place`: :data:`PLAN`, and :data:`REHEARSAL`, the throwaway one
X-cx's migration was rehearsed on) and of its CONFIGURATION: the repository and
its settings, the labels, the board, and the things only the web can set (the
organization's issue types and issue fields, which need a scope the shared token
does not carry; the board's automations and its view's sort, which the API cannot
change, and its view's "Show hierarchy", which the API cannot even read; and
quill's GitHub App).  The cards themselves are content, and their home is the
tracker; nothing here names one.

Run with no flag it changes nothing: it reads GitHub, prints one line per
thing it checked, and exits 1 if anything differs.  A setting no API reads is an
``UNREAD`` line naming the page to look at, never a difference, since no run could
ever pass it.  ``--apply`` makes what is missing and corrects what drifted, then
reports what it could not fix (an issue type, an issue field, a board automation
or a view's sort with the page to fix it on).
It DELETES only what nothing depends on: a label no issue or pull request
carries, and a custom board field that stores a value per card while the
board holds no card, archived ones counted.  Every other difference is
reported for a person to settle, so a re-run never destroys anyone's work.

**One home per fact (rule 14) is why the board stores no field.**  A card's
order is the board's own drag order (``items(orderBy: {field: POSITION})``),
its kind is its issue type, its arc its issue field Arc, whether it shipped is git's
answer (its ship marker ``plan/ships/N``, ruling ``balance:R-BAL359``).  A
field holding any of those would be a second home, so every custom field that
stores a value is a difference.  GitHub's built-in ``Status`` field is the one stored
field the board keeps, and no second home: ``quill sync`` writes it from git on every card the
board holds, as it writes a card's open or closed state, and puts back any other value
(``_status``; rulings plan#1873, plan#1905 and plan#1906, reversing the ruling of 2026-10-03
that kept it unused).  So this module checks the field alone: it holds exactly
:data:`_board_fields.STATUS_OPTIONS`, set on the web, the plan view shows it, and no board item
that is no card holds a value.  Every automation stays off, and matters twice over:
"Auto-close issue" would close a card when its Status became Done, which quill writes -- a
third way to close a card, beside git and the developer's own hand.
**Nor may an automation put a card on the board** (ruling ``balance:R-BAL177``):
the board holds the steps and questions in the developer's order, and each step's tasks
nested under it outside that order, in its card's order of its tasks (rulings plan#1814,
plan#1868), and quill places each one itself, a new
leaf where the step it splits sat, a task at the bottom, where the order does not read
it: filed under a step on the board, or put on by ``quill sync`` once its step is (ruling
plan#1842).  "Auto-add sub-issues to project" would add each sub-issue of a card on the
board at the bottom (measured 2026-10-04, when findings and rulings were their owner's
sub-issues: within ten seconds; since ``R-BAL395`` each is blocked by its owner instead).  A
task under a step on the board quill now puts there itself; the automation's harm is the
rest: a leaf at the bottom of the order, not where the step it splits sat (R-BAL179), until
quill moves it, and any card a person links under one on the board, entering the order
unplaced.  **Nor may one hide
a card**: "Auto-archive items" archives closed cards, and an archived card is in no
view, so the shipped work kept as closed cards (ruling ``balance:R-BAL352``) would
vanish from the view that shows it.  So every automation is off, and any archived
board item is a difference (the coordinator's call, 14:4x 2026-10-21).

**The organization's issue fields are exactly the five declared** (:data:`ISSUE_FIELDS`;
rulings ``balance:R-BAL348``, ``R-BAL354``, and the developer's word of 2026-10-25): Arc, a
single select of the arcs, which a card's arc moves into after the cutover, its matching
label then kept as display; and GitHub's four defaults, Priority, Effort, Start date and
Target date, which the developer keeps, empty for now.  Any other is a difference, deleted
on the web.  The board lists each one it shows as a field of its own, which GitHub marks
``isIssueField``: its value is the issue's, so the board stores nothing for it (finding
plan#1839, measured 2026-10-27), and it is no stored field.  **Every card's arc labels display
its Arc** (R-BAL354, views_spec s.6): each card the rule ``quill sync``'s label pass reports
and writes by finds anything for is a difference (:func:`check_card_arcs`), read as sync reads
them.

Usage, from the repository root::

    python -m tools.quill.setup_tracker            # check only
    python -m tools.quill.setup_tracker --apply    # make and correct
"""
from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import quote

import requests

from tools.ci.arcs import ARCS
from tools.quill._arcs import ARC_FIELD, arc_verdict, card_arcs
from tools.quill._board_fields import (
    GITHUB_STATUS_FIELD,
    STATUS_OPTIONS,
    hidden,
    is_github_status,
    status_column,
    status_field_differences,
    stores_values,
)
from tools.quill._exits import described
from tools.quill._github import (
    APP_DIR,
    CredentialsError,
    GitHub,
    GitHubError,
    TrackerError,
    app_credentials,
    app_installation,
    app_jwt,
    installation_token,
    user_token,
)


@dataclass(frozen=True)
class Place:
    """Where a tracker lives: its organization, its repository and its board's title.

    Every reader compares a place's names as whole TOKENS, never as text: the
    rehearsal's repository name begins with the real one (``shekel-plan`` is a
    prefix of ``shekel-plan-rehearsal``), so a substring or prefix test would
    read the one as the other.
    """

    owner: str
    name: str
    board_title: str

    @property
    def full_name(self) -> str:
        """``owner/name``: how GitHub names the repository in a link (``nameWithOwner``)."""
        return f"{self.owner}/{self.name}"

    @property
    def path(self) -> str:
        """The repository's REST path, ``/repos/owner/name``."""
        return f"/repos/{self.full_name}"


#: The tracker the plan lives in (ruling ``balance:R-BAL170``): the one place
#: this module configures and every quill command reads and writes.
PLAN = Place("saltyreformed-labs", "shekel-plan", "Shekel plan")

#: The throwaway tracker X-cx's migration (L7) was rehearsed on before L8 ran it
#: on :data:`PLAN`; since S3b deleted the migration, only the tests read it, as the
#: place whose name holds :data:`PLAN`'s, which is why a place's names are compared
#: as tokens.
REHEARSAL = Place("saltyreformed-labs", "shekel-plan-rehearsal", "Shekel plan rehearsal")

#: :data:`PLAN`'s names, which this module's checks configure.
ORG, REPO, PROJECT_TITLE = PLAN.owner, PLAN.name, PLAN.board_title

#: The four kinds of card: each holds one arc, its issue field :data:`_arcs.ARC_FIELD`, its
#: arc label display (R-BAL354), and ``check.violations`` holds each to its kind's rules.
CARD_TYPES = ("step", "finding", "ruling", "question")
#: A piece of one step or finding, a commit inside its card's one pull request (ruling
#: ``balance:R-BAL386``): a sub-issue of that card, which holds no Arc and no arc label (its
#: arc is its card's), and is done when its file ``plan/tasks/N`` is in git.  A task under a
#: step the board holds is on it too, nested under that step in the Plan view, outside the
#: board's order, in its card's order of its tasks (rulings plan#1814, plan#1842, plan#1868).
TASK = "task"
#: The kinds of card in the developer's drag order (R-BAL177), the order every reader of the
#: board reads (``_tracker.Board.order``): a task on the board is in none.
IN_ORDER = ("step", "question")
#: Every issue type the plan reads: the four kinds of card, and a task.  The organization's
#: issue types are managed only with the ``admin:org`` scope, which the token every session
#: shares does not carry, so the developer sets them on the web and this module checks them.
ISSUE_TYPES = (*CARD_TYPES, TASK)
ISSUE_TYPES_URL = f"https://github.com/organizations/{ORG}/settings/issue-types"


@dataclass(frozen=True)
class IssueField:
    """One of the organization's issue fields as declared here: its type as GitHub's GraphQL
    names it (``dataType``) and, for a single select, its options, which match in any order
    and which a difference names as ``options_named``."""

    data_type: str
    options: tuple[str, ...] = ()
    options_named: str = "the options declared here"


#: The organization's issue fields, by name (rulings ``balance:R-BAL348``, ``R-BAL354``).
#: Arc (:data:`_arcs.ARC_FIELD`) is a single select whose options are the arcs,
#: :data:`tools.ci.arcs.ARCS`.  GitHub
#: makes four more when issue fields are switched on, Priority, Effort, Start date and Target
#: date.  R-BAL348 ruled them deleted, a click on the web that was never made, and on
#: 2026-10-25 the developer kept them ("I want to use those fields even if they are empty for
#: now"), so each is declared exactly as the organization held it that day.  An
#: organization's issue fields, like its issue types, are changed only with the ``admin:org``
#: scope, so the developer changes them on the web and this module checks them; any other
#: field is a difference.
ISSUE_FIELDS = {
    ARC_FIELD: IssueField("SINGLE_SELECT", ARCS, "the arcs"),
    "Priority": IssueField("SINGLE_SELECT", ("Urgent", "High", "Medium", "Low")),
    "Start date": IssueField("DATE"),
    "Target date": IssueField("DATE"),
    "Effort": IssueField("SINGLE_SELECT", ("High", "Medium", "Low")),
}
ISSUE_FIELDS_URL = f"https://github.com/organizations/{ORG}/settings/issue-fields"

#: The repository's settings this module holds.  ``auto_init`` is a creation
#: argument, not a setting: the README it commits gives the repository a first
#: commit, without which GitHub refuses to create any git reference.  Quill
#: writes none there since the ship marker (R-BAL359): a card's claim is the
#: code repository's branch ``card/N``.
REPOSITORY = {
    "private": True,
    "description": "Shekel's plan: each step, finding, ruling and question is one card.",
    "has_issues": True,
    "has_wiki": False,
}

#: Each arc is one label.  The arcs are :data:`tools.ci.arcs.ARCS`, their one
#: home beside each arc's planning document (step X-cx's L4 deleted this
#: module's second list); a label's colour is the one at its arc's position, read with no
#: wrap round, so an arc added without a colour of its own fails at import rather than
#: wearing another's (ruling plan#2219: each label's colour is its own).  ``tooling``'s is
#: the one the developer gave it on the web.
_ARC_COLORS = ("0e8a16", "1d76db", "5319e7", "d93f0b", "006b75", "c5a100", "793625")

#: The mark every card quill files carries from the call that creates it
#: until its filing's last write removes it (ruling ``balance:R-BAL202``).
FILING = "filing"


def unmarked(labels: Iterable[str]) -> tuple[str, ...]:
    """A card's ``labels`` with the :data:`FILING` mark taken out, in their order: the one
    producer of them, for a decision that sets the mark aside and for the fake tracker's
    removal of it (BAL-620)."""
    return tuple(label for label in labels if label != FILING)


#: The two labels a release reads: a step whose release moves money, and a parent
#: whose leaves ``/release`` ships in one release.
MOVES_MONEY_LABEL, DEPLOY_TOGETHER_LABEL = "moves-money", "deploy-together"
#: The label marking security work, in whatever arc it is (ruling plan#2179).
SECURITY_LABEL = "security"
#: The labels a person chooses for a card: every declared label but the arc labels, which
#: display its Arc, and quill's :data:`FILING` mark.  ``quill file step --label`` and
#: ``quill label`` take their choices from here (ruling plan#2221).
CHOSEN_LABELS = (MOVES_MONEY_LABEL, DEPLOY_TOGETHER_LABEL, SECURITY_LABEL)

#: Every label the tracker carries: one per arc, the ones a person chooses, and
#: quill's :data:`FILING` mark.
LABELS = {
    **{
        arc: (color, f"The {arc.replace('_', '-')} arc")
        for arc, color in zip(ARCS, _ARC_COLORS, strict=True)
    },
    MOVES_MONEY_LABEL: (
        "b60205", "Its release moves money: /release carries at most one such step"
    ),
    DEPLOY_TOGETHER_LABEL: (
        "fbca04", "A parent whose leaves /release ships in one release, never split"
    ),
    SECURITY_LABEL: ("d90466", "Security related issues"),
    FILING: (
        "bfdadc",
        "Quill has not finished filing this card, so it is not offered as work",
    ),
}

#: What quill's App may do, and nothing more: write cards (issues,
#: their sub-issues and dependencies) and place cards on the organization's
#: board.  ``metadata: read`` is mandatory for every App.  ``contents`` left it at
#: X-cx's M1, with the tracker's claim references (R-BAL359: a claim is a branch
#: of the code repository); the installation keeps it until the developer
#: narrows it on the web, and this check reports it until then.
APP_PERMISSIONS = {
    "issues": "write",
    "metadata": "read",
    "organization_projects": "write",
}

#: Quill's App's login, its slug, as GitHub's GraphQL names it when the App closes a card
#: (ruling ``balance:R-BAL227`` renamed the App "Shekel quill"): a close by any other
#: login is a person's (``_tracker.card_from``).  ``Tracker.connect`` refuses credentials
#: that sign in as another App and :func:`check_app` reports one, so every local quill run
#: checks it against GitHub; the card-only reader, whose token is no App's, cannot ask
#: GitHub and reads it from here.
APP_LOGIN = "shekel-quill"

#: What quill reads of the repositories a board is linked to (:func:`linked`).
LINKED_REPOSITORIES = "repositories(first: 100) { totalCount nodes { nameWithOwner } }"

#: The board's view: the open cards, unsorted, so it shows the drag order.
VIEW_NAME = "Plan"
VIEW_FILTER = "is:open"
#: The column the view shows beside its cards, by the type GitHub gives the field: the Parent
#: issue, where a leaf names the step it splits, since a split step is no row (ruling
#: plan#1814).  Its "Show hierarchy", which nests each step's tasks under it, must be on too,
#: but no API reads it (``ProjectV2View`` and ``updateProjectV2View`` hold no such setting,
#: introspected 2026-10-26), so the check prints it as ``UNREAD``.
VIEW_PARENT_COLUMN = "PARENT_ISSUE"
#: The name GitHub gives a new board's one view (measured on the 09-24 demo board).
GITHUB_DEFAULT_VIEW = "View 1"

_PROJECT_FIELDS = """
  id number title public url
  """ + LINKED_REPOSITORIES + """
  fields(first: 50) { nodes { ... on ProjectV2FieldCommon { id name dataType isIssueField }
    ... on ProjectV2SingleSelectField { options { name } } } }
  workflows(first: 50) { nodes { name enabled } }
  views(first: 20) { nodes {
    id number name filter layout sortByFields(first: 1) { totalCount }
    groupByFields(first: 1) { totalCount } verticalGroupByFields(first: 1) { totalCount }
    fields(first: 50) { nodes { ... on ProjectV2FieldCommon { id name dataType isIssueField } } }
  } }
  items(first: 1, archivedStates: [ARCHIVED, NOT_ARCHIVED]) { totalCount }
"""

_PROJECTS = """
query($org: String!) {
  organization(login: $org) { id projectsV2(first: 100) { nodes { id title } } }
}"""

_PROJECT = "query($id: ID!) { node(id: $id) { ... on ProjectV2 {" + _PROJECT_FIELDS + "} } }"

#: Every board item, archived ones included, with its Status and whether it is archived.
_BOARD_ITEMS = (
    """
query($id: ID!, $after: String) {
  node(id: $id) { ... on ProjectV2 {
    items(first: 100, after: $after, archivedStates: [ARCHIVED, NOT_ARCHIVED]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id type isArchived
        content {
          ... on Issue { number repository { name nameWithOwner } }
          ... on PullRequest { number repository { name nameWithOwner } }
        }
        fieldValueByName(name: \""""
    + GITHUB_STATUS_FIELD
    + """\") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
      }
    }
  } }
}"""
)

_LABELS = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    labels(first: 100) {
      nodes { name color description issues { totalCount } pullRequests { totalCount } }
    }
  }
}"""

_ISSUE_FIELDS_QUERY = """
query($org: String!) {
  organization(login: $org) {
    issueFields(first: 100) {
      totalCount
      nodes {
        ... on IssueFieldCommon { name dataType }
        ... on IssueFieldSingleSelect { options { name } }
      }
    }
  }
}"""


@dataclass
class Report:
    """What a run found and did; ``differences`` counts what is still wrong."""

    lines: list[str] = field(default_factory=list)
    differences: int = 0

    def ok(self, what: str) -> None:
        """Record something that matches this file."""
        self.lines.append(f"ok      {what}")

    def made(self, what: str) -> None:
        """Record something ``--apply`` created, corrected or deleted."""
        self.lines.append(f"made    {what}")

    def differs(self, what: str) -> None:
        """Record a difference this run did not fix."""
        self.differences += 1
        self.lines.append(f"DIFFERS {what}")

    def unread(self, what: str) -> None:
        """Record a setting no API reads, for a person to look at.  It is never a difference:
        a read GitHub cuts short is one, as the next run may read it whole, but this one no
        run can read, so counting it would fail every run."""
        self.lines.append(f"UNREAD  {what}")


@dataclass
class LabelChanges:
    """How the repository's labels differ from :data:`LABELS`."""

    create: list[str]
    correct: list[str]
    unused_extra: list[str]
    used_extra: list[str]


def label_changes(declared: dict, actual: list[dict]) -> LabelChanges:
    """Compare declared labels with the repository's.

    ``actual`` is the GraphQL label nodes: ``name``, ``color``,
    ``description`` and the counts of issues and pull requests carrying it,
    open and closed.  A label this file does not declare is ``unused_extra``
    when nothing carries it (deleting it loses nothing) and ``used_extra``
    otherwise.
    """
    by_name = {label["name"]: label for label in actual}
    create = [name for name in declared if name not in by_name]
    correct = [
        name
        for name, (color, description) in declared.items()
        if name in by_name
        and (by_name[name]["color"].lower(), by_name[name]["description"] or "")
        != (color, description)
    ]
    unused_extra, used_extra = [], []
    for name, label in by_name.items():
        if name in declared:
            continue
        carried = label["issues"]["totalCount"] + label["pullRequests"]["totalCount"]
        (used_extra if carried else unused_extra).append(name)
    return LabelChanges(create, correct, unused_extra, used_extra)


def repository_drift(declared: dict, actual: dict) -> dict:
    """The declared settings the repository does not hold, with their declared values."""
    return {key: value for key, value in declared.items() if actual.get(key) != value}


def issue_type_differences(declared: tuple[str, ...], actual: list[dict]) -> list[str]:
    """Each way the organization's issue types differ from ``declared``.

    Every declared type must exist and be enabled; any OTHER enabled type is a
    kind of issue the plan cannot read, so it is a difference too.
    """
    by_name = {issue_type["name"]: issue_type for issue_type in actual}
    differences = []
    for name in declared:
        if name not in by_name:
            differences.append(f"issue type {name!r} is missing")
        elif not by_name[name]["is_enabled"]:
            differences.append(f"issue type {name!r} is disabled")
    differences += [
        f"issue type {name!r} is enabled but is not one of the plan's types"
        for name, issue_type in by_name.items()
        if name not in declared and issue_type["is_enabled"]
    ]
    return differences


def permission_differences(declared: dict, actual: dict) -> list[str]:
    """Each permission the App's installation holds that differs from ``declared``.

    An EXTRA permission is a difference: the App gets what it needs and no more.
    """
    return [
        f"App permission {name!r} is {actual.get(name)!r}, declared {declared.get(name)!r}"
        for name in sorted(set(declared) | set(actual))
        if declared.get(name) != actual.get(name)
    ]


def linked(place: Place, repositories: dict) -> bool | None:
    """Whether a board's linked ``repositories`` (read as :data:`LINKED_REPOSITORIES`) hold
    ``place``'s repository, by its whole ``owner/name``: the one test, for this module's
    check and quill's refusal to write to an unlinked board.

    ``True`` when the read names it, which proves the link however short the read; ``False``
    when a WHOLE read (``totalCount`` nodes) does not; ``None`` when a read GitHub cut short
    does not, which says nothing either way: the check reports it as unread,
    ``--apply`` sends no link for it, and quill refuses the board.
    """
    names = [node["nameWithOwner"] for node in repositories["nodes"]]
    if place.full_name in names:
        return True
    return False if repositories["totalCount"] <= len(names) else None


def is_tracker_issue(node: dict, place: Place) -> bool:
    """Whether a link or a board item's content names an issue of ``place``'s tracker: the
    one test, for a parent, a sub-issue, a blocker and the board alike (``_tracker``), and for
    this module's read of the board's items, on the whole ``owner/name`` token."""
    return (node.get("repository") or {}).get("nameWithOwner") == place.full_name


def _parent_column(fields: list[dict]) -> dict | None:
    """The Parent issue field (:data:`VIEW_PARENT_COLUMN`) among a board's or a view's
    ``fields``, or None."""
    return next((f for f in fields if f and f["dataType"] == VIEW_PARENT_COLUMN), None)


def _plan_views(project: dict) -> list[dict]:
    """The board's views named :data:`VIEW_NAME`: one, on a board as declared."""
    return [view for view in project["views"]["nodes"] if view["name"] == VIEW_NAME]


def _is_card(item: dict) -> bool:
    """Whether a board item (``_BOARD_ITEMS``' node) is a card: an issue of the tracker."""
    return item["type"] == "ISSUE" and is_tracker_issue(item["content"] or {}, PLAN)


def _item_name(item: dict) -> str:
    """A board item as a person finds it: ``card repository#number``, or a pull request's or
    another repository's issue by its kind and ``repository#number``, or its kind and id.

    A draft (typed straight onto the board) has no repository or number, and a
    REDACTED item is one the reader may not see.
    """
    content, kind = item["content"] or {}, item["type"].lower().replace("_", " ")
    if "number" in content:
        return (f"{'card' if _is_card(item) else kind} "
                f"{content['repository']['name']}#{content['number']}")
    return f"{kind} item {item['id']}"


def status_differences(items: list[dict]) -> list[str]:
    """One difference per board item that is no card of the tracker -- a draft, a pull
    request, another repository's issue, one the reader may not see -- holding a Status value
    (``items``: :data:`_BOARD_ITEMS`' nodes).  A card's Status is ``quill sync``'s, which puts
    back any value but its own (ruling plan#1905); sync reads no other item."""
    return [
        f"{_item_name(item)} holds {GITHUB_STATUS_FIELD} {value!r}, and only a card of "
        f"{PLAN.full_name} holds one, which quill writes: clear it"
        for item in items
        if (value := (item["fieldValueByName"] or {}).get("name")) and not _is_card(item)
    ]


def archived_differences(items: list[dict]) -> list[str]:
    """One difference per archived board item, drafts and all (``items``: the items query's
    nodes): an archived card is in no view (ruling ``balance:R-BAL352`` keeps shipped work
    as closed cards a view shows)."""
    return [f"{_item_name(item)} is archived, so no view shows it: restore it from the "
            "board's archived items"
            for item in items if item["isArchived"]]


def board_differences(project: dict) -> list[str]:
    """Each way a board's configuration differs from this file (its cards aside)."""
    differences = []
    if project["public"]:
        differences.append("the board is public")
    link = linked(PLAN, project["repositories"])
    if link is False:
        differences.append(f"the board is not linked to {PLAN.full_name}")
    elif link is None:
        differences.append(
            f"the board links {project['repositories']['totalCount']} repositories and the "
            f"read holds {len(project['repositories']['nodes'])}, none of them "
            f"{PLAN.full_name}: "
            "whether it is linked is unread")
    differences += [
        f"the board stores a field, {board_field['name']!r}"
        for board_field in project["fields"]["nodes"]
        if hidden(board_field)
    ]
    differences += status_field_differences(project)
    differences += [
        f"the board automation {workflow['name']!r} is on, and none may be: switch it off at "
        f"{project['url']}/workflows"
        for workflow in project["workflows"]["nodes"]
        if workflow["enabled"]
    ]
    views = _plan_views(project)
    if not views:
        differences.append(f"the board has no view named {VIEW_NAME!r}")
    for view in views:
        if view["filter"] != VIEW_FILTER:
            differences.append(f"the {VIEW_NAME!r} view's filter is {view['filter']!r}")
        if _parent_column(view["fields"]["nodes"]) is None:
            differences.append(f"the {VIEW_NAME!r} view does not show the Parent issue column, "
                               "where a leaf names the step it splits")
        if status_column(view["fields"]["nodes"]) is None:
            differences.append(f"the {VIEW_NAME!r} view does not show the {GITHUB_STATUS_FIELD} "
                               "column, where quill shows each card's progress (ruling "
                               "plan#1873)")
        if view["sortByFields"]["totalCount"]:
            differences.append(
                f"the {VIEW_NAME!r} view is sorted, which hides the drag order: remove its "
                f"sort at {project['url']}"
            )
        shown = [f["name"] for f in view["fields"]["nodes"] if hidden(f)]
        if shown:
            differences.append(f"the {VIEW_NAME!r} view shows stored fields {shown}")
        if view["layout"] != "TABLE_LAYOUT":
            differences.append(
                f"the {VIEW_NAME!r} view is a {view['layout']}, whose columns are a stored "
                f"field: make it a table at {project['url']}"
            )
        if view["groupByFields"]["totalCount"] or view["verticalGroupByFields"]["totalCount"]:
            differences.append(
                f"the {VIEW_NAME!r} view is grouped, which splits the drag order: remove its "
                f"grouping at {project['url']}"
            )
    return differences


def check_repository(github: GitHub, apply: bool, report: Report) -> dict | None:
    """Check (and with ``apply``, create or correct) the tracker repository."""
    try:
        actual = github.rest("GET", PLAN.path)
    except GitHubError as error:
        if error.status != 404:
            raise
        if not apply:
            report.differs(f"repository {ORG}/{REPO} does not exist")
            return None
        actual = github.rest(
            "POST", f"/orgs/{ORG}/repos", {"name": REPO, "auto_init": True, **REPOSITORY}
        )
        report.made(f"repository {ORG}/{REPO}")
    drift = repository_drift(REPOSITORY, actual)
    if drift and apply:
        actual = github.rest("PATCH", PLAN.path, drift)
        report.made(f"sent repository settings {sorted(drift)}")
        drift = repository_drift(REPOSITORY, actual)
    if drift:
        report.differs(f"repository settings differ: {drift}")
    else:
        report.ok(f"repository {ORG}/{REPO} and its settings")
    return actual


def check_labels(github: GitHub, apply: bool, report: Report) -> None:
    """Check (and with ``apply``, make, correct or delete) the repository's labels."""
    nodes = github.graphql(_LABELS, owner=ORG, name=REPO)["repository"]["labels"]["nodes"]
    changes = label_changes(LABELS, nodes)
    base = f"{PLAN.path}/labels"
    for name in changes.create:
        if apply:
            color, description = LABELS[name]
            github.rest("POST", base, {"name": name, "color": color, "description": description})
            report.made(f"label {name}")
        else:
            report.differs(f"label {name} is missing")
    for name in changes.correct:
        if apply:
            color, description = LABELS[name]
            github.rest(
                "PATCH",
                f"{base}/{quote(name, safe='')}",
                {"color": color, "description": description},
            )
            report.made(f"label {name} colour and description")
        else:
            report.differs(f"label {name} has another colour or description")
    for name in changes.unused_extra:
        if apply:
            github.rest("DELETE", f"{base}/{quote(name, safe='')}")
            report.made(f"deleted label {name!r} (it labelled nothing)")
        else:
            report.differs(f"label {name!r} is not declared here (it labels nothing)")
    for name in changes.used_extra:
        report.differs(f"label {name!r} is not declared here and labels cards: settle it by hand")
    if not (changes.create or changes.correct or changes.unused_extra or changes.used_extra):
        report.ok(f"labels: exactly the {len(LABELS)} declared")


def check_issue_types(github: GitHub, report: Report) -> None:
    """Check the organization's issue types (set on the web; see :data:`ISSUE_TYPES`)."""
    differences = issue_type_differences(
        ISSUE_TYPES, github.rest("GET", f"/orgs/{ORG}/issue-types")
    )
    for difference in differences:
        report.differs(f"{difference}: set it at {ISSUE_TYPES_URL}")
    if not differences:
        report.ok(f"issue types: exactly {', '.join(ISSUE_TYPES)} enabled")


def issue_field_differences(fields: dict) -> list[str]:
    """Each way the organization's issue fields (``issueFields``, read as
    :data:`_ISSUE_FIELDS_QUERY`) differ from those declared, :data:`ISSUE_FIELDS`: each one
    present, of its type, and a single select holding exactly its options, in any order.
    A read GitHub cut short is a difference too: the fields it does not hold are unread."""
    nodes, differences = fields["nodes"], []
    if fields["totalCount"] > len(nodes):
        differences.append(f"the organization has {fields['totalCount']} issue fields and the "
                           f"read holds {len(nodes)}: the rest are unread")
    for name, declared in ISSUE_FIELDS.items():
        found = [node for node in nodes if node["name"] == name]
        if not found:
            differences.append(f"issue field {name!r} is missing: make it")
        for node in found:
            if node["dataType"] != declared.data_type:
                differences.append(
                    f"issue field {name!r} is a {node['dataType']}, not a "
                    f"{declared.data_type.lower().replace('_', ' ')}: correct it")
            elif declared.data_type == "SINGLE_SELECT" and sorted(
                    options := [option["name"] for option in node["options"]]
            ) != sorted(declared.options):
                differences.append(
                    f"issue field {name!r} holds the options {options}, not "
                    f"{declared.options_named} {list(declared.options)}: correct it")
    differences += [f"issue field {node['name']!r} is not declared here: delete it"
                    for node in nodes if node["name"] not in ISSUE_FIELDS]
    return differences


def check_issue_fields(github: GitHub, report: Report) -> None:
    """Check the organization's issue fields (changed on the web; see :data:`ISSUE_FIELDS`)."""
    differences = issue_field_differences(
        github.graphql(_ISSUE_FIELDS_QUERY, org=ORG)["organization"]["issueFields"])
    for difference in differences:
        report.differs(f"{difference} at {ISSUE_FIELDS_URL}")
    if not differences:
        report.ok(f"issue fields: exactly the {len(ISSUE_FIELDS)} declared: "
                  f"{', '.join(map(repr, ISSUE_FIELDS))}")


def check_card_arcs(github: GitHub, report: Report) -> None:
    """Check every card's arc labels against its Arc (ruling ``balance:R-BAL354``, views_spec
    s.6) over the read and by the rule ``quill sync``'s label pass makes
    (:func:`_arcs.card_arcs`, :func:`_arcs.arc_verdict`): each card it finds anything for is
    a difference, named as sync names it: the Arc sync sets from its one arc label (ruling
    plan#1856), what a person must fix in its Arc, which sync reports, or the arc labels sync
    puts back."""
    cards = card_arcs(github, ORG, REPO)
    differ = 0
    for card in cards:
        verdict = arc_verdict(card, card.kind == TASK)
        if verdict.fill:
            report.differs(f"card {REPO}#{card.number} holds no Arc: sync sets it to "
                           f"{verdict.fill!r}, the one arc label it carries (ruling plan#1856)")
        elif verdict.fault:
            report.differs(f"card {REPO}#{card.number} {verdict.fault}")
        elif verdict.add or verdict.remove:
            report.differs(f"card {REPO}#{card.number}'s arc labels do not display its Arc "
                           f"{card.arc!r}: it lacks {list(verdict.add)} and holds "
                           f"{list(verdict.remove)}")
        else:
            continue
        differ += 1
    if not differ:
        report.ok(f"every card's arc labels display its Arc ({len(cards)} cards)")


def _project(github: GitHub, project_id: str) -> dict:
    """One board, read by its id (a fresh board can lag in the organization's list)."""
    return github.graphql(_PROJECT, id=project_id)["node"]


def _board_listing(github: GitHub, place: Place) -> tuple[str, str | None]:
    """The organization's node id and the id of the board titled ``place``'s board title."""
    organization = github.graphql(_PROJECTS, org=place.owner)["organization"]
    title = place.board_title
    matches = [p for p in organization["projectsV2"]["nodes"] if p["title"] == title]
    if len(matches) > 1:
        raise GitHubError(200, f"{len(matches)} boards are titled {title!r}; keep one")
    return organization["id"], (matches[0]["id"] if matches else None)


def find_board(github: GitHub, place: Place) -> str | None:
    """The node id of ``place``'s board (None while there is none): the one lookup both
    this module and quill make."""
    return _board_listing(github, place)[1]


def _find_project(github: GitHub) -> tuple[str, dict | None]:
    """The organization's node id and the board titled :data:`PROJECT_TITLE`, if any."""
    org_id, board_id = _board_listing(github, PLAN)
    return org_id, (_project(github, board_id) if board_id else None)


def _apply_to_view(github: GitHub, project: dict, report: Report) -> None:
    """Give the board its :data:`VIEW_NAME` view, filtered to open cards.

    A view someone made for another purpose is never renamed: only a board's
    lone view still carrying GitHub's default name becomes the plan view, and
    any other board gets a new one.
    """
    views = project["views"]["nodes"]
    named = _plan_views(project)
    if named:
        view = named[0]
    elif len(views) == 1 and views[0]["name"] == GITHUB_DEFAULT_VIEW:
        view = views[0]
    else:
        created = github.graphql(
            "mutation($p: ID!, $name: String!) { createProjectV2View(input: {projectId: $p, "
            "name: $name, layout: TABLE_LAYOUT}) { projectV2View { id } } }",
            p=project["id"],
            name=VIEW_NAME,
        )
        view = {"id": created["createProjectV2View"]["projectV2View"]["id"], "name": VIEW_NAME,
                "filter": ""}
        report.made(f"board view {VIEW_NAME!r}")
    if (view["name"], view["filter"]) != (VIEW_NAME, VIEW_FILTER):
        github.graphql(
            "mutation($id: ID!, $name: String!, $filter: String!) { updateProjectV2View("
            "input: {viewId: $id, name: $name, filter: $filter}) { clientMutationId } }",
            id=view["id"],
            name=VIEW_NAME,
            filter=VIEW_FILTER,
        )
        report.made(f"board view {view['name']!r} -> {VIEW_NAME!r} ({VIEW_FILTER})")


def _set_view_columns(github: GitHub, project: dict, report: Report) -> None:
    """Give the plan view its columns in one write: every stored field but Status hidden
    (:func:`_board_fields.hidden`), its other columns kept, and the board's Parent issue field
    (:data:`VIEW_PARENT_COLUMN`) and Status shown."""
    board = project["fields"]["nodes"]
    for view in _plan_views(project):
        visible = [f for f in view["fields"]["nodes"] if f]
        hide = [f["name"] for f in visible if hidden(f)]
        show = [(name, column) for name, find in (("Parent issue", _parent_column),
                                                  (GITHUB_STATUS_FIELD, status_column))
                if (column := find(board)) is not None and find(visible) is None]
        if not (hide or show):
            continue
        github.graphql(
            "mutation($id: ID!, $fields: [ID!]!) { updateProjectV2View(input: {viewId: $id, "
            "configuration: {visibleFieldIds: $fields}}) { clientMutationId } }",
            id=view["id"],
            fields=[f["id"] for f in [*visible, *(column for _, column in show)]
                    if not hidden(f)],
        )
        if hide:
            report.made(f"hid {hide} from the {VIEW_NAME!r} view")
        for name, _ in show:
            report.made(f"showed the {name} column in the {VIEW_NAME!r} view")


def _apply_to_board(github: GitHub, project: dict, repository: dict, report: Report) -> None:
    """Make a board private, link it and name and filter its view; while it holds no
    card, delete every custom field that stores a value per card."""
    if project["public"]:
        github.graphql(
            "mutation($id: ID!) { updateProjectV2(input: {projectId: $id, public: false}) "
            "{ clientMutationId } }",
            id=project["id"],
        )
        report.made("board made private")
    if linked(PLAN, project["repositories"]) is False:
        github.graphql(
            "mutation($p: ID!, $r: ID!) { linkProjectV2ToRepository("
            "input: {projectId: $p, repositoryId: $r}) { clientMutationId } }",
            p=project["id"],
            r=repository["node_id"],
        )
        report.made(f"board linked to {REPO}")
    _apply_to_view(github, project, report)
    if project["items"]["totalCount"]:
        return
    for board_field in project["fields"]["nodes"]:
        if not stores_values(board_field) or is_github_status(board_field):
            continue
        try:
            github.graphql(
                "mutation($id: ID!) { deleteProjectV2Field(input: {fieldId: $id}) "
                "{ clientMutationId } }",
                id=board_field["id"],
            )
        except GitHubError as error:
            report.lines.append(f"refused deleting board field {board_field['name']!r}: {error}")
            continue
        report.made(f"deleted board field {board_field['name']!r} (the board held no card)")


def _item_differences(github: GitHub, project_id: str) -> list[str]:
    """One difference per board item, archived ones included, that is no card and holds a
    Status (:func:`status_differences`), and one per archived item: one read of every item,
    page by page."""
    differences, after = [], None
    while True:
        items = github.graphql(_BOARD_ITEMS, id=project_id, after=after)["node"]["items"]
        differences += status_differences(items["nodes"]) + archived_differences(items["nodes"])
        if not items["pageInfo"]["hasNextPage"]:
            return differences
        after = items["pageInfo"]["endCursor"]


def check_board(github: GitHub, repository: dict | None, apply: bool, report: Report) -> None:
    """Check (and with ``apply``, create and correct) the organization's board."""
    org_id, project = _find_project(github)
    if project is None:
        if not apply or repository is None:
            report.differs(f"the board {PROJECT_TITLE!r} does not exist")
            return
        created = github.graphql(
            "mutation($owner: ID!, $title: String!, $repo: ID!) { createProjectV2("
            "input: {ownerId: $owner, title: $title, repositoryId: $repo}) "
            "{ projectV2 { id } } }",
            owner=org_id,
            title=PROJECT_TITLE,
            repo=repository["node_id"],
        )
        report.made(f"board {PROJECT_TITLE!r}")
        project = _project(github, created["createProjectV2"]["projectV2"]["id"])
    if apply and repository is not None:
        _apply_to_board(github, project, repository, report)
        project = _project(github, project["id"])
        _set_view_columns(github, project, report)
        project = _project(github, project["id"])
    differences = board_differences(project) + _item_differences(github, project["id"])
    for difference in differences:
        report.differs(difference)
    if not differences:
        report.ok(
            f"board {PROJECT_TITLE!r} ({project['url']}): no stored field but GitHub's "
            f"{GITHUB_STATUS_FIELD}, holding exactly {', '.join(STATUS_OPTIONS)} and on no item "
            f"that is no card, no automation on, no archived item, an unsorted {VIEW_NAME!r} "
            f"table of open cards, ungrouped, showing {GITHUB_STATUS_FIELD} and the Parent "
            "issue column and no other stored field"
        )
    for view in _plan_views(project):
        report.unread(f"the {VIEW_NAME!r} view's \"Show hierarchy\", which must be on so each "
                      "step's tasks nest under it: no API reads it, so look at "
                      f"{project['url']}/views/{view['number']}")


def another_app(slug: str) -> str | None:
    """Why credentials that sign in as the App ``slug`` are not quill's, in plain words; None
    when ``slug`` is :data:`APP_LOGIN`.  The one test, for :func:`check_app`'s difference
    and ``Tracker.connect``'s refusal."""
    if slug == APP_LOGIN:
        return None
    return (f"the App's credentials in {APP_DIR} sign in as {slug!r}, not as quill's App "
            f"{APP_LOGIN!r}: put its credentials there")


def check_app(report: Report) -> None:
    """Check quill's App: the one :data:`APP_LOGIN` names, registered by the organization,
    installed on the tracker only."""
    try:
        client_id, private_key = app_credentials()
        token = app_jwt(client_id, private_key, int(time.time()))
    except FileNotFoundError:
        report.differs(f"the App's credentials are not in {APP_DIR} (app.json, app.pem)")
        return
    except CredentialsError as error:
        report.differs(f"the App's credentials in {APP_DIR} cannot be used: {error}")
        return
    app = GitHub(token).rest("GET", "/app")
    if wrong := another_app(app["slug"]):
        report.differs(wrong)
    if app["owner"]["login"] != ORG:
        report.differs(f"the App {app['slug']!r} is registered by {app['owner']['login']}")
    try:
        installation = app_installation(ORG, token)
    except GitHubError as error:
        if error.status != 404:
            raise
        report.differs(f"the App {app['slug']!r} is not installed on {ORG}")
        return
    differences = permission_differences(APP_PERMISSIONS, installation["permissions"])
    if installation["repository_selection"] != "selected":
        differences.append("the App is installed on ALL of the organization's repositories")
    # The whole installation, never narrowed to one repository: this check is what
    # sees every repository the App reaches.
    scoped = GitHub(installation_token(installation["id"], token, repository=None))
    names = [r["name"] for r in scoped.rest("GET", "/installation/repositories")["repositories"]]
    if names != [REPO]:
        differences.append(f"the App reaches {names}, not only {REPO}")
    for difference in differences:
        report.differs(difference)
    if not differences:
        report.ok(f"App {app['slug']!r}: installed on {REPO} only, permissions as declared")


def main(argv: list[str] | None = None) -> int:
    """Run the checks (and with ``--apply``, the fixes); 0 when nothing differs."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="make and correct, not only check")
    apply = parser.parse_args(argv).apply
    github = GitHub(user_token())
    report = Report()
    try:
        repository = check_repository(github, apply, report)
        if repository is not None:
            check_labels(github, apply, report)
            check_card_arcs(github, report)
        check_issue_types(github, report)
        check_issue_fields(github, report)
        check_board(github, repository, apply, report)
        check_app(report)
    except (GitHubError, TrackerError, requests.RequestException) as error:
        print("\n".join(report.lines))
        print(f"failed: {described(error)}", file=sys.stderr)
        return 2
    print("\n".join(report.lines))
    print(f"{report.differences} difference(s)")
    return 1 if report.differences else 0


if __name__ == "__main__":
    sys.exit(main())
