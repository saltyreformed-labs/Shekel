"""The one check a card passes before it is written, whoever writes it.

Quill runs it before it sends a card (``quill file``) and refuses to
send what fails.  The tracker's Action (step X-cx's leaf L6) runs the SAME
function on a card the developer files or edits by hand, and comments on what
fails without reverting it -- so the two hold a card to one set of rules.  A card is
described to it as a :class:`_draft.Draft`, the card a filing then files.  What
each passes in can differ, never the rules: whether the owner is still work
(:attr:`Owner.live`), which quill reads from git and the cards
(``_state.is_live``) and the Action from whatever it reads; and ``changed``, the
parts a change writes, which a new card's filing and a hand edit name
differently.

What it holds a card to (rulings ``balance:R-BAL136``, ``R-BAL138``,
``R-BAL177``, ``R-BAL178``, ``R-BAL179``, ``R-BAL180``, ``R-BAL354`` and ``R-BAL395``):

- always: its type is one of the four kinds, and it holds one arc, the value of its issue
  field Arc, the arc's one home (rulings ``balance:R-BAL349``, ``R-BAL354``: its arc label is
  display, which no check reads) -- or it is a TASK (ruling ``balance:R-BAL386``), a piece of
  one step or finding, which holds none: its arc is its card's (rule 14), and a task set
  under a parent is a sub-issue of a LIVE step or finding;
- a title the change writes is a short name: present, and at most
  :data:`TITLE_CAP` characters;
- an owner the change sets is the one its kind needs: a finding is blocked by a
  LIVE step a branch builds, never one split into smaller steps (a finding names a live
  owner, and some pull request always owes it: plan#1784, "One owner, always a leaf"), a
  ruling by a step, and a question that names an owner by the step it is asked for
  (plan#1861's item 10), each never its sub-issue, which the step's progress would count
  (``R-BAL395``); a step's
  parent, if it has one, is the LIVE step it splits (a step already done with has
  no work left to split); an owner with no type is no step, whatever the kind;
- a body the change writes is capped: a finding's is one sentence of at most
  400 characters, and a ruling's is :func:`ruling_body`'s shape -- the
  developer's question, then his answer -- at most 2,000 characters in all.
  That they are HIS words, word for word, is the filer's duty; no check can see
  it.
- a Priority or an Effort the change writes is held by a kind that may hold it: an Effort
  by a step or a finding alone, the work a branch builds (ruling plan#2506), and a Priority
  by a step, a finding or a question, what waits on work or on the developer's answer
  (ruling plan#2507); a clear, which writes none, any card may take.
- a suggested Priority the change writes (:class:`_draft.Suggestion`) is held by a step at the
  top level or a question (:func:`takes_suggestion`), never a finding (ruling plan#3226) nor a
  leaf (ruling plan#3229); it is one of :data:`_fields.SUGGESTED_OPTIONS`, and its reason one
  sentence of at most :data:`REASON_CAP` characters (rulings plan#3224, plan#3236).  That a
  filing of such a card names one is the filing's rule (``_filing``), not this check's: a card
  a person files by hand sets its Priority, or none, and suggests nothing.

**Only what a change writes is graded** (``R-BAL136``: "rows nobody edits keep
their text until they close").  A caller names the parts it writes in
``changed``; a new card writes all of them (:data:`NEW_CARD`).  So a migrated
ruling that names no owner, or a finding whose owner has since shipped, is
not refused when only its labels change.

Real production figures are ALLOWED in the private tracker (``R-BAL172``), so
nothing here looks for them.  Nothing here reads GitHub: a caller describes
the card as a :class:`Draft`.  :data:`RULING_CAP` is R-BAL136's 2,000's one
spelling since step X-cx's S3b deleted the plan gate, whose ``RULINGS_ROW_CAP``
spelled it again for a ``rulings.md`` row.
"""
from __future__ import annotations

import re
from collections.abc import Collection

from tools.ci.arcs import ARCS
from tools.quill._draft import Draft, Owner
from tools.quill._fields import EFFORT, FIELDS, PRIORITY, SUGGESTED_KINDS, SUGGESTED_OPTIONS
from tools.quill._state import SHIPPABLE, may_own
from tools.quill.setup_tracker import CARD_TYPES, TASK

#: A NEW finding's text: one sentence of at most this many characters (R-BAL136).
FINDING_CAP = 400
#: A NEW ruling's text: question and answer, at most this many characters
#: (R-BAL136, made a hard cap by R-BAL138).
RULING_CAP = 2000
#: A title a change writes: a short name (R-BAL178) of at most this many
#: characters (R-BAL180).  GitHub's own limit, 1,024 bytes
#: (measured 2026-10-04: 1,024 ASCII characters taken, 1,025 refused), is far
#: above it.
TITLE_CAP = 100
#: A suggestion's reason: one sentence of at most this many characters, GitHub's own cap on a
#: suggestion's rationale (its GraphQL schema, ``IssueFieldCreateOrUpdateInput.rationale``,
#: read 2026-11-10: "Max 280 characters.").  Measured 2026-11-10 16:21 UTC on plan#3178 by
#: ``_filing._suggest`` as quill's App: a reason of 280 ASCII characters taken and read back
#: whole.  A reason of 281, and how GitHub counts a character outside ASCII, are not measured.
REASON_CAP = 280

#: The parts of a card a change can write, beside :data:`_fields.PRIORITY` and
#: :data:`_fields.EFFORT`, the parts holding its Priority and Effort.
TITLE, BODY, OWNER = "title", "body", "owner"
#: A new card writes every part.
NEW_CARD = frozenset({TITLE, BODY, OWNER, PRIORITY, EFFORT})
#: The kinds of card that may hold each issue field quill writes beside the Arc
#: (:data:`_fields.FIELDS`), by the part writing it, and the ruling saying so: an Effort the
#: work a branch builds (ruling plan#2506), a Priority what waits on work or on the
#: developer's answer (ruling plan#2507).  A step split into smaller steps holds no Effort
#: (ruling plan#2555), which the callers apply, as whether a step is split reads the steps
#: linked under it and git (``_filing._finish`` clears it, ``_arc_label.cmd_field`` refuses it).
HOLDERS = {
    PRIORITY: (("step", "finding", "question"), "plan#2507"),
    EFFORT: (("step", "finding"), "plan#2506"),
}

_QUESTION, _ANSWER = "Question: ", "\n\nAnswer: "
#: Where a sentence may end: terminal punctuation (not the last dot of an
#: ellipsis), then any closing quote, bracket or Markdown mark.  A cell cut
#: mid-word fails it; a cut that lands just after a full stop passes, which no
#: punctuation test can see.
_END = r"(?<!\.\.)[.!?][)\"'`*_\]]*"
_ENDS = re.compile(_END + r"\Z")
#: A second sentence INSIDE the text: an end, whitespace, then a capital or an
#: opening mark (backtick, bold or italic, a quote, a bracket or parenthesis).
#: Not a digit: ``P.L. 119`` is one sentence.  Not the dot closing an initialism
#: of two letters or more (``U.S.``, ``e.g.``): a letter, a dot and a letter
#: just before it.  A single letter's dot (``plan A. The ...``) still ends one.
_BREAK = re.compile(r"(?<![A-Za-z]\.[A-Za-z])" + _END + r"\s+[A-Z`*_\"'(\[]")


def alias_mark(alias: str) -> str:
    """How a card's title carries the old id it is known by: ``[alias]``, first, then its
    short name (R-BAL237), as X-cx's migration titled every card it filed.  ``quill show
    OLD-ID`` finds a card by it."""
    return f"[{alias}]"


def ruling_body(question: str, answer: str) -> str:
    """A ruling card's body: the developer's question, then his answer, each word for word."""
    return f"{_QUESTION}{question.strip()}{_ANSWER}{answer.strip()}"


def ruling_question(body: str | None, answer: str) -> str | None:
    """The question of a body that :func:`ruling_body` wrote with THIS ``answer``;
    None for any other body.

    A conversion cut short (R-BAL186: the same command finishes it) may already
    have written the card's body, so the retry reads its question back instead
    of wrapping it a second time.  Only the exact text that conversion writes is
    read back -- ``Question: ``, then anything, then a blank line and this
    answer -- so a question that merely quotes a ruling's shape, or a ruling
    holding another answer, is never cut at a mark inside it.
    """
    text, tail = normalized(body), _ANSWER + normalized(answer)
    if not text.startswith(_QUESTION) or not text.endswith(tail):
        return None
    return text[len(_QUESTION):len(text) - len(tail)].strip()


def in_ruling_shape(body: str | None) -> bool:
    """Whether a body reads as :func:`ruling_body`'s shape: ``Question: ``, then a blank line
    and ``Answer: `` somewhere after it."""
    return _ruling_parts(normalized(body)) is not None


def _ruling_parts(text: str) -> tuple[str, str] | None:
    """``(question, answer)`` of a text in :func:`ruling_body`'s shape, each stripped;
    None when either mark is missing."""
    question, found, answer = text.removeprefix(_QUESTION).partition(_ANSWER)
    if not text.startswith(_QUESTION) or not found:
        return None
    return question.strip(), answer.strip()


def normalized(body: str | None) -> str:
    """A body as the rules read it: CRLF as a web form may send it read as LF, the
    space around it dropped, and GitHub's null for an empty body read as empty."""
    return (body or "").replace("\r\n", "\n").strip()


def _sentence_problems(text: str, what: str, cap: int, source: str) -> list[str]:
    """Why ``text`` is not one sentence of at most ``cap`` characters, ``what`` naming it
    and ``source`` the cap's: a finding's text (:data:`FINDING_CAP`, R-BAL136) and a
    suggestion's reason (:data:`REASON_CAP`, GitHub's) are each one."""
    problems = []
    if "\n" in text:
        problems.append(f"{what} is one sentence, on one line")
    if not _ENDS.search(text):
        problems.append(
            f"{what} is one complete sentence, ending in '.', '!' or '?'; "
            f"it ends {text[-40:]!r}"
        )
    elif found := _BREAK.search(text):
        problems.append(
            f"{what} is ONE sentence; a second begins at "
            f"{text[found.end() - 1:][:40]!r}"
        )
    if len(text) > cap:
        problems.append(f"{what} is at most {cap} characters ({source}); it is {len(text)}")
    return problems


def takes_suggestion(draft: Draft) -> bool:
    """Whether the card ``draft`` describes takes a suggested Priority: one of
    :data:`SUGGESTED_KINDS`, a step at the top level alone, the cards the Inbox lists while
    they hold no Priority (ruling plan#3231); never a finding, whose owner step's Priority
    decides (ruling plan#3226), nor a leaf, which its split step's ranks (ruling plan#3229).
    The one spelling of that set, which the check holds a suggestion to and the filing
    requires one of (``_filing``); GitHub, not quill, reads the Inbox's filter, so it spells
    the set in the board's filter language, its kinds read from :data:`SUGGESTED_KINDS`
    (``_board_views.INBOX``)."""
    return draft.kind in SUGGESTED_KINDS and not (draft.kind == "step"
                                                   and draft.owner is not None)


def _suggestion_problems(draft: Draft) -> list[str]:
    """Why a suggested Priority the change writes is not one the card may hold: it is held by
    a card :func:`takes_suggestion` says takes one, is one of :data:`SUGGESTED_OPTIONS`, and
    its reason is one sentence of at most :data:`REASON_CAP` characters (rulings plan#3224,
    plan#3236); none when the change writes none."""
    if (suggestion := draft.suggestion) is None:
        return []
    problems = []
    if not takes_suggestion(draft):
        held = ("leaf" if draft.kind == "step" else draft.kind) or "card with no type"
        problems.append(f"a suggested Priority is held by a step at the top level or a question "
                        f"alone (rulings plan#3226, plan#3229); this {held} would hold "
                        f"{suggestion.priority!r}")
    if suggestion.priority not in SUGGESTED_OPTIONS:
        problems.append(f"a suggested Priority is one of {', '.join(SUGGESTED_OPTIONS)}; it is "
                        f"{suggestion.priority!r}")
    return problems + _sentence_problems(suggestion.why, "a suggestion's reason", REASON_CAP,
                                         "GitHub's cap")


def _ruling_problems(text: str) -> list[str]:
    """Why ``text`` is not a question and answer within :data:`RULING_CAP` characters."""
    problems = []
    parts = _ruling_parts(text)
    if parts is None or not all(parts):
        problems.append(
            "a ruling's text is the developer's question and his answer, word for word: "
            f"{_QUESTION.strip()!r} then a blank line and {_ANSWER.strip()!r}"
        )
    if len(text) > RULING_CAP:
        problems.append(
            f"a ruling's text is at most {RULING_CAP} characters (R-BAL136, R-BAL138); "
            f"it is {len(text)}"
        )
    return problems


def _parent_named(owner: Owner | None) -> str:
    """A card's owner (:class:`Owner`) as a problem names it: none, a card with no type, a
    step or finding that is done, a step split into smaller steps, or its kind."""
    if owner is None:
        return "none"
    if owner.kind is None:
        return "a card with no type"
    if owner.kind in SHIPPABLE and not owner.live:
        return f"a {owner.kind} that is done"
    if owner.kind == "step" and owner.split:
        return "a step split into smaller steps"
    return owner.kind


def _owner_problems(draft: Draft) -> list[str]:
    """Why a card's owner is not the one its kind needs."""
    owner = draft.owner
    parent = _parent_named(owner)
    is_step = owner is not None and owner.kind == "step"
    if draft.kind == "finding" and not (owner is not None
                                        and may_own(owner.kind, owner.live, owner.split)):
        problem = (f"a finding is blocked by a LIVE step, its owner (R-BAL395), one a branch "
                   f"builds and never one split into smaller steps (plan#1784); its owner is "
                   f"{parent}")
    elif draft.kind == "ruling" and not is_step:
        problem = (f"a ruling is blocked by a step, its owner (R-BAL395); "
                   f"its owner is {parent}")
    elif draft.kind == "question" and owner is not None and not is_step:
        problem = (f"a question that names an owner is blocked by the step it is asked for "
                   f"(R-BAL395); its owner is {parent}")
    elif draft.kind == "step" and owner is not None and not is_step:
        problem = (f"a step's parent is the step it splits, not a {owner.kind}" if owner.kind
                   else f"a step's parent is the step it splits; its parent is {parent}")
    elif draft.kind == "step" and owner is not None and not owner.live:
        problem = f"a step's parent is the LIVE step it splits; its parent is {parent}"
    else:
        return []
    return [problem]


def _title_problems(title: str, changed: Collection[str]) -> list[str]:
    """Why a title the change writes is not a short name (R-BAL178) of at most
    :data:`TITLE_CAP` characters (R-BAL180); none when the change writes no title."""
    title = title.strip()
    if TITLE in changed and not title:
        return ["its title is a short name, and it has none"]
    if TITLE in changed and len(title) > TITLE_CAP:
        return [f"its title is a short name of at most {TITLE_CAP} characters; "
                f"it is {len(title)}"]
    return []


def _field_problems(draft: Draft, changed: Collection[str]) -> list[str]:
    """Why a Priority or an Effort the change writes is on a kind that may not hold it
    (:data:`HOLDERS`); none for a field the change does not write, or writes as none (a
    clear), which any card may take, one holding a value set on the web among them."""
    problems = []
    for part, (kinds, ruling) in HOLDERS.items():
        value = getattr(draft, part)
        if part in changed and value is not None and draft.kind not in kinds:
            problems.append(f"{FIELDS[part]} is held by a {', '.join(kinds[:-1])} or {kinds[-1]} "
                            f"alone (ruling {ruling}); this {draft.kind or 'card with no type'} "
                            f"would hold {value!r}")
    return problems


def _task_problems(draft: Draft, changed: Collection[str]) -> list[str]:
    """Every way a task breaks the plan's rules (R-BAL386): it holds no Arc, since its arc is
    its card's (rule 14); a title it writes is a short name; and a parent it sets
    is a LIVE step or finding, the piece of work it is part of (whether that is a step split
    into smaller steps, which is no work, is the caller's to refuse: it reads the steps
    linked under it); and it takes no suggested Priority (:func:`takes_suggestion`)."""
    problems = []
    if draft.arc is not None:
        problems.append(f"a task holds no Arc: its arc is its card's; it holds {draft.arc!r}")
    problems += _title_problems(draft.title, changed)
    problems += _field_problems(draft, changed)
    problems += _suggestion_problems(draft)
    owner = draft.owner
    if OWNER in changed and not (owner is not None and owner.kind in SHIPPABLE and owner.live):
        problems.append("a task is a sub-issue of a LIVE step or finding, the piece of work it "
                        f"is part of (R-BAL386); its parent is {_parent_named(owner)}")
    return problems


def violations(draft: Draft, changed: Collection[str] = NEW_CARD) -> list[str]:
    """Every way ``draft`` breaks the plan's rules; empty when it may be written.

    ``changed``: the parts the change writes (:data:`TITLE`, :data:`BODY`,
    :data:`OWNER`, :data:`PRIORITY`, :data:`EFFORT`); only those are graded beyond the type
    and the Arc, and a suggested Priority the draft holds, which only a change writing one
    holds (:attr:`_draft.Draft.suggestion`).  A task (:data:`setup_tracker.TASK`) is held to
    its own rules (:func:`_task_problems`).
    """
    if draft.kind == TASK:
        return _task_problems(draft, changed)
    problems = []
    if draft.kind not in CARD_TYPES:
        problems.append(f"its type is {draft.kind!r}, not one of {', '.join(CARD_TYPES)}")
    if draft.arc not in ARCS:
        problems.append(f"it holds one arc in its Arc field, one of {', '.join(ARCS)}; it holds "
                        f"{'none' if draft.arc is None else repr(draft.arc)}")
    problems += _title_problems(draft.title, changed)
    problems += _field_problems(draft, changed)
    problems += _suggestion_problems(draft)
    if OWNER in changed:
        problems += _owner_problems(draft)
    body = normalized(draft.body)
    if BODY in changed and draft.kind == "finding":
        problems += _sentence_problems(body, "a finding's text", FINDING_CAP, "R-BAL136")
    elif BODY in changed and draft.kind == "ruling":
        problems += _ruling_problems(body)
    return problems
