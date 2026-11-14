"""A carry's amount is derived from its source every time it is read.

Task **plan#3987** under plan step **balance:X-ax**, ruling **plan#3973**: a row's
amount is its OWN amount plus the unspent budget of every settled envelope a
carry-forward linked into it (``Transaction.rolled_into_id``, plan#3985), each
source's budget being its own resolved amount, so a chain composes::

    amount(row)  = own_amount(row) + sum(unspent(source) for source into row)
    unspent(src) = max(amount(src) - sum(src.purchases), 0)

Nothing in ``app/`` writes a link yet (plan#3989 does), so every case here
writes one by hand on a source settled through the app's own envelope close
(``transaction_service.settle_from_entries``).  With no link, a row answers its
own amount exactly, which the whole suite grades as unchanged figures; the first
class here states it once directly.

Every figure is worked by hand in the case that asserts it.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.models.transaction import Transaction
from app.services import balance_at, template_amount_service, transaction_service
from app.services.cash_ledger import (
    derived_amount_basis,
    own_amount,
    resolve_transaction_amount,
    unspent_budget,
)
from tests._test_helpers import (
    add_entry,
    capture_sql_statements,
    freeze_today,
    generate_row_of,
    make_expense_template,
)

#: A day inside ``seed_periods``' fourth paycheck (2026-02-13 .. 2026-02-26),
#: after every purchase the chain below records, so the grid draws the target's
#: column and no purchase is dated after the owner's today.
_RENDER_DAY = date(2026, 2, 20)


def _envelope(db_session, seed_user, amount, name="Spending Money"):
    """Return an every-paycheck envelope definition budgeting *amount*."""
    return make_expense_template(
        db_session, seed_user, amount,
        name=name, category_key="Groceries", is_envelope=True,
    )


def _settled(db_session, seed_user, template, period, *purchases):
    """Return *template*'s row in *period* holding *purchases*, closed by the app.

    The close is the envelope's own (``settle_from_entries``: it settles at what
    the purchases sum to), the act a carry-forward settles a source with.

    Args:
        db_session: The test session.
        seed_user: The owner fixture.
        template: The envelope definition.
        period: The row's pay period.
        purchases: ``(amount string, purchase day)`` pairs.

    Returns:
        The settled row.
    """
    row = generate_row_of(template, period)
    for amount, day in purchases:
        add_entry(db_session, seed_user, row, Decimal(amount), day)
    db_session.flush()
    db_session.expire(row, ["entries"])
    transaction_service.settle_from_entries(row)
    db_session.flush()
    return row


def _priced(db_session, row_id: int) -> "tuple[Decimal, Decimal]":
    """Return ``(amount, own amount)`` of row *row_id* off a basis built now.

    Committed and expired first, so the figure is the database's and the basis
    is a pass that began after every write the case made.
    """
    db_session.commit()
    db_session.expire_all()
    row = db_session.get(Transaction, row_id)
    basis = derived_amount_basis(row.user_id, row.scenario_id)
    return resolve_transaction_amount(row, basis), own_amount(row, basis)


class TestTheUnspentBudget:
    """The one producer, at its edges: the refund rule and the overspend floor."""

    def test_a_refund_carries_more_than_the_budget(self):
        """`$100.00` budget, one `-$50.00` refund: `100 - (-50) = $150.00` (R-IK)."""
        refund = SimpleNamespace(amount=Decimal("-50.00"))
        assert unspent_budget(Decimal("100.00"), [refund]) == Decimal("150.00")

    def test_an_overspent_envelope_carries_nothing(self):
        """`$100.00` budget, `$130.00` spent: `max(100 - 130, 0) = $0.00`."""
        spent = SimpleNamespace(amount=Decimal("130.00"))
        assert unspent_budget(Decimal("100.00"), [spent]) == Decimal("0")

    def test_an_envelope_with_no_purchase_carries_its_whole_budget(self):
        """`$100.00` budget, nothing spent: `$100.00`."""
        assert unspent_budget(Decimal("100.00"), []) == Decimal("100.00")


class TestARowWithNoCarryIsItsOwnAmount:
    """No link: the amount is the row's own, which every other suite grades."""

    def test_an_envelope_nothing_rolled_into(self, db, seed_user, seed_periods):
        """A `$500.00` envelope's row answers `$500.00` both ways."""
        template = _envelope(db.session, seed_user, "500.00")
        row = generate_row_of(template, seed_periods[1])
        assert _priced(db.session, row.id) == (
            Decimal("500.00"), Decimal("500.00"),
        )


class TestTheCarriedAmount:
    """A linked source's unspent budget sits on top of the target's own amount."""

    def test_a_carry_adds_the_sources_unspent_budget(
        self, db, seed_user, seed_periods,
    ):
        """`$500.00` budget, `$57.96` spent: the next row is `500 + 442.04 = $942.04`.

        Its own amount stays its definition's `$500.00`: the carry is on top.
        """
        template = _envelope(db.session, seed_user, "500.00")
        source = _settled(
            db.session, seed_user, template, seed_periods[0],
            ("57.96", date(2026, 1, 5)),
        )
        target = generate_row_of(template, seed_periods[1])
        source.rolled_into = target

        assert _priced(db.session, target.id) == (
            Decimal("942.04"), Decimal("500.00"),
        )

    def test_a_price_restated_before_the_source_moves_the_carry(
        self, db, seed_user, seed_periods,
    ):
        """Restated `$450.00` from the source's paycheck: `450 + (450 - 57.96) = $842.04`.

        The source's budget is its CURRENT resolved amount (R-BAL207 at the
        design): `$450.00 - $57.96 = $392.04` carried, on the target's own
        `$450.00`.
        """
        template = _envelope(db.session, seed_user, "500.00")
        source = _settled(
            db.session, seed_user, template, seed_periods[0],
            ("57.96", date(2026, 1, 5)),
        )
        target = generate_row_of(template, seed_periods[1])
        source.rolled_into = target
        template_amount_service.set_amount(
            template, Decimal("450.00"), effective_on=seed_periods[0].start_date,
        )

        assert _priced(db.session, target.id) == (
            Decimal("842.04"), Decimal("450.00"),
        )

    def test_a_late_purchase_on_the_source_moves_the_target(
        self, db, seed_user, seed_periods,
    ):
        """A `$30.00` purchase after the carry: `500 + (500 - 57.96 - 30) = $912.04`.

        The finding's own direction (N-249): a purchase recorded against the
        settled source later takes its money out of what rolled forward, so
        the two still sum to the budget they divided.
        """
        template = _envelope(db.session, seed_user, "500.00")
        source = _settled(
            db.session, seed_user, template, seed_periods[0],
            ("57.96", date(2026, 1, 5)),
        )
        target = generate_row_of(template, seed_periods[1])
        source.rolled_into = target
        assert _priced(db.session, target.id)[0] == Decimal("942.04")

        source = db.session.get(Transaction, source.id)
        add_entry(
            db.session, seed_user, source, Decimal("30.00"), date(2026, 1, 9),
        )

        assert _priced(db.session, target.id) == (
            Decimal("912.04"), Decimal("500.00"),
        )

    def test_two_sources_into_one_row_both_count(
        self, db, seed_user, seed_periods,
    ):
        """`$400.00` and `$300.00` unspent into one row: `500 + 400 + 300 = $1,200.00`."""
        template = _envelope(db.session, seed_user, "500.00")
        first = _settled(
            db.session, seed_user, template, seed_periods[0],
            ("100.00", date(2026, 1, 5)),
        )
        second = _settled(
            db.session, seed_user, template, seed_periods[1],
            ("200.00", date(2026, 1, 20)),
        )
        target = generate_row_of(template, seed_periods[2])
        first.rolled_into = target
        second.rolled_into = target

        assert _priced(db.session, target.id) == (
            Decimal("1200.00"), Decimal("500.00"),
        )

    def test_a_deleted_source_carries_nothing(self, db, seed_user, seed_periods):
        """The source deleted: its budget left the plan with it, so `$500.00`.

        A source that spent nothing, carrying its whole `$500.00`
        (`500 + 500 = $1,000.00` before the delete): the database refuses to
        hide a row holding a purchase (``refuse_hiding_a_row_holding_money``),
        so an unspent source is the only one a delete can reach.
        """
        template = _envelope(db.session, seed_user, "500.00")
        source = _settled(db.session, seed_user, template, seed_periods[0])
        target = generate_row_of(template, seed_periods[1])
        source.rolled_into = target
        assert _priced(db.session, target.id)[0] == Decimal("1000.00")

        db.session.get(Transaction, source.id).is_deleted = True

        assert _priced(db.session, target.id) == (
            Decimal("500.00"), Decimal("500.00"),
        )


def _chain(db_session, seed_user, periods, *, links, name="Spending Money"):
    """Build a `$100.00` envelope carried paycheck to paycheck; return its four rows.

    Three sources settle at `$30.00`, `$50.00` and `$20.00` spent, in the first
    three paychecks; the fourth's row is Projected.  *links* says how many of
    the three carries are written, counted back from the last: ``1`` links only
    the third source into the fourth row, ``3`` the whole chain.  The rows and
    their statuses are the same either way, so a difference between the two is
    the links' alone.

    Hand-worked, all three linked::

        first:  own 100           spent 30, unspent  70
        second: own 100 +  70 = 170, spent 50, unspent 120
        third:  own 100 + 120 = 220, spent 20, unspent 200
        fourth: own 100 + 200 = 300

    Only the last linked: the third's budget is its own `$100.00`, unspent
    `$80.00`, and the fourth is `100 + 80 = $180.00`.
    """
    template = _envelope(db_session, seed_user, "100.00", name)
    first = _settled(
        db_session, seed_user, template, periods[0], ("30.00", date(2026, 1, 5)),
    )
    second = _settled(
        db_session, seed_user, template, periods[1], ("50.00", date(2026, 1, 20)),
    )
    third = _settled(
        db_session, seed_user, template, periods[2], ("20.00", date(2026, 2, 2)),
    )
    fourth = generate_row_of(template, periods[3])
    _link_chain((first, second, third, fourth), links)
    return first, second, third, fourth


def _link_chain(rows, links):
    """Link the last *links* carries of *rows* (each into the row after it)."""
    for source, target in list(zip(rows, rows[1:]))[-links:]:
        source.rolled_into = target


class TestAChainComposes:
    """A row carried into, settled and carried on passes forward what it kept."""

    def test_three_carries_reach_the_fourth_row(self, db, seed_user, seed_periods):
        """The whole chain linked: `$300.00` (worked in :func:`_chain`)."""
        *_, fourth = _chain(db.session, seed_user, seed_periods, links=3)
        assert _priced(db.session, fourth.id) == (
            Decimal("300.00"), Decimal("100.00"),
        )

    def test_the_last_carry_alone(self, db, seed_user, seed_periods):
        """Only the last carry linked: `$180.00` (worked in :func:`_chain`)."""
        *_, fourth = _chain(db.session, seed_user, seed_periods, links=1)
        assert _priced(db.session, fourth.id) == (
            Decimal("180.00"), Decimal("100.00"),
        )


class TestTheChooserOffersTheOwnAmount:
    """The recurrence chooser's "your amount" is the figure the choice replaces."""

    def test_your_amount_leaves_the_carry_out(self, db, seed_user, seed_periods):
        """A target holding `$442.04` carried: "your amount" is its own `$500.00`.

        The chooser asks whether to keep the row's figure or take the
        definition's; the carried money stays on top either way (ruling
        plan#3978), so offering the `$942.04` total would offer to drop it.
        """
        # Pylint: ``import-outside-toplevel`` -- a route module's import pulls
        # the blueprint stack, which only this case needs.
        # pylint: disable=import-outside-toplevel
        from app.routes.templates.crud import _TXN_TEMPLATE_KIND
        template = _envelope(db.session, seed_user, "500.00")
        source = _settled(
            db.session, seed_user, template, seed_periods[0],
            ("57.96", date(2026, 1, 5)),
        )
        target = generate_row_of(template, seed_periods[1])
        source.rolled_into = target
        db.session.commit()

        assert _TXN_TEMPLATE_KIND.resolve_amount(target) == Decimal("500.00")


def _three_into_the_fourth(db_session, seed_user, periods, *, links):
    """Build three settled `$100.00` envelopes and the fourth paycheck's row; link *links* into it.

    The same rows and statuses as :func:`_chain`, but each carry lands
    DIRECTLY in the fourth row, so every link is one level deep: ``1`` links
    only the third envelope, ``3`` all three.  Hand-worked::

        one link:    100 + (100 - 20)                          = 180
        three links: 100 + (100 - 30) + (100 - 50) + (100 - 20) = 300
    """
    template = _envelope(db_session, seed_user, "100.00")
    sources = (
        _settled(db_session, seed_user, template, periods[0],
                 ("30.00", date(2026, 1, 5))),
        _settled(db_session, seed_user, template, periods[1],
                 ("50.00", date(2026, 1, 20))),
        _settled(db_session, seed_user, template, periods[2],
                 ("20.00", date(2026, 2, 2))),
    )
    fourth = generate_row_of(template, periods[3])
    for source in sources[-links:]:
        source.rolled_into = fourth
    return (*sources, fourth)


def _link_all_into_last(db_session, rows):
    """Re-read *rows* and link every one but the last into the last."""
    *sources, last = [db_session.get(Transaction, row.id) for row in rows]
    for source in sources:
        source.rolled_into = last


class TestTheCarriesLoadPerLevel:
    """More carries one level deep, the same statements: never a query per row.

    The carried-from chain is loaded with the row's other pricing inputs
    (ruling plan#4052), a statement per LEVEL of the deepest chain loaded.  So
    three carries into one row cost what one does, and a second chain as deep
    as the first costs nothing more.
    """

    def test_the_balance_fold(self, app, db, seed_user, seed_periods, monkeypatch):
        """One carry, then three: the end balance falls `300 - 180 = $120.00`, same statements.

        The rows and their statuses are identical in both reads; only the two
        extra links differ, so the fourth row's reservation is the only figure
        that moves (worked in :func:`_three_into_the_fourth`).
        """
        freeze_today(monkeypatch, _RENDER_DAY)
        rows = _three_into_the_fourth(db.session, seed_user, seed_periods, links=1)
        user_id = seed_user["user"].id
        account_id = seed_user["account"].id
        end = seed_periods[-1].start_date

        def _balance():
            ctx = balance_at.BalanceContext.build(user_id, as_of=_RENDER_DAY)
            account = db.session.get(type(seed_user["account"]), account_id)
            return balance_at.cash_balance_at(account, ctx, end)

        db.session.commit()

        with app.app_context():
            _balance()  # warm the process caches a first read fills
            db.session.expire_all()
            one, one_statements = capture_sql_statements(_balance)

            _link_all_into_last(db.session, rows)
            db.session.commit()
            db.session.expire_all()
            three, three_statements = capture_sql_statements(_balance)

        assert one - three == Decimal("120.00")
        assert len(three_statements) == len(one_statements), (
            [text for text, _ in three_statements]
        )

    def test_the_grid(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, app, auth_client, db, seed_user, seed_periods, monkeypatch,
    ):
        """One carry, then three: the grid shows `$180.00`, then `$300.00`, same statements.

        Pylint: ``too-many-arguments``, ``too-many-positional-arguments`` (7/5)
        -- pytest fixtures are the parameters, each one this case reads.
        """
        freeze_today(monkeypatch, _RENDER_DAY)
        rows = _three_into_the_fourth(db.session, seed_user, seed_periods, links=1)
        db.session.commit()

        with app.app_context():
            auth_client.get("/grid?periods=3")  # warm the process caches
            db.session.expire_all()
            one, one_statements = capture_sql_statements(
                lambda: auth_client.get("/grid?periods=3"),
            )

            _link_all_into_last(db.session, rows)
            db.session.commit()
            db.session.expire_all()
            three, three_statements = capture_sql_statements(
                lambda: auth_client.get("/grid?periods=3"),
            )

        assert one.status_code == three.status_code == 200
        assert b"$180.00" in one.data
        assert b"$300.00" in three.data
        assert len(three_statements) == len(one_statements), (
            [text for text, _ in three_statements]
        )

    def test_a_second_chain_as_deep_costs_nothing_more(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, app, auth_client, db, seed_user, seed_periods, monkeypatch,
    ):
        """A second three-carry chain adds no statement to the grid: per level, not per row.

        One definition carried paycheck to paycheck (:func:`_chain`), read;
        then a second definition's chain as deep, read again: its rows and
        carries cost no statement, and its fourth row draws `$300.00` beside
        the first's.  The control varies the ROWS: the grid prices every row
        it loads, so a load per row would show as the second chain's twelve
        rows, where varying only the links would not (each row is asked once
        either way, which a probe measured on 2026-11-13).

        Pylint: ``too-many-arguments``, ``too-many-positional-arguments`` (7/5)
        -- pytest fixtures are the parameters, each one this case reads.
        """
        freeze_today(monkeypatch, _RENDER_DAY)
        _chain(db.session, seed_user, seed_periods, links=3)
        db.session.commit()

        with app.app_context():
            auth_client.get("/grid?periods=3")  # warm the process caches
            db.session.expire_all()
            one, one_statements = capture_sql_statements(
                lambda: auth_client.get("/grid?periods=3"),
            )

            _chain(
                db.session, seed_user, seed_periods, links=3, name="Kids' Money",
            )
            db.session.commit()
            db.session.expire_all()
            two, two_statements = capture_sql_statements(
                lambda: auth_client.get("/grid?periods=3"),
            )

        # One cell prints its figure in several places; the second chain's
        # cell prints it as many times again.
        assert one.data.count(b"$300.00") > 0
        assert two.data.count(b"$300.00") == 2 * one.data.count(b"$300.00")
        assert len(two_statements) == len(one_statements), (
            [text for text, _ in two_statements]
        )
