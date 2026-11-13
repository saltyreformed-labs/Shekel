"""The deduction cadences' memo: one resolved cadence's paydays, walked as far as asked.

A leaf of :mod:`app.services.payroll_basis` (task plan#3576), read by
:class:`~app.services.payroll_basis.PayrollBasis`'s ``line_applies_on``.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from app.services.pay_calendar import PayCalendar
from app.services.recurrence import ResolvedRecurrence, projected_occurrence_placements

#: How far past an ask a cadence is walked before it is asked again: every
#: walk reaches at least a year past the payday asked, and each later
#: extension also doubles the span walked so far, so a projection that asks
#: ascending paydays for twenty years re-walks about five times and never
#: walks past twice its horizon.  A walk to the application's last date was
#: the first shape and it cost 800 ms per basis on the developer's eleven
#: lines (measured 2026-09-13): past the saved schedule the ceiling arm
#: re-projects the month for every occurrence, so the walk must reach only
#: what is asked of it.  **The year past the ask is load-bearing, not
#: slack** (an adversarial review of this step): under ``CONTAINING_DATE`` an
#: occurrence up to a full period AFTER a payday places on that payday, so a
#: reach set exactly at the payday asked stops the walk before the
#: occurrence that admits it and caches the wrong ``False``.
_REACH_PAST_ASK = timedelta(days=366)

@dataclass
class _WalkedCadence:
    """One resolved cadence's admitted paydays, walked as far as it has been asked.

    Mutable, and private to :class:`PayrollBasis`: a memo, not a value.
    Every line that resolves to the same :class:`ResolvedRecurrence` shares
    one of these -- the developer's eleven 24-per-year lines are ONE walk --
    because the occurrence set is a function of the cadence and the calendar
    alone.

    Attributes:
        resolved: The cadence walked.
        through: The last day the walk has reached, or ``None`` before it is
            asked anything.
        admitted: Every payday the walk placed an occurrence on, through
            :attr:`through`.
    """

    resolved: ResolvedRecurrence
    through: date | None = None
    admitted: frozenset[date] = frozenset()

    def admits(self, calendar: PayCalendar, payday: date) -> bool:
        """Whether an occurrence lands on the paycheck of *payday*, walking further if needed.

        Refuses nothing: a payday this calendar cannot place is simply not in
        the set.  The engine's refusal of such a payday is
        :func:`~app.services.paycheck_calculator._calendar_questions
        ._month_ordinal`'s, read before any line is priced.
        """
        if self.through is None or payday > self.through:
            self._walk(calendar, payday)
        return payday in self.admitted

    def _walk(self, calendar: PayCalendar, payday: date) -> None:
        """Re-walk from the cadence's first occurrence to a year past *payday* at least.

        A later walk also doubles the span walked so far, so the reach grows
        geometrically with the asks; the set is REPLACED by the longer walk's,
        which is a superset of the shorter's (the same cadence over a longer
        window).
        """
        through = payday + _REACH_PAST_ASK
        if self.through is not None:
            through = max(through, self.through + (self.through - self.resolved.starts_on))
        self.admitted = frozenset(
            placement.period.start_date
            for placement in projected_occurrence_placements(
                self.resolved, calendar, through=through,
            )
            if placement.period is not None
        )
        self.through = through
