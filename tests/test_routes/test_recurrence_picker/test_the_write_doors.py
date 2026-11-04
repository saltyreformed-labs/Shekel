"""The recurrence picker: the write doors refuse an unstorable cadence, and a cleared interval box
cannot re-cadence a rule.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from tests._test_helpers import cadence_payload
from tests.oracles.recurrence_baseline import MONTHLY, QUARTERLY
from tests._definition_price import newest_price

from ._helpers import _savings_account, _expense_type_id, _template_with_cadence


class TestTheWriteDoorsRefuseAnUnstorableCadence:
    """A hand-assembled POST is refused with a message, and writes nothing.

    The end-to-end half of ``validate_authorable_cadence``.  Its predecessor
    covered the equivalent for a submitted PATTERN id and was deleted with the
    field; an adversarial review of plan step R7b-2 caught that nothing had
    replaced it, so a route that accepted the triple and 500'd at the flush
    would have passed the whole suite.

    ``(1, PERIOD, first paycheck on or after)`` is the case since plan step
    **R8-b**: a pay-period cadence's placement is inert, so the offer set
    holds only its containing reading and the funding row renders the other
    disabled.  No click produces it.

    **The case has MOVED THREE TIMES, each time because the gap it named
    closed.**  It was ``(2, MONTH)`` until plan step R7c-c, which freed the
    interval; then ``(1, YEAR, first paycheck)`` until R8-a, which admitted it
    -- that refusal named a first-occurrence derivation ruling **R-R16** had
    already deleted; then ``(1, WEEK)`` until R8-b, which dates a weekly row
    from its occurrence.  The inert placement is a refusal of a different
    kind -- a control the engine would ignore, not a cadence it cannot run --
    and it is what is left for a hand-assembled POST to meet.
    """

    #: The verbatim copy the routes flash.  Pinned because the message names
    #: the two controls the user can change, and the generic
    #: "correct the highlighted errors" prompt names none -- on a redirect that
    #: highlights nothing.
    _REFUSAL = b"That repeat schedule cannot be saved yet"

    def test_a_transaction_template_is_not_created(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /templates refuses, flashes, and persists no rule.

        Both halves matter: a 200 alone would also be produced by a route that
        created the template and redirected to the list.
        """
        assert seed_periods_today
        with app.app_context():
            rules_before = db.session.query(RecurrenceRule).count()

            resp = auth_client.post("/templates", data={
                "name": "Every Other Month Bill",
                "amount": "10.00",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "transaction_type_id": str(_expense_type_id()),
                "account_id": str(seed_user["account"].id),
                **cadence_payload(
                    unit=RecurrenceUnitEnum.PERIOD,
                    placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                ),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert self._REFUSAL in resp.data
            assert db.session.query(TransactionTemplate).filter_by(
                name="Every Other Month Bill",
            ).first() is None
            assert db.session.query(RecurrenceRule).count() == rules_before

    def test_a_transfer_template_is_not_created(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transfers refuses the same triple the same way.

        Swept across both kinds because ``_form_errors`` exists precisely
        because the two forms once explained the same refusal differently.
        """
        assert seed_periods_today
        with app.app_context():
            savings = _savings_account(seed_user)
            rules_before = db.session.query(RecurrenceRule).count()

            resp = auth_client.post("/transfers", data={
                "name": "Every Other Month Transfer",
                "amount": "25.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                **cadence_payload(
                    unit=RecurrenceUnitEnum.PERIOD,
                    placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                ),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert self._REFUSAL in resp.data
            assert db.session.query(TransferTemplate).filter_by(
                name="Every Other Month Transfer",
            ).first() is None
            assert db.session.query(RecurrenceRule).count() == rules_before

    def test_an_edit_leaves_the_existing_rule_untouched(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /templates/<id> refuses and re-points nothing.

        The edit path re-AUTHORS the rule in place, so a door that accepted the
        triple would overwrite a working cadence on a row the user already
        depends on.
        """
        assert seed_periods_today
        with app.app_context():
            month_id = ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.MONTH,
            )
            template = _template_with_cadence(seed_user, MONTHLY)
            template_id, rule_id = template.id, template.recurrence_rule.id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "amount": "1200.00",
                **cadence_payload(
                    unit=RecurrenceUnitEnum.PERIOD,
                    placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                ),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert self._REFUSAL in resp.data
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule.id == rule_id
            assert reloaded.recurrence_rule.unit_id == month_id


class TestAClearedIntervalBoxCannotReCadenceARule:
    """Plan step **R7c-c**: an emptied "every ___" box is refused, not read as 1.

    **The defect this pins moved money, and the whole suite was blind to it.**
    Every case in ``test_recurrence_form_helpers.py`` states ``interval_n``
    explicitly through ``validated_cadence(...)``, so nothing exercised the
    absence -- and the absence is what a browser produces: an ``<input
    type="number">`` cleared by the user submits ``""``, which
    ``_normalize_empty_inputs`` DROPS because the field is not ``allow_none``.
    Both write doors then defaulted to ``1``, so a save with an empty box
    silently re-cadenced a quarterly bill to monthly -- 12 occurrences a year
    where 4 were owed, across the whole projection.

    R7c-c is what made it reachable for the calendar units: the months
    ``<select>`` it replaced could not post an empty value, so only the PERIOD
    unit's free box carried the shape before.

    The interval box is enabled for every chosen cadence, so absence beside a
    named unit is a cleared box or a crafted POST and never "not mine to
    state".  That is why the refusal is the submission's -- see
    ``validate_authorable_cadence`` -- rather than a fourth ``KEY in data``
    read at a route site, which is the copy plan ledger row **D36** warns
    against.
    """

    #: The verbatim copy the routes flash, pinned for the reason the sibling
    #: class pins its own: the generic "correct the highlighted errors" prompt
    #: names no control, on a redirect that highlights nothing.
    _REFUSAL = b"Say how often this repeats"

    def test_an_edit_that_clears_it_leaves_the_stored_interval_alone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The measured case: a quarterly bill stays quarterly.

        Asserting the stored ``interval_n`` rather than the response alone is
        the point -- the pre-fix route ALSO answered 200 here, and redirected
        to a list that says nothing about the cadence.
        """
        assert seed_periods_today
        with app.app_context():
            template = _template_with_cadence(seed_user, QUARTERLY)
            template_id, rule_id = template.id, template.recurrence_rule.id
            assert template.recurrence_rule.interval_n == 3

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "amount": "1200.00",
                **cadence_payload(
                    unit=RecurrenceUnitEnum.MONTH,
                    interval_n=3,
                    starts_on=date(2026, 1, 1),
                ),
                # What the browser posts for a box the user emptied.
                "interval_n": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert self._REFUSAL in resp.data
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule.id == rule_id
            assert reloaded.recurrence_rule.interval_n == 3

    def test_a_create_that_clears_it_authors_no_rule(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The create door refuses the same absence, and writes nothing.

        Swept because the create path carried the identical default and its
        harm is only different in kind: an edit re-cadences a bill that
        exists, a create authors a monthly one the user never asked for.
        """
        assert seed_periods_today
        with app.app_context():
            rules_before = db.session.query(RecurrenceRule).count()

            resp = auth_client.post("/templates", data={
                "name": "Water Bill",
                "amount": "120.00",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "transaction_type_id": str(_expense_type_id()),
                "account_id": str(seed_user["account"].id),
                **cadence_payload(unit=RecurrenceUnitEnum.MONTH, interval_n=3),
                "interval_n": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert self._REFUSAL in resp.data
            assert db.session.query(TransactionTemplate).filter_by(
                name="Water Bill",
            ).first() is None
            assert db.session.query(RecurrenceRule).count() == rules_before

    def test_a_stated_interval_still_saves(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The negative control: the refusal is about ABSENCE, not the field.

        Without it a route that refused every submission carrying an interval
        would pass both cases above -- and re-cadencing a bill deliberately is
        the ordinary edit this form exists for.
        """
        assert seed_periods_today
        with app.app_context():
            template = _template_with_cadence(seed_user, QUARTERLY)
            template_id = template.id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "amount": "1200.00",
                **cadence_payload(
                    unit=RecurrenceUnitEnum.MONTH,
                    interval_n=6,
                    starts_on=date(2026, 1, 1),
                ),
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule.interval_n == 6

    def test_an_amount_only_edit_states_no_cadence_and_is_unaffected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A submission naming NO unit is still a partial update.

        The rule is conditional on a cadence being named, exactly as
        ``validate_recurrence_states_a_start`` is: refusing an absent interval
        unconditionally would make every amount-only edit unsavable, which is
        the failure mode the placement half was written to avoid.
        """
        assert seed_periods_today
        with app.app_context():
            template = _template_with_cadence(seed_user, QUARTERLY)
            template_id, rule_id = template.id, template.recurrence_rule.id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "amount": "1300.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert newest_price(reloaded) == Decimal("1300.00")
            assert reloaded.recurrence_rule.id == rule_id
            assert reloaded.recurrence_rule.interval_n == 3
