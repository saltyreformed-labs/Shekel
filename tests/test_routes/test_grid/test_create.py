"""Grid route tests: the create baseline, the period id the create fragments carry, a row born
projected, and the purchase form's clock.
"""

import re
from datetime import date
from decimal import Decimal

from flask_login import login_user

from app.extensions import db
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.ref import TransactionType
from app import ref_cache
from app.enums import TxnTypeEnum
from app.services import posting_service
from app.services.liability_sign import owed

from tests._test_helpers import (
    create_loan_account,
    current_pay_period,
    one_off_row_of,
    posted_loan_balance_at,
    resolved_amount,
)


class TestCreateBaseline:
    """Tests for POST /create-baseline route."""

    def test_create_baseline_success(self, app, auth_client, seed_user):
        """POST /create-baseline creates a baseline scenario when none exists.

        Verifies: the route creates a Scenario with name='Baseline' and
        is_baseline=True, then redirects to the grid index.
        """
        with app.app_context():
            # Remove the existing baseline so the route has work to do.
            Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).delete()
            db.session.commit()

            response = auth_client.post("/create-baseline")
            assert response.status_code == 302

            scenario = Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).one()
            assert scenario.name == "Baseline"
            assert scenario.is_baseline is True

    def test_create_baseline_idempotent(self, app, auth_client, seed_user):
        """POST /create-baseline with existing baseline does not create a duplicate.

        Verifies: when a baseline already exists (from seed_user fixture),
        the route redirects without creating a second scenario.
        """
        with app.app_context():
            response = auth_client.post("/create-baseline")
            assert response.status_code == 302

            count = Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).count()
            assert count == 1

    def test_create_baseline_reposts_stranded_openings(
        self, app, auth_client, seed_user,
    ):
        """The recovery path re-posts openings stranded by baseline-lessness.

        The Step-5 wiring (plan Section 3.3, point 6): deleting the baseline
        CASCADE-disposes its journal entries -- including the seed
        Checking's $1000.00 opening -- reproducing the baseline-less state
        where an account's corrections had nowhere to post.  The route's
        per-user resync then posts the opening into the NEW baseline in the
        same transaction, so it is not silently stranded until the next
        anchor event.
        """
        with app.app_context():
            checking_id = seed_user["account"].id
            Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).delete()
            db.session.commit()

            response = auth_client.post("/create-baseline")
            assert response.status_code == 302

            new_baseline = Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).one()
            assert posting_service.account_posting_total(
                checking_id, new_baseline.id,
            ) == Decimal("1000.00")

    def test_create_baseline_reposts_a_stranded_loan_opening(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """G1: the recovery path must repost the LOAN openings too, not just cash.

        The loan half of the test above.  A loan's opening posts per SCENARIO, so a
        baseline-less owner's loan has nowhere to put it; deleting the baseline
        reproduces that state exactly (the CASCADE disposes its journal entries,
        the loan's opening among them).

        The route recovered the ACCOUNT anchors and not the loans.  Before the fold
        cutover an ORIGINATED loan with no OPENING posting made the balance seam
        500 every loan surface; since steps C3b1/C3b3 the seam folds the balance
        from SOURCE facts, so a missing opening no longer breaks reads -- but the
        POSTING ledger (the general ledger the balance sheet and statements read)
        is out of sync until this reposts the opening.  So the recovery is pinned
        by reading the POSTINGS, not ``balance_at``: the posting window answers
        ONLY when the opening was reposted, where ``balance_at`` folds the
        $200,000 debt from source either way and cannot tell reposted from not.

        NEGATIVE CONTROL: drop the ``resync_user_loan_postings`` call from
        ``baseline_service.create_baseline_scenario`` and
        ``posted_loan_balance_at`` returns ``None`` (the opening is never
        reposted), while ``balance_at`` still folds the $200,000.00 debt from
        source.

        The two readers speak different signs, and the test reads each in its
        own.  The posting reader answers what the loan OWES; the seam reports
        what it HOLDS -- ``-200,000.00`` -- since plan step credit_card:CC-5-5c
        (ruling R-CC47), so its figure is read through ``owed()``.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from app.services import balance_at
        from app.services.balance_at import BalanceContext

        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Mortgage",
                principal=Decimal("200000.00"), rate=Decimal("0.05000"),
                term=360, origination_date=date(2026, 1, 15), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            loan_id = loan.id
            Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).delete()
            db.session.commit()

            assert auth_client.post("/create-baseline").status_code == 302

            bctx = BalanceContext.build(seed_user["user"].id)
            reloaded = db.session.get(Account, loan_id)
            new_baseline = Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True,
            ).one()
            # The recovery REPOSTED the loan's opening: the posting reader answers
            # $200,000 from the reconciled general ledger (None if still missing).
            # This is what pins the recovery -- balance_at cannot, since it folds
            # the same $200,000 debt from source whether or not the opening was
            # reposted.
            assert posted_loan_balance_at(
                loan_id, new_baseline.id, bctx.as_of,
            ) == Decimal("200000.00")
            # And the user-facing balance is correct: no payment made, so the loan
            # still owes its opening.  The seam holds it as -$200,000.00, so what
            # it owes is read through ``owed()``.
            assert owed(balance_at.balance_at(
                reloaded, bctx, bctx.as_of,
            )) == Decimal("200000.00")

    def test_create_baseline_requires_login(self, app, client):
        """POST /create-baseline without authentication redirects to login.

        Verifies: unauthenticated requests are rejected and no scenario
        is created.
        """
        with app.app_context():
            response = client.post("/create-baseline")
            assert response.status_code == 302
            assert "/login" in response.headers["Location"]

            count = Scenario.query.count()
            assert count == 0

    def test_create_baseline_rejects_get(self, app, auth_client, seed_user):
        """GET /create-baseline returns 405 Method Not Allowed.

        Verifies: the route only accepts POST requests.
        """
        with app.app_context():
            response = auth_client.get("/create-baseline")
            assert response.status_code == 405

    def test_create_baseline_user_isolation(self, app, auth_client, seed_user, second_user):
        """POST /create-baseline creates a scenario for the logged-in user only.

        Verifies: the route uses current_user.id correctly and does not
        affect other users' data.
        """
        with app.app_context():
            # Remove seed_user's baseline.
            Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).delete()
            db.session.commit()

            response = auth_client.post("/create-baseline")
            assert response.status_code == 302

            # The new scenario belongs to seed_user, not second_user.
            new_scenario = Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).one()
            assert new_scenario.user_id == seed_user["user"].id

            # second_user's baseline is untouched.
            other_baseline = Scenario.query.filter_by(
                user_id=second_user["user"].id, is_baseline=True
            ).one()
            assert other_baseline.user_id == second_user["user"].id


class TestBornProjected:
    """A transaction is always created Projected; status moves only via the seam.

    The create schemas drop ``status_id`` and the create routes assign Projected
    unconditionally, so a crafted request cannot mint a born-settled row (which
    would carry no settle day, bypass verify_transition, and post nothing to the
    ledger).  The "record an already-paid item" flow is the correct
    create-Projected-then-mark-done.
    """

    def test_create_with_settled_status_yields_projected(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions carrying a settled status_id still creates Projected."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum

            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            done_id = ref_cache.status_id(StatusEnum.DONE)

            response = auth_client.post("/transactions", data={
                "name": "Crafted Settled",
                "estimated_amount": "75.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "status_id": str(done_id),   # crafted: must be ignored
                "account_id": str(seed_user["account"].id),
            })
            assert response.status_code == 201

            txn = db.session.query(Transaction).filter_by(
                name="Crafted Settled"
            ).one()
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.settled_on is None

    def test_inline_create_with_settled_status_yields_projected(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/inline carrying a settled status_id creates Projected."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum

            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            received_id = ref_cache.status_id(StatusEnum.RECEIVED)

            response = auth_client.post("/transactions/inline", data={
                "estimated_amount": "33.33",
                "account_id": str(seed_user["account"].id),
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "transaction_type_id": expense_type.id,
                "status_id": str(received_id),   # crafted: must be ignored
            })
            assert response.status_code == 201

            txn = (
                db.session.query(Transaction)
                .order_by(Transaction.id.desc())
                .first()
            )
            assert txn is not None
            assert resolved_amount(txn) == Decimal("33.33")
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)

    def test_full_create_form_has_no_status_control(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transactions/new/full renders no status selector (born Projected)."""
        with app.app_context():
            response = auth_client.get(
                "/transactions/new/full",
                query_string={
                    "category_id": seed_user["categories"]["Groceries"].id,
                    "period_id": seed_periods_today[0].id,
                    "account_id": seed_user["account"].id,
                },
            )
            assert response.status_code == 200
            body = response.get_data(as_text=True)
            assert 'name="status_id"' not in body


class TestCreateFragmentsCarryThePeriodId:
    """The two create fragments state their period contract as an ID.

    Plan step **C2-f3e**, closing ledger row **P51**.  Both partials took a
    whole ORM ``PayPeriod`` and read ``period.id`` off it, where their
    sibling ``_transaction_empty_cell.html`` had taken the scalar since plan
    step C2-f2b -- one family of three partials stating one contract two ways.

    **These assert the rendered VALUE, not merely that the route answers
    200**, and the reason is Jinja's default ``Undefined``: this application
    configures no ``StrictUndefined``, so a context key that stops being
    passed renders as the empty string in silence.  ``pay_period_id`` is the
    field that decides which paycheck funds every row created from this cell,
    so a blank one is exactly the failure that must not be able to pass
    quietly (the same argument ``forms.get_quick_edit`` records for priming
    the Estimated field from a map rather than a scalar).
    """

    def _cell_args(self, seed_user, seed_periods_today):
        """Return the query string naming one real cell of the owner's grid."""
        return {
            "category_id": seed_user["categories"]["Groceries"].id,
            "period_id": seed_periods_today[3].id,
            "account_id": seed_user["account"].id,
            "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        }

    def test_quick_create_posts_back_the_requested_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The quick-create form's hidden pay_period_id is the cell's own."""
        with app.app_context():
            period_id = seed_periods_today[3].id
            response = auth_client.get(
                "/transactions/new/quick",
                query_string=self._cell_args(seed_user, seed_periods_today),
            )
            assert response.status_code == 200
            body = response.get_data(as_text=True)
            assert (
                f'name="pay_period_id" value="{period_id}"' in body
            ), body

    def test_quick_create_expand_button_carries_the_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The expand button's data-period-id is the cell's own.

        ``grid_edit.js`` reads it back off the DOM to build the full-create
        URL (``openFullCreate``), so a blank here breaks F2 / the three-dots
        button rather than the save -- a second consumer of the same value,
        and the reason the partial names the id twice.
        """
        with app.app_context():
            period_id = seed_periods_today[3].id
            response = auth_client.get(
                "/transactions/new/quick",
                query_string=self._cell_args(seed_user, seed_periods_today),
            )
            assert response.status_code == 200
            body = response.get_data(as_text=True)
            assert f'data-period-id="{period_id}"' in body, body

    def test_full_create_posts_back_the_requested_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The full-create form's hidden pay_period_id is the cell's own."""
        with app.app_context():
            period_id = seed_periods_today[3].id
            response = auth_client.get(
                "/transactions/new/full",
                query_string=self._cell_args(seed_user, seed_periods_today),
            )
            assert response.status_code == 200
            body = response.get_data(as_text=True)
            assert (
                f'name="pay_period_id" value="{period_id}"' in body
            ), body

    def test_empty_cell_reopens_the_same_cell(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The dash's hx-get names the period it was rendered for.

        Escape swaps the quick-create input back to this fragment, and its
        ``hx-get`` is what a following click re-opens; a blank period there
        would offer the create form for a cell in no paycheck at all.
        """
        with app.app_context():
            period_id = seed_periods_today[3].id
            response = auth_client.get(
                "/transactions/empty-cell",
                query_string=self._cell_args(seed_user, seed_periods_today),
            )
            assert response.status_code == 200
            body = response.get_data(as_text=True)
            assert f"period_id={period_id}" in body, body

    def test_created_row_lands_in_the_requested_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The whole loop: the form the route renders files the row correctly.

        The end-to-end statement the two field assertions above decompose --
        it posts the create form's OWN hidden fields back, so the row's
        ``pay_period_id`` is whatever the fragment actually emitted rather
        than whatever the test would have typed.  A partial that rendered a
        blank id is caught here as a 422, and one that rendered the WRONG id
        as a row in the wrong paycheck.
        """
        with app.app_context():
            period_id = seed_periods_today[3].id
            form = auth_client.get(
                "/transactions/new/quick",
                query_string=self._cell_args(seed_user, seed_periods_today),
            ).get_data(as_text=True)

            hidden = dict(
                re.findall(
                    r'<input type="hidden" name="([^"]+)" value="([^"]*)">',
                    form,
                ),
            )
            assert set(hidden) == {
                "account_id", "category_id", "pay_period_id", "scenario_id",
                "transaction_type_id",
            }, hidden

            created = auth_client.post(
                "/transactions/inline",
                data={**hidden, "estimated_amount": "42.00"},
            )
            assert created.status_code == 201, created.get_data(as_text=True)

            txn = (
                db.session.query(Transaction)
                .order_by(Transaction.id.desc())
                .first()
            )
            assert txn is not None
            assert resolved_amount(txn) == Decimal("42.00")
            assert txn.pay_period_id == period_id


class TestTheAddPurchaseFormReadsTheUsersClock:
    """The mobile card's ``today`` is ``display_today()``, never the process's.

    Plan step S1-c / 12.10.  ``app/routes/transactions/_helpers.py``'s
    ``_render_mobile_card`` passed ``today=date.today()`` into a form whose
    ``purchased_on`` value the SERVICE judges against ``display_today()``
    (ruling R-M, ``entry_service._reject_future_purchase_date``).
    ``date.today()`` reads the PROCESS timezone; ``display_today()`` converts
    UTC now into ``America/New_York``.  On any process not pinned to that zone
    the two disagree for part of every day, and the app's own form then
    defaults to -- and caps at -- a date its own server refuses.

    Latent in production, because the container pins ``TZ: America/New_York``.
    NOT latent in CI, which runs ``TZ=Pacific/Kiritimati`` (UTC+14) precisely to
    catch this shape.  It is the same two-clock defect as finding N-133 / R2.

    **The test pins WHICH CLOCK the route reads, rather than arranging for the
    two to disagree.**  Mutating ``TZ`` + ``time.tzset()`` would reproduce the
    disagreement, but the C library's zone is process-global and survives
    ``monkeypatch``'s env restore (only another ``tzset()`` clears it), so a
    leak would silently re-zone every later test in the same xdist worker --
    measured, while writing this: it broke an unrelated MFA test three files
    away.  A test that can corrupt its neighbours to prove a point about clocks
    is the wrong instrument.  Substituting ``display_today`` for a sentinel is
    exact and side-effect free: the rendered form carries the sentinel if and
    only if the route reads that function, which IS the defect.
    """

    #: A date no clock can produce on its own, so its appearance in the
    #: rendered form can only have come from ``display_today``.
    _SENTINEL = date(2019, 7, 4)

    @staticmethod
    def _envelope_txn(seed_user, period):
        """A Projected envelope row, so the card renders the entries block."""
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        txn = one_off_row_of(
            period,
            name="Two-clock Groceries",
            amount=Decimal("500.00"),
            user_id=period.user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            category_id=seed_user["categories"]["Groceries"].id,
            is_envelope=True,
        )
        db.session.commit()
        return txn

    def test_the_mobile_card_form_defaults_to_the_users_civil_day(
        self, app, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """The rendered add-purchase input comes from ``display_today``.

        ``_render_mobile_card`` is driven directly, on a PROJECTED envelope.
        Its add-purchase input must read ``value`` and ``max`` off the
        substituted ``display_today``, and must not carry the process's own
        ``date.today()``.

        **It drove the ``mark-done`` ROUTE until plan step X-au-c3, and the
        route had to go rather than the assertion.**  ``render=mobile_card`` is
        served by that one route, and marking an envelope paid now CLOSES its
        purchases -- a settled row's add form is withdrawn
        (``_transaction_entries.html``, the developer's 2026-08-17 field-aware
        ruling), so the card that route returns carries no ``purchased_on``
        input at all and there is no clock left in it to read.  Calling the
        helper is what keeps the SUBJECT: the negative control below names that
        function, the ``today`` it passes is the fact under test, and the
        route's own wiring of ``render=mobile_card`` is covered by
        ``TestMobileCardActionBar``.  The row stays Projected, which is the
        state an add form belongs to.

        Both halves are asserted.  The positive alone would pass a build that
        rendered the sentinel somewhere unrelated; the negative alone would
        pass a form that rendered no date at all.

        Negative-controlled (Section 7.3): restoring ``today=date.today()`` in
        ``_render_mobile_card`` fails this with
        ``assert 'value="2019-07-04"' in ...``, measured 2026-08-01.
        """
        # pylint: disable=import-outside-toplevel
        from app.routes.transactions import _helpers

        # The premise: the sentinel is not what any real clock answers, so a
        # render reading ``date.today()`` cannot produce it by coincidence.
        assert date.today() != self._SENTINEL
        monkeypatch.setattr(_helpers, "display_today", lambda: self._SENTINEL)

        with app.app_context(), app.test_request_context("/"):
            period = current_pay_period(
                seed_user["user"].id,
            )
            assert period is not None
            txn = self._envelope_txn(seed_user, period)
            # The card is drawn against its requester's balance line since
            # plan step credit_card:CC-4-2 (``fragment_balance_line`` reads
            # ``current_user``), and every route that renders it sits behind
            # the login gate; this direct call supplies the owner the gate
            # would have.
            login_user(seed_user["user"])
            html = _helpers._render_mobile_card(
                txn, card_prefix="tp", can_edit=True,
            )

        assert 'name="purchased_on"' in html, (
            "the add-purchase form must actually have rendered"
        )
        assert f'value="{self._SENTINEL.isoformat()}"' in html
        assert f'max="{self._SENTINEL.isoformat()}"' in html
        assert date.today().isoformat() not in html
