"""
Shekel Budget App -- Is Next Year's Tax Law In?

The command-line door to :func:`app.services.tax_law_alarm.alarm`, the one
check behind the tax-law alarms (plan step salary:X-at-4; rulings
salary:R-SAL74, R-SAL86, R-SAL87).  The GitHub alarms run it:

* ``.github/workflows/tax-law.yml`` every week, at the NOTICE stage: it fails
  from November 1 until next year's law is in, so GitHub emails its failure
  even when nobody is pushing;
* at the REFUSE stage, failing from December 1: ``ci.yml``'s ``tax-law`` job on
  every pull request, and ``docker-publish.yml`` before it builds the release
  image (ruling salary:R-SAL88).  The pull-request check is judged when the
  pull request is pushed, so one checked green in November could still merge
  after it; the image's check is judged when the release is built, which stops
  that merge -- or a tag -- shipping a release without next year.  And because
  GitHub can re-run only a failed build and reuse a check that passed before
  December, the image's check also hands the build the instant its refusal
  starts (``--starts-epoch``), and the build refuses once that has passed.

The third alarm, the banner on every owner page, reads the same check in
``app/context_processors.py``; this script is not in its path.

Usage:
    python scripts/check_tax_law.py notice    # fails from November 1
    python scripts/check_tax_law.py refuse    # fails from December 1
    python scripts/check_tax_law.py refuse --starts-epoch
        # prints the Unix time the refusal starts for the law as it stands

**It reads the REAL clock**, as the display-timezone date
(:func:`app.utils.dates.display_today`), and that is why it is a script and
not a test: the weekly calendar sweep fakes the clock for the whole suite
(``SHEKEL_FAKE_TODAY``), and an alarm inside the suite would fire or stay
silent on the faked day rather than the real one.  Nothing here needs a
database or an application context -- the law is a module constant.

Exit status: 0 when the law is complete through the year the stage asks for,
1 when it is not, 2 on a usage error (argparse's).  ``--starts-epoch`` judges
nothing: it prints and exits 0.

Test entry point:
    ``report(law, today, stage)`` returns the lines printed and the exit
    status; tests pass the law and the day, so no test reads the clock.
"""

import argparse
import os
import sys
from datetime import datetime, time

# Ensure the project root is on sys.path so 'app' is importable when run as
# ``python scripts/check_tax_law.py`` (sys.path[0] is scripts/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pylint: wrong-import-position -- these imports must follow the sys.path
# bootstrap above; 'app' is only importable once the project root is on the
# path.
from app import tax_law  # pylint: disable=wrong-import-position
# Pylint: wrong-import-position -- the same sys.path bootstrap.
from app.services import tax_law_alarm  # pylint: disable=wrong-import-position
# Pylint: wrong-import-position -- the same sys.path bootstrap.
from app.utils import dates  # pylint: disable=wrong-import-position


def report(law, today, stage) -> tuple[list[str], int]:
    """Judge *law* on *today* for *stage*; return the lines to print and the exit status.

    Args:
        law: The :class:`app.tax_law.TaxLaw` to judge.
        today: The display-timezone date to judge it on.
        stage: The :class:`app.services.tax_law_alarm.Stage` asking.

    Returns:
        ``(lines, status)``: what the run prints, and 0 when the law is
        complete through the year *stage* asks for on *today*, else 1.
    """
    due = tax_law_alarm.due_year(today, stage)
    found = tax_law_alarm.alarm(law, today, stage)
    carried = ", ".join(str(year.tax_year) for year in law.years) or "no year"
    lines = [
        f"The tax law carries: {carried}.",
        f"Today ({dates.DISPLAY_TIMEZONE.key}) is {today.isoformat()}; the "
        f"{stage.name.lower()} alarm asks for the law through {due}.",
    ]
    if not found:
        lines.append(f"OK: the tax law is complete through {due}.")
        return lines, 0
    lines.extend(_describe(gap) for gap in found)
    lines.append(
        "Add it: a year is a new app/tax_law/_year_<YYYY>.py listed in LAW "
        "(app/tax_law/__init__.py); a state goes in its year's module.  "
        "Transcribe from the published sources each year module cites."
    )
    return lines, 1


def _describe(gap) -> str:
    """Return the one line naming *gap* and what prices it meanwhile.

    Args:
        gap: A :class:`app.services.tax_law_alarm.Gap`.

    Returns:
        A ``MISSING:`` line.
    """
    if gap.fallback_year is None:
        return f"MISSING: the whole {gap.tax_year} tax law; no year prices it meanwhile."
    if gap.state is None:
        older = " and ".join(
            f"{state.state} on {state.fallback_year}'s" for state in gap.priced_older
        )
        older = f", except {older}" if older else ""
        return (
            f"MISSING: the whole {gap.tax_year} tax law; priced on "
            f"{gap.fallback_year}'s rules meanwhile{older}."
        )
    return (
        f"MISSING: {gap.state} in the {gap.tax_year} tax law; priced on "
        f"{gap.fallback_year}'s rules meanwhile."
    )


def starts_epoch(law, stage) -> int:
    """Return the Unix time at which *stage* starts naming a gap in *law* as it stands.

    Midnight, in the display timezone, of
    :func:`app.services.tax_law_alarm.speaks_from` -- so the timezone rule
    stays here, in Python, and the workflow that reads the number compares two
    integers.  ``0`` when the law carries no year, whose alarm speaks on every
    date.

    Args:
        law: The :class:`app.tax_law.TaxLaw` as it stands.
        stage: The :class:`app.services.tax_law_alarm.Stage` asking.

    Returns:
        Seconds since the epoch.
    """
    day = tax_law_alarm.speaks_from(law, stage)
    if day is None:
        return 0
    return int(datetime.combine(day, time.min, tzinfo=dates.DISPLAY_TIMEZONE).timestamp())


def main(argv=None) -> int:
    """Judge the shipped law on today's display date for the stage named; print; return the status.

    Args:
        argv: The arguments after the program name; ``sys.argv[1:]`` when
            ``None``.

    Returns:
        The exit status (:func:`report`'s), or 0 for ``--starts-epoch``.
    """
    parser = argparse.ArgumentParser(
        description="Is the tax law complete for the year today calls for?",
    )
    parser.add_argument(
        "stage",
        choices=[stage.name.lower() for stage in tax_law_alarm.Stage],
        help="notice: fails from November 1; refuse: fails from December 1",
    )
    parser.add_argument(
        "--starts-epoch", action="store_true",
        help="print the Unix time the stage starts refusing the law as it stands",
    )
    args = parser.parse_args(argv)
    stage = tax_law_alarm.Stage[args.stage.upper()]
    if args.starts_epoch:
        print(starts_epoch(tax_law.LAW, stage))
        return 0
    lines, status = report(tax_law.LAW, dates.display_today(), stage)
    print("\n".join(lines))
    return status


if __name__ == "__main__":
    sys.exit(main())
