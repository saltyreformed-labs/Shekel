"""Account route tests: whom the reconcile route refuses, what one POST writes, and the cash figure
beside the booked one.
"""

from datetime import timedelta
from decimal import Decimal

from app.extensions import db
from app.utils.dates import display_today
from tests._test_helpers import create_account_of_type, create_loan_account, resolved_amount
from app.models.ref import AccountType
from app.models.transaction import Transaction
from app.services import account_service
from app.services.row_valuation import settled_figure

from ._helpers import _TestTheReconcileRouteMembers


class TestTheReconcileRoute(_TestTheReconcileRouteMembers):
    """Refusals, envelope groups and the write door; members on _TestTheReconcileRouteMembers."""

    def test_a_companion_cannot_reach_the_reconcile_route(
        self, app, companion_client, seed_user, seed_periods_today,
    ):
        """Owner-only, and a companion gets 404 rather than 403.

        The COMPANION is the case that matters, not an unrelated stranger: a
        companion is authenticated AND holds granted access to this owner's
        transactions and their entries (``TestCompanionAccess`` in
        ``test_routes/test_entries.py``), so ``@require_owner`` refusing them is
        a real decision rather than a trivial one.  Reconciling is an owner
        judgement about the owner's own bank statement.

        The project's security response rule: 404 for both "not found" and
        "not yours", so a probe cannot use the status code as an existence
        oracle.  Both doors carry ``@require_owner``, so both are asserted --
        a read that leaked the outstanding list would disclose the owner's
        spending even if the write were refused.
        """
        with app.app_context():
            account_id = seed_user["account"].id

        assert companion_client.get(
            f"/accounts/{account_id}/reconcile",
        ).status_code == 404
        assert companion_client.post(
            f"/accounts/{account_id}/reconcile",
            data={"entry_ids": ["1"]},
        ).status_code == 404

    def test_reconciling_a_savings_account_leaves_checking_alone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Successor to the retired "a non-checking true-up does not clear".

        The bulk clear was checking-only by construction; the reconcile is
        scoped by ``account_id`` instead, so the property survives the re-ruling
        with a different mechanism and is re-asserted rather than assumed
        covered by the two-checking-accounts case beside it.  A debit purchase
        never reaches a savings statement, so reconciling savings must offer
        nothing and record nothing.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [("90.00", past, False, None)],
            )
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("1000.00"),
                ),
            )
            db.session.commit()
            savings_id = savings.id
            entry_id = self._entries_of(txn.id)[0].id

            self._true_up(auth_client, savings_id, "1500.00")

            listed = auth_client.get(f"/accounts/{savings_id}/reconcile")
            assert b"90.00" not in listed.data

            auth_client.post(
                f"/accounts/{savings_id}/reconcile",
                data={"entry_ids": [str(entry_id)]},
            )

            assert self._entries_of(txn.id)[0].settled_on is None

    def test_a_submitted_id_that_is_not_a_number_does_not_raise(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Finding N-136: this door used to 500 on a forged ``entry_ids``.

        ``str.isdigit()`` is true for 888 characters and 128 of them make
        ``int()`` raise, and ``app/error_handlers.py`` has no ``ValueError``
        arm -- so a superscript two reached the user as an unhandled 500.
        (The 500 HANDLER still renders ``errors/500.html`` with ``DEBUG``
        off, so no traceback was shown; an earlier wording here said "and a
        stack trace" and was wrong about the response, not the raise.)  The
        submission is now answered: the value names no row, so it is
        dropped, and the purchase it could not name is left outstanding.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [("106.86", past, False, None)],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": ["\N{SUPERSCRIPT TWO}"]},
            )

            assert response.status_code == 200
            assert self._entries_of(txn.id)[0].settled_on is None
            # Still offered, so the panel tells the truth about what is left.
            assert b"106.86" in response.data

    def test_an_id_spelled_in_another_digit_script_reconciles_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """One row id has ONE spelling (plan step X-ae's ASCII ruling).

        Measured before the fix: Eastern Arabic numerals pass ``isdigit()``
        AND convert cleanly, so this exact submission returned 200 and really
        stamped the entry.  The crash fix alone would have kept that -- an id
        would have had ten spellings, only one of which any form of ours
        emits.  It is the same purchase and the same real id, submitted in
        the other script, and now it records nothing.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [("106.86", past, False, None)],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            entry_id = self._entries_of(txn.id)[0].id

            eastern_arabic = str(entry_id).translate(
                str.maketrans("0123456789", "٠١٢٣٤"
                                            "٥٦٧٨٩"),
            )
            # The premise: this really is the same id, and it really does
            # satisfy the predicate the route used to guard with.
            assert eastern_arabic.isdigit()
            assert int(eastern_arabic) == entry_id

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"entry_ids": [eastern_arabic]},
            )

            assert response.status_code == 200
            assert self._entries_of(txn.id)[0].settled_on is None

    def test_one_unparseable_id_does_not_discard_the_valid_ones(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A junk value costs its own id, not the whole submission.

        The set-operation posture the writer already takes toward an id that
        is real but not the user's -- refusing the batch would punish the
        user for a value their browser never sent.
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
                data={"entry_ids": [
                    "\N{SUPERSCRIPT TWO}", str(ticked_id), "not-an-id",
                ]},
            )

            assert response.status_code == 200
            by_id = {e.id: e for e in self._entries_of(txn.id)}
            assert by_id[ticked_id].settled_on == display_today()
            assert by_id[untouched_id].settled_on is None

    def test_the_panel_404s_for_an_account_this_page_does_not_serve(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Finding **N-216**: both reconcile doors take the page's kind gate.

        The cash detail page and its other three fragments all resolve through
        ``_cash_page.load_cash_account_or_404``, which 404s the kinds this page
        does not serve; these two routes guarded on OWNERSHIP alone, so
        ``GET /accounts/<loan id>/reconcile`` rendered a cash-reconciliation
        panel for an amortizing account and answered "every purchase recorded
        on this account has been matched to your bank" -- a sentence that is
        not about a loan.

        **A 200 with an empty list is what it did, which is why a
        content assertion could not have caught it.** The list is empty because
        a loan's ``account_anchor_history`` carries only its origination row, so
        the offer set's date bound admits nothing: a property of the DATA, and
        one plan step X-f2-c2 removes when the set widens to transactions and a
        loan's projected transfer shadows become tickable. The status code is
        therefore the only honest oracle here.

        Both doors are asserted: a READ that rendered the panel is the leak, and
        a WRITE that accepted a tick against a loan is the money.
        """
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Van Loan")
            db.session.commit()
            loan_id = loan.id

        assert auth_client.get(
            f"/accounts/{loan_id}/reconcile",
        ).status_code == 404
        assert auth_client.post(
            f"/accounts/{loan_id}/reconcile",
            data={"entry_ids": ["1"]},
        ).status_code == 404

    def test_a_true_up_on_a_kind_this_panel_refuses_prompts_NOTHING(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Finding **N-216**'s THIRD door, and the regression the first fix made.

        The reconcile panel has three doors, not two: the two routes, and the
        post-true-up modal ``prompt_fragment`` mounts out of band.  The anchor
        editor opens on every kind except an amortizing one, so a 401(k) or
        Property true-up reaches that modal -- and once the two ROUTES 404'd
        those kinds, the modal still rendered with its checkboxes while its
        submit button POSTed to a door that refused.  **htmx swaps only 2xx**,
        so the button did nothing, silently and forever: strictly worse than
        the state before the gate, and the exact failure
        ``app/error_handlers.py`` refuses to ship for a mutating fragment.

        The premise is asserted, not assumed: the true-up itself must SUCCEED
        (200) on this kind, or the modal's absence would prove nothing.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            retirement = create_account_of_type(
                seed_user, db.session, "401(k)", "Empower 401(k)",
                anchor_balance=Decimal("50000.00"),
            )
            db.session.commit()
            retirement_id = retirement.id
            # An outstanding purchase ON that account, so the offer set is
            # non-empty and an ungated modal would really render.
            self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("40.00", past, False, None)],
                account=retirement,
            )

            trued_up = self._true_up(auth_client, retirement_id, "51000.00")

            assert trued_up.status_code == 200
            body = trued_up.data.decode()
            assert "data-modal-auto-show" not in body
            assert "Mark ticked as posted" not in body

    def test_purchases_are_grouped_under_their_own_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling **R-EW**: a purchase nests under the thing it belongs to.

        The flat list this replaced named each purchase's parent in a trailing
        fragment on its own line, so two purchases against one envelope repeated
        that envelope's name twice and a purchase against another sat between
        them under whatever purchase date ordered it. Grouped, each envelope's
        name appears ONCE and its purchases are contiguous beneath it.

        Graded on the rendered document rather than on the producer, because the
        producer's grouping is already pinned in
        ``test_services/test_reconcile_service/test_the_transaction_arm.py`` and what this asserts
        is that the panel consumes it: the name appears exactly once per envelope, each
        block carries its own subtotal, and every purchase is still individually
        tickable (the two acts stay independent, R-EW's second half).
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            groceries = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("40.00", past, False, None),
                    ("60.00", past, False, None),
                ],
            )
            gas = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("25.00", past, False, None)],
                name="Gas",
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            body = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            ).data.decode()

            # (i) Each block names itself ONCE, not once per purchase --
            # which is the whole difference between a heading and a trailing
            # fragment on every line.
            assert body.count("Groceries") == 1
            assert body.count("Gas") == 1
            # (ii) Its own subtotal: 40.00 + 60.00 = 100.00, hand-computed.
            assert "$100.00" in body
            # (iii) Every purchase is still its OWN tick (R-EW: the two acts
            # stay independent).
            grocery_ids = [e.id for e in self._entries_of(groceries.id)]
            gas_ids = [e.id for e in self._entries_of(gas.id)]
            for entry_id in grocery_ids + gas_ids:
                assert f'value="{entry_id}"' in body
            # (iv) And they are CONTIGUOUS beneath their own heading, which is
            # what "nested" means and what (i) alone does not prove: a flat
            # list that merely printed each name once would interleave.  Read
            # off the rendered document by position rather than asserted about
            # markup, so a restyle does not fail it and a re-ordering does.
            at = body.index
            assert at("Groceries") < min(
                at(f'value="{i}"') for i in grocery_ids
            )
            assert max(
                at(f'value="{i}"') for i in grocery_ids
            ) < at("Gas") < min(at(f'value="{i}"') for i in gas_ids)

    # ── Plan step X-f2-c2: the door settles ROWS too ──────────────

    def test_one_POST_settles_a_purchase_AND_its_parents_close(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The ORDER of the two writers, graded end to end.

        **This is the control for the defect the ordering exists to prevent.**
        The purchase arm's scope requires a PROJECTED parent, so settling the
        envelope's close FIRST takes that parent out of scope and every ticked
        purchase on it is silently skipped -- reported as success, with the
        purchases still reading outstanding on the next render.  Ticking a whole
        block at once is the ordinary way to walk a statement, not an exotic
        one.

        Shown to FIRE: swapping the two service calls in
        ``record_reconciliation`` leaves ``settled_on`` NULL on the purchase
        while the envelope settles.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [("106.86", past, False, None)],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            entry_id = self._entries_of(txn.id)[0].id

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "entry_ids": [str(entry_id)],
                    "transaction_ids": [str(txn.id)],
                },
            )

            assert response.status_code == 200
            db.session.expire_all()
            assert self._entries_of(txn.id)[0].settled_on == display_today()
            settled = db.session.get(Transaction, txn.id)
            assert settled.settled_on == display_today()
            # A ``purchases`` record stores no figure: the row's own entries
            # state it (plan step X-au-c3).
            assert settled_figure(settled) == Decimal("106.86")

    def test_a_submitted_amount_box_corrects_what_the_row_books(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling **R-FB**, through the real form field.

        The panel renders ``actual_amount-<id>`` for a correctable row; the
        route pairs it back to its own row by NAME rather than by position, so
        this grades the pairing as well as the write.  An envelope with no
        entries is the correctable case (ruling **R-FF**).
        """
        with app.app_context():
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "transaction_ids": [str(txn.id)],
                    f"settled_amount-{txn.id}": "412.09",
                },
            )

            assert response.status_code == 200
            db.session.expire_all()
            settled = db.session.get(Transaction, txn.id)
            assert settled_figure(settled) == Decimal("412.09")
            # The correction is the RECORD's; the plan the row derives from
            # its definition is untouched by it.
            assert resolved_amount(settled) == Decimal("500.00")

    def test_a_malformed_amount_refuses_and_commits_NOTHING(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The panel's rejection surface, and the atomicity behind it.

        A crafted POST can send anything; the browser's ``type="number"`` is a
        convenience, not a boundary.  Without the designed refusal this is an
        unhandled Marshmallow error -- a 500 on a write door.

        **The ticked purchase must not survive either**, which is what makes
        this an atomicity control rather than a validation one: both writers
        run inside one transaction, so a refusal rolls back the half that had
        already succeeded.  It answers 400 WITH the designed-fragment header,
        because htmx leaves a bare 4xx unswapped and the button would read as
        broken.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [("106.86", past, False, None)],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            entry_id = self._entries_of(txn.id)[0].id

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "entry_ids": [str(entry_id)],
                    "transaction_ids": [str(txn.id)],
                    f"settled_amount-{txn.id}": "-12",
                },
            )

            assert response.status_code == 400
            assert response.headers["Shekel-Designed-Fragment"] == "1"
            db.session.expire_all()
            assert self._entries_of(txn.id)[0].settled_on is None
            assert db.session.get(Transaction, txn.id).settled_on is None

    def test_a_tick_that_landed_on_nothing_SAYS_so(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A submission the scope dropped is reported, not rendered as success.

        Both arms drop an out-of-scope id silently -- the set-operation form of
        "404 for not-found and not-yours" -- and the ordinary way to reach it is
        a second device settling the same rows while a statement is being
        walked.  For the purchase arm that hides a column stamp; for this one it
        hides a status change, an amount and a ledger posting, so "saved" would
        be a false sentence about money.

        Shown to FIRE: dropping the notice renders the plain success panel.
        """
        with app.app_context():
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")
            # Settle it out from under the panel, exactly as another device
            # would, then submit the tick the stale panel still shows.
            auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"transaction_ids": [str(txn.id)]},
            )

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"transaction_ids": [str(txn.id)]},
            )

            assert response.status_code == 200
            assert b"had already been settled" in response.data or (
                b"changed while you were reconciling" in response.data
            )

    def test_a_row_INSIDE_a_block_gets_no_box_whatever_its_flag_says(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling **R-FF** is honoured in BOTH renderings of a settle row.

        The panel had two copies of that row and read ``is_correctable`` in only
        one, so a block with purchases printed a static figure whatever the
        producer said.  Through plan step ``X-bi-3e`` this case turned
        purchase-tracking off after the rows carried entries, which made the
        block's settle correctable, and graded that the box appeared.  Since
        ``balance:X-bi-4a`` a row holding purchases takes its figure from them
        whatever its definition's flag says (ruling **R-BAL78**;
        ``settles_from_entries`` reads the purchases alone), so the same act
        leaves the block NOT correctable and BOTH renderings must agree on
        that: no box, in the with-children arm the flag used to reach.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            txn = self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [("50.00", past, False, None)],
            )
            # Purchase-tracking off, entries already recorded.
            txn.template.is_envelope = False
            db.session.commit()
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            body = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            ).data.decode()

            # The entry checkbox proves the block took the WITH-CHILDREN arm,
            # which is the arm that used to ignore the flag.
            assert 'name="entry_ids" value="' in body
            assert f'name="settled_amount-{txn.id}"' not in body

    def test_the_write_door_still_404s_a_kind_this_panel_does_not_serve(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Finding **N-216**'s gate, re-graded now that the door writes more.

        The kind gate ran before the offer set widened, so its own oracle was
        the status code and nothing else -- a loan answered 200 with an empty
        list only because a loan's ``account_anchor_history`` carries just its
        origination row.  X-f2-c2 removes that accident: a loan's projected
        rows would now be offered, so the gate is the only thing refusing.
        """
        with app.app_context():
            # The gate's own predicate is ``has_amortization`` (plus
            # appreciation and the two investment categories), so the fixture
            # picks the type by THAT column rather than by a name.
            loan_type = (
                db.session.query(AccountType)
                .filter_by(has_amortization=True).first()
            )
            loan = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    name="Van Loan",
                    account_type_id=loan_type.id,
                    anchor_balance=Decimal("9000.00"),
                ),
            )
            db.session.commit()

            assert auth_client.get(
                f"/accounts/{loan.id}/reconcile",
            ).status_code == 404
            assert auth_client.post(
                f"/accounts/{loan.id}/reconcile", data={},
            ).status_code == 404


class TestTheCashFigureRendersBesideTheBookedOne:
    """Finding **N-226**'s caption, from the template rather than the producer.

    An envelope books ``sum(entries)`` over EVERY entry, and TWO kinds of those
    do not leave checking at the tick: a card purchase (it leaves through its
    own CC Payback sibling) and, since plan step X-f3b (ruling **R-FM**), one
    that has ALREADY posted on its own recorded day.  So a `$40` debit plus a
    `$60` card purchase is offered at `$100.00` on a screen captioned "tick
    everything your statement shows", against a statement showing `$40`.

    The developer ruled the remedy 2026-08-12: print the cash figure BESIDE
    the booked one.  The producer's half is graded at the service; the branch
    that RENDERS it had no test, so the caption could be deleted or the two
    figures swapped with the suite green.
    """

    _make_grocery_txn_with_entries = (
        TestTheReconcileRoute._make_grocery_txn_with_entries
    )
    _true_up = staticmethod(TestTheReconcileRoute._true_up)

    def test_a_card_purchase_prints_what_the_STATEMENT_shows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """`$100.00` booked, `$40.00` on the statement, and the panel says both.

        Shown to FIRE: deleting the ``cash_amount`` block leaves the panel
        offering `$100.00` against a statement showing `$40.00` with nothing
        explaining the difference.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today, [
                    ("40.00", past, False, None),
                    ("60.00", past, True, None),
                ],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            body = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            ).data.decode()

            assert "$100.00" in body, "the figure a tick BOOKS"
            assert "$40.00 leaves your account now" in body
            assert "on a card or has already posted" in body

    def test_an_envelope_with_NO_card_purchase_prints_one_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The negative control: the caption appears only where it is true.

        Without it, a template that printed the line unconditionally -- or
        printed the booked figure twice -- would satisfy the case above while
        telling every user that part of every envelope had gone elsewhere.

        The single purchase here carries NO posting day, which is what keeps
        this a negative control after plan step X-f3b: one that had already
        posted would legitimately print the caption.
        """
        with app.app_context():
            past = display_today() - timedelta(days=1)
            self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("40.00", past, False, None)],
            )
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            body = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            ).data.decode()

            assert "leaves your account now" not in body
            assert "on a card or has already posted" not in body
