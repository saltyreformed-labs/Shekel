"""Archiving a salary profile records what it was pricing (finding N-261).

Plan step **balance:X-au-d**, developer ruling 2026-09-02.

**The defect these grade did not exist before that cutover and is not
hypothetical.**  A paycheck row STORED its figure, so deactivating the profile
that computed it moved nothing.  A declared row is priced by its DEFINITION, and
once no ACTIVE profile names that definition the salary-linked refinement stops
applying -- amount rule 3 answers instead, from the single price version
``routes/salary/profiles.delete_profile`` opens the template's series at.  That
version is the ``default_amount`` scalar, which
``template_amount_service.is_salary_linked_template`` documents as *vestigial*
for exactly this kind of template, and a scalar cannot express a paycheck.

Measured on the 2026-09-02 production clone WITHOUT the freeze: 50 of 59 rows
re-priced and the projected balance moved **-$9,677.24**, because the rows range
from ``$2,483.19`` to ``$3,328.41`` and every one of them became ``$2,572.78``.
With it: **0 rows move and the balance moves `$0.00`**, re-measured the same way.

The remedy is the act a settle already performs one tier up -- record what a row
was worth at the moment the thing that computes it goes away -- and these cases
grade it on a real profile with a real paycheck engine behind it, because a
planted derivation could not tell the frozen figure from the vestigial one.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    AmountSourceEnum,
    MovementFigureSourceEnum,
    SettledDayBasisEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.extensions import db
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.transaction_template import TransactionTemplate
from tests._test_helpers import (
    cover_bare_settled_row,
    repriced_by_the_owner,
    capture_sql_statements,
    generate_row_of,
    make_every_period_rule,
    start_test_pay_list,
)
from app.services import salary_profile_service, status_seam, template_amount_service
from app.services.cash_ledger import (
    derived_amount_basis,
    amounts_by_key,
    contributions_by_key,
)
from app.utils.dates import display_today

#: The vestigial scalar the archive states as the template's price.  Chosen far
#: from any paycheck this profile computes so a frozen figure and a fallen-back
#: one are never the same number by accident -- which is what makes every
#: assertion below able to fail.
_VESTIGIAL = Decimal("11.11")


def _salary_profile(seed_user):
    """Return an ACTIVE salary profile and the template it prices.

    A real profile with a real annual salary, because the whole subject is the
    difference between what the paycheck engine answers and what the template's
    scalar does -- a planted derivation could not express it.

    Args:
        seed_user: The seeded owner bundle.

    Returns:
        ``(profile, template)``, both flushed.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=next(iter(seed_user["categories"].values())).id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
        name="Paycheck",
        default_amount=_VESTIGIAL,
    )
    db.session.add(template)
    db.session.flush()
    # The CADENCE onto the definition (plan step R-F6).  Without it the
    # template names no rule, ``resolve_generation_plan`` answers ``None`` and
    # a regeneration maintains nothing -- which is a fixture that could not
    # grade the reactivation case below, and which an early run of this file
    # proved by passing the archive cases and failing that one.
    make_every_period_rule(db.session, template)
    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        filing_status_id=db.session.query(FilingStatus).first().id,
        template_id=template.id,
        name="N-261 Salary",
        state_code="NC",
        is_active=True,
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("4000.00"))  # $104,000.00 a year / 26
    db.session.flush()
    return profile, template


def _declared_row(template, period, *, status=StatusEnum.PROJECTED):
    """Return the engine's row of *template* in *period*: derived, no figure.

    (:func:`generate_row_of`, plan step balance:X-cf.)  The engine writes
    Projected; any other *status* is laid on bare, which is the one axis the
    settled case below is about.

    Args:
        template: The salary-linked definition.
        period: The pay period the row is funded in.
        status: Which status to give it.

    Returns:
        The flushed :class:`~app.models.transaction.Transaction`.
    """
    txn = generate_row_of(template, period)
    if status is not StatusEnum.PROJECTED:
        txn.status_id = ref_cache.status_id(status)
        db.session.flush()
    return txn


def _archive(auth_client, profile):
    """Archive *profile* through the DOOR, and return its response.

    **It drives the route rather than reproducing its body**, which an
    adversarial review of this step required: a fixture that re-spelled
    ``delete_profile``'s sequence would grade the fixture's ordering, and two
    spellings of one sequence with no reconciler is the shape (``CLAUDE.md``
    rule 14) this whole file is about.  The service's order-sensitivity is
    demonstrated separately, on the service, by
    :meth:`TestArchivingFreezesWhatItWasPricing.test_freezing_AFTER_the_flag_would_record_the_vestigial_scalar`.

    Args:
        auth_client: The authenticated test client.
        profile: The profile to archive.

    Returns:
        The route's response.
    """
    return auth_client.post(
        f"/salary/{profile.id}/delete", follow_redirects=True,
    )


class TestArchivingFreezesWhatItWasPricing:
    """The rows keep their last derived figure, so the archive moves no money.

    **Two states are deliberately uncovered and named rather than left to be
    found**, both by an adversarial review of this step:

    * **rows spanning ACCOUNTS** need no case.  The basis is keyed on owner and
      SCENARIO and the resolver's only pin is ``scenario_id``, so an account
      plays no part in what the freeze resolves.
    * **a template a SECOND profile still names** cannot be stored within one
      scenario since plan step salary:X-av-1
      (``uq_salary_profiles_scenario_template``, rulings **R-SAL63** and
      **R-SAL69**, closing finding **N-294**).  ACROSS scenarios it is
      storable (R-SAL69: a what-if scenario may give the paycheck its own
      salary) and unreachable, because no code in ``app/`` creates a
      non-baseline scenario; there the freeze, which walks the template's rows
      in every scenario, would take the other scenario's rows to OWN while
      its profile still priced them -- one of the five template-keyed doors
      ledger row **SAL-570** records.  It is NOT covered here because a case
      asserting behaviour in a state no door can produce grades the fixture
      rather than the app.
    """

    def test_the_balance_does_not_move_and_the_figures_are_the_PAYCHECKS(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """Every declared row keeps what its profile paid, not the scalar.

        Two assertions and both are needed.  The balance one is the money
        claim; the FIGURE one is what stops it passing vacuously -- a freeze
        that recorded ``$11.11`` on every row would also leave the balance
        unmoved if the comparison were taken after the freeze rather than
        before it.
        """
        with app.app_context():
            profile, template = _salary_profile(seed_user)
            rows = [
                _declared_row(template, period)
                for period in seed_periods[:4]
            ]
            db.session.commit()
            basis = derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id)
            before = amounts_by_key(rows, basis)
            before_total = sum(
                contributions_by_key(rows, basis).values(),
            )
            assert all(
                figure > _VESTIGIAL for figure in before.values()
            ), "the paycheck must differ from the scalar or nothing is graded"

            assert _archive(auth_client, profile).status_code == 200

            after_basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            assert sum(
                contributions_by_key(rows, after_basis).values(),
            ) == before_total
            for row in rows:
                db.session.refresh(row)
                assert row.estimated_amount == before[row.id]
                assert row.amount_source_id is None

    def test_freezing_AFTER_the_flag_would_record_the_vestigial_scalar(
        self, app, db, seed_user, seed_periods,
    ):
        """WHY the service is order-sensitive, in both of its wrong orders.

        **This is a demonstration and not the ordering GATE, which an
        adversarial review of this step corrected in place.**  The route's own
        order is graded by the door cases above and below -- they POST to
        ``/salary/<id>/delete`` and assert the paycheck survives, so moving
        ``archive_profile`` below ``profile.is_active = False`` in
        ``routes/salary/profiles.py`` fails them.  What this case adds is the
        REASON: it shows what each wrong order produces, so a reader moving that
        call knows what they would be buying.

        ``is_salary_linked_template`` reads the identity-mapped collection, so a
        pending ``is_active = False`` is visible to it immediately -- which is
        exactly what makes a wrong order silent.  The two wrong orders differ,
        and the second is the more likely single-line move:

          * AFTER the flag AND after ``set_amount`` -- the template's series
            answers, and the row freezes at the vestigial ``$11.11``;
          * BETWEEN them -- the series is still empty, ``_stated_amount``
            refuses, ``archive_profile`` skips the row, and it comes away STILL
            DECLARED and unpriceable.
        """
        with app.app_context():
            profile, template = _salary_profile(seed_user)
            row = _declared_row(template, seed_periods[0])
            between = _declared_row(template, seed_periods[1])
            db.session.flush()

            profile.is_active = False
            template.is_active = False
            db.session.flush()
            # Wrong order two: between the flag and the series.
            assert salary_profile_service.archive_profile(profile) == 0
            assert between.amount_source_id is not None
            assert between.estimated_amount is None

            template_amount_service.set_amount(
                template, template.default_amount,
                effective_on=display_today(),
            )
            db.session.flush()
            # Wrong order one: after both.
            salary_profile_service.archive_profile(profile)

            assert row.estimated_amount == _VESTIGIAL, (
                "freezing after the flag records the vestigial scalar -- which "
                "is the -$9,677.24 this ordering exists to prevent"
            )

    def test_a_SETTLED_row_is_frozen_too_and_its_money_is_untouched(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """A settled row's PLAN is a derivation like any other (ruling R-JB).

        It re-prices to the same vestigial scalar and is frozen for the same
        reason, even though no balance depends on it -- a settled row is worth
        what it RECORDED.

        **The RECORD is asserted, not just the plan**, which an adversarial
        review of this step required: without it a freeze that also wrote
        the record would pass in full while restating what a received
        paycheck really paid, which is the one thing this act must never do.
        """
        with app.app_context():
            profile, template = _salary_profile(seed_user)
            settled = _declared_row(
                template, seed_periods[0], status=StatusEnum.RECEIVED,
            )
            settled.settled_on = date(2026, 1, 5)
            settled.settled_day_basis_id = ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.ENTERED,
            )
            db.session.flush()
            cover_bare_settled_row(db.session, settled, Decimal("4000.00"))
            db.session.commit()
            basis = derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id)
            planned = amounts_by_key([settled], basis)[settled.id]
            recorded = sum(contributions_by_key([settled], basis).values())

            _archive(auth_client, profile)
            db.session.refresh(settled)

            assert settled.estimated_amount == planned
            assert planned > _VESTIGIAL
            # The RECORD, two ways: the covering movement's figure and who
            # wrote it, and what a balance actually folds for the row.
            assert status_seam.recorded_settlement(settled) == status_seam.Settlement(
                Decimal("4000.00"), MovementFigureSourceEnum.RESOLVED,
            )
            assert sum(
                contributions_by_key(
                    [settled],
                    derived_amount_basis(
                        seed_user["user"].id, seed_user["scenario"].id,
                    ),
                ).values(),
            ) == recorded

    def test_a_profile_with_no_template_freezes_nothing(
        self, app, db, seed_user,
    ):  # pylint: disable=unused-argument
        """The totality arm: a profile that prices nothing is a no-op.

        ``SalaryProfile.template_id`` is nullable, so this is a state the
        model admits rather than one a caller has to avoid.
        """
        with app.app_context():
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=db.session.query(FilingStatus).first().id,
                name="Templateless",
                state_code="NC",
                is_active=True,
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("1923.08"))  # $50,000.00 a year / 26
            db.session.flush()

            assert salary_profile_service.archive_profile(profile) == 0

    def test_a_row_that_OWNS_its_figure_is_left_alone(
        self, app, db, seed_user, seed_periods,
    ):
        """A hand-priced paycheck has no derivation to lose.

        The freeze is about rows whose producer is going away; a row that
        already states its own figure is not one of them, and re-writing it
        would be a second writer of a column a human owns.
        """
        with app.app_context():
            profile, template = _salary_profile(seed_user)
            # The engine's row, re-priced by its owner: the re-price door's
            # two acts on the definition's own row.
            owned = repriced_by_the_owner(
                generate_row_of(template, seed_periods[0]), "1234.56",
            )

            assert salary_profile_service.archive_profile(profile) == 0
            assert owned.estimated_amount == Decimal("1234.56")


class TestTheFreezeResolvesOneBasisPerScenario:
    """The paycheck engine runs ONCE per archive, not once per row.

    Findings **N-228** and **N-268**'s shape, and the reason the freeze groups
    its rows: a basis is pinned to an owner and a SCENARIO and holds the salary
    derivation lazily, so building a fresh one per row runs
    ``paycheck_calculator.project_salary`` over the owner's whole pay-period set
    once per row.  On production that is 59 projections for one click.
    """

    def test_the_projection_is_resolved_once_however_many_rows(
        self, app, db, seed_user, seed_periods,
    ):
        """Six rows in one scenario, one read of the owner's pay schedule.

        Graded on the QUERY the projection makes rather than on elapsed time:
        ``SalaryPricing._breakdown_by_period`` derives the owner's calendar, which
        reads ``budget.pay_schedule`` exactly once per instance.  A basis per
        row makes an instance per row and this count becomes six.  The control
        was shown to fire by building the basis inside the loop, which is the
        shape it exists to forbid.
        """
        with app.app_context():
            profile, template = _salary_profile(seed_user)
            for period in seed_periods[:6]:
                _declared_row(template, period)
            db.session.commit()

            frozen, statements = capture_sql_statements(
                lambda: salary_profile_service.archive_profile(profile),
            )

            schedule_reads = [
                text for text, _params in statements
                if "budget.pay_schedule" in text
            ]
            assert frozen == 6
            assert len(schedule_reads) == 1, (
                "the salary derivation must resolve ONCE for the whole "
                f"archive; got {len(schedule_reads)} pay-schedule reads"
            )


class TestReactivationNeedsNoCounterpart:
    """The regeneration that already runs puts the FUTURE rows back on it.

    **And it reaches only the future, which is a boundary rather than a
    caveat.**  ``salary_regeneration.regenerate_salary_transactions``
    (a route helper until plan step salary:S3-f-3) regenerates with
    ``effective_from=date.today()``, so a frozen row in a PAST
    period is outside the maintain window and stays frozen.  That is the right
    answer for it -- the paycheck it plans has already happened, and its last
    derived figure is what it was worth -- but it is stated here because an
    early draft of the service's docstring claimed the round trip was total,
    and this file's own first run refuted that by leaving a 2024 row frozen.
    """

    def test_a_frozen_FUTURE_row_is_re_declared_by_the_regeneration(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """Archive then reactivate, through the app's own two doors.

        ``reactivate_profile`` regenerates, and the maintain pass writes each
        non-override projected row's whole ownership from its definition
        (``recurrence_engine._amounts._generated_amount_ownership``) -- which
        for a template an active profile names is a DECLARATION.  So the round
        trip needs no un-freeze act for a row that pass can reach, and this
        asserts that rather than assuming it.
        """
        with app.app_context():
            profile, template = _salary_profile(seed_user)
            future = seed_periods_today[8]
            row = _declared_row(template, future)
            assert row.occurs_on == future.start_date, (
                "the maintain pass reaches a row by the occurrence the engine "
                "wrote on it; a PERIOD-unit rule's occurrence is the payday"
            )
            db.session.commit()

            auth_client.post(
                f"/salary/{profile.id}/delete", follow_redirects=True,
            )
            db.session.refresh(row)
            assert row.amount_source_id is None, "the archive froze it"
            assert row.estimated_amount > _VESTIGIAL

            auth_client.post(
                f"/salary/{profile.id}/reactivate", follow_redirects=True,
            )
            db.session.refresh(row)

            assert row.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            assert row.estimated_amount is None

    def test_a_frozen_PAST_row_stays_frozen(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """The boundary, asserted rather than described.

        Without this case the class's claim would read as total, and the first
        reader to move a frozen 2024 row would find it had not come back.
        """
        with app.app_context():
            profile, template = _salary_profile(seed_user)
            past = seed_periods_today[0]
            row = _declared_row(template, past)
            assert row.occurs_on == past.start_date
            db.session.commit()

            auth_client.post(
                f"/salary/{profile.id}/delete", follow_redirects=True,
            )
            db.session.refresh(row)
            frozen_at = row.estimated_amount

            auth_client.post(
                f"/salary/{profile.id}/reactivate", follow_redirects=True,
            )
            db.session.refresh(row)

            assert row.amount_source_id is None
            assert row.estimated_amount == frozen_at
            assert frozen_at > _VESTIGIAL, (
                "the row must have frozen at its PAYCHECK; a scalar here "
                "would make the case above self-consistent and wrong"
            )


@pytest.mark.usefixtures("app")
class TestTheArchiveRouteFreezes:
    """The service is reached through the DOOR, not only in a unit test."""

    def test_the_delete_route_freezes_before_it_deactivates(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """POST /salary/<id>/delete leaves every row holding its paycheck.

        The route test the unit cases need beside them: a service that is never
        called from the door would pass every case above.
        """
        with app.app_context():
            profile, template = _salary_profile(seed_user)
            rows = [
                _declared_row(template, period)
                for period in seed_periods[:3]
            ]
            db.session.commit()
            basis = derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id)
            before = amounts_by_key(rows, basis)

            response = auth_client.post(
                f"/salary/{profile.id}/delete", follow_redirects=True,
            )

            assert response.status_code == 200
            for row in rows:
                db.session.refresh(row)
                assert row.estimated_amount == before[row.id]
                assert row.estimated_amount > _VESTIGIAL
