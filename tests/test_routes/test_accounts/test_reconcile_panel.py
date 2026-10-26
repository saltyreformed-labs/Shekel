"""Account route tests: the reconcile panel's offers and the day it stamps, and the route's
branches no other case reached.
"""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.utils.dates import display_today
from tests._test_helpers import (
    an_entered_day,
    append_balance_assertion,
    generate_row_of,
    make_expense_template,
    open_books_before_the_first_assertion,
    settle_instant_on,
    settlement_if_settling,
)
from app.models.ref import AccountType, Status
from app.models.transaction import Transaction
from app.services import account_service, status_seam, transaction_service
from app.services.row_valuation import settled_figure
from app.services.settle_day import record_settle_day

from ._helpers import _TestTheReconcileRouteMembers


class TestTheReconcileRoute(_TestTheReconcileRouteMembers):
    """The panel's offers and the stamped day; members on _TestTheReconcileRouteMembers."""

    def test_a_true_up_alone_records_no_posting_day(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """RE-RULED: the true-up route reconciles NOTHING (ruling R-DH (d)).

        The inversion this whole class turns on.  Three past-dated purchases,
        one true-up through the real route: every ``settled_on`` is still NULL
        afterwards, because whether the bank has taken a purchase is not
        derivable from the fact that a balance was entered.

        The response carries the reconcile PROMPT instead -- the question the
        engine used to answer on the user's behalf, now asked.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("106.86", past, False, None),
                    ("249.71", past, False, None),
                    ("105.77", past, False, None),
                ],
            )

            response = self._true_up(
                auth_client, seed_user["account"].id, "4537.66",
            )

            entries = self._entries_of(txn.id)
            assert len(entries) == 3
            assert all(e.settled_on is None for e in entries)
            # The prompt rides along on the true-up's own response.  The copy
            # widened at plan step X-f2-c2 (ruling R-FD: the panel offers
            # deposits too, so it cannot say "purchases"); what this asserts is
            # unchanged -- that the prompt is IN the true-up's own body.
            assert b"Tick everything your statement shows" in response.data

    def test_ticking_a_purchase_stamps_the_asserted_day(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST reconcile records the assertion's own day on the ticked rows.

        Two outstanding purchases; the user ticks one.  Its ``settled_on``
        becomes the day the balance was asserted for (today, via the true-up
        route), and the other is untouched -- the whole point of asking rather
        than guessing is that the answer can be partial.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("106.86", past, False, None),
                    ("249.71", past, False, None),
                ],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            ticked, untouched = self._entries_of(txn.id)
            ticked_id, untouched_id = ticked.id, untouched.id

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(ticked_id)]},
            )
            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            by_id = {e.id: e for e in self._entries_of(txn.id)}
            assert by_id[ticked_id].settled_on == display_today()
            assert by_id[untouched_id].settled_on is None
            # The re-rendered panel still offers the one left outstanding.
            assert b"249.71" in response.data

    def test_the_panel_lists_only_what_is_still_outstanding(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET reconcile offers the unrecorded purchases and drops the rest.

        One purchase already carries a posting day and one does not.  Only the
        second is offered: a purchase whose posting day is recorded is not
        outstanding, whatever that day is, so listing it would ask the user to
        confirm something they already have.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("106.86", past, False, past),
                    ("249.71", past, False, None),
                ],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            )

            assert response.status_code == 200
            assert b"249.71" in response.data
            assert b"106.86" not in response.data

    def test_a_purchase_made_after_the_statement_is_not_offered(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """RE-RULED from "future-dated entries are not cleared".

        A purchase made AFTER the day the balance was read cannot be inside it,
        so it is neither listed nor stamped -- and a forged id for it changes
        nothing.  The bound is real rather than cosmetic: stamping it would
        write a ``settled_on`` earlier than its own ``purchased_on``, which
        ``ck_transaction_entries_settled_not_before_purchase`` refuses at the
        database.  Filtering here makes that constraint a backstop instead of a
        reachable 500.
        """
        with app.app_context():
            future = display_today() + timedelta(days=7)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("50.00", future, False, None),
                ],
            )
            self._true_up(auth_client, seed_user["account"].id, "5000.00")
            entry_id = self._entries_of(txn.id)[0].id

            listed = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            )
            # Asserted on the entry's OWN checkbox, not on its amount as a
            # substring.  The panel offers the envelope's close too since plan
            # step X-f2-c2, and that row's figure is sum(entries) = $50.00, so
            # a ``b"50.00" not in`` assertion now reads the wrong row's money
            # and would pass or fail for reasons unrelated to this bound.
            assert (
                f'name="entry_ids" value="{entry_id}"'.encode()
                not in listed.data
            )

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(entry_id)]},
            )

            assert response.status_code == 200
            assert self._entries_of(txn.id)[0].settled_on is None

    def test_a_purchase_on_a_settled_parent_IS_offered(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """RE-RULED TWICE; this is the third and current ruling.

        It began as "entries on non-projected parents are not cleared", became
        "the entry reservation prices only PROJECTED rows, so a purchase on a
        settled parent is inert", and is now the opposite of both (developer,
        2026-08-17).  Ruling **R-FM** falsified the "inert" premise one plan
        step earlier: the row's own leg of the time (``cash_ledger.
        settled_cash_leg``, deleted at ruling **R-BAL81**) subtracted every
        POSTED purchase from a settled row's close, so recording the day moved
        that purchase's cash off the close's day and onto the bank's; since
        ``balance:X-bi-4a`` every purchase is a movement of its own and the
        row books nothing (ruling **R-BAL80**).  The total never moves; the
        DAY does, and a paper statement is reconciled by day.

        Driven through the ROUTE rather than the service, which is the half its
        sibling in ``test_reconcile_service`` cannot see: the panel must LIST
        the purchase as well as accept it, and the two are different code.

        The parent is settled through ``status_seam.apply_status_change``
        rather than by assigning ``status_id``.  A raw assignment builds a
        settled row carrying no settle day and no covering movement -- one no
        door in the app can create, which ``integrity_check`` DC-11 names and
        ``balance_predicates.settled_day`` refuses to fold.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("100.00", past, False, None),
                ],
            )
            paid = db.session.query(Status).filter_by(name="Paid").one()
            status_seam.apply_status_change(
                txn, paid.id,
                settlement=settlement_if_settling(txn, paid.id),
            )
            db.session.commit()
            self._true_up(auth_client, seed_user["account"].id, "5000.00")
            entry_id = self._entries_of(txn.id)[0].id

            listed = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            )
            assert b"100.00" in listed.data

            auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(entry_id)]},
            )

            assert self._entries_of(txn.id)[0].settled_on is not None

    def test_an_already_recorded_purchase_is_not_re_stamped(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """RE-RULED from "already cleared entries unchanged" -- idempotence.

        A purchase whose exact posting day the user already sharpened on the
        entry form keeps that day.  Re-submitting its id must not overwrite it
        with the assertion's coarser upper bound: the outstanding scope is
        ``settled_on IS NULL``, so the row simply does not match.
        """
        with app.app_context():
            past = display_today() - timedelta(days=2)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("100.00", past, False, past),
                ],
            )
            self._true_up(auth_client, seed_user["account"].id, "5000.00")
            entry_id = self._entries_of(txn.id)[0].id

            auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(entry_id)]},
            )

            assert self._entries_of(txn.id)[0].settled_on == past

    def test_reconciling_one_account_does_not_touch_another(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A reconcile records ONLY the reconciled account's purchases.

        KEPT verbatim in intent from the retired bulk-clear class, because the
        invariant is unchanged by the re-ruling and is still the one that
        misprices real money.  Accounts carry no per-type uniqueness, so a user
        may hold more than one checking account.  Reconciling account A records
        what A's statement showed; recording B's purchases there would drop B's
        reservation without ever raising B's anchor, silently inflating B's
        projected balance (#8).  It fails under an owner-only filter and passes
        once the filter is scoped by ``account_id``.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)

            # Second checking account on the SAME user.
            checking_type = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one()
            account_b = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Checking 2",
                    anchor_balance=Decimal("2000.00"),
                ),
            )
            db.session.add(account_b)
            # RE-EXPRESSED at plan step pay_calendar:C18-a under CLAUDE.md
            # rule 5, developer-confirmed 2026-09-22 ("Yes: open books before
            # rows"): this account's books open before the schedule, as the
            # seeded account's do.  ``create_account`` opens them TODAY, after
            # the paycheck the grocery row is generated into, and since ruling
            # R-PC85 a definition's occurrences start above its account's books.
            open_books_before_the_first_assertion(db.session, account_b)
            db.session.commit()
            account_b_id = account_b.id

            txn_a = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("60.00", past, False, None)],
            )
            txn_b = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("75.00", past, False, None)],
                account=account_b,
            )

            # BOTH accounts have an asserted day, so B's purchase failing to
            # be recorded cannot be blamed on B having no assertion.
            self._true_up(auth_client, seed_user["account"].id, "5000.00")
            self._true_up(auth_client, account_b_id, "3000.00")

            entry_a_id = self._entries_of(txn_a.id)[0].id
            entry_b_id = self._entries_of(txn_b.id)[0].id

            # Reconcile account A, submitting BOTH ids -- the cross-account id
            # is exactly the forged submission the scope has to reject.
            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(entry_a_id), str(entry_b_id)]},
            )
            assert response.status_code == 200

            assert self._entries_of(txn_a.id)[0].settled_on == display_today()
            assert self._entries_of(txn_b.id)[0].settled_on is None

    def test_the_stamped_day_is_the_ASSERTED_day_not_today(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The route resolves the account's asserted day; it does not use today.

        **The two are the same day in every other test in this class**, because
        each trues up through the PATCH route and ``apply_anchor_true_up``
        stamps ``display_today()``.  So none of them can tell
        ``cash_ledger.reconciled_through(account.id)`` apart from
        ``display_today()`` -- measured: substituting the latter in the POST
        door (then ``accounts.anchor.reconcile_purchases``, now
        ``accounts.reconcile.record_reconciliation``) left the whole 7,721-test
        suite green.  This is the case that separates them, and it is the only
        one.

        ``observed_on`` is USER-SUPPLIED (plan step 2), so a back-dated
        assertion is an ordinary state: "my statement is dated the 3rd, not
        today".  Here the account's latest assertion is for the day after the
        first period started -- weeks in the past -- and the ticked purchase
        must be stamped with THAT day.

        What the wrong clock costs, and why it is not merely untidy: the
        purchase would get ``settled_on = today``, which is AFTER the asserted
        day, so ``ReconciledThrough.covers`` answers False, the reservation never
        drops, and the projection stays low by the whole purchase.  Worse, the
        row now fails the ``settled_on IS NULL`` clause of
        ``reconcile_service._purchases._outstanding_scope``, so the panel can
        never offer it again -- the user cannot fix it from the surface that
        broke it.
        """
        with app.app_context():
            account = seed_user["account"]
            asserted_on = seed_periods_today[0].start_date + timedelta(days=1)
            purchased_on = seed_periods_today[0].start_date
            # The premise: the asserted day is genuinely in the past, so
            # "stamped the asserted day" and "stamped today" are different
            # answers rather than the same one twice.
            assert asserted_on < display_today()

            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("64.20", purchased_on, False, None)],
            )
            append_balance_assertion(
                db.session, account,
                Decimal("1000.00"), settle_instant_on(asserted_on),
            )
            db.session.commit()
            entry_id = self._entries_of(txn.id)[0].id

            # The panel offers it against the BACK-DATED day.
            listed = auth_client.get(f"/accounts/{account.id}/reconcile")
            assert b"64.20" in listed.data

            response = auth_client.post(
                f"/accounts/{account.id}/reconcile",
                data={"entry_ids": [str(entry_id)]},
            )
            assert response.status_code == 200

            stamped = self._entries_of(txn.id)[0].settled_on
            assert stamped == asserted_on
            assert stamped != display_today()


# ── Account Type CRUD ─────────────────────────────────────────────



class TestTheReconcileRoutesUngradedBranches:
    """Finding **N-232**: three branches of the POST that nothing could fail.

    Opened by X-f2-c2's own adversarial correctness review.  The route's code
    is correct under trace -- which is exactly why the holes survived -- but
    three of its arms had ZERO coverage, and one of them is the arm the route's
    own docstring argues matters most:

    * the PARTIAL-landing notice (``recorded < asked``).  The one test that
      reached this code took the nothing-landed arm, so deleting the partial
      branch or swapping the two message constants left the suite green.  For
      the purchase arm a dropped tick hides a column stamp; since X-f2-c2 it
      hides a status change, an amount and a ledger posting.
    * the ``StaleDataError`` refusal, which renders the panel as a designed 400
      rather than letting htmx leave a broken button.
    * that an amount box submitted WITHOUT its checkbox is INERT.  That is the
      modal case rather than an exotic one: the panel renders a box on every
      correctable row and an HTML form posts them ALL, so a five-row panel
      posts five figures on every submit.
    """

    _make_grocery_txn_with_entries = (
        TestTheReconcileRoute._make_grocery_txn_with_entries
    )
    _true_up = staticmethod(TestTheReconcileRoute._true_up)
    _entries_of = staticmethod(TestTheReconcileRoute._entries_of)

    @staticmethod
    def _bill(seed_user, period, *, name="Electricity", amount="180.00"):
        """Create a projected NON-envelope row -- correctable, so it draws a box.

        The engine's row of a priced definition (:func:`generate_row_of`,
        plan step balance:X-cf-4), so the figure the box prefills is what
        the row RESOLVES to.
        """
        template = make_expense_template(
            db.session, seed_user, amount=amount, name=name,
            category_key="Groceries",
        )
        return generate_row_of(template, period)

    def test_a_PARTLY_landed_submission_says_so(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """One tick lands, one was already settled: the partial notice, not "saved".

        The ordinary way to reach it is a second device settling a row while a
        statement is being walked.  Answered as a 200 -- the request succeeded
        and the refreshed list is the useful part -- with the sentence that
        says half of it was left alone.  Shown to FIRE: swapping the partial
        and stale message constants fails this and its sibling below.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("106.86", past, False, None),
                    ("249.71", past, False, None),
                ],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            lands, already = self._entries_of(txn.id)
            lands_id, already_id = lands.id, already.id
            # The second entry is settled behind the panel's back -- the second
            # device.  The form still posts both ids.
            record_settle_day(db.session.get(type(already), already_id), an_entered_day(past))
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(lands_id), str(already_id)]},
            )

            assert response.status_code == 200
            assert b"had already been settled elsewhere" in response.data
            assert b"nothing was recorded" not in response.data
            by_id = {e.id: e for e in self._entries_of(txn.id)}
            assert by_id[lands_id].settled_on == display_today()

    def test_a_submission_that_lands_on_NOTHING_says_something_else(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The control for the sibling above: the two arms say different things.

        Without both, one message constant answers every case and the branch
        that chooses between them grades nothing.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("106.86", past, False, None)],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            entry_id = self._entries_of(txn.id)[0].id
            record_settle_day(
                db.session.get(
                    type(self._entries_of(txn.id)[0]), entry_id,
                ),
                an_entered_day(past),
            )
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [str(entry_id)]},
            )

            assert response.status_code == 200
            assert b"nothing was recorded" in response.data
            assert b"had already been settled elsewhere" not in response.data

    def test_a_stale_settle_re_renders_the_panel_as_a_designed_400(
        self, app, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """A concurrent commit mid-reconcile is a designed refusal, not a 500.

        The race is engineered the way ``test_optimistic_locking_c18``'s
        mark-done case does it: another writer's commit lands as the settle
        verb begins, after the reconcile read the row, where the verb took the
        row's lock until plan step ``balance:X-bn`` (the developer placed it
        just before that lock, Round 12 Q1, 2026-09-23, "Move the change
        earlier"; plan step ``credit_card:CC-5-4a-4``, ruling **R-CC96**).
        Since X-bn no request of the owner's can land there (ruling
        **R-CC106**), so the writer is a separate connection holding no owner
        lock.  The version moves, and the version-pinned WHERE fails.  The
        response carries ``Shekel-Designed-Fragment`` because htmx
        leaves a 4xx non-swapping, so a refusal without it renders NOTHING and
        the button reads as broken -- worse than the error it reports.  Shown
        to FIRE: deleting the route's ``except StaleDataError`` arm turns this
        into a 500.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods_today[0])
            bill_id = bill.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            fired = {"flag": False}
            real_settle = transaction_service.settle_transaction

            def make_stale_then_settle(row, *args, **kwargs):
                if not fired["flag"] and row.id == bill_id:
                    fired["flag"] = True
                    with db.engine.connect() as conn:
                        conn.execute(
                            text(
                                "UPDATE budget.transactions "
                                "SET version_id = version_id + 1 WHERE id = :id"
                            ),
                            {"id": bill_id},
                        )
                        conn.commit()
                return real_settle(row, *args, **kwargs)

            monkeypatch.setattr(
                transaction_service, "settle_transaction", make_stale_then_settle,
            )
            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"transaction_ids": [str(bill_id)]},
            )

            assert response.status_code == 400, response.data
            assert response.headers.get("Shekel-Designed-Fragment") == "1"
            assert b"changed while you were reconciling" in response.data

            db.session.expire_all()
            assert db.session.get(Transaction, bill_id).settled_on is None

    def test_an_amount_box_submitted_WITHOUT_its_checkbox_is_inert(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The modal case: an HTML form posts every box it renders.

        The panel prefills a box on every correctable row, so a five-row panel
        posts five figures on every submit whether or not the user ticked
        those rows.  Nothing may happen to a row whose id is not in
        ``transaction_ids`` -- the corrections map is passed through and the
        arm's own scope is what decides, so an unticked row is simply not in
        the narrowed set.
        """
        with app.app_context():
            ticked = self._bill(seed_user, seed_periods_today[0],
                                name="Electricity", amount="180.00")
            unticked = self._bill(seed_user, seed_periods_today[0],
                                  name="Water", amount="60.00")
            ticked_id, unticked_id = ticked.id, unticked.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "transaction_ids": [str(ticked_id)],
                    f"settled_amount-{ticked_id}": "175.42",
                    f"settled_amount-{unticked_id}": "1.00",
                },
            )
            assert response.status_code == 200

            db.session.expire_all()
            settled = db.session.get(Transaction, ticked_id)
            left_alone = db.session.get(Transaction, unticked_id)
            assert settled_figure(settled) == Decimal("175.42")
            assert status_seam.recorded_settlement(left_alone) is None
            assert left_alone.settled_on is None
            assert left_alone.status.name == "Projected"

    def test_a_forged_box_for_ANOTHER_accounts_row_changes_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A crafted correction cannot reach outside the reconciled account.

        The ids never leave the arm's scope, so a row on a second account is
        not in the narrowed set and its amount box is read by nothing.  The
        negative half of the "the scope is the security property" claim, taken
        from the ROUTE rather than from the service.
        """
        with app.app_context():
            other = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    name="Second Checking",
                    account_type_id=seed_user["account"].account_type_id,
                    anchor_balance=Decimal("100.00"),
                ),
            )
            db.session.flush()
            # RE-EXPRESSED at plan step pay_calendar:C18-a under CLAUDE.md
            # rule 5, developer-confirmed 2026-09-22 ("Yes: open books before
            # rows"): this account's books open before the schedule, as the
            # seeded account's do.  ``create_account`` opens them TODAY, after
            # the paycheck the grocery row is generated into, and since ruling
            # R-PC85 a definition's occurrences start above its account's books.
            open_books_before_the_first_assertion(db.session, other)
            elsewhere = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [], account=other,
                name="Elsewhere",
            )
            elsewhere_id = elsewhere.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "transaction_ids": [str(elsewhere_id)],
                    f"settled_amount-{elsewhere_id}": "999.99",
                },
            )
            assert response.status_code == 200
            assert b"nothing was recorded" in response.data

            db.session.expire_all()
            untouched = db.session.get(Transaction, elsewhere_id)
            assert status_seam.recorded_settlement(untouched) is None
            assert untouched.settled_on is None

    def test_a_row_the_verb_would_not_read_a_box_for_renders_NONE(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The negative control for ruling **R-FF**'s box.

        An envelope CARRYING entries derives its amount, so the settle verb
        ignores a submitted figure -- and the panel must therefore render no
        input for it.  Without this the correctable cases are satisfied by a
        panel that draws a box on every row and silently drops two thirds of
        what it collects.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            envelope = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("106.86", past, False, None)],
            )
            bill = self._bill(seed_user, seed_periods_today[0])
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert f'name="settled_amount-{bill.id}"' in body
            assert f'name="settled_amount-{envelope.id}"' not in body

    def test_a_correction_ABOVE_the_columns_domain_is_a_designed_refusal(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A figure the column cannot hold is a 400, never a 500 mid-statement.

        The record's figure (``budget.transaction_entries.amount``, the
        covering movement's; the row's own ``settled_amount`` through plan
        step ``balance:X-bi-4b-1``) is ``numeric(12, 2)``, so a figure at or
        above ``10 ** 10`` cannot be stored.  The schema bounded
        the field below (``>= 0``) and not above, so such a value passed
        validation, reached the settle verb and died at the DATABASE as a
        ``DataError`` -- an unhandled 500 on a door an ordinary crafted POST
        reaches.

        **The blast radius is what makes it worth a test rather than a shrug.**
        A statement walk is ONE act committed once (the route's own
        "four purchases and their envelope's close mean all five or none"), so
        a single unstorable box discards every other tick submitted with it.

        Shown to FIRE: removing the field's ``max`` turns this into a 500.
        """
        with app.app_context():
            ticked = self._bill(seed_user, seed_periods_today[0])
            alongside = self._bill(seed_user, seed_periods_today[0],
                                   name="Water", amount="60.00")
            ticked_id, alongside_id = ticked.id, alongside.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "transaction_ids": [str(ticked_id), str(alongside_id)],
                    f"settled_amount-{ticked_id}": "10000000000.00",
                },
            )

            assert response.status_code == 400, response.status_code
            assert response.headers.get("Shekel-Designed-Fragment") == "1"

            # The whole act was refused, so the row ticked ALONGSIDE the bad
            # box is still outstanding rather than half-committed.
            db.session.expire_all()
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            for row_id in (ticked_id, alongside_id):
                row = db.session.get(Transaction, row_id)
                assert row.status_id == projected_id
                assert row.settled_on is None
                assert status_seam.recorded_settlement(row) is None
