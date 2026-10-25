"""
Shekel Budget App -- the doors on a ONE-TIME transfer (a rule-less definition's)

Plan step ``balance:X-ci-1`` (rulings **R-BAL92** to **R-BAL94**, **R-BAL96**,
**R-BAL97**; findings **BAL-492** and **BAL-493**).  A transfer that does not
repeat is a rule-less ``TransferTemplate`` plus the one ``Transfer`` it PLACED
in the paycheck the owner chose (``routes/transfers/_instances``), the same
shape ``balance:X-bi-7b`` gave a one-off transaction -- and until this leaf
every door read the LINK as *generated*: a typed figure or a period move
flagged it ``is_override`` and made its figure its own, after which its
definition never spoke to it again (transfer 409 on the 2026-09-20 restore),
and the discardable count promised it back after a truncate that no rule would
write.  ``TransferTemplate.recurs`` / ``Transfer.recurs`` / ``Transfer.is_placed``
are the accessors every such site reads now (``PaycheckLine.recurs`` beside
them, R-BAL97), and the transaction twin of each door is pinned in
``tests/test_routes/test_one_off_row_doors.py``.

Pinned per door, each beside its recurring or ad-hoc control so the arms are
graded against each other and not against a shape the app never writes:

* BIRTH -- ``occurs_on = due_date`` on the transfer the create form places
  (R-BAL94);
* the DISCARDABLE COUNT -- a one-time transfer needs the owner's confirmation
  before a truncate takes it (BAL-492's transfer half);
* the OVERRIDE FLIP -- a period move and a typed figure flag only a
  RECURRING definition's transfer (R-BAL93; BAL-493's route writer);
* a PERIOD MOVE RE-PLACES the transfer inside the service door (R-BAL93 /
  R-BAL96): the target paycheck's start unless the owner had stated a day,
  ``occurs_on`` following; a date typed beside the move is the owner's;
* a TYPED FIGURE restates the definition's price in place and re-attaches the
  transfer (R-BAL92); the definition's next rename reaches it;
* the DUE-DATE gate's three arms -- a one-time transfer's date moves and
  never clears, a recurring one's is refused, an ad-hoc one's still clears;
  the form's input is ``required`` on the first, absent on the second;
* a day a SIBLING of the definition already answers is refused with a
  sentence (a cleared cadence's survivors);
* CARRY-FORWARD moves a one-time transfer without the flag and re-places it
  (BAL-493's third writer).

**Two shapes of rule-less transfer are graded**, and which each case uses is
deliberate: :func:`_one_time` is the app's own producer (the create form's
default selection, one transfer, one-version series), :func:`_survivors` is
a cleared cadence (several transfers of one definition, each answering the
occurrence the cadence named -- what the sibling refusal is graded on).
"""
import io
import pathlib
import re
import tokenize
from datetime import date, timedelta
from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

from app import ref_cache
from app.enums import AcctTypeEnum, AmountSourceEnum, StatusEnum
from app.exceptions import PayPeriodDiscardRequired
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.paycheck_line import PaycheckLine
from app.models.template_amount_version import TemplateAmountVersion
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import (
    carry_forward_service,
    pay_period_admin,
    transfer_service,
)
from app.services.amount_ownership import state_own_amount
from app.services.balance_at import BalanceContext
from app.services.row_valuation import settled_figure
from app.services.template_amount_service import amount_versions
from app.utils.dates import display_today
from tests._test_helpers import (
    create_loan_account,
    create_savings_account,
    freeze_today,
    generate_transfer_of,
    make_cadence_rule,
    make_transfer_template,
    state_template_price,
    transfer_amount,
)
from tests.oracles.recurrence_baseline import MONTHLY_FIRST
from tests.test_routes._statement_forms import form_fields


def _savings(seed_user):
    """A savings account, opened at `$0.00` today; every case moves money later."""
    savings = create_savings_account(
        seed_user, db.session, "One-Time Savings", Decimal("0.00"),
    )
    db.session.commit()
    return savings


def _one_time(auth_client, seed_user, savings, period, *, name="Once Payment",
              amount="500.00"):
    """Place a one-time transfer through the create form's DEFAULT selection.

    The app's one producer of the shape (``_materialize_one_time_transfer``):
    a rule-less definition stating *amount*, and its single transfer in
    *period*, due on the paycheck's start.
    """
    resp = auth_client.post("/transfers", data={
        "name": name,
        "default_amount": amount,
        "from_account_id": str(seed_user["account"].id),
        "to_account_id": str(savings.id),
        "recurrence_unit": "",
        "start_period_id": str(period.id),
        "category_id": str(seed_user["categories"]["Rent"].id),
    }, follow_redirects=True)
    assert resp.status_code == 200, resp.data
    template = (
        db.session.query(TransferTemplate)
        .filter_by(user_id=seed_user["user"].id, name=name)
        .one()
    )
    xfer = db.session.query(Transfer).filter_by(
        transfer_template_id=template.id,
    ).one()
    assert xfer.is_placed is True
    assert xfer.recurs is False
    return xfer


def _survivors(seed_user, savings, periods):
    """The engine's transfers of a definition whose cadence was then cleared."""
    template = make_transfer_template(db.session, seed_user, savings)
    rows = [generate_transfer_of(template, period) for period in periods]
    template.recurrence_rule = None
    db.session.commit()
    for row in rows:
        assert row.is_placed is True
    return rows


def _recurring(seed_user, savings, period):
    """THE CONTROL: the engine's transfer of a definition that still repeats."""
    template = make_transfer_template(db.session, seed_user, savings)
    xfer = generate_transfer_of(template, period)
    db.session.commit()
    assert xfer.recurs is True
    return xfer


def _card(auth_client, xfer):
    """Return the transfer's full-edit card, rendered."""
    resp = auth_client.get(f"/transfers/{xfer.id}/full-edit")
    assert resp.status_code == 200
    return resp.data.decode()


def _submit(auth_client, xfer, **changes):
    """PATCH *xfer* with what its card renders, *changes* typed over it.

    A browser submits every control the card renders at the value it
    renders, so the payload is READ off the card rather than written by
    hand; a changed field replaces every pair of that name.
    """
    rendered = form_fields(
        _card(auth_client, xfer), f"/transfers/instance/{xfer.id}",
        attribute="hx-patch",
    )
    payload = [pair for pair in rendered if pair[0] not in changes]
    payload.extend(changes.items())
    return auth_client.patch(
        f"/transfers/instance/{xfer.id}", data=MultiDict(payload),
    )


def _future(seed_periods_today):
    """The seeded paychecks the card's period select offers (today onward)."""
    return [p for p in seed_periods_today if p.start_date > display_today()]


def _shadows(xfer_id):
    return db.session.query(Transaction).filter_by(
        transfer_id=xfer_id, is_deleted=False,
    ).all()


def _rename_definition(auth_client, xfer, name):
    """Save the definition's edit form with a new *name* and nothing else."""
    template = xfer.template
    return auth_client.post(f"/transfers/{template.id}", data={
        "name": name,
        "default_amount": str(template.default_amount),
        "from_account_id": str(template.from_account_id),
        "to_account_id": str(template.to_account_id),
        "recurrence_unit": "",
        "category_id": str(template.category_id),
        "version_id": str(template.version_id),
    }, follow_redirects=True)


class TestTheAccessors:
    """``recurs`` on the three definition kinds, and ``is_placed`` on the row."""

    def test_the_transfer_twin_answers_by_its_definitions_rule(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _savings(seed_user)
            one_time = _one_time(
                auth_client, seed_user, savings, _future(seed_periods_today)[0],
            )
            recurring = _recurring(seed_user, savings, seed_periods_today[0])
            assert one_time.template.recurs is False
            assert recurring.template.recurs is True
            assert (one_time.recurs, one_time.is_placed) == (False, True)
            assert (recurring.recurs, recurring.is_placed) == (True, False)

    def test_an_ad_hoc_transfer_is_neither(
        self, app, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _savings(seed_user)
            xfer = Transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            assert (xfer.recurs, xfer.is_placed) == (False, False)

    def test_a_paycheck_line_answers_by_its_rule(self, app):
        with app.app_context():
            line = PaycheckLine()
            assert line.recurs is False

    @pytest.mark.parametrize("model, name", [
        (Transfer, "recurs"), (Transfer, "is_placed"),
        (TransferTemplate, "recurs"), (PaycheckLine, "recurs"),
    ])
    def test_the_class_level_name_refuses_to_key_a_query(self, model, name):
        """A ``DerivedFlag``: ``filter_by(recurs=True)`` fails to BUILD."""
        with pytest.raises(TypeError, match="per-row derivation"):
            _ = getattr(model, name) == True  # noqa: E712  pylint: disable=singleton-comparison


class TestBirth:
    """R-BAL94 at the one producer: the transfer answers its own due date."""

    def test_a_one_time_transfer_records_its_due_date_as_its_occurrence(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _savings(seed_user)
            period = _future(seed_periods_today)[0]
            xfer = _one_time(auth_client, seed_user, savings, period)
            assert xfer.due_date == period.start_date
            assert xfer.occurs_on == period.start_date
            assert xfer.is_override is False
            assert xfer.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            assert transfer_amount(xfer) == Decimal("500.00")


class TestTheDiscardableCount:
    """BAL-492's transfer half: ``count_discardable_items`` loads and asks."""

    def test_a_one_time_transfer_in_the_tail_requires_confirm(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A transfer no rule would write back is counted, link or not.

        The arm read ``transfer_template_id IS NULL`` in SQL, so this
        transfer -- linked, Projected, unflagged -- was counted REGENERABLE
        and truncate promised it back.  It counts as ONE (its two shadows are
        excluded from the transaction arm by ``transfer_id IS NULL``).
        """
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            _one_time(auth_client, seed_user, savings, future[2])
            user_id = seed_user["user"].id
            with pytest.raises(PayPeriodDiscardRequired) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=future[1].id,
                )
            assert excinfo.value.count == 1

    def test_a_recurring_transfer_in_the_tail_still_needs_none(
        self, app, seed_user, seed_periods_today,
    ):
        """THE CONTROL: the engine's transfer is regenerable."""
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            _recurring(seed_user, savings, future[2])
            user_id = seed_user["user"].id
            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=future[1].id,
            )
            db.session.commit()
            assert deleted >= 1


class TestTheOverrideFlip:
    """``mutations.update_transfer``: the flip keys on ``recurs`` (R-BAL93)."""

    def test_a_period_move_does_not_flip_a_one_time_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            xfer = _one_time(auth_client, seed_user, savings, future[0])
            resp = _submit(auth_client, xfer, pay_period_id=str(future[1].id))
            assert resp.status_code == 200, resp.data
            assert resp.headers["HX-Trigger"] == "gridRefresh"
            db.session.refresh(xfer)
            assert xfer.pay_period_id == future[1].id
            assert xfer.is_override is False
            assert all(s.is_override is False for s in _shadows(xfer.id))

    def test_a_period_move_still_flips_a_recurring_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: the flip the regeneration relies on is untouched."""
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            xfer = _recurring(seed_user, savings, future[0])
            resp = _submit(auth_client, xfer, pay_period_id=str(future[1].id))
            assert resp.status_code == 200, resp.data
            db.session.refresh(xfer)
            assert xfer.pay_period_id == future[1].id
            assert xfer.is_override is True

    def test_a_typed_figure_does_not_flip_a_one_time_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _savings(seed_user)
            xfer = _one_time(
                auth_client, seed_user, savings, _future(seed_periods_today)[0],
            )
            resp = _submit(auth_client, xfer, amount="525.00")
            assert resp.status_code == 200, resp.data
            db.session.refresh(xfer)
            assert xfer.is_override is False

    def test_a_typed_figure_still_flips_and_detaches_a_recurring_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: one occurrence among many becomes the owner's."""
        with app.app_context():
            savings = _savings(seed_user)
            xfer = _recurring(seed_user, savings, _future(seed_periods_today)[0])
            resp = _submit(auth_client, xfer, amount="525.00")
            assert resp.status_code == 200, resp.data
            db.session.refresh(xfer)
            assert xfer.is_override is True
            assert xfer.amount_source_id is None
            assert xfer.amount == Decimal("525.00")
            assert xfer.template.default_amount == Decimal("200.00")


class TestAPeriodMoveRePlaces:
    """R-BAL93 inside the service door (R-BAL96): the date follows the placement."""

    def test_a_default_dated_transfer_takes_the_target_paychecks_start(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Born on its paycheck's start, moved: due on the new one's start.

        ``occurs_on`` follows (R-BAL94), both shadows carry the date
        (Transfer Invariant 3), and the price is unchanged (a flat series).
        """
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            xfer = _one_time(auth_client, seed_user, savings, future[0])
            assert xfer.due_date == future[0].start_date
            resp = _submit(auth_client, xfer, pay_period_id=str(future[1].id))
            assert resp.status_code == 200, resp.data
            db.session.refresh(xfer)
            assert xfer.pay_period_id == future[1].id
            assert xfer.due_date == future[1].start_date
            assert xfer.occurs_on == future[1].start_date
            assert all(s.due_date == future[1].start_date for s in _shadows(xfer.id))
            assert transfer_amount(xfer) == Decimal("500.00")

    def test_an_owner_dated_transfer_keeps_its_day(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: a date other than the source's start is the owner's."""
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            xfer = _one_time(auth_client, seed_user, savings, future[0])
            stated = future[0].start_date + timedelta(days=6)
            resp = _submit(auth_client, xfer, due_date=stated.isoformat())
            assert resp.status_code == 200, resp.data
            db.session.refresh(xfer)
            assert (xfer.due_date, xfer.occurs_on) == (stated, stated)
            resp = _submit(auth_client, xfer, pay_period_id=str(future[1].id))
            assert resp.status_code == 200, resp.data
            db.session.refresh(xfer)
            assert xfer.pay_period_id == future[1].id
            assert (xfer.due_date, xfer.occurs_on) == (stated, stated)

    def test_a_date_typed_beside_the_move_is_the_owners(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            xfer = _one_time(auth_client, seed_user, savings, future[0])
            typed = future[1].start_date + timedelta(days=4)
            resp = _submit(
                auth_client, xfer,
                pay_period_id=str(future[1].id), due_date=typed.isoformat(),
            )
            assert resp.status_code == 200, resp.data
            db.session.refresh(xfer)
            assert xfer.pay_period_id == future[1].id
            assert (xfer.due_date, xfer.occurs_on) == (typed, typed)

    def test_a_recurring_transfers_move_leaves_its_date_and_occurrence_alone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: the engine's occurrence is not the paycheck's."""
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            xfer = _recurring(seed_user, savings, future[0])
            before = (xfer.due_date, xfer.occurs_on)
            resp = _submit(auth_client, xfer, pay_period_id=str(future[1].id))
            assert resp.status_code == 200, resp.data
            db.session.refresh(xfer)
            assert (xfer.due_date, xfer.occurs_on) == before

    def test_a_service_caller_cannot_move_one_without_re_placing_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The rule lives in the door, so a bare ``pay_period_id`` re-places too."""
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            xfer = _one_time(auth_client, seed_user, savings, future[0])
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, pay_period_id=future[2].id,
            )
            db.session.commit()
            db.session.refresh(xfer)
            assert (xfer.due_date, xfer.occurs_on) == (
                future[2].start_date, future[2].start_date,
            )
            assert xfer.is_override is False


class TestTheRestate:
    """R-BAL92 at the popover: the definition's price is corrected in place."""

    def test_a_typed_figure_restates_the_definition_and_re_attaches_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """`$500.00` on the definition, `$525.00` typed: ONE version, both read it."""
        with app.app_context():
            savings = _savings(seed_user)
            xfer = _one_time(
                auth_client, seed_user, savings, _future(seed_periods_today)[0],
            )
            resp = _submit(auth_client, xfer, amount="525.00")
            assert resp.status_code == 200, resp.data
            db.session.refresh(xfer)
            assert xfer.amount is None
            assert xfer.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            assert xfer.is_override is False
            assert transfer_amount(xfer) == Decimal("525.00")
            assert [v.amount for v in amount_versions(xfer.template)] == [
                Decimal("525.00"),
            ]
            assert xfer.template.default_amount == Decimal("525.00")
            for shadow in _shadows(xfer.id):
                assert shadow.amount_source_id == ref_cache.amount_source_id(
                    AmountSourceEnum.PARENT_TRANSFER,
                )

    def test_a_transfer_the_old_flip_detached_is_re_attached_by_its_next_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Transfer 409's state -- OWN `$2,000.00`, flagged -- heals on a typed figure."""
        with app.app_context():
            savings = _savings(seed_user)
            xfer = _one_time(
                auth_client, seed_user, savings, _future(seed_periods_today)[0],
            )
            state_own_amount(xfer, Decimal("2000.00"))
            xfer.is_override = True
            db.session.commit()
            assert xfer.amount_source_id is None
            resp = _submit(auth_client, xfer, amount="2100.00")
            assert resp.status_code == 200, resp.data
            db.session.refresh(xfer)
            assert xfer.amount is None
            assert xfer.is_override is False
            assert all(s.is_override is False for s in _shadows(xfer.id))
            assert transfer_amount(xfer) == Decimal("2100.00")
            assert xfer.template.default_amount == Decimal("2100.00")

    def test_the_definitions_rename_reaches_the_transfer_after_a_typed_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """BAL-493's whole point: the definition still speaks to the row."""
        with app.app_context():
            savings = _savings(seed_user)
            xfer = _one_time(
                auth_client, seed_user, savings, _future(seed_periods_today)[0],
            )
            resp = _submit(auth_client, xfer, amount="525.00")
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            xfer = db.session.get(Transfer, xfer.id)
            resp = _rename_definition(auth_client, xfer, "Once Payment (renamed)")
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            xfer = db.session.get(Transfer, xfer.id)
            assert xfer.name == "Once Payment (renamed)"
            assert transfer_amount(xfer) == Decimal("525.00")

    def test_a_figure_typed_beside_status_paid_is_what_the_legs_book(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The reimbursement came in at `$525.00`: type it, mark Paid, one save.

        Found by X-ci-1's adversarial review and reproduced: as first built
        the popover restated the definition AFTER the door, and the settle
        inside the door booked the pre-restate `$500.00` on both legs while
        the plan read `$525.00`.  The restate is the door's own act now
        (``definition_price``, R-BAL96 as amended), ordered before the settle.
        """
        with app.app_context():
            savings = _savings(seed_user)
            xfer = _one_time(
                auth_client, seed_user, savings, _future(seed_periods_today)[0],
            )
            resp = _submit(
                auth_client, xfer, amount="525.00",
                status_id=str(ref_cache.status_id(StatusEnum.DONE)),
            )
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            xfer = db.session.get(Transfer, xfer.id)
            assert xfer.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert transfer_amount(xfer) == Decimal("525.00")
            assert xfer.template.default_amount == Decimal("525.00")
            assert xfer.is_override is False
            legs = _shadows(xfer.id)
            assert len(legs) == 2
            assert [settled_figure(leg) for leg in legs] == [Decimal("525.00")] * 2

    def test_the_service_refuses_the_act_on_a_recurring_transfer(
        self, app, seed_user, seed_periods_today,
    ):
        """A recurring definition's transfer is one occurrence among many."""
        with app.app_context():
            savings = _savings(seed_user)
            xfer = _recurring(seed_user, savings, _future(seed_periods_today)[0])
            with pytest.raises(ValueError, match="one occurrence among many"):
                transfer_service.update_transfer(
                    xfer.id, seed_user["user"].id,
                    definition_price=Decimal("525.00"),
                )

    def test_the_service_refuses_the_act_beside_an_ownership(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two statements of what prices the pair are refused as one call."""
        with app.app_context():
            savings = _savings(seed_user)
            xfer = _one_time(
                auth_client, seed_user, savings, _future(seed_periods_today)[0],
            )
            with pytest.raises(ValueError, match="beside amount_ownership"):
                transfer_service.update_transfer(
                    xfer.id, seed_user["user"].id,
                    definition_price=Decimal("525.00"),
                    amount_ownership=AmountOwnership.own(Decimal("525.00")),
                )

    def test_a_figure_typed_beside_a_move_restates_at_the_moved_date(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Act 2 before act 3: the version the NEW date reads is corrected.

        A cleared cadence's survivor whose series holds two versions: one
        before the target paycheck, one on it.  Moved and re-priced in one
        save, the version the re-placed date reads takes the figure; the
        other is untouched.
        """
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            (xfer,) = _survivors(seed_user, savings, [future[0]])
            xfer.template.amount_versions.append(TemplateAmountVersion(
                effective_date=future[1].start_date, amount=Decimal("300.00"),
            ))
            db.session.commit()
            resp = _submit(
                auth_client, xfer,
                pay_period_id=str(future[1].id), amount="325.00",
            )
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            xfer = db.session.get(Transfer, xfer.id)
            assert xfer.due_date == future[1].start_date
            assert transfer_amount(xfer) == Decimal("325.00")
            assert sorted(
                (v.effective_date, v.amount) for v in amount_versions(xfer.template)
            ) == sorted([
                (amount_versions(xfer.template)[0].effective_date, Decimal("200.00")),
                (future[1].start_date, Decimal("325.00")),
            ])


class TestTheDueDateGate:
    """``_reject_generated_due_date_edit``'s three arms, and the form's input."""

    def test_the_card_offers_the_input_required_on_a_one_time_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _savings(seed_user)
            xfer = _one_time(
                auth_client, seed_user, savings, _future(seed_periods_today)[0],
            )
            html = _card(auth_client, xfer)
            match = re.search(r'<input type="date" name="due_date"[^>]*>', html)
            assert match is not None
            assert "required" in match.group(0)

    def test_the_card_renders_text_on_a_recurring_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _savings(seed_user)
            xfer = _recurring(seed_user, savings, _future(seed_periods_today)[0])
            html = _card(auth_client, xfer)
            assert 'name="due_date"' not in html
            assert "Set by this recurring transfer" in html

    def test_moving_the_date_lands_on_all_three_rows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            xfer = _one_time(auth_client, seed_user, savings, future[0])
            moved = future[0].start_date + timedelta(days=3)
            resp = _submit(auth_client, xfer, due_date=moved.isoformat())
            assert resp.status_code == 200, resp.data
            db.session.refresh(xfer)
            assert (xfer.due_date, xfer.occurs_on) == (moved, moved)
            assert all(s.due_date == moved for s in _shadows(xfer.id))
            assert xfer.is_override is False

    def test_clearing_the_date_is_refused_with_the_reason(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            xfer = _one_time(auth_client, seed_user, savings, future[0])
            resp = _submit(auth_client, xfer, due_date="")
            assert resp.status_code == 400, resp.data
            assert b"can be moved but not cleared" in resp.data
            db.session.refresh(xfer)
            assert xfer.due_date == future[0].start_date

    def test_a_recurring_transfer_still_refuses_the_field(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _savings(seed_user)
            xfer = _recurring(seed_user, savings, _future(seed_periods_today)[0])
            before = xfer.due_date
            resp = auth_client.patch(f"/transfers/instance/{xfer.id}", data={
                "due_date": (before + timedelta(days=2)).isoformat(),
                "version_id": xfer.version_id,
            })
            assert resp.status_code == 400, resp.data
            assert b"comes from its recurring transfer" in resp.data
            db.session.refresh(xfer)
            assert xfer.due_date == before


class TestTheOccurrenceFollowsOnlyAMovedDate:
    """R-BAL94's writer fires on a date that MOVES, never on a re-posted one.

    Found by X-ci-1's adversarial review: the full-edit form posts
    ``due_date`` on every save, so the first cut re-keyed ``occurs_on`` onto
    the due date on a notes-only save.  A cleared cadence's survivor is where
    the two differ -- here a first-of-month rule, whose occurrence is the 1st
    and whose due day is its paycheck's start -- and a cadence re-added later
    that names the 1st adopts the row only while ``occurs_on`` still says so.
    ``seed_periods`` is a fixed schedule (2026-01-02, biweekly); the 2026-02-01
    occurrence is placed in the paycheck starting on or after it, 02-13, and
    is due on that payday.
    """

    def _first_of_month_survivor(self, seed_user, savings, seed_periods):
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="First of month",
            default_amount=Decimal("200.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        make_cadence_rule(template, MONTHLY_FIRST, starts_on=date(2026, 2, 1))
        xfer = generate_transfer_of(template, seed_periods[3])
        template.recurrence_rule = None
        db.session.commit()
        assert xfer.is_placed is True
        assert (xfer.occurs_on, xfer.due_date) == (date(2026, 2, 1), date(2026, 2, 13))
        return xfer

    def test_a_save_that_re_posts_the_date_re_keys_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):
        with app.app_context():
            xfer = self._first_of_month_survivor(seed_user, _savings(seed_user), seed_periods)
            resp = _submit(auth_client, xfer, notes="a note")
            assert resp.status_code == 200, resp.data
            db.session.refresh(xfer)
            assert xfer.notes == "a note"
            assert (xfer.occurs_on, xfer.due_date) == (date(2026, 2, 1), date(2026, 2, 13))

    def test_a_moved_date_carries_the_occurrence(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """THE CONTROL: the writer still fires when the date moves."""
        with app.app_context():
            xfer = self._first_of_month_survivor(seed_user, _savings(seed_user), seed_periods)
            resp = _submit(auth_client, xfer, due_date="2026-02-16")
            assert resp.status_code == 200, resp.data
            db.session.refresh(xfer)
            assert (xfer.occurs_on, xfer.due_date) == (date(2026, 2, 16), date(2026, 2, 16))


class TestADayASiblingAnswersIsRefused:
    """The occurrence index's rule, as a sentence (a cleared cadence's survivors)."""

    def test_moving_onto_a_siblings_day_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            first, second = _survivors(seed_user, savings, future[:2])
            assert second.occurs_on is not None
            resp = _submit(auth_client, first, due_date=second.occurs_on.isoformat())
            assert resp.status_code == 400, resp.data
            assert b"already due that day" in resp.data
            db.session.refresh(first)
            assert first.due_date != second.occurs_on

    def test_a_period_move_onto_a_siblings_day_is_refused_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The re-placed date is what is graded, not the submitted one."""
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            first, second = _survivors(seed_user, savings, future[:2])
            assert second.occurs_on == future[1].start_date
            assert first.due_date == future[0].start_date
            resp = _submit(auth_client, first, pay_period_id=str(future[1].id))
            assert resp.status_code == 400, resp.data
            assert b"already due that day" in resp.data
            db.session.refresh(first)
            assert first.pay_period_id == future[0].id

    def test_the_same_transfers_own_day_collides_with_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: an untouched save re-submits the day it already answers."""
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            first, _ = _survivors(seed_user, savings, future[:2])
            resp = _submit(auth_client, first)
            assert resp.status_code == 200, resp.data


class TestCarryForward:
    """BAL-493's third writer: the transfer move flags only a recurring one."""

    def test_a_one_time_transfer_is_carried_whole_and_re_placed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _savings(seed_user)
            future = _future(seed_periods_today)
            xfer = _one_time(auth_client, seed_user, savings, future[0])
            count = carry_forward_service.carry_forward_unpaid(
                future[0].id, future[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()
            assert count == 1
            db.session.refresh(xfer)
            assert xfer.pay_period_id == future[1].id
            assert xfer.is_override is False
            assert (xfer.due_date, xfer.occurs_on) == (
                future[1].start_date, future[1].start_date,
            )
            for shadow in _shadows(xfer.id):
                assert shadow.pay_period_id == future[1].id
                assert shadow.is_override is False
                assert shadow.due_date == future[1].start_date


class TestAOneTimePaymentIntoALoan:
    """R-BAL93's loan clause: the re-placed date IS the installment the guard grades.

    ``seed_periods`` is a fixed biweekly schedule from 2026-01-02; today is
    frozen at 2026-03-01 (inside it, ahead of the loan) so the card's period
    select offers the paychecks the case moves between.  The mortgage
    originates 2026-04-15: a one-time payment placed in the 05-08 paycheck
    (due 05-08) and moved to the 04-10 paycheck is re-placed on 04-10, which
    is at or before origination, so ruling **R-C** refuses the MOVE -- the
    installment moved with the date, as re-dating it by hand would.  Without
    the re-placing the due date would stay 05-08 and the move would land a
    payment in a paycheck that ends before its own installment.
    """

    @pytest.fixture(autouse=True)
    def _frozen(self, monkeypatch):
        freeze_today(monkeypatch, date(2026, 3, 1))

    def test_a_move_whose_re_placed_date_precedes_origination_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        with app.app_context():
            mortgage = create_loan_account(
                seed_user, db.session, name="Mortgage",
                principal=Decimal("200000.00"), rate=Decimal("0.05000"),
                term=360, origination_date=date(2026, 4, 15), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            db.session.commit()
            after = seed_periods[9]
            before = seed_periods[7]
            assert after.start_date == date(2026, 5, 8)
            assert before.start_date == date(2026, 4, 10)
            xfer = _one_time(
                auth_client, seed_user, mortgage, after, name="Extra principal",
            )
            assert xfer.due_date == date(2026, 5, 8)

            resp = _submit(auth_client, xfer, pay_period_id=str(before.id))

            assert resp.status_code == 400, resp.data
            assert b"before it originates" in resp.data
            assert b"2026-04-10" in resp.data
            db.session.refresh(xfer)
            assert xfer.pay_period_id == after.id
            assert (xfer.due_date, xfer.occurs_on) == (
                date(2026, 5, 8), date(2026, 5, 8),
            )


class TestTheDoorsKeepTheAdHocShape:
    """Until X-ci-3 an ad-hoc transfer keeps every act it had (0 exist on production)."""

    def test_an_ad_hoc_transfers_typed_figure_is_its_own_and_unflagged(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _savings(seed_user)
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=_future(seed_periods_today)[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("75.00")),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                    name="Ad hoc",
                ),
            )
            db.session.commit()
            resp = _submit(auth_client, xfer, amount="80.00")
            assert resp.status_code == 200, resp.data
            db.session.refresh(xfer)
            assert xfer.amount == Decimal("80.00")
            assert xfer.amount_source_id is None
            assert xfer.is_override is False
            assert xfer.occurs_on is None


def _code_lines_matching(pattern, path):
    """Return *path*'s CODE lines matching *pattern* -- strings and comments blanked.

    A ``code`` census filter over one file: a docstring naming the old
    spelling is prose, not a use of it.
    """
    source = path.read_text()
    by_line = {}
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.STRING, tokenize.COMMENT, tokenize.FSTRING_MIDDLE):
            continue
        by_line.setdefault(tok.start[0], []).append(tok.string)
    return [
        line for line, tokens in sorted(by_line.items())
        if pattern.search(" ".join(tokens))
    ]


def test_the_census_reads_three_accessor_bodies():
    """The step's marker, re-run here: every no-cadence site reads ``recurs``.

    Twelve code lines spelled ``recurrence_rule is (not) None`` on the base
    tree; the three left are the accessor bodies, one per definition kind
    (R-BAL97).  This pins the ANSWER, so a fourth spelling fails here.
    """
    pattern = re.compile(r"recurrence_rule is (not )?None")
    hits = {
        str(path): lines
        for path in pathlib.Path("app").rglob("*.py")
        if (lines := _code_lines_matching(pattern, path))
    }
    assert {path: len(lines) for path, lines in hits.items()} == {
        "app/models/paycheck_line.py": 1,
        "app/models/transaction_template.py": 1,
        "app/models/transfer_template.py": 1,
    }
