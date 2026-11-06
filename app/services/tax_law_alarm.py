"""
Shekel Budget App -- Tax Law Alarm

**Is the tax law the app carries complete for the year a date calls for?**
ONE pure check (ruling **salary:R-SAL74**, plan step **salary:X-at-4**) that
every alarm reads, so no two alarms can disagree about which year is missing:

* the banner on every owner page, from November 1 (:data:`Stage.NOTICE`;
  ruling **R-SAL87** put it on every page), ``app/context_processors.py`` and
  ``templates/_tax_law_notice.html``;
* the weekly scheduled GitHub run, failing from November 1, and from December 1
  (:data:`Stage.REFUSE`) the refusal: the ``tax-law`` job in ``ci.yml`` on
  every pull request, and the same check before ``docker-publish.yml`` builds
  the release image, so a pull request checked green in November cannot ship
  one after it and neither can a tag -- all three through
  ``scripts/check_tax_law.py``.

**Why alarms, and why here.**  The law is one copy in the code
(:mod:`app.tax_law`), so a new year arrives only in a release that adds it.
Nothing prices a missing year as missing:
:func:`~app.services.tax_config_service.resolve_tax_year` answers a year the
law lacks with the latest earlier year's rules (ruling **R-SAL76**), which is
the right answer to "what should this paycheck cost?" and the wrong one to
"has anyone added next year?".  Forgetting is silent by construction, so this
check makes it loud.

**What "complete" means (ruling R-SAL86).**  A year is in when the law carries
it and it lists every state an earlier year lists: a state, once supported,
is supported in every later year.  Federal rules and FICA need no check here,
because :class:`app.tax_law.TaxYearLaw` refuses a year without them and
:class:`app.tax_law.TaxLaw` refuses a skipped year, so a year the law carries
has both.  A year MAY ship in parts -- federal before a state that publishes
later -- and every alarm keeps naming the missing state until it lands.

**A state is listed from the law's first year** (ruling **R-SAL129**, plan
step salary:X-at-3): :class:`app.tax_law.TaxLaw` refuses a state first listed
in a later year, so no profile's earlier years reach FORWARD to a later year's
rules.  Every state the law lists is therefore owed by every year after the
first, and a gap is only ever a later year that lacks one.  (Until X-at-3
this module read the opposite -- a state first listed later owed nothing to
the years before it -- and left the question to that step.)

The check reads no clock: every caller passes the day it means, which is what
lets the tests drive it to any date.  What keeps the weekly calendar sweep's
faked clock away from the GitHub alarms is that the script runs outside
pytest and reads the real one; the in-app banner, rendered inside the suite,
does move with the sweep's date.
"""

import enum
from dataclasses import dataclass
from datetime import date

from app.services.tax_config_service import resolve_tax_year


class Stage(enum.Enum):
    """When in the year before a tax year an alarm starts asking for it.

    Each value is the ``(month, day)`` from which the alarm asks for NEXT
    year's law; before it, the alarm asks only for the current year's.  The
    IRS and the Social Security Administration usually publish in October or
    November, so the notice starts when the figures are normally out and the
    refusal a month later (ruling R-SAL74).
    """

    #: The in-app banner and the weekly GitHub run.
    NOTICE = (11, 1)
    #: CI refuses every pull request, and the release image refuses to build.
    REFUSE = (12, 1)


@dataclass(frozen=True)
class Gap:
    """One piece of the law a due year lacks, and what prices it meanwhile.

    Attributes:
        tax_year: The year missing the piece.
        state: The two-letter state the year lacks, or ``None`` when the
            WHOLE year is absent.
        fallback_year: The year whose rules price the missing piece until it
            is added: the resolver's answer for that piece's own series.  For
            a whole year that is the federal and FICA series, which every
            listed state shares unless the newest year itself lacks it.
            ``None`` only when the law carries no year at all.
        priced_older: On a whole-year gap only, one state :class:`Gap` for
            each listed state priced on an OLDER year than ``fallback_year``
            (the newest year lacks it), so the notice can name each part's
            year in one sentence (ruling salary:R-SAL91).  Empty otherwise.
    """

    tax_year: int
    state: str | None
    fallback_year: int | None
    priced_older: tuple["Gap", ...] = ()


def due_year(today: date, stage: Stage) -> int:
    """Return the latest tax year *stage* asks the law for on *today*.

    Next year from the stage's start date on, this year before it.

    Args:
        today: The day the alarm is judged on.
        stage: Which alarm is asking.

    Returns:
        The tax year the law must carry, complete, on *today*.
    """
    return today.year + 1 if (today.month, today.day) >= stage.value else today.year


def gaps(law, through_year: int) -> tuple[Gap, ...]:
    """Return every piece of the law missing through *through_year*, oldest first.

    Each year after the law's first, up to *through_year*, is judged: a year
    the law does not carry is one whole-year :class:`Gap` -- carrying, in
    ``priced_older``, each listed state its newest year lacks -- and a carried
    year lacking a state the law lists is one :class:`Gap` per state.  The
    law's first year lists every state (:class:`app.tax_law.TaxLaw` refuses a
    law whose first year does not, ruling R-SAL129), so it owes nothing; a
    law with no years at all lacks *through_year* whole.

    Args:
        law: The :class:`app.tax_law.TaxLaw` to judge.
        through_year: The latest year to judge (:func:`due_year`).

    Returns:
        The gaps, by year and then by state; empty when the law is complete.
    """
    carried = {year.tax_year: year for year in law.years}
    if not carried:
        return (Gap(tax_year=through_year, state=None, fallback_year=None),)
    first = min(carried)
    found = []
    listed = set(law.supported_states)
    for tax_year in range(first + 1, through_year + 1):
        year = carried.get(tax_year)
        if year is None:
            whole_year = resolve_tax_year(tax_year, tuple(carried))
            found.append(Gap(tax_year, None, whole_year, tuple(
                gap for gap in _state_gaps(law, listed, tax_year)
                if gap.fallback_year != whole_year
            )))
            continue
        found.extend(_state_gaps(law, listed - set(year.states), tax_year))
    return tuple(found)


def _state_gaps(law, states, tax_year: int) -> list[Gap]:
    """Return one :class:`Gap` per state in *states* for *tax_year*, alphabetically.

    Each names the year :func:`~app.services.tax_config_service.resolve_tax_year`
    picks from the years the law lists that state in -- the one series
    (:meth:`app.tax_law.TaxLaw.years_listing`) the paycheck's state line is
    priced from too, so the two cannot name different years.

    Args:
        law: The :class:`app.tax_law.TaxLaw` being judged.
        states: The two-letter states *tax_year* lacks.
        tax_year: The year lacking them.

    Returns:
        The gaps, by state.
    """
    return [
        Gap(tax_year, state, resolve_tax_year(
            tax_year, tuple(year.tax_year for year in law.years_listing(state)),
        ))
        for state in sorted(states)
    ]


def speaks_from(law, stage: Stage) -> date | None:
    """Return the first display date on which *stage* names a gap in *law* as it stands.

    ``alarm(law, day, stage)`` is empty on every day before it and non-empty on
    it and every day after, because a stage asks for a year that only grows
    with the date.  The release image's build reads it (ruling
    salary:R-SAL88): the check job hands the build this instant, and a build
    started on or after it refuses even when GitHub re-runs only the failed
    build and reuses a check judged before it.

    Args:
        law: The :class:`app.tax_law.TaxLaw` as it stands.
        stage: Which alarm is asking.

    Returns:
        The date, or ``None`` when the law carries no year, whose alarm
        speaks on every date.
    """
    if not law.years:
        return None
    newest = law.years[-1].tax_year
    found = gaps(law, newest)
    first_missing = found[0].tax_year if found else newest + 1
    return date(first_missing - 1, *stage.value)


def alarm(law, today: date, stage: Stage) -> tuple[Gap, ...]:
    """Return what *stage* would name as missing on *today* -- the one check every alarm reads.

    Args:
        law: The :class:`app.tax_law.TaxLaw` to judge.
        today: The day the alarm is judged on (the display-timezone date).
        stage: Which alarm is asking.

    Returns:
        The :class:`Gap` tuple; empty means the alarm stays silent.
    """
    return gaps(law, due_year(today, stage))
