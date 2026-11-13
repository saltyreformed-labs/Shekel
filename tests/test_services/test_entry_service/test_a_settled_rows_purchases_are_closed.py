"""A settled row's purchases are closed, and the helper that marks a purchase settled guards its
own precondition.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service
from app.exceptions import ValidationError
from app import ref_cache
from app.enums import StatusEnum
from app.services import status_seam
from app.services.row_valuation import settled_figure
from app.utils.dates import display_today
from tests._test_helpers import (
    typed,
    account_never_asserted,
    an_entered_day,
    purchases_of,
    reassert_balance_on,
    settle_instant_on,
    settlement_if_settling,
)
from tests._test_helpers import mark_purchase_settled

from ._helpers import _make_entry, _outstanding_debit


class TestTheMarkPurchaseSettledHelperGuardsItsPrecondition:
    """``tests._test_helpers.mark_purchase_settled`` -- the helper's own guard.

    A TEST helper, graded here because its failure mode is what finding
    N-132 / R8 is about and an untested failure mode is one a future edit
    weakens for free.  It is the successor to ``add_entry(..., is_cleared=True)``,
    and the flag it replaced was an UNCONDITIONAL claim that a purchase was
    inside the anchor -- so fixtures set it on accounts whose only assertion
    PREDATED the purchase by months, a state production cannot reach, and those
    fixtures passed for years while silently no longer discriminating the case
    they named.

    The helper does what the flag did AND asserts the precondition the flag let
    fixtures skip.  These are its two refusals, each with the failure message
    checked, because a helper that failed with a bare ``AssertionError`` would
    send the next reader hunting through the balance fold instead of at the
    fixture.
    """

    def test_it_refuses_when_the_assertion_predates_the_purchase(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The N-132 shape itself: settled AFTER the account's latest assertion.

        The account asserts its balance for the first period's start day; a
        purchase settled a week later is not inside it, however the fixture is
        spelled, so a suite expecting the settled bucket is asking for a state
        its own account cannot be in.  The message names BOTH days so the fix
        -- assert again, or accept the outstanding bucket -- is visible without
        reading the helper.

        **The asserted day is stated here rather than inherited** (plan step
        X-f3c-2c): the seeded account carries only its ORIGINATION assertion,
        dated on the bootstrap day before the calendar, so a case that turns on
        which day was last asserted says which day that is.
        """
        with app.app_context():
            reassert_balance_on(
                db.session, seed_user["account"],
                settle_instant_on(seed_periods[0].start_date),
            )
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            db.session.commit()
            too_late = seed_periods[0].start_date + timedelta(days=7)

            with pytest.raises(AssertionError) as excinfo:
                mark_purchase_settled(
                    db.session, seed_user["account"], entry,
                    settled_on=too_late,
                )

            message = str(excinfo.value)
            assert too_late.isoformat() in message
            assert seed_periods[0].start_date.isoformat() in message
            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_it_refuses_when_the_account_has_asserted_nothing(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """No assertion at all means there is nothing to be inside of.

        ``ReconciledThrough.covers`` is total in both the argument and the
        boundary and answers False for a missing assertion, so a fixture that skipped this check would
        build a purchase the projection reads as outstanding while the test
        asserted the settled figure -- failing later, in the balance, with no
        indication that the ACCOUNT was the problem.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            # **Built rather than emptied** (plan step X-f3c-2c): an assertion
            # is append-only at the database tier, so an account that has
            # asserted nothing is one the assertion factory never touched.  The
            # guard reads the ACCOUNT it is handed, which is what lets this
            # case grade it against an account other than the purchase's.
            account = account_never_asserted(
                seed_user, db.session, name="Silent",
                opening_equity=Decimal("0.00"),
            )
            db.session.commit()

            with pytest.raises(AssertionError) as excinfo:
                mark_purchase_settled(db.session, account, entry)

            assert "has asserted no balance" in str(excinfo.value)

    def test_it_records_the_day_when_the_assertion_covers_it(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The positive control: it still does what the flag did.

        Without this the two refusals above would be satisfied by a helper that
        refused everything.  A purchase settled on the account's own asserted
        day passes, and ``settled_on`` defaults to the purchase's own
        ``purchased_on`` -- "it posted the day I bought it", the shape a
        same-day debit has.  The asserted day is stated here for the reason the
        case above states it (plan step X-f3c-2c).
        """
        with app.app_context():
            reassert_balance_on(
                db.session, seed_user["account"],
                settle_instant_on(seed_periods[0].start_date),
            )
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(
                txn, seed_user, purchased_on=seed_periods[0].start_date,
            )
            db.session.commit()

            mark_purchase_settled(db.session, seed_user["account"], entry)
            db.session.commit()

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on == seed_periods[0].start_date


class TestASettledRowsPurchasesAreClosed:
    """Finding **N-229**, widened to the settled BAND at plan step X-au-c3.

    A settled row's money has MOVED, and every entry door would RE-COST it:
    adding a purchase grows what the row cost, deleting one shrinks it, and
    re-pricing one does either.  So all three refuse on Paid and Received as
    well as on the terminal ``Settled`` (developer ruling, 2026-08-17).

    **The refusal is FIELD-AWARE, and the one field it admits is
    ``settled_on``** -- the day the BANK took a purchase.  That is an
    observation about money which has ALREADY moved rather than a restatement of
    how much moved: it re-dates that purchase's cash and changes no total.  The
    last two cases below are that half of the rule; ``_COST_BEARING_FIELDS`` is
    where it is stated.

    **What decides the width is carry-forward.**  It rolls an envelope's unspent
    remainder into the NEXT period's row and then settles the source at what was
    spent -- so a purchase recorded against the closed source afterwards raises
    its cost while the later row still holds the rolled-forward money, and the
    same dollars are counted twice.  A forgotten purchase belongs in the period
    that now holds the money; the way to record it here is to put the row back
    to Projected, add it, and close the row again.

    **It was the ARCHIVE status alone until this step**, because a Paid envelope
    re-derived its figure from its entries and late-posting purchases were
    therefore meant to land on one.  That re-derivation is deleted: it moved
    money in the OPTIMISTIC direction with no human act, crashing a ``$500``
    close to ``$50`` on the first back-filled purchase and handing ``$450`` of
    already-spent money back to the projection.

    Before plan step X-ap a new purchase against an ARCHIVED row was ACCEPTED,
    persisted, and half-processed: ``actual_amount`` was not recomputed (that
    half graded ``is_done`` -- exactly Paid) while the postings WERE reconciled
    (that half graded the settled BAND), so the ledger moved and the figure it
    was derived from did not.
    """

    @staticmethod
    def _close(txn):
        """Move *txn* Projected -> Paid through the real seam."""
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.DONE),
            settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
        )
        db.session.flush()

    def test_update_is_refused(self, app, db, seed_user, seed_entry_template):
        """An existing purchase on a settled row cannot be re-priced.

        The row was closed carrying a $50.00 purchase; re-pricing it to
        $500.00 would rewrite what the books already say the row cost.

        Specimen was the terminal ``Settled`` ARCHIVE until plan step
        **balance:X-am** deleted that status.  ``_reject_settled_parent`` reads
        the BAND and never the member -- that is this class's whole subject --
        so the case moves to Paid unchanged.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            entry = _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)

            with pytest.raises(ValidationError, match="has settled"):
                entry_service.update_entry(
                    entry.id, seed_user["user"].id, figure=typed(Decimal("500.00")),
                )

            # No rollback: the guard runs BEFORE the setattr loop, so a refused
            # call stages nothing -- which is the property being asserted.
            assert entry.amount == Decimal("50.00")

    def test_delete_of_an_undated_purchase_is_admitted(
        self, app, db, seed_user, seed_entry_template,
    ):
        """An UNDATED purchase CAN be removed from a settled ``purchases`` row.

        **The refusal's SENTENCE moved twice and then its arm was lifted.**
        Plan step ``bank_import:X-f6f`` gave the ARCHIVE one of its own; plan
        step **balance:X-am** deleted the archive and that sentence; and plan
        step ``balance:X-bi-4a`` lifted the arithmetic arm itself (ruling
        **R-BAL77**): removing an UNDATED debit purchase used to shrink what
        the row recorded as costing on a past day, because the row's own leg
        booked it on the close day.  A plan row books nothing of its own
        now, so the removal removes a movement in flight and nothing else --
        the ordinary direction of every other removal.  The STORED-figure
        refusal beside it stands (``test_a_PAID_row_refuses_a_late_purchase``
        and its siblings).
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            entry = _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)
            entry_id = entry.id

            entry_service.delete_entry(entry_id, seed_user["user"].id)
            db.session.flush()
            assert db.session.get(TransactionEntry, entry_id) is None

    def test_a_PAID_row_refuses_a_late_purchase(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The refusal is the settled BAND, not the archive status alone.

        **This asserted the opposite until plan step X-au-c3**: a Paid envelope
        accepted a late purchase and re-derived its figure from it, so a
        ``$50.00`` purchase recorded after a close at the row's ``$500.00``
        estimate re-priced the row to ``$50.00`` -- putting ``$450`` of
        already-spent money back in the projection with no human act, and
        double-counting it against the leftover carry-forward had already rolled
        into the next period.

        The row is left exactly as the close left it: worth what it recorded,
        with no purchase persisted.  Shown to FIRE: narrowing the guard back to
        ``is_archived`` let the entry through -- that predicate is itself
        deleted at plan step **balance:X-am** with the status it named, so the
        narrowing this case refuses is no longer expressible.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            self._close(txn)
            recorded_before = settled_figure(txn)

            with pytest.raises(ValidationError, match="has settled"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("50.00")),
                        description="Late purchase",
                        purchased_on=display_today(),
                    ),
                )

            assert settled_figure(txn) == recorded_before
            # No purchase landed; the covering movement is not one (X-bi-3a).
            assert purchases_of(txn.id) == []

    def test_the_BANK_POSTING_DAY_is_still_recordable(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The one edit a closed row admits, and the reason the guard is FIELD-AWARE.

        **Everything above is an argument about what the row COST.  The day the
        BANK took a purchase is not that** (developer ruling, 2026-08-17).
        Recording it changes no total: a purchase is a movement of its own
        and the row books nothing (ruling **R-BAL80**), so what the family
        moves is the same before and after -- the purchase leaves the
        in-flight tier for the dated stream.  (Through plan step ``X-bi-3e``
        the row's own leg subtracted every POSTED purchase from the close, so
        the two summed to the row's whole debit either way.)  What moves is
        the DAY, which is what a paper statement is reconciled against.

        Refusing it would strand already-spent money on the day the envelope
        happened to be closed, with no door to correct it: measured on the
        2026-08-17 production dump, 28 closed envelopes hold 61 debit purchases
        carrying no posting day, ``$4,360.07`` between them.

        That split is this step's own three-lifetime model read one level down.
        A purchase's amount is WHAT MOVED and its posting day is an ASSERTION
        about when -- the same two facts ``settled_amount`` and ``settled_on``
        are on the parent, with the same answer: the assertion may be recorded,
        corrected and withdrawn long after the figure is final.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            entry = _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)
            recorded_before = settled_figure(txn)
            posted_on = display_today()

            entry_service.update_entry(
                entry.id, seed_user["user"].id, settle_day=an_entered_day(posted_on),
            )
            db.session.flush()

            assert entry.settled_on == posted_on
            # What the row COST is untouched -- the whole point of admitting
            # this field and no other.
            assert settled_figure(txn) == recorded_before

            # And it can be WITHDRAWN again, which is what "the statement does
            # not actually show it" means.
            entry_service.update_entry(
                entry.id, seed_user["user"].id, settle_day=None,
            )
            db.session.flush()
            assert entry.settled_on is None
            assert settled_figure(txn) == recorded_before

    def test_a_posting_day_edit_that_also_RE_PRICES_is_refused(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The guard grades the whole submission, not its most innocent field.

        A caller cannot smuggle a re-price past a closed row by sending it
        alongside the one field the row admits.  ``_reject_settled_parent``
        refuses when ANY cost-bearing field is present, so the mixed submission
        is refused whole and neither field lands.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            entry = _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)

            with pytest.raises(ValidationError, match="has settled"):
                entry_service.update_entry(
                    entry.id, seed_user["user"].id,
                    settle_day=an_entered_day(display_today()),
                    figure=typed(Decimal("500.00")),
                )

            # The guard runs BEFORE the setattr loop, so nothing is staged.
            assert entry.amount == Decimal("50.00")
            assert entry.settled_on is None

    def test_the_PURCHASE_DAY_is_recordable_on_a_closed_row(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The second field the guard admits, and the one it was WRONG about.

        ``purchased_on`` is the BUDGET clock -- the day the purchase was made.
        A census of every reader in ``app/`` finds no money rule among them:
        ``Transaction.entries``' ordering, the out-of-period WARNING, the
        reconcile panel's sort and its offer predicate, the matcher's
        ``expected_on``, and three template fields.  What the row COST is
        ``amount`` and ``is_credit``, and neither moves here.

        **It was refused until plan step ``bank_import:X-f6a-3b``**, because
        :data:`~app.services.entry_service._doors._COST_BEARING_FIELDS` was
        spelled ``_UPDATABLE_FIELDS - {"settled_on"}`` -- a set defined by what
        it excludes, which silently claimed two fields nothing prices.  Ruling
        **R-FW**'s purchase-day correction submits this field beside
        ``settled_on`` in ONE ``update_entry`` call, so on the developer's own
        statement 13 of the 15 corrections the review screen offered were
        refused: the screen rendered an Accept button that could never succeed.

        Shown to FIRE: restoring ``purchased_on`` to the cost-bearing set makes
        this raise.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            made_on = display_today() - timedelta(days=5)
            entry = _make_entry(
                txn, seed_user["user"], amount="50.00", purchased_on=made_on,
            )
            self._close(txn)
            recorded_before = settled_figure(txn)

            corrected = made_on - timedelta(days=3)
            entry_service.update_entry(
                entry.id, seed_user["user"].id, purchased_on=corrected,
            )
            db.session.flush()

            assert entry.purchased_on == corrected
            # What the row COST is untouched, which is the whole reason the
            # field is admitted.
            assert settled_figure(txn) == recorded_before

    def test_the_DESCRIPTION_is_editable_on_a_closed_row(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The other field the corrected set stopped claiming.

        Nothing outside ``app/templates/`` reads it, so renaming a purchase on
        a closed row changes no figure and no derivation.  It was refused for
        the same reason ``purchased_on`` was: the set was defined by
        subtraction rather than by what prices a row.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            entry = _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)
            recorded_before = settled_figure(txn)

            entry_service.update_entry(
                entry.id, seed_user["user"].id, description="Food Lion",
            )
            db.session.flush()

            assert entry.description == "Food Lion"
            assert settled_figure(txn) == recorded_before
