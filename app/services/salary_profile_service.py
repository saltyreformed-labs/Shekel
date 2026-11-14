"""
Shekel Budget App -- What ARCHIVING a salary profile does to the rows it priced.

One act: :func:`archive_profile`.  A salary profile is the producer behind
amount rule 2, so archiving it removes the thing that prices every paycheck row
the profile's template generated -- and a row whose producer is gone must keep
what it was last worth rather than fall to whatever answers next.

**It exists because plan step X-au-d made a `$0.00` defect live** (finding
**N-261**, ruled by the developer 2026-09-02).  Before that cutover a paycheck
row STORED its figure, so archiving moved nothing.  A declared row is priced by
its DEFINITION, and once no active profile names that definition the
salary-linked refinement stops applying: amount rule 3 answers instead, from the
one price version ``routes/salary/profiles.delete_profile`` opens the template's
series at -- today's paycheck since plan step X-bp, and until then the
template's ``default_amount`` scalar, a copy of the paycheck as of the last
salary save that the same step dropped.  One figure cannot express a paycheck per
period: measured on the 2026-09-02 production clone, when that version was the
scalar, archiving re-priced 50 of 59 rows and moved the projected balance by
**-$9,677.24**, because those rows range from ``$2,483.19`` to ``$3,328.41``
and every one of them would have become ``$2,572.78``.

**The remedy is the act a SETTLE already performs, one tier up.**  A settle
records what a row was worth at the moment its money moved, because the
resolution is a point in time (plan step X-au-c3; the covering movement on
the ``resolved`` source since ``balance:X-bi-4b-2``).  This records what a
row was worth at the moment its
PRODUCER went away, for the same reason: after the archive the figure is not
re-derivable, so it is a record rather than a derivation (``CLAUDE.md`` rule 14
and ruling **R-JA** -- a value that is stored is stored in ONE place, and this
is where the paycheck's last answer is kept).  The two rejected alternatives are
in the ruling: REFUSING the archive makes deactivating a profile a 52-click
chore at the moment the owner has changed jobs, and ACCEPTING the re-price is
the ``-$9,677.24``.

**Reactivation needs no counterpart, and that is a property of the existing
code rather than an omission.**  ``reactivate_profile`` regenerates, and the
maintain pass writes each non-override projected row's whole ownership from its
definition (``recurrence_engine._amounts._derive_row_fields``) -- which since
plan step balance:X-au-e is a DECLARATION for every template, an active
profile's included.  So the rows go back on the profile by the pass that already
runs, and amount rule 2 prices them from the reactivated profile.  A row the owner had re-priced by
hand carries ``is_override`` and is held back as a conflict, exactly as it is
for any other template edit.

*The claim above is bounded, and the bound was MEASURED rather than reasoned:
``salary_regeneration.regenerate_salary_transactions`` (a route helper until
plan step salary:S3-f-3) regenerates with ``effective_from=date.today()``, so
a frozen row in a PAST period is outside the maintain window and stays frozen.
That is the right answer for it -- the paycheck it plans has already happened
-- but a first draft of this paragraph said the round trip was total, and
``tests/test_services/test_archiving_a_salary_profile.py`` refuted it on its
first run and now pins both halves.*

**Why it is a service and not two lines in the route.**  It resolves money and
it writes money rows, which is what ruling **R-HJ** puts behind a service door;
and it names ``cash_ledger``, which the salary READ tier may not -- amount rule
2's producer is ``income_service``, and that package is reached from the cash
ledger at call time precisely to keep the paycheck stack off its load path
(finding **N-267**).  This module sits ABOVE both, so the arrow runs one way.

Boundary discipline (``CLAUDE.md`` Architecture / B6-01): ORM rows in, nothing
out.  Mutates in place and does not commit -- the caller owns the unit of work,
because the archive is one optimistic-locked transaction with the profile's own
deactivation in it.
"""

import logging

from app.exceptions import AmountUnresolvable
from app.services.amount_ownership import state_own_amount
from app.services.cash_ledger import derived_amount_basis, own_amount
from app.utils.log_events import BUSINESS, EVT_SALARY_ROWS_FROZEN, log_event

logger = logging.getLogger(__name__)


def archive_profile(profile) -> int:
    """Freeze the rows *profile* prices, so archiving it moves no money.

    Called BEFORE the profile is deactivated and while it is still the
    producer, which is the whole of the ordering: ``is_salary_linked_template``
    reads the identity-mapped ``salary_profiles`` collection, so a pending
    ``is_active = False`` is already visible to it (that predicate's own
    docstring records why it reads the relationship rather than issuing a
    query).  Resolving after the flag would therefore resolve every row through
    amount rule 3 and freeze each at the series' one price, the very figure
    this exists to avoid.

    Every DECLARED row of the profile's template is frozen, whatever its status
    -- not the projected ones alone.  A settled row's plan is a derivation like
    any other (ruling **R-JB**) and it re-prices to the same one price, so
    leaving it would restate what a received paycheck was expected to be.
    Measured 2026-09-02: **+$627.13 over production's eight settled paychecks,
    of which SEVEN move** -- ``$2,483.19 -> $2,572.78``, ``+$89.59`` each --
    while the eighth already sits at the scalar and moves ``$0.00``.  None of
    it touches a balance, because a settled row is worth what it RECORDED.

    A row that REFUSES is left declared, deliberately: a refusal here means no
    active profile in that row's SCENARIO prices it, so this profile was not
    its producer and freezing it would store a figure nobody computed.  **What
    then happens to such a row is worse rather than neutral, and an adversarial
    review of this step corrected a claim here that said otherwise**: it stays
    declared, and after the archive amount rule 3 answers it from the series'
    one price -- a loud ``AmountUnresolvable`` becomes a silent figure.  It is
    ``$0.00`` today because no code path constructs a non-baseline scenario
    (``auth_service.py`` and ``baseline_service.py`` both pass
    ``is_baseline=True``), which is a FACT from finding **N-253** rather than
    an argument.

    Args:
        profile: The :class:`~app.models.salary_profile.SalaryProfile` about to
            be archived.  Its ``is_active`` must still be ``True``.

    Returns:
        How many rows were frozen.  Zero for a profile with no template, and
        for one whose template has generated nothing.
    """
    template = profile.template
    if template is None:
        return 0

    # ONE basis per SCENARIO, not one per row.  A basis is pinned to an owner
    # and a scenario and holds the derivations lazily
    # (``cash_ledger.derived_amount_basis``), so a fresh one per row would run
    # ``paycheck_calculator.project_salary`` over the owner's whole pay-period
    # set once per row -- 59 projections on production for one click, which is
    # findings **N-228** / **N-268** exactly.  A row states its own scenario in
    # a column and the resolver REFUSES a foreign basis, so the grouping is the
    # key rather than a convenience.
    bases: dict[int, object] = {}
    frozen = 0
    for row in template.transactions:
        if row.amount_source_id is None:
            continue
        if row.scenario_id not in bases:
            bases[row.scenario_id] = derived_amount_basis(
                profile.user_id, row.scenario_id,
            )
        # The row's OWN amount, never its total (plan step balance:X-ax): the
        # freeze states a derived figure as owned, and money a carry rolled
        # into the row is derived from its source and stays on top, so
        # freezing the total would count it twice.
        try:
            resolved = own_amount(row, bases[row.scenario_id])
        except AmountUnresolvable:
            continue
        state_own_amount(row, resolved)
        frozen += 1

    log_event(
        logger, logging.INFO, EVT_SALARY_ROWS_FROZEN, BUSINESS,
        "Salary rows frozen at their last derived figure",
        user_id=profile.user_id,
        salary_profile_id=profile.id,
        template_id=template.id,
        frozen_count=frozen,
    )
    return frozen
