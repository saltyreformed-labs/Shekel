"""The one check a card passes before it is written, whoever writes it.

Quill runs it before it sends a card (``quill file``) and refuses to
send what fails.  The tracker's Action (step X-cx's leaf L6) runs the SAME
function on a card the developer files or edits by hand, and comments on what
fails without reverting it -- so the two hold a card to one set of rules.  What
each passes in can differ, never the rules: whether the owner is still work
(:attr:`Owner.live`), which quill reads from git and the cards
(``_state.is_live``) and the Action from whatever it reads; and ``changed``, the
parts a change writes, which a new card's filing and a hand edit name
differently.

What it holds a card to (rulings ``balance:R-BAL136``, ``R-BAL138``,
``R-BAL177``, ``R-BAL178``, ``R-BAL179``, ``R-BAL180`` and ``R-BAL395``):

- always: its type is one of the four kinds, and it carries exactly one arc
  label -- or it is a TASK (ruling ``balance:R-BAL386``), a piece of one step or finding,
  which carries none: its arc is its card's (rule 14), and a task set under a parent is a
  sub-issue of a LIVE step or finding;
- a title the change writes is a short name: present, and at most
  :data:`TITLE_CAP` characters;
- an owner the change sets is the one its kind needs: a finding is blocked by a
  LIVE step (a finding names a live owner) and a ruling by a step, each never its
  sub-issue, which the step's progress would count (``R-BAL395``); a step's
  parent, if it has one, is the LIVE step it splits (a step already done with has
  no work left to split); an owner with no type is no step, whatever the kind;
- a body the change writes is capped: a finding's is one sentence of at most
  400 characters, and a ruling's is :func:`ruling_body`'s shape -- the
  developer's question, then his answer -- at most 2,000 characters in all.
  That they are HIS words, word for word, is the filer's duty; no check can see
  it.

**Only what a change writes is graded** (``R-BAL136``: "rows nobody edits keep
their text until they close").  A caller names the parts it writes in
``changed``; a new card writes all of them (:data:`NEW_CARD`).  So a migrated
ruling that names no owner, or a finding whose owner has since shipped, is
not refused when only its labels change.

Real production figures are ALLOWED in the private tracker (``R-BAL172``), so
nothing here looks for them.  Nothing here reads GitHub: a caller describes
the card as a :class:`Draft`.  Until step X-cx's cutover (L8) deletes the plan
gate's registry arms, ``tools/plan_gate/_rulings.RULINGS_ROW_CAP`` spells
R-BAL136's 2,000 again, for a ``rulings.md`` row.
"""
from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass

from tools.ci.arcs import ARCS
from tools.quill._state import SHIPPABLE
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

#: The parts of a card a change can write.
TITLE, BODY, OWNER = "title", "body", "owner"
#: A new card writes every part.
NEW_CARD = frozenset({TITLE, BODY, OWNER})

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


@dataclass(frozen=True)
class Owner:
    """A card's owner, as the check reads it -- the card a finding or ruling is blocked
    by (``R-BAL395``), a step's or task's parent: its issue type (None when it has
    none), and the caller's answer to whether it is still work -- quill
    asks git and the card, the Action the card."""

    kind: str | None
    live: bool


@dataclass(frozen=True)
class Draft:
    """A card as it would stand after the change; ``owner`` None when it has none
    (:class:`Owner`)."""

    kind: str | None
    title: str
    body: str | None
    labels: tuple[str, ...]
    owner: Owner | None = None


def alias_mark(alias: str) -> str:
    """How a card's title carries the old id it is known by: ``[alias]``, first.  ``quill
    show OLD-ID`` finds a card by it; X-cx's migration titles every card with it (draft 4
    s.3, :func:`card_title`)."""
    return f"[{alias}]"


def card_title(alias: str, name: str) -> str:
    """A migrated card's title: its :func:`alias_mark`, then its short name (R-BAL237)."""
    return f"{alias_mark(alias)} {name.strip()}"


def title_alias(title: str) -> str | None:
    """The old id a title carries as :func:`card_title` writes it -- its
    :func:`alias_mark`, first, then a space -- or None for a title that carries none.
    X-cx's migration finds each item's card by it (L7 draft 4 s.4).  An alias holds no
    ``]``: the first one closes the mark."""
    alias, closed, _ = title.removeprefix("[").partition("] ")
    if not title.startswith("[") or not closed or not alias or "]" in alias:
        return None
    return alias


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


def _sentence_problems(text: str) -> list[str]:
    """Why ``text`` is not one sentence of at most :data:`FINDING_CAP` characters."""
    problems = []
    if "\n" in text:
        problems.append("a finding's text is one sentence, on one line")
    if not _ENDS.search(text):
        problems.append(
            f"a finding's text is one complete sentence, ending in '.', '!' or '?'; "
            f"it ends {text[-40:]!r}"
        )
    elif found := _BREAK.search(text):
        problems.append(
            f"a finding's text is ONE sentence; a second begins at "
            f"{text[found.end() - 1:][:40]!r}"
        )
    if len(text) > FINDING_CAP:
        problems.append(
            f"a finding's text is at most {FINDING_CAP} characters (R-BAL136); it is "
            f"{len(text)}"
        )
    return problems


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
    step or finding that is done, or its kind."""
    if owner is None:
        return "none"
    if owner.kind is None:
        return "a card with no type"
    if owner.kind in SHIPPABLE and not owner.live:
        return f"a {owner.kind} that is done"
    return owner.kind


def _owner_problems(draft: Draft) -> list[str]:
    """Why a card's owner is not the one its kind needs."""
    owner = draft.owner
    parent = _parent_named(owner)
    is_step = owner is not None and owner.kind == "step"
    if draft.kind == "finding" and not (is_step and owner.live):
        problem = (f"a finding is blocked by a LIVE step, its owner (R-BAL395); "
                   f"its owner is {parent}")
    elif draft.kind == "ruling" and not is_step:
        problem = (f"a ruling is blocked by a step, its owner (R-BAL395); "
                   f"its owner is {parent}")
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


def _task_problems(draft: Draft, changed: Collection[str]) -> list[str]:
    """Every way a task breaks the plan's rules (R-BAL386): it carries no arc label, since
    its arc is its card's (rule 14); a title it writes is a short name; and a parent it sets
    is a LIVE step or finding, the piece of work it is part of (whether that is a step split
    into smaller steps, which is no work, is the caller's to refuse: it reads the steps
    linked under it)."""
    problems = []
    if arcs := sorted(label for label in draft.labels if label in ARCS):
        problems.append(f"a task carries no arc label: its arc is its card's; it has {arcs}")
    problems += _title_problems(draft.title, changed)
    owner = draft.owner
    if OWNER in changed and not (owner is not None and owner.kind in SHIPPABLE and owner.live):
        problems.append("a task is a sub-issue of a LIVE step or finding, the piece of work it "
                        f"is part of (R-BAL386); its parent is {_parent_named(owner)}")
    return problems


def violations(draft: Draft, changed: Collection[str] = NEW_CARD) -> list[str]:
    """Every way ``draft`` breaks the plan's rules; empty when it may be written.

    ``changed``: the parts the change writes (:data:`TITLE`, :data:`BODY`,
    :data:`OWNER`); only those are graded beyond the type and arc label.  A task
    (:data:`setup_tracker.TASK`) is held to its own rules (:func:`_task_problems`).
    """
    if draft.kind == TASK:
        return _task_problems(draft, changed)
    problems = []
    if draft.kind not in CARD_TYPES:
        problems.append(f"its type is {draft.kind!r}, not one of {', '.join(CARD_TYPES)}")
    arcs = sorted(label for label in draft.labels if label in ARCS)
    if len(arcs) != 1:
        problems.append(f"it carries exactly one arc label of {', '.join(ARCS)}; it has {arcs}")
    problems += _title_problems(draft.title, changed)
    if OWNER in changed:
        problems += _owner_problems(draft)
    body = normalized(draft.body)
    if BODY in changed and draft.kind == "finding":
        problems += _sentence_problems(body)
    elif BODY in changed and draft.kind == "ruling":
        problems += _ruling_problems(body)
    return problems
