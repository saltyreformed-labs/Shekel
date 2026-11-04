"""A failed run of the weekly update, told to the developer (ruling plan#2466): an issue in this
repository titled for the day, which mentions him, links the run and names each job's result.

**It installs nothing** (plan#2481).  Its job (``.github/workflows/updates.yml``'s ``alert``,
run when the refresh, the publish, the queue or the release-due job failed) runs this module on
the runner's own Python with the standard library alone (``python3 -S``, which puts no installed
package on the path), and the runner's own ``gh`` sends the issue.  The other jobs each install
the lock, a pull of uv from ghcr.io and downloads of its Python and the packages, so a failure
there would otherwise stop the one job that reports it.  The ubuntu-26.04 image holds Python
3.14.4 and GitHub CLI 2.103.2 (runner-images ``ubuntu26/20261026.180``'s list, read
2026-11-01).  It holds only the workflow's own token, widened to ``issues: write`` for its one
job, and runs no package code.

A mention is what reaches him (measured 2026-10-27, on the tracker), and an issue is a
mention's home that a failed run always has; he closes it once what failed is fixed.  Each
failed run opens one, and a re-run that fails again opens another.

Usage, where ``<result>`` is each job's as GitHub gives it (``success``, ``failure``,
``cancelled`` or ``skipped``)::

    GH_TOKEN=<token> GITHUB_REPOSITORY=<owner>/<name> \\
        python3 -S -m tools.update.alert <run-url> <job>=<result> ...

It prints the issue's link.  Exit status: 0 the issue opened; 1 GitHub refused it or could not
be reached, or a variable is unset, saying which.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime

from tools.update.app import DEVELOPER, REPOSITORY_VARIABLE, unset

#: The variable holding the workflow's own token, which the alert's job and the due job each
#: widen to ``issues: write``: the one ``gh`` reads first.
ISSUE_TOKEN_VARIABLE = "GH_TOKEN"


def issue(day: date, run: str, results: Sequence[tuple[str, str]]) -> dict[str, str]:
    """The issue telling the developer that the run at ``run`` failed on ``day``: its title and
    body, each of ``results`` a job's name and result."""
    lines = "\n".join(f"- {job}: {result}" for job, result in results)
    return {"title": f"The weekly update failed {day:%Y-%m-%d}",
            "body": f"@{DEVELOPER} the weekly update's run failed: {run}\n\n{lines}\n\n"
                    "Close this issue once what failed is fixed (ruling plan#2466)."}


def sent(repository: str, body: dict[str, str],
         number: int | None = None) -> subprocess.CompletedProcess:
    """``body`` posted as a new issue of ``repository`` by the runner's ``gh``, or as a change
    to its issue ``number``, with the token in the environment: one ``gh api`` POST or PATCH of
    the JSON on its input, which prints the issue's link, or exits 1 saying why on its stderr,
    ``gh: <message> (HTTP <status>)`` (the POST measured 2026-11-01 with gh 2.102.1)."""
    issues = f"repos/{repository}/issues"
    return subprocess.run(["gh", "api", *((issues, "--method", "POST") if number is None else
                                          (f"{issues}/{number}", "--method", "PATCH")),
                           "--input", "-", "--jq", ".html_url"],
                          input=json.dumps(body), capture_output=True, text=True, check=False)


def opened(send: Callable[[str, dict[str, str]], subprocess.CompletedProcess],
           repository: str, day: date, run: str, results: Sequence[tuple[str, str]]) -> int:
    """Open :func:`issue` in ``repository`` by ``send``, printing its link; exit as the module
    says."""
    done = send(repository, issue(day, run, results))
    if done.returncode:
        print(f"update alert: no issue was opened: {done.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"update alert: opened {done.stdout.strip()}")
    return 0


def _result(text: str) -> tuple[str, str]:
    """``<job>=<result>`` as a job's name and its result."""
    job, equals, result = text.partition("=")
    if not (job and equals and result):
        raise argparse.ArgumentTypeError(f"{text!r} is no <job>=<result>")
    return job, result


def main(argv: Sequence[str] | None = None,
         today: Callable[[], date] = lambda: datetime.now(UTC).date()) -> int:
    """Open the issue for the run the command line names, in the repository the environment
    names, ``gh`` reading the token from it."""
    parser = argparse.ArgumentParser(prog="python3 -S -m tools.update.alert",
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
    return opened(sent, os.environ[REPOSITORY_VARIABLE], today(), args.run, args.results)


if __name__ == "__main__":
    sys.exit(main())
