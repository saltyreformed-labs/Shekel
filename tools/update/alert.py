"""A failed run of the weekly update, told to the developer (ruling plan#2466): an issue in this
repository titled for the day, which mentions him, links the run and names each job's result.

**It runs main's own code holding only the workflow's own token**, widened to ``issues: write``
for its one job (``.github/workflows/updates.yml``'s ``alert``, run when the refresh, the
publish or the queue failed), with the ``landing`` group and no package code.  A mention is
what reaches him (measured 2026-10-27, on the tracker), and an issue is a mention's home that
a failed run always has; he closes it once what failed is fixed.  Each failed run opens one,
and a re-run that fails again opens another.

Usage, where ``<result>`` is each job's as GitHub gives it (``success``, ``failure``,
``cancelled`` or ``skipped``)::

    GITHUB_TOKEN=<token> GITHUB_REPOSITORY=<owner>/<name> \\
        python -m tools.update.alert <run-url> <job>=<result> ...

It prints the issue's link.  Exit status: 0 the issue opened; 1 GitHub refused it or could not
be reached, or a variable is unset, saying which.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime

import requests

from tools.quill.door import GitHub, GitHubError, described
from tools.update.app import DEVELOPER, REPOSITORY_VARIABLE, unset

#: The variable holding the workflow's own token, which the alert's job widens to ``issues:
#: write``.
ISSUE_TOKEN_VARIABLE = "GITHUB_TOKEN"


def issue(day: date, run: str, results: Sequence[tuple[str, str]]) -> dict[str, str]:
    """The issue telling the developer that the run at ``run`` failed on ``day``: its title and
    body, each of ``results`` a job's name and result."""
    lines = "\n".join(f"- {job}: {result}" for job, result in results)
    return {"title": f"The weekly update failed {day:%Y-%m-%d}",
            "body": f"@{DEVELOPER} the weekly update's run failed: {run}\n\n{lines}\n\n"
                    "Close this issue once what failed is fixed (ruling plan#2466)."}


def opened(github: GitHub, repository: str, day: date, run: str,
           results: Sequence[tuple[str, str]]) -> int:
    """Open :func:`issue` in ``repository``, printing its link; exit as the module says."""
    try:
        answer = github.rest("POST", f"/repos/{repository}/issues", issue(day, run, results))
    except (GitHubError, requests.RequestException) as error:
        print(f"update alert: no issue was opened: {described(error)}", file=sys.stderr)
        return 1
    print(f"update alert: opened {answer['html_url']}")
    return 0


def _result(text: str) -> tuple[str, str]:
    """``<job>=<result>`` as a job's name and its result."""
    job, equals, result = text.partition("=")
    if not (job and equals and result):
        raise argparse.ArgumentTypeError(f"{text!r} is no <job>=<result>")
    return job, result


def main(argv: Sequence[str] | None = None,
         today: Callable[[], date] = lambda: datetime.now(UTC).date()) -> int:
    """Open the issue for the run the command line names, with the token and in the repository
    the environment names."""
    parser = argparse.ArgumentParser(prog="python -m tools.update.alert",
                                     description="A failed run of the weekly update, told to "
                                                 "the developer (ruling plan#2466).")
    parser.add_argument("run", help="the failed run's page")
    parser.add_argument("results", nargs="+", type=_result, metavar="<job>=<result>",
                        help="each job's result")
    args = parser.parse_args(argv)
    if missing := unset(ISSUE_TOKEN_VARIABLE, REPOSITORY_VARIABLE):
        print(f"update alert: {', '.join(missing)} unset, so no issue was opened",
              file=sys.stderr)
        return 1
    return opened(GitHub(os.environ[ISSUE_TOKEN_VARIABLE]), os.environ[REPOSITORY_VARIABLE],
                  today(), args.run, args.results)


if __name__ == "__main__":
    sys.exit(main())
