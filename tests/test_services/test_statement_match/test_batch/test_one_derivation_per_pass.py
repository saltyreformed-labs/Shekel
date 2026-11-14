"""One derivation per pass: what an earlier item claims a later one cannot, a sibling write against
a stale price, the scope derived once for the screen and the doors, and the pass's one amount
basis (plan step X-au-j).
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db as _db
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.services import statement_match
from app.services.statement_match import MatchSubmission, Consent, ReviewedBatch

from tests._test_helpers import purchases_of, count_amount_bases

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_purchase,
    a_scope,
    a_transaction,
    an_import,
)
from app.models.amount_ownership import AmountOwnership

from ._helpers import _batch, _match, _creation


class TestOneDerivationStAYSCorrectAcrossThePass:
    """Freshness: what an EARLIER item claims, a later item cannot.

    The scope is derived once for a whole pass -- 3.593 s against 215 acts --
    and holds every row the account could offer, priced.  What it deliberately
    does NOT hold is which of them a match has claimed, because that is exactly
    what the pass changes.  These are the cases that fail against a scope with
    the claims baked in.
    """

    def test_a_second_item_cannot_name_a_row_the_first_matched(
        self, app, db, seed_user,
    ):
        """The same row, twice in one pass.

        ``propose`` partitions its rows, so the screen cannot produce this --
        but a crafted submission can, and ``uq_statement_match_members_
        entry`` -- the row's one payment, since plan step
        ``credit_card:CC-5-4a-2`` dropped the row column's own index -- would
        answer it with an ``IntegrityError`` after the first item had already
        moved a day.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            first = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            second = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
                sequence_in_group=1,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
                status=StatusEnum.DONE, settled_on=day,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[first], transactions=[row]),
                _match(seed_user, lines=[second], transactions=[row]),
            ])

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert "no longer available" in outcome.refused[0].reason
            assert db.session.query(StatementMatch).count() == 1

    def test_a_creation_cannot_target_an_envelope_a_match_claimed(
        self, app, db, seed_user,
    ):
        """The collision the developer ruled on, in the order they ruled.

        4 envelopes on the developer's own statement are both named by a
        proposal and offered as a destination for a creatable line, so 15 of
        the 91 lines land here.  Matches run first and the CREATION is refused:
        the proposal explains money the records already hold against a line the
        bank showed, where the line can be re-aimed next pass.

        It also has to be refused for a money reason.  An envelope's cash leg
        already covers its own outstanding purchases, so a match on the
        envelope AND a new purchase inside it would count that purchase twice.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00", is_envelope=True,
            )
            matched_line = a_bank_line(
                seed_user, statement, amount="-500.00", posted_on=day,
            )
            swipe = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
                sequence_in_group=1,
            )

            outcome = _batch(
                seed_user,
                matches=[_match(
                    seed_user, lines=[matched_line], transactions=[envelope],
                )],
                creations=[_creation(
                    seed_user, swipe, transaction_id=envelope.id,
                )],
            )
            db.session.flush()

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert "not one this purchase can go into" in (
                outcome.refused[0].reason
            )
            assert outcome.refused[0].line_ids == (swipe.id,)
            # No PURCHASE was created; the envelope the match closed holds
            # only the covering movement the seam wrote for its close (plan
            # step X-bi-3a).
            assert purchases_of(envelope) == []

    def test_a_matched_purchase_blocks_a_later_match_on_its_PARENT(
        self, app, db, seed_user,
    ):
        """The interaction that makes a shared PRICE safe at all.

        Through plan step ``balance:X-bi-4a``'s first cut a candidate's
        figure was ``gross - card entries - posted purchases``, so the way
        one item could move a figure another item named was by posting or
        adding a purchase under it -- a parent and its own child -- and
        matching the purchase first dropped the envelope's leg by the
        purchase's amount, so the second item would have been accepted
        against a price the app no longer held.  Under ruling **R-BAL81** an
        envelope holding purchases is worth ``0`` to the offer in EITHER
        order, so the second item is refused as unavailable rather than
        re-priced; what this case still grades is that the pass refuses it
        (a scope with the claims baked in would offer it), and the re-pricing
        of a row another item moves is
        :class:`TestASIBLINGWriteCannotBookAgainstAStalePrice`'s.

        **The sentence is the availability one, not the double-count one.**
        A row worth nothing can match no bank line, so ``resolve_rows``
        refuses it as unavailable.  ``record_match``'s guard against an
        envelope named beside its own purchase, which this paragraph said was
        never asked here, is deleted since plan step
        ``credit_card:CC-5-4a-5``, leaf 5c-2c-1 (finding **CC-386**, ruling
        **R-CC144**); its last live shape -- an envelope matched EMPTY,
        reverted, then given a purchase -- was a false refusal and matches
        since leaf 5c-2b (finding **CC-385**, ruling **R-CC143**;
        ``test_accept/test_what_the_door_refuses.py
        ::TestEveryOtherRefusalFires::
        test_a_purchase_under_a_REVERTED_matched_envelope_matches``).
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            envelope = a_transaction(
                seed_user, name="Groceries", amount="100.00", is_envelope=True,
            )
            purchase = a_purchase(
                seed_user, envelope, amount="25.00", purchased_on=day,
            )
            child_line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
            )
            parent_line = a_bank_line(
                seed_user, statement, amount="-100.00", posted_on=day,
                sequence_in_group=1,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[child_line], entries=[purchase]),
                _match(
                    seed_user, lines=[parent_line], transactions=[envelope],
                ),
            ])

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert "no longer available" in outcome.refused[0].reason
            assert envelope.settled_on is None, (
                "the envelope was matched against a figure the pass had "
                "already moved"
            )


    def test_an_envelope_item_is_refused_and_its_purchase_item_lands(
        self, app, db, seed_user,
    ):
        """The other direction, and under ruling R-BAL81 it cannot start.

        Through plan step ``balance:X-bi-4a``'s first cut an envelope matched
        FIRST kept its figure, so re-pricing had nothing to say about the
        purchase inside it, and what refused the second item was the guard
        that knew WHY (``_reject_parent_and_its_own_purchase``: the envelope's
        cash leg covered its own outstanding purchases, so naming both counted
        that purchase in two terms; measured on a production clone at plan
        step X-f6a-2, two matched line-sets worth `-284.33` backed by
        `-265.69` of ledger, the projected balance `$18.64` high).  A row that
        settles from its purchases is worth ``0`` to the offer now and is not
        a candidate, so the FIRST item is the one refused -- as unavailable,
        by ``_resolve`` -- and the purchase item lands on its own.  The guard
        was never reached from this shape after that ruling, and it is deleted
        since plan step ``credit_card:CC-5-4a-5``, leaf 5c-2c-1 (finding
        **CC-386**, ruling **R-CC144**).
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            envelope = a_transaction(
                seed_user, name="Groceries", amount="300.00", is_envelope=True,
            )
            purchase = a_purchase(
                seed_user, envelope, amount="25.00", purchased_on=day,
            )
            parent_line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
            )
            child_line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
                sequence_in_group=1,
            )

            outcome = _batch(seed_user, matches=[
                _match(
                    seed_user, lines=[parent_line], transactions=[envelope],
                ),
                _match(seed_user, lines=[child_line], entries=[purchase]),
            ])

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert outcome.refused[0].line_ids == (parent_line.id,)
            assert "no longer available" in outcome.refused[0].reason
            assert purchase.settled_on == day
            assert envelope.settled_on is None


class TestASIBLINGWriteCannotBookAgainstAStalePrice:
    """The counterexample that refuted this step's first safety argument.

    That argument was: one act can only move a figure another act names by
    adding a purchase to that row or posting one under it, which makes the two
    an envelope and its own child -- and
    ``_reject_parent_and_its_own_purchase`` refused exactly that.  **Measured
    false by adversarial financial review 2026-08-19**, with a booked figure.
    That guard is deleted since plan step ``credit_card:CC-5-4a-5``, leaf
    5c-2c-1 (finding **CC-386**, ruling **R-CC144**): under ruling **R-BAL81**
    a row that settles from its purchases is worth ``0`` to the offer, so the
    pair it refused is refused as no longer available instead.

    ``entry_service.update_entry`` -- which every matched PURCHASE goes through
    -- calls ``entry_credit_workflow.sync_entry_payback``, and that WRITES the
    envelope's CC Payback figure (its ``estimated_amount``, through
    ``amount_ownership.state_own_amount``) to the sum of its card entries.  A
    payback is a transaction on the SAME account, so it is a candidate priced
    off that column; and it is the purchase's SIBLING under one envelope, not
    its parent, so a guard keyed on a parent and its own child could never
    have seen the relation.

    Against a once-derived price the second act was accepted at the stale
    figure: the ledger booked `$50.00` for a `-$60.00` bank line and the
    account read **`$10.00` high**.  The answer is that every act re-prices the
    rows it names (:func:`~app.services.statement_match._valuation.repriced`),
    which is total where an enumeration of sibling writers is one writer from
    being wrong again, and then refuses a row that moved after the screen was
    reviewed (``_resolve._reject_moved_since_review``, finding **N-336**).  So
    the case pins: the purchase's match lands, and the payback's is refused as
    "reviewed against different figures", naming `-60.00` and `-50.00`, with
    the payback left unsettled.

    **Both acts are ordinary screen proposals in the same sweep class**, so one
    click of "tick all that mark a row as having happened" plus Apply submits
    them together.
    """

    @staticmethod
    def _drifted_payback(db, seed_user):
        """Stage an envelope, a card entry, its payback, and a debit purchase.

        The payback's own figure is moved off the sum of its card entries --
        which is what an owner does when they correct a projected CC Payback to
        what their card statement says, through the ordinary transaction edit
        door.  It is the state that arms the defect.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", amount="300.00", is_envelope=True,
        )
        a_purchase(
            seed_user, envelope, amount="50.00", description="Card",
            purchased_on=seed_user["bootstrap_period"].start_date,
            is_credit=True,
        )
        debit = a_purchase(
            seed_user, envelope, amount="25.00", description="Aldi",
            purchased_on=seed_user["bootstrap_period"].start_date,
        )
        # **Built the way ``credit_workflow._create_payback`` builds one**, and
        # it has to be: ``ck_transactions_one_pricing_link`` admits at most one
        # of ``template_id`` / ``transfer_id`` / ``credit_payback_for_id``, so
        # the sibling builder's template makes a payback the database refuses.
        # A fixture the app could not produce would grade an unreachable case.
        payback = Transaction(
            account_id=seed_user["account"].id,
            template_id=None,
            user_id=seed_user['bootstrap_period'].user_id,
            pay_period_id=seed_user["bootstrap_period"].id,
            scenario_id=seed_user["scenario"].id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            name="CC Payback: Groceries",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            # DRIFTED off the sum of its card entries, which is what an owner
            # correcting a projected payback to their card statement produces.
            amount_ownership=AmountOwnership.own(Decimal("60.00")),
            credit_payback_for_id=envelope.id,
        )
        _db.session.add(payback)
        _db.session.flush()
        return envelope, debit, payback

    def test_the_stale_sibling_is_REFUSED_rather_than_booked(
        self, app, db, seed_user,
    ):
        """The whole finding, as one pass."""
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            _, debit, payback = self._drifted_payback(db, seed_user)
            debit_line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
            )
            payback_line = a_bank_line(
                seed_user, statement, amount="-60.00", posted_on=day,
                sequence_in_group=1,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[debit_line], entries=[debit]),
                _match(
                    seed_user, lines=[payback_line], transactions=[payback],
                ),
            ])
            _db.session.flush()

            assert outcome.applied_count == 1, (
                "the purchase match must land -- otherwise the second act was "
                "never offered the stale price and this grades nothing"
            )
            assert outcome.refused_count == 1
            # The FIGURES, because that is what went wrong: the pass moved the
            # payback to 50.00 and the bank line says 60.00.
            #
            # **The refusal's VEHICLE moved at plan step
            # ``bank_import:X-f6d-3`` and the subject did not.**  It used to be
            # ``_reject_unbalanced`` -- the re-priced row no longer summed to
            # its line -- and it is now the N-336 guard, which fires first
            # because the row moved AFTER this item was reviewed.  That is the
            # better diagnosis of the same fact: the unbalance was the SYMPTOM
            # and "the row you reviewed at -60.00 now stands at -50.00" is the
            # cause, named.  ``_reject_unbalanced`` still owns the case where
            # nothing moved and the two sides simply disagree -- the `$0.05`
            # payroll shortfall (finding **N-239**), which
            # ``test_accept.test_what_is_recorded``'s own
            # ``test_a_five_cent_shortfall_is_refused`` fires on.
            assert "reviewed against different figures" in (
                outcome.refused[0].reason
            )
            assert "-60.00" in outcome.refused[0].reason
            assert "-50.00" in outcome.refused[0].reason
            assert payback.settled_on is None, (
                "a -60.00 bank line was explained by a row worth 50.00"
            )


class TestTheScopeIsDerivedONCE:
    """The step's own measurement, asserted rather than described.

    12.88 minutes of derivation became 5.80 s because the account is derived
    once per PASS rather than once per ACT.  A later change that gave a door
    its own scope again would restore the cost silently -- every other test
    in this package would still pass, and the only thing that would move is the wall clock.
    """

    def test_the_offer_set_is_built_once_however_many_items_run(
        self, app, db, seed_user, monkeypatch,
    ):
        """The firing control for the whole step."""
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            items = []
            for index in range(4):
                line = a_bank_line(
                    seed_user, statement, amount=f"-{100 + index}.00",
                    posted_on=day, sequence_in_group=index,
                )
                row = a_transaction(
                    seed_user, name=f"Bill {index}", amount=f"{100 + index}.00",
                    status=StatusEnum.DONE, settled_on=day,
                )
                items.append(_match(
                    seed_user, lines=[line], transactions=[row],
                ))

            # **The counter goes in AFTER the fixture is staged**, and that is
            # a property of the subject rather than a convenience: what is
            # under test is how many times the BATCH derives the account, and
            # ``_match`` derives one of its own to read the reviewed state a
            # tick carries (plan step ``bank_import:X-f6d-3``) exactly as the
            # SCREEN does.  Counting the screen's derivation as the door's
            # would grade the fixture.
            calls = []
            real = statement_match._scope.candidates_for

            def _counted(account_id, calendar, basis):
                calls.append(account_id)
                return real(account_id, calendar, basis)

            monkeypatch.setattr(
                statement_match._scope, "candidates_for", _counted,
            )

            outcome = _batch(seed_user, matches=items)

            assert outcome.applied_count == 4
            assert calls == [seed_user["account"].id], (
                "the account was derived once per ACT again, which is the "
                "12.88 minutes finding N-306 measures"
            )


def test_a_scope_serves_the_screen_and_the_doors_alike(app, db, seed_user):
    """One derivation, two consumers, and they must agree about the account.

    The route builds a scope, renders the screen from it and applies the pass
    against it.  A reader and a writer disagreeing about what an account holds
    is the class of defect this package's one-scope rule exists to remove.
    """
    with app.app_context():
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=day,
        )
        a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=day + timedelta(days=3),
        )
        scope = a_scope(seed_user)

        review = statement_match.review_set(scope)

        assert len(review.proposals) == 1
        proposal = review.proposals[0]
        outcome = statement_match.apply_reviewed(ReviewedBatch(
            consent=Consent.TICKED,
            incomes=(),
            skips=(),
            matches=(MatchSubmission(
                line_ids=frozenset(
                    bank.line_id for bank in proposal.lines
                ),
                rows=frozenset(
                    statement_match.as_reviewed(row) for row in proposal.rows
                ),
            ),),
            creations=(),
        ), scope)

        assert outcome.applied_count == 1
        assert outcome.corrected_count == 1
        assert line.id in {
            item.line_ids[0] for item in outcome.applied
        }


class TestThePassHoldsONEAmountBasis:
    """Plan step **X-au-j**, finding **N-309**: the derivations are the PASS's.

    An :class:`~app.services.cash_ledger.AmountBasis` holds the owner's live
    derivations -- the paycheck engine run over the whole pay-period set, and
    each loan's P&I, payment day and escrow history.  ``amount_basis``'s own
    docstring says calling those per row is finding **N-228**, and N-309
    measured this pass doing exactly that: **609 salary-pricing and 609
    loan-pricing constructions** over 825 candidates, `4.7 s` to render, with
    the accept door paying it all again.

    It is :class:`TestTheScopeIsDerivedONCE` one column over and it fails the
    same way: a later change that let a producer build its own would restore
    the cost in SILENCE, because every figure would still be right.  Only the
    wall clock moves, which is why the count is asserted and the timing is not.
    """

    def test_one_basis_serves_every_candidate_the_pass_prices(
        self, app, db, seed_user, monkeypatch,
    ):
        """The firing control: ONE construction, however many rows are priced.

        **The rows are PROJECTED deliberately, and that is what makes this
        control sharp.**  A settled row is valued at its covering movement
        (``status_seam.covered_cash_leg``, ruling **R-BAL81**) and never
        reaches the resolver, so a pass of settled rows builds one basis
        whether or not this step shipped -- the assertion would hold over a
        broken tree.  Each of these prices.
        """
        with app.app_context():
            for index in range(8):
                a_transaction(
                    seed_user, name=f"Bill {index}",
                    amount=f"{100 + index}.00",
                )
            _db.session.commit()

            built = count_amount_bases(monkeypatch)
            scope = statement_match.ReviewScope.build(
                seed_user["user"].id, seed_user["account"].id,
            )

            assert len(scope.candidates.rows) >= 8, (
                "the pass must price several rows -- otherwise one basis and "
                "one per row are the same number and this grades nothing"
            )
            assert len(built) == 1, (
                f"the pass built {len(built)} amount bases for "
                f"{len(scope.candidates.rows)} candidates; X-au-j makes it one"
            )
            assert built == [
                (seed_user["user"].id, seed_user["scenario"].id),
            ]

    def test_the_accept_door_prices_against_the_PASS_basis(
        self, app, db, seed_user, monkeypatch,
    ):
        """And the accept door builds none of its own either.

        ``repriced`` re-derives every row an act names -- that is finding
        N-306's fix and it stays -- but re-deriving a ROW is not re-deriving
        the OWNER: N-309's measurement says the accept door paid for the whole
        salary and loan derivation again, per act, on top of the pass that had
        just built one.  Asserted separately from the scope build above,
        because the two are different callers and a change could restore the
        cost at either.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            items = []
            for index in range(4):
                line = a_bank_line(
                    seed_user, statement, amount=f"-{100 + index}.00",
                    posted_on=day, sequence_in_group=index,
                )
                # PROJECTED, for the reason the control above states: a
                # settled row is valued from its record and never reaches the
                # resolver, so a batch of settled rows would build one basis
                # whether or not this step shipped.  A first draft of THIS
                # case used settled rows and passed against a planted defect.
                row = a_transaction(
                    seed_user, name=f"Bill {index}", amount=f"{100 + index}.00",
                )
                items.append(_match(
                    seed_user, lines=[line], transactions=[row],
                ))

            built = count_amount_bases(monkeypatch)
            outcome = _batch(seed_user, matches=items)

            assert outcome.applied_count == 4
            # ONE for the pass, plus ONE per SETTLED ROW -- and the second term
            # is a different question that this step does not answer.  A settle
            # is a single-row WRITE and resolves its own basis by design
            # (``transaction_service._settle.settle_transaction``: "ONE
            # settle resolves ONE basis"); what X-au-j fixes is the READ under
            # it, which used to build one per row PRICED on top of all of that.
            #
            # **Counted in ROWS, not matches**, because those are two different
            # numbers and only the fixture makes them equal: each match here
            # names exactly one transaction.  A first version asserted
            # ``1 + outcome.applied_count`` and would have gone silently wrong
            # -- while still printing the per-settle rule -- the moment a case
            # gave one match two rows (adversarial review 2026-08-20).
            settled_rows = sum(
                1 for item in items for row in item.rows
                if row.kind is statement_match.RowKind.TRANSACTION
            )
            assert settled_rows == 4, "the fixture pins one row per match"
            assert len(built) == 1 + settled_rows, (
                f"{len(built)} amount bases were built settling "
                f"{settled_rows} rows; the pass holds ONE for every row it "
                "prices, and each settle resolves its own"
            )
