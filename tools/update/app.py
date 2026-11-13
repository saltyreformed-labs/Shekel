"""The update App as the workflow's keyed jobs hand it to the queue (:mod:`tools.update.queue`)
and the publish (:mod:`tools.update.publish`), ruling plan#2448: the variables holding its
token and naming the repository it acts on, and the branch every update merges into; the
developer the alert's and the release-due job's issues mention (rulings plan#2466, plan#2829;
the queue's comments mention no one, ruling plan#3622); and the one home
of what the update's record is read by (ruling plan#3619): each bot the update acts as, by the
login GitHub's GraphQL names it, which bot wrote a node (:func:`bot_of`), and the marker the
App's comments hold for each thing they tell, which the queue writes and reads back and
``quill sync`` files a card for, or puts on the card open for that pull request
(``tools/quill/_waits.py``, ruling plan#3722).

A leaf of the standard library and :mod:`tools.ci.gitcmd`: the alert, which runs with no
installed package (``python3 -S``), imports it, and so does quill.
"""
from __future__ import annotations

import hashlib
import os
import re

from tools.ci.gitcmd import DEV

#: The variable holding the update App's token, minted for the one job that reads it.
TOKEN_VARIABLE = "UPDATE_TOKEN"
#: The variable naming the repository, ``<owner>/<name>``, as GitHub Actions sets it.
REPOSITORY_VARIABLE = "GITHUB_REPOSITORY"
#: The branch every update merges into.
INTO = DEV.partition("/")[2]
#: The developer's GitHub login, whom the alert's issue and the release-due issue mention: a
#: mention is what reaches him (measured 2026-10-27, on the tracker).  The queue's comments
#: mention no one (ruling plan#3622), the card ``quill sync`` files for each reaching him.
DEVELOPER = "SaltyReformed"
#: The update App's slug, the login GraphQL names the bot it acts as by, which opens its pull
#: requests and writes its comments (measured 2026-11-12 on PR 668; REST names it
#: ``shekel-updates[bot]``).
APP = "shekel-updates"
#: The login GraphQL names the bot of the workflow's own token by, which opens the alert's
#: and the release-due job's issues (measured 2026-11-13 on #676; REST names it
#: ``github-actions[bot]``).
ACTIONS = "github-actions"

#: A marker in a comment of the App's, naming by its digest one thing it told
#: (:func:`marker`), so nothing a pull request carries is spelled inside an HTML comment; and
#: the expression finding each in a text, made from it.  Its word ``mention`` is from when the
#: comments mentioned the developer (until plan#3627), and stays: every marker written stays
#: on GitHub, read back by the queue and by ``quill sync``, so a new form would tell and file
#: each thing again.
_MARKER = "<!-- update: mention {} -->"
_MARKED = re.compile(re.escape(_MARKER).replace(r"\{\}", "([0-9a-f]{64})"))


def unset(*names: str) -> list[str]:
    """Each of the variables ``names`` that is unset or empty, in their order."""
    return [name for name in names if not os.environ.get(name, "")]


def bot_of(node: dict) -> str | None:
    """The login of the bot that wrote ``node``, a pull request, issue or comment as GraphQL
    answers its ``author { __typename login }``: None for a person's, whatever its login, and
    for an author since deleted, which GitHub answers as null.  A login alone names no bot: a
    user may hold one a bot's slug spells, as no user held ``shekel-updates`` on 2026-11-13
    (``gh api users/shekel-updates``: 404) but nothing says none can, and a comment of theirs
    holding a marker would be read as the App's.  GraphQL's ``__typename`` tells them apart:
    ``Bot`` for the App's comment on PR 668 and the workflow's issue #676 (measured
    2026-11-13 13:12 UTC)."""
    author = node["author"]
    return author["login"] if author and author["__typename"] == "Bot" else None


def marker(told: str) -> str:
    """The marker naming ``told``, one thing the update tells, by its digest: the one form of
    it, which the queue writes and every reader of the record finds (:func:`markers`)."""
    return _MARKER.format(hashlib.sha256(told.encode()).hexdigest())


def markers(text: str) -> tuple[str, ...]:
    """Each marker (:func:`marker`) ``text`` holds, in its order, each once."""
    return tuple(dict.fromkeys(_MARKER.format(digest) for digest in _MARKED.findall(text)))
