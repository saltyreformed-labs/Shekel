"""Shekel Budget App -- Is this calendar flow an INFREQUENT one?

The analytics calendar asterisks a day cell holding a definition that repeats
less often than monthly.  Plan step **R7a-2b** made that a DERIVATION over the
two-axis ``(interval_n, unit)`` reading, where it was a hand-enumerated set of
three pattern names living in ``calendar_service``.

**Its own module because it is the one part of the calendar that is about RECURRENCE
rather than about calendars**, and because ``calendar_service`` sits at 857 of pylint's
1,000-line ceiling (measured 2026-09-01; it read 891 undated, and the module has moved
twice since): the derivation needs its reasoning written down, and a cap is a forcing
function rather than a ceiling to raise (the planning conventions' rule 4).  Two
facts moved here with it -- what
"infrequent" MEANS, and when the owner's pay cadence has to be resolved to
answer -- and neither has a second home.

Pure apart from :func:`badge_cadence`, which is the one function here that
reads the database, and reads only the owner's cadence.
"""
from app.services.pay_calendar import PayCadence, PayCalendarError, cadence_for
from app.services.recurrence import cadence_of
from app.services.transfer_legs import PlanItem
from app.utils.money import MONTHS_PER_YEAR

# What "infrequent" MEANS: a definition that fires less often than once a
# month.
#
# **DERIVED since plan step R7a-2b**, where this was a hand-enumerated
# ``frozenset`` of Quarterly, Semi-Annual and Annual.  It reproduces those
# three (4, 2 and 1 a year) and **flips two shapes a user can author TODAY**:
# ``Every N Periods`` at ``interval_n >= 3`` (26/3 = 8.67 a year biweekly --
# less often than quarterly, and badged frequent), and ``Every Period`` for an
# owner paid every 32 days or less often (11 a year).  It is per-OWNER too,
# which an enumeration of pattern NAMES could not be: ``Every 2 Periods`` is 13
# a year biweekly and 6 monthly-paid.
_INFREQUENT_BELOW_PER_YEAR = MONTHS_PER_YEAR


def badge_cadence(
    user_id: int, transactions: list[PlanItem],
) -> PayCadence | None:
    """Return the owner's pay cadence, or ``None`` when nothing can be badged.

    The infrequent badge is per-OWNER since plan step R7a-2b, so the build
    needs the owner's cadence -- but only if there is a REPEATING row to badge,
    and a page must not fail for a fact it does not use.  The test is on the
    recurrence rule rather than on the transaction list: a month of purely
    manual entries reads no cadence, and an earlier draft testing
    ``if not transactions`` would have resolved one for them.

    **The absence is provably not a missing value.** ``budget.transactions``
    carries ``pay_period_id NOT NULL``, so a transaction implies a pay period,
    which since plan step C4-b-2 implies a ``budget.pay_schedule`` row
    (``fk_pay_periods_schedule``) and therefore a stored cadence.  *That second
    link used to be "or by inferring from that period's own length", the arm
    findings **P8** and **P35** were about; the key replaced an inference with
    a guarantee.*  An owner with
    no transactions therefore cannot reach :func:`is_infrequent` at all, and
    is the only owner for whom this answers ``None``.  The type says
    "unreadable BECAUSE unread" rather than "unknown".

    Args:
        user_id: The owner whose calendar is being built.
        transactions: The items this build will badge -- rows, and since
            leaf ``balance:X-bi-6-1b`` transfer legs, which never repeat
            (``TransferLeg.recurs`` is ``False``: a transfer's rule is its
            own definition's, not a transaction rule).

    Returns:
        The owner's :class:`~app.services.pay_calendar.PayCadence`, or ``None``
        when no row in this build repeats.

    Raises:
        PayCalendarError: The owner has a repeating row and no resolvable
            cadence.  *Its one named route was plan finding **P8** -- a
            schedule-row-less owner's cadence INFERRED from their last period's
            stored length, which nothing bounded above -- and plan step C4-b-2
            closed it: ``fk_pay_periods_schedule`` forbids that owner and the
            inference is deleted, so a repeating row's NOT NULL
            ``pay_period_id`` now guarantees a schedule row behind it as well
            as a period.*  Declared still, because this function RESOLVES the
            cadence itself (:func:`~app.services.pay_calendar.cadence_for`
            below) rather than reading one a caller hands it -- so the refusal
            is its own to make and belongs in its own signature.
    """
    if not any(txn.recurs for txn in transactions):
        return None
    return cadence_for(user_id)




def is_infrequent(
    txn: PlanItem, pay_cadence: PayCadence | None,
) -> bool:
    """Check whether a transaction's recurrence fires less often than monthly.

    True when the definition's cadence fires fewer than
    :data:`_INFREQUENT_BELOW_PER_YEAR` times a year -- the three the retired
    enumeration listed, and the two shapes of today's closed set it got wrong;
    see that constant's comment.  False for a transaction that does not
    ``recurs`` -- no template, or a template with no recurrence rule, the
    last of which is how a non-recurring definition is modelled (plan step
    R2e-3); the one accessor since plan step balance:X-bi-7a, where this
    function and :func:`badge_cadence` each spelled it inline.

    Args:
        txn: The row -- or transfer leg, which answers ``False`` at the
            ``recurs`` gate -- whose definition is being classified.
        pay_cadence: How often the owner is paid
            (:class:`~app.services.pay_calendar.PayCadence`), or ``None`` when
            this build had nothing to badge (:func:`badge_cadence`).  Read
            only for a paycheck-space cadence, which is the one kind whose
            frequency is a property of the owner rather than of the calendar.

    Returns:
        Whether the badge applies.

    Raises:
        RecurrenceResolutionError: The rule names a pattern this application
            does not model.  **NEW at plan step R7a-2b**, where the enumerated
            set answered ``False`` for such a rule and the calendar rendered
            it as a frequent flow.  The same disposition every other reader of
            a stored rule has, and the same one the Recurring surface states
            (``recurring_view._build_section``): a badge computed from a
            cadence nobody can derive is a guess.  Reachable only through a
            rule naming the surviving ``Once`` ``ref`` row, which no write door
            can author (plan step R2e-2) and no production row uses.
        PayCalendarError: *pay_cadence* is ``None`` beside a repeating
            definition.  Structurally unreachable -- ``budget.transactions``
            carries ``pay_period_id NOT NULL``, so a transaction reaching here
            implies a pay period implies a resolvable cadence, and
            :func:`badge_cadence` answers ``None`` only for a build with no
            transactions at all.  Raised rather than badged either way: a
            silent ``False`` here would tell the user an annual bill is a
            frequent one.
    """
    if not txn.recurs:
        return False
    rule = txn.template.recurrence_rule
    if pay_cadence is None:
        raise PayCalendarError(
            f"transaction {txn.id} has a recurrence rule but this calendar "
            f"build resolved no pay cadence.  A transaction carries a NOT "
            f"NULL pay_period_id, so its owner has a period and therefore a "
            f"cadence; reaching here means the build was told it had no rows "
            f"to badge and then found one."
        )
    cadence = cadence_of(rule)
    return cadence.occurrences_per_year(pay_cadence) < (
        _INFREQUENT_BELOW_PER_YEAR
    )


__all__ = ["badge_cadence", "is_infrequent"]
