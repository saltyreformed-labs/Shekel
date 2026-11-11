"""Transfer update into a loan: the standing payment stays on its loan (R-R76), and the destination
is owner-checked before its loan terms are read.
"""

import pytest

from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.routes._loan_destination import LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION
from tests._test_helpers import cadence_payload, make_loan_payment_template

from ._helpers import (
    _mortgage,
    _savings,
    _recurring_into,
    _cadence,
    _post_update,
    _flashes,
    _reload,
    _generated_into,
    _assert_saved,
    _assert_refused_to_the_edit_form,
)


class TestTheStandingPaymentCannotChangeDestination:
    """Ruling **R-R76**: a loan's standing payment stays on its loan."""

    def _standing_payment(self, seed_user, loan):
        """Return the loan's COMMITTED standing payment and its stored start."""
        template = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        return template, template.recurrence_rule.starts_on

    @pytest.mark.parametrize("elsewhere", ["savings", "another loan"])
    def test_moved_off_its_loan_is_refused(
        self, auth_client, seed_user, seed_periods, elsewhere,  # pylint: disable=unused-argument
    ):
        """Whatever the new destination is, and nothing is changed.

        What the browser posts for the standing payment: the cadence, and
        neither bound (both rows render locked).  Before this step the row
        moved, the loan was left with no payment projecting against it, and
        the old loan's cached payoff travelled with the row.

        NEGATIVE CONTROL: delete the ``is_standing_loan_payment`` arm from
        ``settle_destination_for_update`` and the row moves.
        """
        loan = _mortgage(seed_user)
        destination = (
            _savings(seed_user) if elsewhere == "savings"
            else _mortgage(seed_user, name="Van")
        )
        template, stored_start = self._standing_payment(seed_user, loan)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(destination.id),
            **cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
            ),
        )

        _assert_refused_to_the_edit_form(
            resp, template, LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION, auth_client,
        )
        template = _reload(template)
        assert template.to_account_id == loan.id
        assert template.recurrence_rule.starts_on == stored_start
        assert _generated_into(destination) == []

    def test_a_settings_carrying_second_payment_may_not_move_either(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The union the twin refusal covers, not the identity alone (**R-R76**).

        A generic recurring transfer is the loan's standing payment (older
        id); the loan dashboard's own door then writes a SECOND, DERIVE-mode
        payment with a settings row.  Not standing -- but its amount is
        priced off the loan it pays into, so moved to savings every read of
        its cash would refuse.  An adversarial review of this step found the
        first cut asking the identity alone; the developer ruled the union.

        NEGATIVE CONTROL: drop ``is_loan_payment(template)`` from the rule-1
        test in ``settle_destination_for_update`` and this moves.
        """
        loan = _mortgage(seed_user)
        standing = _recurring_into(seed_user, loan)
        second = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        assert second.id > standing.id, "precondition: the generic one is standing"
        savings = _savings(seed_user)

        resp = _post_update(
            auth_client, second,
            to_account_id=str(savings.id),
            **cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
            ),
        )

        _assert_refused_to_the_edit_form(
            resp, second, LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION, auth_client,
        )
        assert _reload(second).to_account_id == loan.id

    @pytest.mark.parametrize("shape", [
        pytest.param({}, id="a partial submission with no cadence key"),
        pytest.param({"recurrence_unit": ""}, id="a clear beside the move"),
    ])
    def test_the_move_is_refused_before_the_cadence_is_read(
        self, auth_client, seed_user, seed_periods, shape,  # pylint: disable=unused-argument
    ):
        """Rule 1 speaks first, whatever the submission says about the cadence.

        A partial submission moving the standing payment is refused before
        any cadence is read (the completion never runs); a "Does not repeat"
        beside the move is refused with THIS sentence and not
        ``LOAN_PAYMENT_CANNOT_BE_ONE_TIME`` -- the destination settle runs
        ahead of the recurrence refusals, and nothing else pins that order.
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        template, stored_start = self._standing_payment(seed_user, loan)

        resp = _post_update(
            auth_client, template, to_account_id=str(savings.id), **shape,
        )

        _assert_refused_to_the_edit_form(
            resp, template, LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION, auth_client,
        )
        template = _reload(template)
        assert template.to_account_id == loan.id
        assert template.recurrence_rule.starts_on == stored_start

    def test_a_second_transfer_into_a_paid_loan_may_move(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """CONTROL for the identity: only the STANDING payment is held.

        A second recurring transfer into a paid loan is not the loan's
        payment, so moving it onto savings is an ordinary edit and the
        loan keeps its payment.
        """
        loan = _mortgage(seed_user)
        standing, _ = self._standing_payment(seed_user, loan)
        savings = _savings(seed_user)
        second = _recurring_into(seed_user, loan)

        resp = _post_update(
            auth_client, second,
            to_account_id=str(savings.id),
            **_cadence(),
        )

        _assert_saved(resp, auth_client)
        assert _reload(second).to_account_id == savings.id, _flashes(auth_client)
        assert _reload(standing).to_account_id == loan.id

    def test_an_edit_that_keeps_the_destination_is_not_a_move(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """CONTROL: a destination posted UNCHANGED is not a move.

        What every rename posted until plan step R7d-f-5; since then the
        standing payment's form renders its destination control disabled and
        posts NO key, and that payload is
        ``test_transfer_edit_form_locks.py``'s.  This case keeps the
        posted-unchanged shape: an API client, or a form rendered before the
        control locked.
        """
        loan = _mortgage(seed_user)
        template, stored_start = self._standing_payment(seed_user, loan)
        renamed = f"{template.name}, renamed"

        resp = _post_update(
            auth_client, template,
            name=renamed,
            to_account_id=str(loan.id),
            **cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
            ),
        )

        _assert_saved(resp, auth_client)
        template = _reload(template)
        assert template.name == renamed, _flashes(auth_client)
        assert template.to_account_id == loan.id
        assert template.recurrence_rule.starts_on == stored_start


class TestTheDestinationIsOwnerCheckedBeforeItIsRead:
    """The settle reads the destination's loan terms, so ownership comes first."""

    def test_a_foreign_loan_as_the_new_destination_is_refused_unread(
        self, auth_client, seed_user, seed_second_user, seed_periods, monkeypatch,  # pylint: disable=unused-argument
    ):
        """Another owner's loan is refused before its terms are loaded.

        ``test_audit_fixes.py::test_update_transfer_template_with_foreign_to_account``
        already probes this route's destination; what this adds is the READ
        that made the order matter: ``load_loan_params`` is replaced by a spy
        that fails the case if the settle reaches it at all.  The refusal is
        the ownership sentence, to the edit form, and the definition is
        unchanged.

        NEGATIVE CONTROL: move the ``_first_unowned_template_fk`` block back
        below the settle in ``update_transfer_template`` and the spy fires.
        """
        foreign_loan = _mortgage(seed_second_user, name="Not Yours")
        savings = _savings(seed_user)
        template = _recurring_into(seed_user, savings)
        stored_start = template.recurrence_rule.starts_on

        def _never(account_id):
            raise AssertionError(
                f"loan terms read for account {account_id} before ownership",
            )

        # The package attribute the settle reads through
        # (``loan_loaders.load_loan_params``); the seam's own ``from``-import
        # binding is untouched, and nothing else on this path reaches it.
        monkeypatch.setattr("app.services.loan_loaders.load_loan_params", _never)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(foreign_loan.id),
            **_cadence(),
        )

        _assert_refused_to_the_edit_form(
            resp, template, "Invalid destination account.", auth_client,
        )
        template = _reload(template)
        assert template.to_account_id == savings.id
        assert template.recurrence_rule.starts_on == stored_start
