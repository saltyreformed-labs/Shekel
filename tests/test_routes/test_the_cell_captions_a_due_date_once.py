"""The grid cell's due-date caption: one rule, two surfaces, no query.

``grid/_transaction_cell.html`` prints ``Due: m/d`` when a row's ``due_date``
is not the PAYDAY of the paycheck it is filed in.  Two surfaces draw that
cell -- the desktop grid's row macro on a page render, and
``routes/_render_helpers.render_transaction_cell`` on every HTMX swap -- and
until pay-calendar plan step **C4-a-1** both answered the question the same
wrong way: the template read ``t.pay_period.start_date``, a lazy relationship
load issued from inside the render.

**Nothing graded any of it**, which is why this file exists.  ``grep -rn "Due:"
tests/`` came back empty the day C4-a-1 changed the line, so the whole
11,250-test suite was green either way.

**And the first fix was a bare context variable, which an adversarial review
measured as no fix at all**: Jinja's default ``Undefined`` compares unequal to
a date WITHOUT raising, so a surface that forgot to publish the payday
captioned every dated row silently.  The decision is a per-transaction MAP the
partial INDEXES for exactly that reason, which is this template's standing rule
for ``budgets``, and
:meth:`TestTheCaptionMapIsREQUIRED.test_a_render_without_the_map_FAILS` is what
holds it -- the property, rather than a paragraph claiming it.

Four properties:

* a row due AWAY from its payday is captioned, on BOTH surfaces;
* a row due ON its payday is NOT, on both -- the assertion that fails when a
  surface stops publishing the map;
* a render that publishes no map RAISES rather than captioning everything;
* and the page's ``budget.pay_periods`` query count does not grow when the same
  rows are spread across more paychecks, which is what keeps the template off
  the database.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests._test_helpers import (
    add_txn,
    capture_sql_statements,
    freeze_today,
)

_ONE_DAY = timedelta(days=1)

#: The day every render below is taken on -- inside ``seed_periods``' first
#: paycheck (2026-01-02 .. 2026-01-15), so the grid draws that column.
_RENDER_DAY = date(2026, 1, 5)


def _pay_period_selects(statements):
    """Return the captured statements that SELECT from ``budget.pay_periods``.

    Args:
        statements: ``(statement, parameters)`` pairs from
            :func:`~tests._test_helpers.capture_sql_statements`.

    Returns:
        The subset whose text reads that table.  A joined load counts: the
        point is how many times the render asks the table anything, not how it
        spells the ask.
    """
    return [
        text for text, _ in statements
        if "budget.pay_periods" in text
        and text.lstrip().upper().startswith("SELECT")
    ]


class TestTheCaptionShowsOnlyWhenTheDueDateIsNotThePayday:
    """The rule itself, on the page and on the fragment."""

    def test_the_page_captions_a_row_due_AWAY_from_its_payday(
        self, app, auth_client, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument
        """Payday 2026-01-02, due 2026-01-08: the page prints ``Due: 1/8``."""
        freeze_today(monkeypatch, _RENDER_DAY)
        add_txn(
            db.session, seed_user, seed_periods[0], "away from payday",
            "25.00", due_date=date(2026, 1, 8),
        )
        db.session.commit()

        with app.app_context():
            response = auth_client.get("/grid?periods=3")

        assert response.status_code == 200
        assert b"Due: 1/8" in response.data

    def test_the_page_captions_NOTHING_for_a_row_due_ON_its_payday(
        self, app, auth_client, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument
        """Payday 2026-01-02, due 2026-01-02: no caption at all.

        **This is the assertion that fails when the DECISION goes wrong** --
        when a surface publishes a map built against the wrong paydays, or
        against a row set that does not include this row.  A test asserting
        only the POSITIVE case above would pass in either state.  The map being
        absent ENTIRELY is caught one class down, where it raises.
        """
        freeze_today(monkeypatch, _RENDER_DAY)
        add_txn(
            db.session, seed_user, seed_periods[0], "on the payday",
            "25.00", due_date=seed_periods[0].start_date,
        )
        db.session.commit()

        with app.app_context():
            response = auth_client.get("/grid?periods=3")

        assert response.status_code == 200
        assert b"Due:" not in response.data

    def test_the_fragment_captions_a_row_due_AWAY_from_its_payday(
        self, app, auth_client, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument
        """The HTMX cell swap answers the page's question the same way.

        The two surfaces resolve the payday differently by design -- the page
        macro is already drawing that paycheck's column, and the fragment
        resolves it through the owner's calendar -- so what they must agree on
        is the ANSWER, which is what these two fragment tests grade.
        """
        freeze_today(monkeypatch, _RENDER_DAY)
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "away from payday",
            "25.00", due_date=date(2026, 1, 8),
        )
        db.session.commit()

        with app.app_context():
            response = auth_client.get(f"/transactions/{txn.id}/cell")

        assert response.status_code == 200
        assert b"Due: 1/8" in response.data

    def test_the_fragment_captions_NOTHING_for_a_row_due_ON_its_payday(
        self, app, auth_client, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument
        """The fragment's half of the negative case (see the page's)."""
        freeze_today(monkeypatch, _RENDER_DAY)
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "on the payday",
            "25.00", due_date=seed_periods[0].start_date,
        )
        db.session.commit()

        with app.app_context():
            response = auth_client.get(f"/transactions/{txn.id}/cell")

        assert response.status_code == 200
        assert b"Due:" not in response.data


class TestThePageAsksTheScheduleNoMoreForMorePaychecks:
    """The template does not query, and this is what says so.

    Asserted as an INVARIANT between two renders rather than as a count,
    because a count is a number someone has to maintain and every legitimate
    change to the route breaks it.  What cannot legitimately change is that
    drawing rows from six paychecks asks ``budget.pay_periods`` no more often
    than drawing rows from one: the render derives the owner's calendar, and
    everything after that reads the value.
    """

    def test_spreading_the_rows_over_six_paychecks_adds_no_query(
        self, app, auth_client, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument
        """One paycheck's rows, then six paychecks' rows: the same query count.

        **What this guards is the INTERMEDIATE state, and saying so is the
        point.**  It passes on the merge base too, and that is not a defect in
        it: there the template DOES read ``t.pay_period`` per cell, but the
        cash-ledger loader's ``joinedload(pay_period)`` had already warmed
        SQLAlchemy's identity map for those very rows, so the reads cost
        nothing extra and the count is flat either way.  Measured 2026-08-27 on
        the merge base: 29 statements and 4 ``budget.pay_periods`` reads for
        BOTH renders.

        Delete that eager load without moving the template -- which is the
        half-done shape of this change, and the one a future edit is most
        likely to land in -- and the counts diverge: 30 statements / 3 reads for
        one paycheck against 35 / 8 for six.  **That** is what fails here, and
        it is worth guarding precisely because neither half alone looks wrong.

        (``expire_all`` runs before the second render and does NOT make the
        template's read visible on its own: that render's own cash fold reloads
        the rows before the first cell is drawn.  This docstring claimed
        otherwise until an adversarial review measured it.)
        """
        freeze_today(monkeypatch, _RENDER_DAY)
        for index in range(6):
            add_txn(
                db.session, seed_user, seed_periods[0], f"first paycheck {index}",
                "25.00", due_date=date(2026, 1, 8),
            )
        db.session.commit()

        with app.app_context():
            one_paycheck, narrow = capture_sql_statements(
                lambda: auth_client.get("/grid?periods=6"),
            )
        assert one_paycheck.status_code == 200

        for index in range(6):
            add_txn(
                db.session, seed_user, seed_periods[index], f"spread {index}",
                "25.00", due_date=seed_periods[index].start_date + _ONE_DAY,
            )
        db.session.commit()
        db.session.expire_all()

        with app.app_context():
            six_paychecks, wide = capture_sql_statements(
                lambda: auth_client.get("/grid?periods=6"),
            )
        assert six_paychecks.status_code == 200

        # Not vacuous: the second render really did draw more rows, in more
        # columns, than the first.
        assert six_paychecks.data.count(b"Due:") > one_paycheck.data.count(
            b"Due:",
        )
        assert len(_pay_period_selects(wide)) == len(
            _pay_period_selects(narrow),
        )


class TestTheCaptionMapIsREQUIRED:
    """A surface that publishes no map RAISES rather than captioning everything.

    **The replacement for a claim, and the claim was measured false.**  The
    first build of C4-a-1's caption published a bare ``cell_payday`` and its
    own docstring said a surface forgetting it "must fail rather than draw a
    caption it cannot justify".  Rendering the real template without it draws
    the caption on every dated row instead: ``t.due_date != Undefined``
    evaluates ``True`` and raises nothing.  A per-transaction MAP subscript
    does raise, which is why ``budgets`` beside it is a map and why the
    decision is one -- and the property is asserted here rather than described
    in a comment.
    """

    def test_a_render_without_the_map_FAILS(self, app):
        """``due_captions[t.id]`` on an absent map raises ``UndefinedError``.

        The twin of
        ``test_grid_entries.TestTheAmountComesFromTheMap.test_a_render_without_the_map_FAILS_rather_than_falling_back``,
        for the fourth thing this cell draws.  It renders the partial directly
        rather than through a route, because what is under test is the
        TEMPLATE's contract: every route that draws it publishes the map, so no
        route can reach this state and no route test could grade it.
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel

        import jinja2  # pylint: disable=import-outside-toplevel

        txn = SimpleNamespace(
            id=1, name="Rent", settled_amount=None,
            estimated_amount=Decimal("1200.00"), due_date=date(2026, 1, 8),
            status=SimpleNamespace(is_settled=False, name="Projected"),
            status_id=99, transfer_id=None, credit_payback_for_id=None,
            is_expense=True, tracks_purchases=False, notes=None,
        )
        template = app.jinja_env.get_template("grid/_transaction_cell.html")
        with app.test_request_context("/"):
            with pytest.raises(
                jinja2.exceptions.UndefinedError, match="due_captions",
            ):
                template.render(
                    txn=txn,
                    budgets={1: Decimal("1200.00")},
                    settled={1: None},
                    retained={1: None},
                    # The balance line is the FIFTH required key since plan
                    # step credit_card:CC-4-2 and is read before the caption;
                    # ``None`` (no balance line) is a legitimate value that
                    # draws no chip, so the render reaches the map under test.
                    # Its own absence is graded by
                    # ``test_grid.test_accounts_and_cards``'s
                    # ``TestTheChipMarksARowOnAnotherAccount``.
                    account=None,
                )
