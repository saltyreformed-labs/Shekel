"""The update App as the workflow's keyed jobs hand it to the queue (:mod:`tools.update.queue`)
and the publish (:mod:`tools.update.publish`), ruling plan#2448: the variables holding its
token and naming the repository it acts on, and the branch every update merges into; and the
developer every alert of the update mentions (rulings plan#2466, plan#2467).
"""
from __future__ import annotations

import os

from tools.ci.gitcmd import DEV

#: The variable holding the update App's token, minted for the one job that reads it.
TOKEN_VARIABLE = "UPDATE_TOKEN"
#: The variable naming the repository, ``<owner>/<name>``, as GitHub Actions sets it.
REPOSITORY_VARIABLE = "GITHUB_REPOSITORY"
#: The branch every update merges into.
INTO = DEV.partition("/")[2]
#: The developer's GitHub login, whom every alert of the update mentions: a mention is what
#: reaches him (measured 2026-10-27, on the tracker).
DEVELOPER = "SaltyReformed"


def unset(*names: str) -> list[str]:
    """Each of the variables ``names`` that is unset or empty, in their order."""
    return [name for name in names if not os.environ.get(name, "")]
