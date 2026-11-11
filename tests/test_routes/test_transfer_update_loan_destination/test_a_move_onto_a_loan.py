"""Transfer update into a loan: a recurring transfer moved onto a loan takes the create door's
rules (REC-521's second shape).
"""

from datetime import date
from decimal import Decimal

from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.ref import RecurrenceUnit
from app.routes._recurrence_form_refusals import LOAN_PAYMENT_BOUND_IS_DERIVED
from app.schemas.validation import end_bound_before_start_message
from app.services.recurrence import UNREADABLE_CADENCE_MESSAGE, stored_cadence
from tests._test_helpers import create_loan_account, make_loan_payment_template

from ._helpers import (
    _ORIGINATION,
    _FIRST_INSTALLMENT,
    _TYPED_START,
    _LATER_STOP,
    _STORED_STOP,
    _STORED_STOP_BEFORE_START,
    _STORED_START_AFTER_ORIGINATION,
    _ORIGINATION_REFUSAL,
    _mortgage,
    _savings,
    _one_off_into,
    _recurring_into,
    _first_installment_as_stored_by_an_every_paycheck_rule,
    _cadence,
    _post_update,
    _flashes,
    _reload,
    _generated_into,
    _assert_saved,
    _assert_refused_to_the_edit_form,
)


class TestARecurringTransferMovedOntoALoan:
    """REC-521's second shape: the destination move takes the create rules."""

    def test_the_stored_stop_re_posted_is_refused(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """What an ordinary browser posts: the stored stop, still in the open control.

        Refused, and the definition is exactly as it was: still into savings,
        its start and its stop untouched.

        NEGATIVE CONTROL: delete the ``settle_destination_for_update`` call
        and the move proceeds with the savings transfer's own start (the
        schedule's opening payday); regeneration then meets the transfer
        service's R-C refusal of a payment before origination, unhandled on
        this route -- measured: ``ValidationError: Cannot pay 'Mortgage'
        before it originates`` -- which is REC-521's own "a refusal on an
        ordinary edit" price, and this case fails on it.
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        template = _recurring_into(seed_user, savings, stop=_STORED_STOP)
        stored_start = template.recurrence_rule.starts_on

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(starts_on=stored_start),
            recurrence_end_mode="on_date", end_date=_STORED_STOP.isoformat(),
        )

        _assert_refused_to_the_edit_form(
            resp, template, LOAN_PAYMENT_BOUND_IS_DERIVED, auth_client,
        )
        template = _reload(template)
        assert template.to_account_id == savings.id
        assert template.recurrence_rule.starts_on == stored_start
        assert template.recurrence_rule.end_date == _STORED_STOP
        assert _generated_into(loan) == []

    def test_never_moves_it_with_the_derived_start_and_no_stop(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The owner sets "Never" and the move lands as the loan's payment.

        The re-point branch reads the PRESENT ``starts_on`` the settle wrote,
        so the stored savings start is replaced by the loan's first
        installment; the generated rows into the loan begin there and not
        before origination (the $3,220.92 phantom-debit shape).
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        template = _recurring_into(seed_user, savings, stop=_STORED_STOP)
        stored_start = template.recurrence_rule.starts_on

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id),
            **_cadence(starts_on=stored_start),
            recurrence_end_mode="never",
        )

        _assert_saved(resp, auth_client)
        template = _reload(template)
        assert template.to_account_id == loan.id, _flashes(auth_client)
        rule = template.recurrence_rule
        assert rule.starts_on == _FIRST_INSTALLMENT
        assert rule.end_date is None and rule.max_occurrences is None
        generated = _generated_into(loan)
        assert generated, "the moved definition generated nothing"
        assert generated[0].occurs_on == _FIRST_INSTALLMENT

    def test_an_absent_bound_writes_the_unbounded_rule_over_a_stored_stop(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Ruling **R-R77**: nothing stated makes the stop the loan's.

        The update door's ordinary reading of an absent bound -- leave the
        stored one alone -- would carry ``_STORED_STOP`` onto the loan's
        standing payment, where the door reads it as the chokepoints' cache
        and the next loan edit overwrites it (the N-512 shape).  The stored
        stop was the owner's word about a savings transfer, not about the
        loan; and a locked "Ends" row posts nothing, so this is the reading
        the affordance R7d-f-5 adds must be able to lock under.

        NEGATIVE CONTROL: return ``end_bound`` instead of ``NEVER_ENDS`` from
        ``_settle_standing_payment`` and ``end_date`` reads ``_STORED_STOP``
        (the arm is one line since plan step R7d-g-2: the standing branch
        hands the unbounded rule on unconditionally, the stored bound being
        graded nowhere).
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        template = _recurring_into(seed_user, savings, stop=_STORED_STOP)
        stored_start = template.recurrence_rule.starts_on

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(starts_on=stored_start),
        )

        _assert_saved(resp, auth_client)
        template = _reload(template)
        assert template.to_account_id == loan.id, _flashes(auth_client)
        rule = template.recurrence_rule
        assert rule.starts_on == _FIRST_INSTALLMENT
        assert rule.end_date is None and rule.max_occurrences is None

    def test_a_partial_submission_is_completed_from_the_stored_cadence(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A move that restates no cadence still derives the start.

        The re-point branch runs only when the cadence is submitted, so the
        settle completes the payload from the stored row: the cadence is
        unchanged, the start is the loan's, the stop is the loan's (ruling
        **R-R77**).

        NEGATIVE CONTROL: return *submission* uncompleted from
        ``_complete_the_cadence`` (``_cadence_unit_to_settle`` until plan step
        R7e, which wrote the cadence's three keys into the payload) and the
        row moves with its savings start.
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        template = _recurring_into(seed_user, savings, stop=_STORED_STOP)
        before = stored_cadence(template.recurrence_rule)

        resp = _post_update(auth_client, template, to_account_id=str(loan.id))

        _assert_saved(resp, auth_client)
        template = _reload(template)
        assert template.to_account_id == loan.id, _flashes(auth_client)
        rule = template.recurrence_rule
        assert stored_cadence(rule) == before
        assert rule.starts_on == (
            _first_installment_as_stored_by_an_every_paycheck_rule(seed_user)
        )
        assert rule.end_date is None and rule.max_occurrences is None

    def test_a_partial_move_of_an_unreadable_cadence_is_refused(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A cadence the app cannot read cannot be completed, so the move is refused.

        The state ``UNREADABLE_CADENCE_MESSAGE`` names -- a ``unit_id`` the
        enums do not model -- manufactured the way ``test_recurrence_picker``
        manufactures it.  Without the refusal this would be an
        ``AttributeError`` on the reading, a 500.
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        template = _recurring_into(seed_user, savings)
        surplus = RecurrenceUnit(name="Blue Moon")
        db.session.add(surplus)
        db.session.flush()
        template.recurrence_rule.unit_id = surplus.id
        template.recurrence_rule.unit_has_day_of_month = surplus.has_day_of_month
        db.session.commit()
        assert stored_cadence(template.recurrence_rule) is None, (
            "precondition: the cadence is unreadable"
        )

        resp = _post_update(auth_client, template, to_account_id=str(loan.id))

        _assert_refused_to_the_edit_form(
            resp, template, UNREADABLE_CADENCE_MESSAGE, auth_client,
        )
        template = _reload(template)
        assert template.to_account_id == savings.id
        assert template.recurrence_rule.unit_id == surplus.id

    def test_onto_a_paid_loan_both_stored_bounds_stay_the_owners(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A second transfer keeps its stored start AND stop; nothing is derived.

        CONTROL for ruling **R-R77**'s reach (the unbounded rule is written
        only where the loan derives the stop) and, since plan step R7d-g-2,
        for ruling **R-R81**'s: the stored start rides through the move
        untouched.  MOVED BY RULING: this case asserted the derived start
        beside the stored stop.  The stored start is after origination on
        purpose -- the fixture's own, the schedule's opening payday, is
        before it and the move would be refused for that (the next case).
        """
        loan = _mortgage(seed_user)
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        savings = _savings(seed_user)
        template = _recurring_into(
            seed_user, savings,
            stop=_STORED_STOP, start=_STORED_START_AFTER_ORIGINATION,
        )
        stored_start = template.recurrence_rule.starts_on

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(starts_on=stored_start),
        )

        _assert_saved(resp, auth_client)
        template = _reload(template)
        assert template.to_account_id == loan.id, _flashes(auth_client)
        rule = template.recurrence_rule
        assert rule.starts_on == stored_start
        assert rule.end_date == _STORED_STOP

    def test_onto_a_paid_loan_a_stored_start_before_origination_is_refused(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A stored start the loan predates refuses the move, not the window.

        The every-paycheck fixture's start is the schedule's opening payday
        (2026-01-02), before the loan originates; moving it onto a paid loan
        would generate rows the transfer service refuses (R-C), so the door
        refuses first with the origination sentence (ruling **R-R81**).
        Posted as a PARTIAL submission -- no cadence keys, the shape a form
        that changes only the destination sends -- so the start graded is the
        STORED one, which is the branch's second read.  MOVED BY RULING: this
        case stored a stop before the derived start and asserted the window
        sentence; nothing derives a start for a second transfer now, and a
        stored pair that was valid stays valid.
        """
        loan = _mortgage(seed_user)
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        savings = _savings(seed_user)
        template = _recurring_into(
            seed_user, savings, stop=_STORED_STOP_BEFORE_START,
        )
        stored_start = template.recurrence_rule.starts_on
        assert stored_start < _ORIGINATION, "precondition: the fixture's start"

        resp = _post_update(auth_client, template, to_account_id=str(loan.id))

        _assert_refused_to_the_edit_form(
            resp, template, _ORIGINATION_REFUSAL, auth_client,
        )
        assert end_bound_before_start_message(
            _STORED_STOP_BEFORE_START, _FIRST_INSTALLMENT,
        ) not in _flashes(auth_client)
        template = _reload(template)
        assert template.to_account_id == savings.id
        assert template.recurrence_rule.starts_on == stored_start
        assert template.recurrence_rule.end_date == _STORED_STOP_BEFORE_START

    def test_a_stored_stop_before_the_derived_start_is_replaced_where_the_loan_derives_the_stop(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The one input where ruling **R-R77** and the window rule pull apart.

        A payment-less loan, a stored stop that PRECEDES the derived start,
        and nothing posted for "Ends": the stop is the loan's now, so the
        stored one is REPLACED by the unbounded rule and never graded against
        the start -- a door that let the stored bound through to the window
        rule would refuse this edit with a sentence about a stop the owner
        cannot see.  This is the case that makes the graded half of the
        R-R77 arm visible (its sibling above passes with that half disabled).

        Since plan step R7d-g-2 the standing branch grades no stored bound at
        all (``_settle_standing_payment`` refuses a REAL stated stop and hands
        ``NEVER_ENDS`` on; the window comparison went with ruling **R-R81**),
        so what this case pins is that the stored stop is neither graded nor
        kept.  NEGATIVE CONTROL: return ``end_bound`` instead of
        ``NEVER_ENDS`` there and ``end_date`` reads the stored stop.
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        template = _recurring_into(
            seed_user, savings, stop=_STORED_STOP_BEFORE_START,
        )
        stored_start = template.recurrence_rule.starts_on

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(starts_on=stored_start),
        )

        _assert_saved(resp, auth_client)
        assert end_bound_before_start_message(
            _STORED_STOP_BEFORE_START, _FIRST_INSTALLMENT,
        ) not in _flashes(auth_client)
        template = _reload(template)
        assert template.to_account_id == loan.id
        rule = template.recurrence_rule
        assert rule.starts_on == _FIRST_INSTALLMENT
        assert rule.end_date is None and rule.max_occurrences is None

    def test_a_partial_move_onto_savings_leaves_the_rule_alone(
        self, auth_client, seed_user, seed_periods, monkeypatch,  # pylint: disable=unused-argument
    ):
        """CONTROL for the completion's reach: only a LOAN destination completes.

        A partial submission moving a repeating transfer onto another savings
        account submits no cadence, so the recurrence step takes no branch
        and the rule is not re-authored -- the partial-update contract for
        every non-loan destination.  Completed, the re-point branch would run
        a full ``reauthor_rule`` with the stored triple, which for a
        paycheck-cadence rule under a rebuilt schedule can re-normalise the
        start (plan ledger row **D39**'s shape).  The write door is replaced
        by a spy that fails the case if it is reached at all; an adversarial
        review of this step found the first cut completing before it knew the
        destination was a loan.

        NEGATIVE CONTROL: move the ``load_loan_params`` read in
        ``settle_destination_for_update`` below the ``_complete_the_cadence``
        call and the spy fires.
        """
        savings = _savings(seed_user)
        other = _savings(seed_user, name="Other Savings")
        template = _recurring_into(seed_user, savings, stop=_STORED_STOP)
        stored_start = template.recurrence_rule.starts_on

        def _never(rule, spec, calendar):
            raise AssertionError(
                f"rule {rule.id} re-authored as {spec!r} on a move onto "
                f"savings ({calendar!r})",
            )

        # The name the re-point branch calls through; the fixture above wrote
        # its stop through the service's own binding before this runs.
        monkeypatch.setattr(
            "app.routes._recurrence_form_helpers.reauthor_rule", _never,
        )

        resp = _post_update(auth_client, template, to_account_id=str(other.id))

        _assert_saved(resp, auth_client)
        template = _reload(template)
        assert template.to_account_id == other.id
        assert template.recurrence_rule.starts_on == stored_start
        assert template.recurrence_rule.end_date == _STORED_STOP

    def test_a_one_time_transfer_moved_onto_a_loan_still_does_not_repeat(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A move that names no cadence authors none, whatever the destination.

        Both wire shapes: the browser's "Does not repeat" (a present empty
        unit) and a partial submission with no cadence key at all.  Nothing
        is derived because nothing repeats; the move itself lands.
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        for shape in ({"recurrence_unit": ""}, {}):
            template = _one_off_into(
                seed_user, savings, name=f"one-off {len(shape)}",
            )

            resp = _post_update(
                auth_client, template, to_account_id=str(loan.id), **shape,
            )

            _assert_saved(resp, auth_client)
            template = _reload(template)
            assert template.to_account_id == loan.id
            assert template.recurrence_rule is None

    def test_a_clamped_payment_day_reaches_the_re_pointed_rule(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The nominal day travels through the RE-POINT branch, not only the authoring one.

        A servicer's day 31 first billing in a 30-day month: the derived pair
        is the month's last day plus ``nominal_day = 31``, and on a move the
        re-point branch reads ``nominal_day`` beside the STATED start the
        settle wrote (``RecurrenceSubmission.applied_to``).  A door that
        wrote the date alone would model every later installment a day early.
        """
        loan = create_loan_account(
            seed_user, db.session, name="Day 31",
            principal=Decimal("200000.00"), rate=Decimal("0.05000"),
            term=360, origination_date=date(2026, 5, 15), payment_day=31,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        savings = _savings(seed_user)
        template = _recurring_into(seed_user, savings)
        stored_start = template.recurrence_rule.starts_on

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(starts_on=stored_start),
            recurrence_end_mode="never",
        )

        _assert_saved(resp, auth_client)
        rule = _reload(template).recurrence_rule
        assert rule.starts_on == date(2026, 6, 30)
        assert rule.nominal_day == 31

    def test_moved_between_non_loan_accounts_nothing_is_derived(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """CONTROL: a move onto another savings account keeps the owner's word."""
        savings = _savings(seed_user)
        other = _savings(seed_user, name="Other Savings")
        template = _recurring_into(seed_user, savings, stop=_STORED_STOP)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(other.id),
            **_cadence(),
            recurrence_end_mode="on_date", end_date=_LATER_STOP.isoformat(),
        )

        _assert_saved(resp, auth_client)
        template = _reload(template)
        assert template.to_account_id == other.id, _flashes(auth_client)
        rule = template.recurrence_rule
        assert rule.starts_on == _TYPED_START
        assert rule.end_date == _LATER_STOP
