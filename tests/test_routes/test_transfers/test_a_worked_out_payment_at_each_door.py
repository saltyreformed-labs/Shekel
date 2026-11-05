"""Every door that can price a card's worked-out payment holds a read pass.

Plan step **credit_card:CC-6**, its task plan#2859.  A worked-out row stores no
figure, so a basis held by no read pass refuses it (``AmountUnresolvable``), and
until this task every door here did.  Each case drives the door a browser drives,
on a row nobody has typed over.

**The money in every case, unless a case says otherwise.**  $500.00 was charged to
the Visa on Jan 5 and recorded.  The everything-on-the-card payment repeats each
payday, so Jan 16's row asks what the card owed at the end of Jan 15, $500.00, and
Jan 30's asks 500 - 500 = $0.00.  **A case's clock is its fixture's**: Jan 10, 2026
for the transfer doors, ahead of those rows, Feb 2 where a statement or a paycheck
has to have passed, and the wall clock where a row overdue prices the same.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import time_machine

from app import ref_cache
from app.enums import (
    CardPaymentModeEnum,
    MovementFigureSourceEnum,
    SettledDayBasisEnum,
    StatusEnum,
)
from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.routes._transfer_creation_helpers import TRANSFER_TEMPLATE_KIND
from app.services import (
    carry_forward_service,
    reconcile_service,
    statement_match,
    transfer_recurrence,
    transfer_service,
)
from app.services.balance_at import BalanceContext
from app.services.balance_at._card_payment_doors import card_payment_price
from app.services.cash_ledger import AmountRule, resolve_transfer_amount, transfer_amount_rule
from app.services.generation_schedule import GenerationSchedule
from app.services.settle_day import SettleDay
from app.services.spending_report_service import (
    SpendingWindow,
    compute_spending_report,
)
from app.services.stated_figure import StatedFigure
from app.services.statement_match import RowKind
from tests._test_helpers import on_both_sides, outstanding_set_of
from tests.test_routes._statement_forms import reconcile_form_fields
from tests.test_routes.test_statement_reconcile import _open, _post, _row_tokens
from tests.test_services.test_card_payment_price import (
    _card,
    _row,
    _settle,
    _spend,
    _spent,
    _works_out,
)
from tests.test_services.test_reconcile_service._helpers import _reconciled
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    an_import,
)

_JAN_10 = datetime(2026, 1, 10, 17, 0, tzinfo=timezone.utc)
_JAN_16 = date(2026, 1, 16)
_JAN_28 = datetime(2026, 1, 28, 17, 0, tzinfo=timezone.utc)
_JAN_30 = date(2026, 1, 30)
_FEB_2 = datetime(2026, 2, 2, 17, 0, tzinfo=timezone.utc)


@pytest.fixture(name="read_on_jan_10")
def _read_on_jan_10():
    """Hold both clocks at Jan 10, 2026, ahead of every row a case reads."""
    with time_machine.travel(_JAN_10, tick=False):
        yield


@pytest.fixture(name="read_on_feb_2")
def _read_on_feb_2():
    """Hold both clocks at Feb 2, 2026, after a statement a case trues up to."""
    with time_machine.travel(_FEB_2, tick=False):
        yield


@pytest.fixture(name="read_on_jan_28")
def _read_on_jan_28():
    """Hold both clocks at Jan 28, 2026, ahead of Jan 30's row."""
    with time_machine.travel(_JAN_28, tick=False):
        yield


@pytest.fixture(name="template")
def _template(seed_user, seed_periods):
    """$500.00 on the card, and its everything-on-the-card payment each payday."""
    card = _card(seed_user)
    _spent(seed_user, card, seed_periods[0], "500.00", date(2026, 1, 5))
    return _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)


def _price(template, due):
    """What a fresh pass prices the row dated *due* at."""
    db.session.expire_all()
    return resolve_transfer_amount(
        _row(template, due), BalanceContext.build(template.user_id).amounts(),
    )


def _true_up(client, account_id, balance, observed_on):
    """State *balance* as *account_id*'s on *observed_on*, through the anchor door."""
    return client.patch(
        f"/accounts/{account_id}/true-up",
        data={"anchor_balance": balance, "observed_on": observed_on},
        headers={"HX-Request": "true"},
    )


def _body(response):
    """Return a 200's text, the status graded with the body in its message."""
    text = response.get_data(as_text=True)
    assert response.status_code == 200, text
    return text


@pytest.mark.usefixtures("read_on_jan_10")
class TestTheTransferDoors:
    """The six doors plan#2890's trace measured raising, each on an untyped row."""

    def test_the_cell_draws_the_price(self, auth_client, template):
        """GET /transfers/cell answers the worked-out $500.00."""
        row = _row(template, _JAN_16)

        assert "500.00" in _body(auth_client.get(f"/transfers/cell/{row.id}"))
        assert transfer_amount_rule(row) is AmountRule.CARD_PAYMENT

    def test_the_quick_edit_prefills_the_price(self, auth_client, template):
        """GET /transfers/quick-edit puts $500.00 in the money box a save posts."""
        row = _row(template, _JAN_16)

        html = _body(auth_client.get(f"/transfers/quick-edit/{row.id}"))

        assert 'value="500.00"' in html

    def test_the_full_edit_prefills_the_price(self, auth_client, template):
        """GET the full-edit popover puts $500.00 in its money box."""
        row = _row(template, _JAN_16)

        html = _body(auth_client.get(f"/transfers/{row.id}/full-edit"))

        assert 'value="500.00"' in html

    def test_typing_over_it_is_a_partial_payment(self, auth_client, template):
        """PATCH $300.00: the row owns it, and Jan 30 asks 500 - 300 = $200.00."""
        row = _row(template, _JAN_16)

        html = _body(auth_client.patch(
            f"/transfers/instance/{row.id}",
            data={
                "amount": "300.00",
                # What the quick edit renders beside its box, as a browser posts.
                "amount_as_rendered": "500.00",
                "version_id": str(row.version_id),
            },
        ))

        assert "300.00" in html
        assert _price(template, _JAN_16) == Decimal("300.00")
        assert _price(template, _JAN_30) == Decimal("200.00")
        assert transfer_amount_rule(_row(template, _JAN_16)) is not AmountRule.CARD_PAYMENT

    def test_cancel_answers_with_a_cell(self, auth_client, template):
        """It committed, then raised on the redraw: both now land, and Jan 30 owes it.

        A cancelled row pays nothing, so Jan 30 asks the whole $500.00.
        """
        row = _row(template, _JAN_16)

        _body(auth_client.post(f"/transfers/instance/{row.id}/cancel"))

        db.session.expire_all()
        assert _row(template, _JAN_16).status_id == ref_cache.status_id(
            StatusEnum.CANCELLED,
        )
        assert _price(template, _JAN_30) == Decimal("500.00")

    def test_mark_paid_books_the_price(self, auth_client, template):
        """POST mark-done books $500.00 as the app's own figure, not a typed one."""
        row = _row(template, _JAN_16)

        _body(auth_client.post(f"/transfers/instance/{row.id}/mark-done"))

        db.session.expire_all()
        paid = _row(template, _JAN_16)
        assert paid.settled_figure == Decimal("500.00")
        assert paid.settled_figure_source_id != ref_cache.movement_figure_source_id(
            MovementFigureSourceEnum.TYPED,
        )
        assert _price(template, _JAN_30) == Decimal("0.00")

    def test_a_grid_side_draws_the_price_through_the_row_helper(
        self, auth_client, seed_user, template,
    ):
        """The side's cell is ``fragment_amounts``' answer, and it reads $500.00."""
        row = _row(template, _JAN_16)
        checking_id = seed_user["account"].id

        html = _body(auth_client.patch(
            f"/transfers/instance/{row.id}",
            data={
                "name": "Pay the Visa now",
                "version_id": str(row.version_id),
                "leg_account_id": str(checking_id),
            },
        ))

        assert f'data-leg-account-id="{checking_id}"' in html
        assert "$500" in html or "500.00" in html


@pytest.mark.usefixtures("read_on_jan_10")
class TestTheChooser:
    """The conflict chooser's one-argument amount, its interface kept."""

    def test_the_kind_prices_the_row(self, template):
        """It opens a pass per row and answers the worked-out $500.00."""
        assert TRANSFER_TEMPLATE_KIND.resolve_amount(
            _row(template, _JAN_16),
        ) == Decimal("500.00")


class TestTheReconcilePanel:
    """The panel offers at the pass's price, and a tick books from its own.

    **The money here adds $200.00 charged Jan 20** where a case needs a later row
    to owe something: Jan 16 asks $500.00, Jan 30 asks 700 - 500 = $200.00.
    """

    def test_it_offers_the_price(self, seed_user, template):
        """Checking's panel at Jan 31 offers Jan 16's leg at the worked-out $500.00."""
        row = _row(template, _JAN_16)

        groups = outstanding_set_of(
            _reconciled(seed_user, observed_on=date(2026, 1, 31)),
        ).groups

        block = next(
            group for group in groups
            if group.key == (row.id, seed_user["account"].id)
        )
        assert block.settle.amount == Decimal("500.00")

    def test_a_leg_worth_nothing_is_not_offered(self, seed_user, template):
        """Jan 2 and Jan 30 ask $0.00, so only Jan 16's leg is on the panel.

        Paid, it leaves nothing, so the true-up's prompt has nothing to ask.
        """
        row = _row(template, _JAN_16)
        statement = _reconciled(seed_user, observed_on=date(2026, 2, 1))

        assert [group.key for group in outstanding_set_of(statement).groups] == [
            (row.id, seed_user["account"].id),
        ]

        _settle(row, date(2026, 1, 16))
        db.session.expire_all()
        assert outstanding_set_of(statement).is_empty

    def test_a_tick_with_no_figure_reads_what_the_first_booked(self, seed_user, template):
        """$300.00 stated on Jan 16 and Jan 30's box cleared: Jan 30 books $400.00.

        $200.00 more on Jan 20, so Jan 16 asks $500.00 and Jan 30 asks $200.00.  A
        pass handed down the save would answer Jan 30 from the fold as it stood
        before the first tick, $200.00.  Each settle's own pass reads
        700 - 300 = $400.00.  Jan 16 settles first, the arm walking them by date.
        """
        _spent(
            seed_user, _row(template, _JAN_16).to_account,
            db.session.get(PayPeriod, _row(template, _JAN_16).pay_period_id),
            "200.00", date(2026, 1, 20),
        )
        first = _row(template, _JAN_16)
        second = _row(template, _JAN_30)

        reconcile_service.record_reconciliation(
            reconcile_service.ReconcileSubmission(
                statement=_reconciled(seed_user, observed_on=date(2026, 2, 1)),
                entry_ids=set(),
                transaction_ids=set(),
                corrections={},
                transfer_ids={first.id, second.id},
                transfer_corrections={first.id: Decimal("300.00")},
            ),
        )
        db.session.commit()

        db.session.expire_all()
        assert _row(template, _JAN_16).settled_figure == Decimal("300.00")
        assert _row(template, _JAN_30).settled_figure == Decimal("400.00")

    def test_a_tick_books_the_figure_its_box_showed(
        self, read_on_feb_2, auth_client, seed_user, template,
    ):
        """A browser posts Jan 30's box as drawn: $200.00 books, and Feb 13 asks the rest.

        $700.00 charged, $300.00 typed on Jan 16 and $200.00 left as shown, so
        Feb 13's row asks 700 - 300 - 200 = $200.00.  The box's figure is recorded
        as typed, which nobody did: finding plan#2952.
        """
        _spent(
            seed_user, _row(template, _JAN_16).to_account,
            db.session.get(PayPeriod, _row(template, _JAN_16).pay_period_id),
            "200.00", date(2026, 1, 20),
        )
        checking_id = seed_user["account"].id
        first_id = _row(template, _JAN_16).id
        second_id = _row(template, _JAN_30).id
        _body(_true_up(auth_client, checking_id, "1000.00", "2026-02-01"))

        _body(auth_client.post(
            f"/accounts/{checking_id}/reconcile",
            data={
                "transfer_ids": [str(first_id), str(second_id)],
                f"transfer_amount-{first_id}": "300.00",
                f"transfer_amount-{second_id}": "200.00",
            },
        ))

        db.session.expire_all()
        assert _row(template, _JAN_16).settled_figure == Decimal("300.00")
        assert _row(template, _JAN_30).settled_figure == Decimal("200.00")
        assert _price(template, date(2026, 2, 13)) == Decimal("200.00")

    def test_the_panels_answer_reads_the_save(
        self, read_on_feb_2, auth_client, seed_user, template,
    ):
        """After $300.00 on Jan 16's tick, the POST's own panel offers Jan 30 at $200.00.

        Its answer is drawn off a pass opened after the commit; a pass that had
        priced the panel before the save would offer $0.00.  Both clocks at Feb 2,
        after the true-up, because a statement cannot be dated ahead of today.
        """
        checking_id = seed_user["account"].id
        first_id = _row(template, _JAN_16).id
        _body(_true_up(auth_client, checking_id, "1000.00", "2026-02-01"))

        html = _body(auth_client.post(
            f"/accounts/{checking_id}/reconcile",
            data={
                "transfer_ids": [str(first_id)],
                f"transfer_amount-{first_id}": "300.00",
            },
        ))

        assert 'value="200.00"' in html, html


@pytest.mark.usefixtures("read_on_jan_10")
class TestCarryForward:
    """The preview and the move, each on an untyped row."""

    def test_the_preview_names_the_price(self, seed_user, seed_periods, template):
        """The modal's transfer plan is the worked-out $500.00."""
        row = _row(template, _JAN_16)
        ctx = BalanceContext.build(seed_user["user"].id)

        preview = carry_forward_service.preview_carry_forward(
            row.pay_period_id, seed_periods[2].id, ctx.scenario_id, balance_ctx=ctx,
        )

        plan = next(
            plan for plan in preview.plans if plan.item.transfer.id == row.id
        )
        assert plan.budget == Decimal("500.00")

    def test_a_row_of_a_carried_scenario_refuses(self, seed_user, seed_periods, template):
        """The deriver answers for its pass's scenario, and a carried one is another.

        The amount model's check passes such a row, because the basis names the
        scenario it carries within, so the refusal is the deriver's own.
        """
        what_if = Scenario(
            user_id=seed_user["user"].id, name="What if", is_baseline=False,
        )
        db.session.add(what_if)
        db.session.flush()
        ctx = BalanceContext.build(seed_user["user"].id)
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_pass(ctx), what_if.id,
        )
        db.session.commit()
        foreign = db.session.query(Transfer).filter_by(
            transfer_template_id=template.id, scenario_id=what_if.id,
            due_date=_JAN_16,
        ).one()

        with pytest.raises(AmountUnresolvable, match="read pass folds"):
            card_payment_price(foreign, BalanceContext.build(seed_user["user"].id))


class TestCarryForwardThroughTheBrowser:
    """The modal and the press, each on an untyped row, at Feb 2's paycheck."""

    def test_the_modal_names_the_price_and_the_press_moves_the_row(
        self, read_on_feb_2, auth_client, seed_periods, template,
    ):
        """Jan 16's paycheck carries into Jan 30's: the modal says $500.00, then it moves."""
        row_id = _row(template, _JAN_16).id
        source_id = seed_periods[1].id

        modal = _body(auth_client.get(f"/pay-periods/{source_id}/carry-forward-preview"))
        assert "$500.00" in modal, modal

        _body(auth_client.post(f"/pay-periods/{source_id}/carry-forward"))

        db.session.expire_all()
        assert db.session.get(Transfer, row_id).pay_period_id == seed_periods[2].id


class TestTheScreensThatReadThePass:
    """The statement screen and the Spending tab, drawn with a worked-out row on them."""

    def test_the_statement_screen_applies_the_match_it_draws(
        self, read_on_feb_2, auth_client, seed_user, template,
    ):
        """A -$500.00 line, its card opened, the leg ticked, Apply: it books $500.00.

        The page's own bytes, so a scope that could not price the leg offers no
        row to tick.
        """
        row_id = _row(template, _JAN_16).id
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-500.00",
            posted_on=date(2026, 1, 20),
        )
        db.session.commit()
        page = _open(auth_client, seed_user, line.id)
        fields = reconcile_form_fields(page)
        tokens = _row_tokens(page, line.id)
        assert len(tokens) == 1, tokens
        ticked = (
            [] if any(name == f"rows-{line.id}" for name, _ in fields)
            else [(f"rows-{line.id}", tokens[0])]
        )

        applied = _post(
            auth_client, seed_user, fields + ticked + [("ok", str(line.id))], page,
        )

        assert applied.status_code == 200, applied.get_data(as_text=True)
        db.session.expire_all()
        assert db.session.get(Transfer, row_id).settled_figure == Decimal("500.00")

    def test_the_spending_tab_draws_the_surprise(
        self, auth_client, template,
    ):
        """Paid $450.00 against $500.00: the tab names both, off the route's pass."""
        row = _row(template, _JAN_16)
        transfer_service.settle_transfer(
            row.id, row.user_id,
            submitted=StatedFigure(
                amount=Decimal("450.00"), source=MovementFigureSourceEnum.TYPED,
            ),
            side_days=on_both_sides(
                row.from_account_id, row.to_account_id,
                SettleDay(day=_JAN_16, basis=SettledDayBasisEnum.ENTERED),
            ),
        )
        db.session.commit()

        html = _body(auth_client.get(
            "/analytics/spending?month=1&year=2026", headers={"HX-Request": "true"},
        ))

        assert "$450" in html, html
        assert "$500" in html, html


class TestTheSpendingReport:
    """A settled payment's estimate is the card's price, read through the pass."""

    def test_a_paid_payment_is_a_surprise_against_its_price(
        self, seed_user, seed_periods, template,
    ):
        """Paid $450.00 against its $500.00: the estimate is the row's own price."""
        row = _row(template, _JAN_16)
        transfer_service.settle_transfer(
            row.id, row.user_id,
            submitted=StatedFigure(
                amount=Decimal("450.00"), source=MovementFigureSourceEnum.TYPED,
            ),
            side_days=on_both_sides(
                row.from_account_id, row.to_account_id,
                SettleDay(day=date(2026, 1, 16), basis=SettledDayBasisEnum.ENTERED),
            ),
        )
        db.session.commit()

        report = compute_spending_report(
            BalanceContext.build(seed_user["user"].id),
            SpendingWindow(window_type="month", month=1, year=2026),
            user_settings=None,
        )

        assert [
            (surprise.estimated, surprise.actual)
            for surprise in report.surprises.rows
        ] == [(Decimal("500.00"), Decimal("450.00"))]


class TestTheStatementReview:
    """The review offers at the pass's price, and an act books from the settle's own."""

    def test_it_offers_the_price(self, seed_user, template):
        """Checking's review offers Jan 16's leg at -$500.00 of cash."""
        row = _row(template, _JAN_16)

        candidate = next(
            candidate for candidate in a_scope(seed_user).candidates.rows
            if (candidate.kind, candidate.row_id) == (RowKind.LEG, row.id)
        )

        assert candidate.cash_amount == Decimal("-500.00")

    def test_a_match_books_the_price(self, seed_user, template):
        """A -$500.00 bank line matched to it settles it at the worked-out $500.00."""
        row = _row(template, _JAN_16)
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-500.00", posted_on=date(2026, 1, 20),
        )
        scope = a_scope(seed_user)

        statement_match.accept_match(
            a_submission(scope, lines=[line], transfers=[row]), scope,
        )
        db.session.commit()

        db.session.expire_all()
        assert _row(template, _JAN_16).settled_figure == Decimal("500.00")

    def test_an_act_that_moved_a_later_payment_refuses_the_later_match(
        self, read_on_jan_28, seed_user, seed_periods,
    ):
        """A stale price is refused by name and never booked, on the card's statement.

        A $500.00 bill dated Jan 20 is unmarked on the card, so Jan 30's row asks
        $500.00.  The first match states the bank's -$510.00 for the bill, so the
        row now asks $510.00.  The pass still says $500.00, the same as the bank's
        second line, so the re-read passes; the settle's own pass books $510.00,
        and what the act left no longer explains that line.  The act rolls back:
        the row stays unpaid and the first match stands.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[1], "500.00", date(2026, 1, 20))
        bill = db.session.query(Transaction).filter_by(account_id=card.id).one()
        template = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
        payment = _row(template, _JAN_30)
        statement = an_import(seed_user, account=card)
        lines = [
            a_bank_line(
                seed_user, statement, amount=amount, posted_on=posted_on,
                sequence_in_group=index,
            )
            for index, (amount, posted_on) in enumerate((
                ("-510.00", date(2026, 1, 27)), ("500.00", date(2026, 1, 28)),
            ))
        ]
        review = a_scope(seed_user, card)
        assert next(
            candidate for candidate in review.candidates.rows
            if (candidate.kind, candidate.row_id) == (RowKind.LEG, payment.id)
        ).cash_amount == Decimal("500.00")
        matches = (
            a_submission(
                review, lines=[lines[0]], transactions=[bill], residual="-10.00",
                attributed=(RowKind.TRANSACTION, bill),
            ),
            a_submission(review, lines=[lines[1]], transfers=[payment]),
        )

        outcome = statement_match.apply_reviewed(
            statement_match.ReviewedBatch(
                consent=statement_match.Consent.TICKED, matches=matches,
                creations=(), incomes=(), skips=(),
            ),
            a_scope(seed_user, card),
        )
        db.session.commit()

        db.session.expire_all()
        assert [item.line_ids for item in outcome.applied] == [(lines[0].id,)], outcome
        assert [item.line_ids for item in outcome.refused] == [(lines[1].id,)]
        assert "moved one of its own rows" in outcome.refused[0].reason
        assert _row(template, _JAN_30).settled_figure is None
        assert _price(template, _JAN_30) == Decimal("510.00")
