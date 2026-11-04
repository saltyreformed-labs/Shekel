"""The recurrence picker: the copy the user reads, a deliberate clear on a transaction template,
and the end bound's offer set.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import ref_cache
from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction_template import TransactionTemplate
from app.services.recurrence import _picker
from app.services.recurrence import (
    END_BOUND_KINDS,
    EndsAfterOccurrences,
    EndsOnDate,
    NeverEnds,
    authorable_cadences,
    end_bound_options,
    picker_model,
)
from tests._test_helpers import cadence_payload
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests._definition_price import newest_price

from ._helpers import _option_labels, _template_with_cadence


class TestTheCopyTheUserActuallyReads:
    """The picker's words, pinned verbatim.

    Restored after an adversarial review of plan step R7b-2 found the whole
    label surface unpinned: the migration's successor test compared option
    VALUES between the two forms and never read a label.  The template chooses
    between two of them -- ``unit_label_one if interval_n == 1 else
    unit_label_many`` -- so swapping the pair in ``_picker._UNIT_LABELS``
    renders "1 months", "3 month", "6 month", "1 years" with every other test
    in the suite still green.  ``CadenceOption``'s ``KeyError``-on-missing-copy
    design catches an ABSENT label; nothing caught a wrong one.
    """

    def test_the_unit_options_read_as_plurals(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The unit select is worded for "Repeats <unit>", so plural.

        **The first entry is a PLACEHOLDER on the transaction form since plan
        step balance:X-bi-7b** (ruling R-BAL23): a transaction definition with
        no rule is a one-off, made at the Budget grid, so this form offers no
        "Does not repeat" -- the empty entry asks for a cadence and the schema
        refuses its value.  The transfer form keeps the option
        (``test_the_transfer_form_still_offers_does_not_repeat``).
        """
        assert seed_periods_today
        body = auth_client.get("/templates/new").data.decode()

        assert _option_labels(body, "recurrence_unit") == [
            "Choose how it repeats", "paychecks", "weeks", "months", "years",
        ]

    def test_the_transfer_form_still_offers_does_not_repeat(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The transfer form IS the one-time transfer's door until its own step decides."""
        assert seed_periods_today
        body = auth_client.get("/transfers/new").data.decode()

        assert _option_labels(body, "recurrence_unit") == [
            "Does not repeat", "paychecks", "weeks", "months", "years",
            "statement due dates",
        ]

    # ``test_the_interval_options_agree_with_their_own_counts`` was here until
    # plan step R7c-c.  It pinned the interval ``<select>``'s labels -- "1
    # paycheck", "1 month", "3 months", "6 months", "1 year" -- which was the
    # one place the template picked between ``unit_label_one`` and
    # ``unit_label_many``.  That control is deleted: every interval is a free
    # number box, so the count is typed rather than chosen and there is no
    # per-count noun to word.  Both labels still ride on the option, because
    # plan step R8's controls will need them; nothing renders the singular
    # today, which is why nothing pins it.

    def test_the_placement_options_are_worded_over_the_date(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Generic over the occurrence DATE rather than per unit.

        The old picker's "Monthly (first paycheck of month)" fused a unit, an
        interval, an anchor day and this choice into one name, which is why
        "every other month, funded from the first paycheck" had nowhere to
        live.
        """
        assert seed_periods_today
        body = auth_client.get("/templates/new").data.decode()

        assert _option_labels(body, "recurrence_placement") == [
            "The paycheck that covers the date",
            "The first paycheck starting on or after the date",
        ]

    def test_both_forms_word_every_cadence_identically(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A user meets one vocabulary whichever kind they are creating.

        Every CADENCE reads the same on both forms.  The one entry that differs
        is the empty one, by ruling (R-BAL23, plan step balance:X-bi-7b): the
        transfer form's "Does not repeat" is a choice and the transaction
        form's "Choose how it repeats" is a placeholder, so it is compared
        separately above rather than folded into this equality.  The transfer
        form's last cadence is its own, a card's statement due dates (plan step
        credit_card:CC-6), so the transaction form's are compared with the rest.
        """
        assert seed_periods_today
        transaction = auth_client.get("/templates/new").data.decode()
        transfer = auth_client.get("/transfers/new").data.decode()

        assert _option_labels(transaction, "recurrence_unit")[1:] == (
            _option_labels(transfer, "recurrence_unit")[1:-1]
        )
        assert _option_labels(transaction, "recurrence_placement") == (
            _option_labels(transfer, "recurrence_placement")
        )


class TestADeliberateClearIsRefusedOnATransactionTemplate:
    """The empty unit is no longer a choice on this kind (R-BAL23, balance:X-bi-7b).

    This class was the negative control for the unreadable-cadence refusal
    above -- "a rule the form COULD show is cleared by the empty unit" -- and
    the developer ruled that door shut (2026-09-13): a transaction definition
    with no rule is a one-off, made at the Budget grid, so a cadence cannot be
    CLEARED from the form.  The control that a route refusing EVERY save would
    still fail is that a real cadence change lands (``test_a_modelled_rule_
    can_be_re_pointed``); the transfer twin's clear is graded in
    ``test_recurrence_clear``.
    """

    def test_a_modelled_rule_is_not_cleared_by_the_empty_unit(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The empty unit meets ``A_CADENCE_IS_REQUIRED``; rule and amount stand."""
        assert seed_periods_today
        with app.app_context():
            template = _template_with_cadence(seed_user, EVERY_PERIOD)
            template_id, rule_id = template.id, template.recurrence_rule.id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "amount": "1300.00",
                "recurrence_unit": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"needs a cadence" in resp.data
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule is not None
            assert reloaded.recurrence_rule.id == rule_id
            assert db.session.get(RecurrenceRule, rule_id) is not None
            assert newest_price(reloaded) == Decimal("1200.00")

    def test_a_modelled_rule_can_be_re_pointed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The firing control: a stated cadence still lands on the same door."""
        assert seed_periods_today
        with app.app_context():
            template = _template_with_cadence(seed_user, EVERY_PERIOD)
            template_id, rule_id = template.id, template.recurrence_rule.id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "amount": "1200.00",
                **cadence_payload(unit=RecurrenceUnitEnum.MONTH),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"needs a cadence" not in resp.data
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule is not None
            assert reloaded.recurrence_rule.id == rule_id
            assert reloaded.recurrence_rule.unit_id == ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.MONTH,
            )


@pytest.mark.usefixtures("app")
class TestTheEndBoundOfferSet:
    """What the "Ends" control offers, derived from the shape set (R7b-3).

    The same property plan step R7b-2 gave the cadence controls, applied to
    the closing bound: the offer set and the dispatch that accepts a
    submission read ONE table, so a shape is offerable and submittable
    together or neither.
    """

    def test_it_offers_every_shape_and_only_those(self):
        """Derived from ``END_BOUND_KINDS``, in the tuple's own order.

        Never first, because it is the default a create form starts on.
        """
        offered = [option.token for option in end_bound_options()]

        assert offered == [kind.token for kind in END_BOUND_KINDS]

    def test_every_offer_is_worded(self):
        """A blank ``<option>`` is a choice the user cannot understand."""
        for option in end_bound_options():
            assert option.label.strip(), f"{option.token} has no label"

    def test_a_shape_without_copy_raises_rather_than_rendering_blank(self):
        """The contract ``_UNIT_LABELS`` holds, for the bound's shapes.

        A shape added at plan step R8 without copy fails at the first render
        instead of shipping an empty entry in a control that decides when a
        bill stops being charged.
        """
        with patch.object(
            _picker, "END_BOUND_KINDS",
            (*END_BOUND_KINDS, SimpleNamespace(token="unworded")),
        ):
            with pytest.raises(KeyError):
                end_bound_options()

    def test_each_offer_names_the_control_its_shape_needs(self):
        """The script shows one input and disables the rest from this.

        Stated by the OFFER rather than by the template, so a shape and the
        control it reads a value from cannot be added apart.  The unbounded
        shape names none, which is what "no value input" is.
        """
        by_token = {
            option.token: option.needs_field_id
            for option in end_bound_options()
        }

        assert by_token[NeverEnds.token] is None
        assert by_token[EndsOnDate.token] == "field-end-date"
        assert by_token[EndsAfterOccurrences.token] == "field-max-occurrences"

    def test_the_picker_model_carries_them(self):
        """One producer call per render, for every control on the form."""
        assert picker_model(authorable_cadences()).end_bounds == end_bound_options()
