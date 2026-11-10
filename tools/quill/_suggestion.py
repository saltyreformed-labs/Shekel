"""quill's suggested Priority on a card: GitHub's own pending suggestion on the card's issue
field Priority, its reason the suggestion's rationale (rulings plan#3224, plan#3233,
plan#3236), which the developer accepts or sets aside, the card's Priority blank until he does.

:class:`Suggestions` reads the one quill's App made on a card (:meth:`Suggestions.pending`),
puts one (:meth:`Suggestions.put`) and takes quill's own back (:meth:`Suggestions.withdraw`),
each write read back; :func:`take_back` takes back the one a card holds, for a write deciding
its Priority.  GitHub's REST refuses a suggestion (measured 2026-11-10 on plan#3178:
"rationale", "suggest" are not permitted keys), so the two writes are GraphQL mutations, sent
through :meth:`Suggestions._write`, the second door beside ``_tracker.Board._write`` (ruling
plan#3257): it names one card's node id as ``$issue``, a card the tracker read, and no other.
The board needs its door because it is the organization's, which a node id can reach whatever
the token; a card is the tracker repository's, and quill's App token is narrowed to that
repository (``_tracker.Tracker.connect``).  ``test__board_fence.py`` holds each mutation
quill's source shows to one of the two doors, and names what it cannot see, a door's own
``_write`` among them: a ``_write`` naming another ``$issue`` passes it, so a reviewer of a
change to this one reads it.

What GitHub does, measured 2026-11-10 on the task plan#3178 with the developer's go-ahead
(rulings plan#3224, plan#3233):

- quill's App puts a suggestion, reads a card's pending suggestions and takes its own back
  (its actor a Bot's node id), each through this module's code, but reads not the issue field
  a suggestion targets (``issueField`` is answered FORBIDDEN, "Resource not accessible by
  integration").  So a suggestion is read as quill's by its actor's login, and as Priority's
  because quill suggests no other field: :meth:`Suggestions.put` names Priority alone.
- While it is pending the card's Priority holds nothing.  The developer setting another
  Priority on GitHub's own screen cleared it; the same set through the API left it pending
  beside the value, so a write setting a Priority takes quill's back.
- Once set aside, a suggestion left nothing in the card's timeline (ruling plan#3236: quill
  keeps no copy).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tools.quill import _queries
from tools.quill._draft import Suggestion
from tools.quill._fields import PRIORITY_FIELD
from tools.quill._github import GitHub, TrackerError, reading
from tools.quill.setup_tracker import Place

if TYPE_CHECKING:
    from tools.quill._tracker import BareCard

#: The suggestion's two writes, the only GraphQL mutations quill sends beside the board's, each
#: sent by :meth:`Suggestions._write` alone: ``$issue`` is the card's node id, ``$field`` its
#: issue field Priority's, ``$option`` the option suggested, ``$why`` its reason, ``$actor``
#: the node id of who made the suggestion taken back.
SUGGEST = ("mutation($issue: ID!, $field: ID!, $option: ID!, $why: String!) { "
           "setIssueFieldValue(input: {issueId: $issue, issueFields: [{fieldId: $field, "
           "singleSelectOptionId: $option, suggest: true, rationale: $why}]}) "
           "{ clientMutationId } }")
WITHDRAW = ("mutation($issue: ID!, $actor: ID!, $field: ID!) { rejectPendingIssueSuggestions("
            "input: {issueId: $issue, actorId: $actor, suggestions: [{kind: FIELD, "
            "issueFieldId: $field}]}) { clientMutationId } }")


@dataclass(frozen=True)
class Pending:
    """quill's pending suggestion on a card, as read: what it suggests, and ``actor``, the node
    id of the account that made it, which taking it back names."""

    suggestion: Suggestion
    actor: str


class Suggestions:
    """``place``'s cards' suggested Priorities, read and written through ``github`` as
    ``app_login``, the login a suggestion of quill's is read by."""

    def __init__(self, github: GitHub, place: Place, app_login: str) -> None:
        """Read and write ``place``'s suggestions through ``github`` as ``app_login``."""
        self.github = github
        self.place = place
        self.app_login = app_login

    def _write(self, mutation: str, card: BareCard, **variables: str) -> dict:
        """Send one of the suggestion's writes, naming ``card``'s node id as ``$issue`` and no
        other: the door every write of a suggestion passes through (ruling plan#3257)."""
        return self.github.graphql(mutation, issue=card.node_id, **variables)

    def pending(self, card: BareCard) -> Pending | None:
        """quill's pending suggestion on ``card``: the one suggestion on an issue field whose
        actor is :attr:`app_login`; None when it holds none.

        Raises:
            TrackerError: When GitHub answers the card as null, or it holds more than one
                suggestion of quill's, which makes one.
        """
        with reading(f"plan#{card.number}'s pending suggestions"):
            issue = self.github.graphql(_queries.pending_suggestions(self.place),
                                        number=card.number)["repository"]["issue"]
        if issue is None:
            raise TrackerError(f"plan#{card.number} could not be read: GitHub answered it as "
                               "null")
        own = [node for node in issue["pendingSuggestions"]
               if node.get("__typename") == "PendingFieldSuggestion"
               and (node.get("actor") or {}).get("login") == self.app_login]
        if len(own) > 1:
            raise TrackerError(f"plan#{card.number} holds {len(own)} pending suggestions of "
                               f"{self.app_login}'s, and quill makes one: set them aside on "
                               "the web")
        if not own:
            return None
        node = own[0]
        return Pending(Suggestion(node["suggestedValue"], node["rationale"] or ""),
                       node["actor"]["id"])

    def _priority(self) -> tuple[str, dict[str, str]]:
        """The node id of the issue field Priority and each of its options' by name, in one
        read (:func:`_queries.select_options`).

        Raises:
            TrackerError: When the repository holds no one field named Priority in a read
                GitHub may have cut short.
        """
        with reading(f"{self.place.full_name}'s issue fields' options"):
            fields = self.github.graphql(_queries.select_options(self.place))[
                "repository"]["issueFields"]
        found = [node for node in fields["nodes"] if (node or {}).get("name") == PRIORITY_FIELD]
        if len(found) != 1:
            raise TrackerError(
                f"{self.place.full_name} holds {len(found)} issue fields named "
                f"{PRIORITY_FIELD!r} in a read of {len(fields['nodes'])} of its "
                f"{fields['totalCount']}: run python -m tools.quill.setup_tracker")
        return found[0]["id"], {option["name"]: option["id"] for option in found[0]["options"]}

    def put(self, card: BareCard, suggestion: Suggestion) -> None:
        """Suggest ``suggestion``'s Priority for ``card``, its reason ``suggestion.why``, read
        back (:meth:`pending`).

        Raises:
            TrackerError: When Priority has no such option, or the read after the write does
                not hold the suggestion as written.
        """
        field, options = self._priority()
        if suggestion.priority not in options:
            raise TrackerError(f"the issue field {PRIORITY_FIELD} has no option "
                               f"{suggestion.priority!r}, only {sorted(options)}: run python -m "
                               "tools.quill.setup_tracker")
        self._write(SUGGEST, card, field=field, option=options[suggestion.priority],
                    why=suggestion.why)
        held = self.pending(card)
        if held is None or held.suggestion != suggestion:
            raise TrackerError(f"plan#{card.number} holds the pending suggestion "
                               f"{held.suggestion if held else None} after {suggestion} was "
                               "written: fix it on the web")

    def withdraw(self, card: BareCard, pending: Pending) -> None:
        """Take back ``pending``, quill's suggestion on ``card`` as :meth:`pending` read it,
        read back.

        Raises:
            TrackerError: When the read after it still holds a suggestion of quill's.
        """
        field, _ = self._priority()
        self._write(WITHDRAW, card, actor=pending.actor, field=field)
        if (held := self.pending(card)) is not None:
            raise TrackerError(f"plan#{card.number} still holds quill's pending suggestion "
                               f"{held.suggestion} after it was taken back: set it aside on "
                               "the web")


def take_back(suggestions: Suggestions, card: BareCard) -> Pending | None:
    """Take back quill's pending suggestion on ``card``, read back (:meth:`Suggestions.pending`,
    :meth:`Suggestions.withdraw`): the one taken back, None when none was pending.  For a write
    that decides the card's Priority, which GitHub's API leaves the suggestion pending beside
    (measured 2026-11-10 on plan#3178), so a later Accept would overwrite it: ``quill
    priority``'s set, a filing finished over a card a person gave a Priority while it was
    still being filed (``_filing._suggest``), and a question's conversion into its ruling,
    which holds none (ruling plan#2507).  Never for a clear, which decides nothing: the card
    holds no Priority after it, the state the suggestion waits in, and the reason, of which
    quill keeps no copy (ruling plan#3236), would be lost.  ``suggestions`` is the tracker's,
    the tests' fake among them."""
    if (pending := suggestions.pending(card)) is not None:
        suggestions.withdraw(card, pending)
    return pending
