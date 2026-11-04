"""The recurrence picker: an edit form starts on its rule's cadence, and an unreadable rule cannot
be destroyed by saving it.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services.recurrence import UNREADABLE_CADENCE_MESSAGE
from tests._test_helpers import cadence_payload
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    EVERY_N_PERIODS,
    MONTHLY,
    MONTHLY_FIRST,
    QUARTERLY,
    SEMI_ANNUAL,
    ANNUAL,
)
from tests._definition_price import newest_price

from ._helpers import (
    _unmodelled_unit_id,
    _assert_unreadable,
    _savings_account,
    _tag,
    _options,
    _selected,
    _template_with_cadence,
    _transfer_template_with_cadence,
)


# ── The edit form's starting state ───────────────────────────────────


class TestAnEditFormStartsOnItsRulesCadence:
    """Property 4: the controls read back what the rule actually means."""

    @pytest.mark.parametrize(
        ("cadence", "unit", "interval", "placement"),
        [
            (
                EVERY_PERIOD, RecurrenceUnitEnum.PERIOD,
                1, PeriodPlacementEnum.CONTAINING_DATE,
            ),
            (
                MONTHLY, RecurrenceUnitEnum.MONTH,
                1, PeriodPlacementEnum.CONTAINING_DATE,
            ),
            (
                MONTHLY_FIRST, RecurrenceUnitEnum.MONTH,
                1, PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            ),
            (
                QUARTERLY, RecurrenceUnitEnum.MONTH,
                3, PeriodPlacementEnum.CONTAINING_DATE,
            ),
            (
                SEMI_ANNUAL, RecurrenceUnitEnum.MONTH,
                6, PeriodPlacementEnum.CONTAINING_DATE,
            ),
            (
                ANNUAL, RecurrenceUnitEnum.YEAR,
                1, PeriodPlacementEnum.CONTAINING_DATE,
            ),
        ],
        ids=lambda value: getattr(value, "label", None),
    )
    def test_each_stored_cadence_preselects_its_own_axes(
        self, app, auth_client, seed_user, seed_periods_today,
        cadence, unit, interval, placement,
    ):
        """Every named cadence, read back onto the three controls.

        Swept rather than sampled because a mapping wrong for ONE of them shows
        a user the wrong cadence on the screen where they are about to re-save
        it.  The interval is read off the number box for every unit since plan
        step R7c-c, where a calendar cadence used to preselect an option in a
        ``<select>`` -- and a quarterly rule reading back "1 month" is three
        times the projected spend.
        """
        assert seed_periods_today
        with app.app_context():
            template_id = _template_with_cadence(seed_user, cadence).id
            expected_unit = str(ref_cache.recurrence_unit_id(unit))
            expected_placement = str(ref_cache.period_placement_id(placement))

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        assert _selected(body, "recurrence_unit") == expected_unit
        assert _selected(body, "recurrence_placement") == expected_placement
        assert f'value="{interval}"' in _tag(body, "interval_n")

    def test_an_every_n_rule_prefills_its_own_interval(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A stated interval reads back off the column that stores it."""
        assert seed_periods_today
        with app.app_context():
            template_id = _template_with_cadence(
                seed_user, EVERY_N_PERIODS, interval_n=4,
            ).id

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        assert 'value="4"' in _tag(body, "interval_n")

    def test_an_interval_the_closed_set_could_never_name_reads_back(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Every 2 months" round-trips through the form.

        The cadence this whole arc exists for: it resolves and walks correctly
        and had nowhere to be written until plan step R7c-c freed the interval.
        A form that still rendered a 1 / 3 / 6 ``<select>`` would show it as
        one of those, and re-saving would silently re-cadence a bill.
        """
        assert seed_periods_today
        with app.app_context():
            template_id = _template_with_cadence(
                seed_user, MONTHLY, interval_n=2,
            ).id
            month_id = str(ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.MONTH,
            ))

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        assert _selected(body, "recurrence_unit") == month_id
        assert 'value="2"' in _tag(body, "interval_n")


class TestAnUnreadableRuleCannotBeDestroyedBySavingIt:
    """A stored cadence the app cannot read renders UNSET -- and is SAFE.

    An HTML ``<select>`` whose selected value is absent from its options does
    not fail: the browser silently selects the first, and that option submits.
    Before plan step R7b-2 the picker met that by keeping the stored pattern as
    a trailing option, because the first entry is the empty "Does not repeat"
    one whose save DELETES the rule and sweeps its future rows (plan step
    R2e-1).

    The two-axis controls carry no such id, so there is nothing to keep
    selected -- they render unset, which means the unit select's first entry IS
    selected.  ``UNREADABLE_CADENCE_MESSAGE`` therefore promises the user that
    "saving it unchanged will be refused", and this class holds the code to
    that promise: the refusal is the SERVER's, made from the two facts it
    already holds (the stored rule is unreadable, the submission names no
    cadence), not a hidden field a client can drop.

    **The unreadable COLUMN moved at plan step R7c-c.**  It was a ``pattern_id``
    the enum did not name; that column is dropped, so the state a rule can still
    reach is a ``unit_id`` the enums do not name.  Same class of state, same
    disposition, same promise.
    """

    def test_the_transaction_form_renders_the_controls_unset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """No cadence is preselected, because none of them is this rule's."""
        assert seed_periods_today
        with app.app_context():
            surplus_id = _unmodelled_unit_id()
            _assert_unreadable(surplus_id)
            template_id = _template_with_cadence(
                seed_user, MONTHLY, unit_id=surplus_id,
            ).id

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        # The empty "Does not repeat" entry is what "unset" means on a
        # <select>, and it is the whole assertion: the surplus id is a real
        # ``ref.recurrence_units`` row, so a control that rendered it would
        # be offering a cadence nothing can resolve.
        assert _selected(body, "recurrence_unit") == ""
        assert str(surplus_id) not in {
            value for value, _ in _options(body, "recurrence_unit")
        }

    def test_the_transfer_form_renders_the_controls_unset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The same on the transfer form."""
        assert seed_periods_today
        with app.app_context():
            savings = _savings_account(seed_user)
            surplus_id = _unmodelled_unit_id()
            template_id = _transfer_template_with_cadence(
                seed_user, savings, MONTHLY, unit_id=surplus_id,
            ).id

        body = auth_client.get(f"/transfers/{template_id}/edit").data.decode()

        assert _selected(body, "recurrence_unit") == ""

    def test_the_user_is_told_the_cadence_is_unreadable(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A warning names the state and the repair.

        With no odd option left on the control, the sentence is the whole of
        the explanation rather than half of it.
        """
        assert seed_periods_today
        with app.app_context():
            surplus_id = _unmodelled_unit_id()
            template_id = _template_with_cadence(
                seed_user, MONTHLY, unit_id=surplus_id,
            ).id

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        assert UNREADABLE_CADENCE_MESSAGE.split(".", maxsplit=1)[0] in body

    def test_saving_it_unchanged_is_refused_and_the_rule_survives(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The load-bearing one: re-submitting the form destroys nothing.

        The user opens the edit form to change the amount and saves.  The unit
        select submits the empty value the form rendered -- which on any other
        template means "does not repeat", and would DELETE the rule and sweep
        its future rows.  It is not a choice here: the form could not offer
        this rule's cadence, so the empty submission carries no intent, and
        the server refuses rather than acting on it.
        """
        assert seed_periods_today
        with app.app_context():
            surplus_id = _unmodelled_unit_id()
            template = _template_with_cadence(
                seed_user, MONTHLY, unit_id=surplus_id,
            )
            template_id, rule_id = template.id, template.recurrence_rule.id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "amount": "1300.00",
                # Exactly what the rendered form submits.
                "recurrence_unit": "",
                "recurrence_placement": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            # The REASON is asserted, not just the survival: without it this
            # test passes against a route that refused the POST for an
            # unrelated reason.  **The sentence is the SCHEMA's since plan
            # step balance:X-bi-7b** (R-BAL23): a transaction template refuses
            # an empty unit before the route reads a field, so the reader
            # meets "choose how it repeats" -- what the unset picker asks --
            # and ``UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`` is now the transfer
            # door's sentence alone (``test_the_transfer_door_refuses_it_too``).
            assert b"needs a cadence" in resp.data
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule.id == rule_id, (
                "the rule was detached by a save the form promised to refuse"
            )
            assert db.session.get(RecurrenceRule, rule_id) is not None, (
                "the rule row was DELETED by a save the form promised to refuse"
            )
            # The refused edit committed nothing, amount included.
            assert newest_price(reloaded) == Decimal("1200.00")

    def test_the_transfer_door_refuses_it_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The same refusal on the other kind, because the guard is shared.

        Both routes dispatch through ``resolve_recurrence_rule_for_update``,
        and that function checks TWO refusals in order -- the loan-payment one
        and this -- so an ordering change between them would pass a suite that
        only drove the transaction form.
        """
        assert seed_periods_today
        with app.app_context():
            savings = _savings_account(seed_user)
            surplus_id = _unmodelled_unit_id()
            template = _transfer_template_with_cadence(
                seed_user, savings, MONTHLY, unit_id=surplus_id,
            )
            template_id = template.id
            rule_id = template.recurrence_rule.id

            resp = auth_client.post(f"/transfers/{template_id}", data={
                "name": "To Savings",
                "amount": "75.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "recurrence_unit": "",
                "recurrence_placement": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(TransferTemplate, template_id)
            assert reloaded.recurrence_rule.id == rule_id
            assert db.session.get(RecurrenceRule, rule_id) is not None
            assert newest_price(reloaded) == Decimal("50.00")

    def test_choosing_a_cadence_repairs_the_rule(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The repair the surface asks for actually works.

        The class above proves the OTHER action is refused; a suite holding
        only that can watch the advertised remedy turn into a 500, which is
        what happened at plan step R7b-1 -- reading the rule's authored state
        on the way to REPLACING its cadence raised on the cadence being
        replaced.  ``recurrence_spec_with_cadence`` is what fixed it.
        """
        assert seed_periods_today
        with app.app_context():
            surplus_id = _unmodelled_unit_id()
            _assert_unreadable(surplus_id)
            template = _template_with_cadence(
                seed_user, MONTHLY, unit_id=surplus_id,
            )
            template_id, rule_id = template.id, template.recurrence_rule.id
            month_id = ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.MONTH,
            )

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "amount": "1200.00",
                **cadence_payload(
                    unit=RecurrenceUnitEnum.MONTH,
                    starts_on=date(2026, 9, 1),
                ),
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            # The SAME rule row, re-pointed -- not a new one, so every
            # generated row keeps its lineage.
            assert reloaded.recurrence_rule.id == rule_id
            assert reloaded.recurrence_rule.unit_id == month_id
            # The repaired rule fires on the date the form stated, which is
            # the whole of what it says about its cycle since plan step
            # R7c-c: the day and the month are that date's.
            assert reloaded.recurrence_rule.starts_on == date(2026, 9, 1)
