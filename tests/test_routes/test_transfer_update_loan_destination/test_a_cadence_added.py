"""Transfer update into a loan: a cadence added to a one-time transfer into a loan takes the create
door's rules (REC-521's first shape).
"""

from datetime import date

import pytest

from app.extensions import db
from app.routes._recurrence_form_refusals import LOAN_PAYMENT_BOUND_IS_DERIVED
from app.schemas.validation import end_bound_before_start_message
from tests._test_helpers import make_loan_payment_template

from ._helpers import (
    _ORIGINATION,
    _FIRST_INSTALLMENT,
    _TYPED_START,
    _LATER_STOP,
    _EARLIER_STOP,
    _ORIGINATION_REFUSAL,
    _mortgage,
    _savings,
    _one_off_into,
    _cadence,
    _post_update,
    _flashes,
    _reload,
    _generated_into,
    _assert_saved,
    _assert_refused_to_the_edit_form,
)


class TestACadenceAddedToAOneTimeTransferIntoALoan:
    """REC-521's first shape: the authoring branch takes the create rules."""

    @pytest.mark.parametrize("bound", [
        pytest.param(
            {"recurrence_end_mode": "on_date",
             "end_date": _LATER_STOP.isoformat()},
            id="on a date",
        ),
        pytest.param(
            {"recurrence_end_mode": "after_occurrences", "max_occurrences": "12"},
            id="after a count",
        ),
    ])
    def test_a_real_stop_is_refused_where_the_loan_holds_no_payment(
        self, auth_client, seed_user, seed_periods, bound,  # pylint: disable=unused-argument
    ):
        """Both stop shapes, and NOTHING is authored.

        The loan holds no active recurring transfer, so the definition would
        be its payment the moment it repeats, and a loan payment carries no
        authored stop (ruling **R-R59**).  Before this step the authoring
        branch wrote the typed start and this stop.

        NEGATIVE CONTROL: delete the ``settle_destination_for_update`` call
        from ``update_transfer_template`` and both cases save with the posted
        bound and ``starts_on == _TYPED_START``.
        """
        loan = _mortgage(seed_user)
        template = _one_off_into(seed_user, loan)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(), **bound,
        )

        _assert_refused_to_the_edit_form(
            resp, template, LOAN_PAYMENT_BOUND_IS_DERIVED, auth_client,
        )
        template = _reload(template)
        assert template.recurrence_rule is None, "a refused edit authored a rule"
        assert template.to_account_id == loan.id
        assert _generated_into(loan) == []

    def test_never_writes_the_unbounded_rule_from_the_loans_first_installment(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The typed start is replaced by the loan's, and "Never" is the rule.

        The edit form's "Starts on" box is open for a definition with no
        rule, so an ordinary browser posts the date in it; the loan's first
        contractual installment is what the rule fires from, whatever was
        typed -- exactly what the create door does for a browser whose lock
        did not run.

        NEGATIVE CONTROL: replace the ``settle_loan_start`` call in
        ``settle_destination_for_update`` with a bare ``loan_cadence_start``
        (deriving without writing into the payload) and ``starts_on`` reads
        ``_TYPED_START``.
        """
        loan = _mortgage(seed_user)
        template = _one_off_into(seed_user, loan)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(),
            recurrence_end_mode="never",
        )

        _assert_saved(resp, auth_client)
        rule = _reload(template).recurrence_rule
        assert rule is not None, _flashes(auth_client)
        assert rule.starts_on == _FIRST_INSTALLMENT
        assert rule.end_date is None and rule.max_occurrences is None
        generated = _generated_into(loan)
        assert generated, "the definition generated nothing"
        assert generated[0].occurs_on == _FIRST_INSTALLMENT

    def test_an_absent_bound_writes_the_unbounded_rule(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """What a locked "Ends" row will post once R7d-f-5 locks it: nothing.

        The authoring branch already read an absent bound as "ends never";
        this pins that the settle leaves that reading standing beside the
        derived start (ruling **R-R77**'s authoring case).
        """
        loan = _mortgage(seed_user)
        template = _one_off_into(seed_user, loan)

        resp = _post_update(
            auth_client, template, to_account_id=str(loan.id), **_cadence(),
        )

        _assert_saved(resp, auth_client)
        rule = _reload(template).recurrence_rule
        assert rule is not None, _flashes(auth_client)
        assert rule.starts_on == _FIRST_INSTALLMENT
        assert rule.end_date is None and rule.max_occurrences is None

    def test_into_a_paid_loan_both_bounds_are_the_owners(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A loan already holding a payment makes this a SECOND transfer.

        Its stop is its owner's (ruling **R-R77**'s second-transfer clause)
        and, since plan step R7d-g-2, so is its start (ruling **R-R81**):
        the typed date is stored, not the loan's first installment.  MOVED
        BY RULING: until R7d-g-2 this case asserted the derived start beside
        the owner's stop, the derive-then-discard shape plan ledger row
        **D50** measured.

        NEGATIVE CONTROL: delete the ``loan_holds_no_active_payment`` gate
        from ``settle_destination_for_update`` and the start comes back
        derived.
        """
        loan = _mortgage(seed_user)
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        template = _one_off_into(seed_user, loan)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(),
            recurrence_end_mode="on_date", end_date=_LATER_STOP.isoformat(),
        )

        _assert_saved(resp, auth_client)
        rule = _reload(template).recurrence_rule
        assert rule is not None, _flashes(auth_client)
        assert rule.starts_on == _TYPED_START
        assert rule.end_date == _LATER_STOP
        generated = [
            row for row in _generated_into(loan)
            if row.transfer_template_id == template.id
        ]
        assert generated, "the second transfer generated nothing"
        assert generated[0].occurs_on == _TYPED_START, (
            "a second transfer must generate from its owner's start, not the "
            "loan's first installment"
        )

    def test_into_a_paid_loan_a_start_at_or_before_origination_is_refused(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The one thing the loan still says about a second transfer's start.

        Ruling **R-R81**: refused at or before the loan's origination, with
        the door's own sentence -- the transfer service's R-C floor is the
        same fact, but it would refuse the first generated row after the
        definition was flushed.  Both the boundary day and one before it,
        because the service's boundary is ``<=`` and this must match it.
        """
        loan = _mortgage(seed_user)
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        template = _one_off_into(seed_user, loan)

        for typed in (_ORIGINATION, date(2026, 4, 14)):
            resp = _post_update(
                auth_client, template,
                to_account_id=str(loan.id), **_cadence(starts_on=typed),
            )
            _assert_refused_to_the_edit_form(
                resp, template, _ORIGINATION_REFUSAL, auth_client,
            )
            assert _reload(template).recurrence_rule is None

        # The control: the day after originates.
        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id),
            **_cadence(starts_on=date(2026, 4, 16)),
        )
        _assert_saved(resp, auth_client)
        assert _reload(template).recurrence_rule.starts_on == date(2026, 4, 16)

    def test_on_a_paid_loan_the_schema_grades_the_owners_pair(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A second transfer's window is graded against ITS OWN start.

        MOVED BY RULING at plan step R7d-g-2 (**R-R81**): this case posted a
        typed start before origination and a stop after it, and asserted the
        window sentence against the DERIVED start.  Nothing is derived for a
        second transfer now, so the schema's own comparison
        (``require_end_bound_after_start``) grades the posted pair: a stop
        before the typed start is refused with the same sentence, and neither
        the derived-stop nor the origination sentence speaks.
        """
        loan = _mortgage(seed_user)
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        template = _one_off_into(seed_user, loan)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id),
            **_cadence(starts_on=_TYPED_START),
            recurrence_end_mode="on_date", end_date=_EARLIER_STOP.isoformat(),
        )

        _assert_refused_to_the_edit_form(
            resp, template,
            end_bound_before_start_message(_EARLIER_STOP, _TYPED_START),
            auth_client,
        )
        assert LOAN_PAYMENT_BOUND_IS_DERIVED not in _flashes(auth_client)
        assert _ORIGINATION_REFUSAL not in _flashes(auth_client)
        assert _reload(template).recurrence_rule is None

    def test_a_stop_before_the_derived_start_is_refused_as_a_stop_not_as_a_window(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """On a payment-less loan the derived-stop sentence speaks; no window sentence exists.

        The create door's twin case says why: since plan step R7d-g-2 the
        doors grade no stop against a derived start (ruling **R-R81**), so a
        real stop here is refused for being stated at all, whatever its date.
        """
        loan = _mortgage(seed_user)
        template = _one_off_into(seed_user, loan)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id),
            **_cadence(starts_on=date(2025, 12, 1)),
            recurrence_end_mode="on_date", end_date=_EARLIER_STOP.isoformat(),
        )

        _assert_refused_to_the_edit_form(
            resp, template, LOAN_PAYMENT_BOUND_IS_DERIVED, auth_client,
        )
        assert end_bound_before_start_message(
            _EARLIER_STOP, _FIRST_INSTALLMENT,
        ) not in _flashes(auth_client)
        assert _reload(template).recurrence_rule is None

    def test_a_savings_destination_keeps_the_owners_start_and_stop(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """CONTROL for the rule's reach: nothing is derived for a savings account."""
        savings = _savings(seed_user)
        template = _one_off_into(seed_user, savings)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(savings.id), **_cadence(),
            recurrence_end_mode="on_date", end_date=_LATER_STOP.isoformat(),
        )

        _assert_saved(resp, auth_client)
        rule = _reload(template).recurrence_rule
        assert rule is not None, _flashes(auth_client)
        assert rule.starts_on == _TYPED_START
        assert rule.end_date == _LATER_STOP
