"""Account route tests: when a true-up prompts to reconcile, and the reconcile route's transfer
arm.
"""

import re
from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.utils.dates import display_today
from tests._test_helpers import (
    create_transfer,
    open_books_before_the_first_assertion,
    transfer_side_figure,
    transfer_side_record,
)
from app.models.transfer import Transfer
from app.services import account_service, cash_ledger
from app.services.row_valuation import settled_contribution
from app.services.transfer_legs import leg_of, parent_of

from ._helpers import _ACK_SWAP, _TestTheReconcileRouteMembers


class TestTheReconcileRoute(_TestTheReconcileRouteMembers):
    """The prompt after a true-up; members on _TestTheReconcileRouteMembers."""

    @pytest.mark.parametrize(
        "revert",
        [None, "dashboard", "accounts", "investment", "cash"],
        ids=["grid", "dashboard", "cockpit", "investment-hero", "cash-hero"],
    )
    def test_a_back_dated_true_up_does_not_prompt_to_reconcile(
        self, app, auth_client, seed_user, seed_periods_today, revert,
    ):
        """The prompt follows the COVERAGE BOUNDARY, not the click.

        **This is the money defect a neutral adversarial review of plan step
        X-f1c4c reproduced end to end.**  The prompt is keyed on
        ``cash_ledger.reconciled_through`` -- ``MAX(observed_on)`` -- which a
        back-dated assertion does not move.  Before that step every cash true-up
        stamped today, so the submitted day and that maximum were the same value
        by construction; a user-supplied day decoupled them and nothing
        re-coupled them.

        What that cost: submit an OLD statement's balance and the modal opens
        against the LATEST assertion's day, offering purchases made after that
        statement.  Ticking one is a settlement the user cannot have observed,
        and it releases the envelope budget being held for it -- reproduced at
        ``$120.00`` of projected checking balance on money that never left the
        bank.

        The purchase here is dated AFTER the back-dated statement day precisely
        so it is one the statement could not show.

        **Parametrized over all five openers**, because the success response
        BRANCHES on the surface token and a re-review proved the branch
        ungraded: with both original cases submitting no ``revert``, a mutant
        that kept the pre-fix prompt on the ``accounts`` / ``investment`` /
        ``cash`` arm passed the whole 7,843-test suite.  ``cash`` is the cash
        detail page -- the surface a user would most plausibly enter an old
        statement from, and the one that already carries the reconcile section.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()
            statement_day = today - timedelta(days=20)
            # An outstanding purchase the modal WOULD offer.
            self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("120.00", today - timedelta(days=2), False, None)],
            )
            # Establish a recent coverage boundary, so the back-dated
            # submission below is genuinely not the account's latest day.
            self._true_up(auth_client, acct_id, "1000.00")

            response = self._true_up(
                auth_client, acct_id, "2500.00",
                observed_on=statement_day, revert=revert,
            )

            html = response.data.decode()
            assert "data-modal-auto-show" not in html, (
                "a back-dated assertion reconciles nothing new, so the prompt "
                "must not ask against a statement it does not describe"
            )
            # The user is TOLD the back-dated write landed, because the cell
            # re-renders from the GOVERNING assertion and would otherwise be
            # indistinguishable from having done nothing.
            #
            # **On ALL FIVE surfaces since plan step X-f1e3.**  It used to ride
            # the per-surface ``#anchor-as-of`` caption and so reached one:
            # three surfaces carry no such element and were skipped outright,
            # and the dashboard's copy was destroyed by the ``balanceChanged``
            # refresh this same response fires (finding N-199).  The mount
            # asserted below is what makes five a property of the structure.
            #
            # **It TARGETS the mount rather than carrying its id, since plan
            # step X-f2-b** (finding N-206).  The swap was an outerHTML
            # replacement of ``#anchor-ack-mount``, so a second back-dated save
            # inside the 8s autohide window destroyed the first toast; it
            # APPENDS now.  What is graded is unchanged -- the acknowledgement
            # reaches the ONE global mount rather than a per-surface element --
            # and the marker for it is the swap attribute that names the mount.
            assert _ACK_SWAP in html, (
                "the acknowledgement must ride the global base.html mount, "
                "not a per-surface element only some surfaces carry"
            )
            assert 'id="anchor-ack-mount"' not in html, (
                "the fragment must APPEND into the mount, never re-emit it: "
                "re-emitting IS the outerHTML swap that destroys a "
                "still-visible predecessor (N-206)"
            )
            # **The negatives are scoped to the acknowledgement fragment, not
            # to the whole body, and that is load-bearing.**  On the grid the
            # response ALSO carries the ``#anchor-as-of`` snippet, which
            # correctly says "as of <today>" because today's assertion is the
            # one that still governs.  A whole-body ``not in`` would read that
            # correct caption as the acknowledgement naming the wrong day and
            # fail the honest code, which is a control that fires on the truth.
            #
            # The as-of snippet is REMOVED before slicing rather than the
            # slice being taken on faith.  Taking everything after the swap
            # marker makes the negatives depend on the order the route
            # concatenates its fragments (``html + as_of + feedback``): move
            # the acknowledgement earlier and ``f"as of {today}" not in ack``
            # would silently start grading the as-of snippet instead, which is
            # a control that stops testing its own subject without failing.
            assert html.count(_ACK_SWAP) == 1
            ack = re.sub(
                r'<small[^>]*id="anchor-as-of".*?</small>', "", html,
                flags=re.DOTALL,
            ).split(_ACK_SWAP, 1)[1]
            assert "Balance recorded" in ack
            # **The attribute that makes the toast VISIBLE, graded** -- the
            # whole feature hangs on one token and nothing saw it.  Vendored
            # Bootstrap carries ``.toast:not(.show){display:none}``, and the
            # only thing that adds ``.show`` to a fragment arriving by
            # out-of-band swap is ``app.js``'s ``[data-toast-auto-show]``
            # handler.  Deleting this attribute leaves the acknowledgement in
            # the DOM and permanently invisible -- N-199's exact symptom --
            # and an adversarial review MEASURED the whole suite still
            # passing on that mutation.  The reconcile prompt's twin marker
            # is already graded two tests down; this one was the only
            # auto-show marker in the app that nothing checked.
            assert "data-toast-auto-show" in ack, (
                "without this marker the toast swaps in with display:none "
                "and the user sees nothing -- the defect N-199 records"
            )
            # The DAY is graded, not just the phrase: a mutant naming the
            # governing assertion's day instead of the submitted one also
            # passed the whole suite, and it is worse than silence -- it
            # affirmatively tells the user the correction landed on a day it
            # did not.
            assert f"as of {statement_day.strftime('%b %-d, %Y')}" in ack
            assert (
                f"as of {today.strftime('%b %-d, %Y')}" not in ack
            ), "the acknowledgement must name the SUBMITTED day, not today"
            # The BALANCE too, for the same reason the day is graded: the
            # acknowledgement is the only evidence the write landed, so naming
            # the governing figure instead of the submitted one would confirm
            # a value the user did not enter.
            assert "$2,500.00" in ack
            assert "$1,000.00" not in ack, (
                "the acknowledgement must name the SUBMITTED balance, not the "
                "one that still governs"
            )

    def test_re_recording_the_same_balance_later_is_acknowledged(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A real write that changes no figure says so (finding **N-204**).

        The state the OLD predicate could not see, and the reason the
        acknowledgement stopped being the reconcile prompt's complement.
        Re-record the balance that already governs, for a LATER day, with
        nothing outstanding: a row IS appended (the day differs, so ruling
        R-EQ's duplicate test does not fire), the coverage boundary MOVES, and
        the balance cell re-renders to the figure it already showed -- so the
        submission was the boundary, took the prompt branch, and the prompt was
        empty.  Nothing at all reached the screen.

        Measured on production: this exact shape, an equal balance re-asserted
        on a later day, occurs once in the real Checking account's 57
        assertions.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            statement_day = display_today() - timedelta(days=6)

            self._true_up(
                auth_client, acct_id, "1500.00", observed_on=statement_day,
            )
            response = self._true_up(
                auth_client, acct_id, "1500.00", observed_on=display_today(),
            )

            html = response.data.decode()
            # A row really was written -- the coverage boundary moved -- so
            # this is a write that produced no visible change, not a no-op.
            assert (
                cash_ledger.reconciled_through(acct_id).observed_day
                == display_today()
            )
            assert "data-modal-auto-show" not in html, (
                "nothing is outstanding, so the prompt is empty and the "
                "acknowledgement is the only thing that can speak"
            )
            assert _ACK_SWAP in html
            ack = html.split(_ACK_SWAP, 1)[1]
            assert "Balance recorded" in ack
            assert "$1,500.00" in ack

    def test_a_write_that_moves_the_figure_is_not_acknowledged(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The non-vacuity control for the case above.

        Identical fixture and identical days; only the submitted BALANCE
        differs, so the cell re-renders to a new figure and IS its own
        acknowledgement.  Without this, firing the toast unconditionally would
        pass the test above while burying every ordinary true-up under a
        redundant confirmation.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            statement_day = display_today() - timedelta(days=6)

            self._true_up(
                auth_client, acct_id, "1500.00", observed_on=statement_day,
            )
            response = self._true_up(
                auth_client, acct_id, "1600.00", observed_on=display_today(),
            )

            assert _ACK_SWAP not in response.data.decode(), (
                "the balance cell already shows the new figure, so a toast "
                "repeating it is noise"
            )

    def test_the_prompt_still_wins_when_there_is_something_to_reconcile(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A visible prompt IS the acknowledgement, so the toast stays quiet.

        The exclusivity, graded on the one state where the re-key could have
        produced BOTH: the figure does not move (so the toast's first clause
        holds) and the submission is the coverage boundary with a purchase
        outstanding (so the prompt opens).  The prompt is captioned with the
        balance and day just recorded, which is why it discharges the
        acknowledgement rather than competing with it.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()
            self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("120.00", today - timedelta(days=2), False, None)],
            )
            self._true_up(
                auth_client, acct_id, "1500.00",
                observed_on=today - timedelta(days=6),
            )
            response = self._true_up(
                auth_client, acct_id, "1500.00", observed_on=today,
            )

            html = response.data.decode()
            assert "data-modal-auto-show" in html
            assert _ACK_SWAP not in html, (
                "the prompt names the balance and day just recorded, so a "
                "toast beside it says the same thing twice"
            )

    def test_a_blank_date_box_is_acknowledged_for_TODAY(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The acknowledgement names the resolved day, not a guess.

        The one arm of the day the suite did not grade.  A blank date box
        carries no day; the write door resolves it to the user's today, and the
        route may not re-read the clock to name it -- so it reads the day back
        off the assertion that GOVERNS after the write.  A mutation naming any
        other day (the account's ``created_at``, the previous assertion's day)
        renders a wrong date under a correct balance, which is worse than
        silence: it affirmatively tells the user their record landed on a day
        it did not.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()

            self._true_up(
                auth_client, acct_id, "1500.00",
                observed_on=today - timedelta(days=6),
            )
            # No ``observed_on`` at all -- the shape an older client submits,
            # and the one the fallback exists for.
            response = self._true_up(auth_client, acct_id, "1500.00")

            ack = response.data.decode().split(_ACK_SWAP, 1)[1]
            assert f"as of {today.strftime('%b %-d, %Y')}" in ack
            assert (
                f"as of {(today - timedelta(days=6)).strftime('%b %-d, %Y')}"
                not in ack
            ), "a blank date box means TODAY, not the day that governed before"

    def test_an_idempotent_re_assert_does_not_claim_to_have_recorded(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling R-EQ's UNCHANGED outcome gets its own copy.

        Submitting the governing balance for the governing DAY writes nothing
        and is rolled back, while the route reports success.  That state
        reaches the acknowledgement under the re-keyed predicate -- the figure
        did not move and the prompt is empty -- so the copy has to say what
        happened.  "Balance recorded" over a write that wrote nothing is the
        same class of untruth the acknowledgement exists to remove.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()

            self._true_up(auth_client, acct_id, "1500.00", observed_on=today)
            before = db.session.query(AccountAnchorHistory).filter_by(
                account_id=acct_id,
            ).count()
            response = self._true_up(
                auth_client, acct_id, "1500.00", observed_on=today,
            )

            # Nothing was written, which is the premise of the copy below.
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=acct_id,
            ).count() == before

            ack = response.data.decode().split(_ACK_SWAP, 1)[1]
            assert "Balance confirmed" in ack
            assert "Balance recorded" not in ack, (
                "nothing was recorded -- ruling R-EQ rolled the submission "
                "back -- so the copy must not say it was"
            )
            assert "$1,500.00" in ack

    @pytest.mark.parametrize(
        "revert",
        [None, "dashboard", "accounts", "investment", "cash"],
        ids=["grid", "dashboard", "cockpit", "investment-hero", "cash-hero"],
    )
    def test_an_ordinary_true_up_still_prompts_to_reconcile(
        self, app, auth_client, seed_user, seed_periods_today, revert,
    ):
        """The non-vacuity control for the case above.

        Identical fixture, identical outstanding purchase; only the submitted
        day differs.  Without this, suppressing the prompt UNCONDITIONALLY
        would pass the back-dated case -- and the date box is pre-filled with
        today, so every ordinary one-click true-up now submits an explicit day
        and would have been caught by a blanket rule.

        Both spellings of "now" are graded: the field omitted (an older client,
        and the shape every other true-up test submits) and the field carrying
        today (what the editor actually sends since plan step X-f1c4c).  Both
        are checked on EVERY opener, so the surface branch cannot regress on one
        of them silently.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()
            self._make_grocery_txn_with_entries(
                seed_user, seed_periods_today,
                [("120.00", today - timedelta(days=2), False, None)],
            )

            omitted = self._true_up(
                auth_client, acct_id, "1000.00", revert=revert,
            )
            assert "data-modal-auto-show" in omitted.data.decode()

            explicit = self._true_up(
                auth_client, acct_id, "1100.00", observed_on=today,
                revert=revert,
            )
            html = explicit.data.decode()
            assert "data-modal-auto-show" in html, (
                "the editor pre-fills today, so this is the ORDINARY path -- "
                "a rule keyed on 'was a day submitted' would break it"
            )
            # Nothing to acknowledge: this submission IS the boundary.
            assert "recorded as of" not in html


class TestTheTransferArmThroughItsROUTE:
    """Plan step **X-f2-c3**: the TRANSFER arm, from the door rather than the service.

    The arm's rules are graded exhaustively at the service
    (``test_services/test_reconcile_service/test_the_transfer_arm.py::TestTheTransferArm``).  What
    is graded HERE is the part only the route and the template can get wrong:
    that a transfer reaches the panel at all, that its section prints the
    sentence saying a SECOND account moves, and that a tick posted as an
    ordinary form submission settles the transfer and records both its sides.

    **The section note is the one piece of copy on this screen that describes
    a side effect.**  Every other row settles what the user is looking at; a
    transfer settles the matching row on an account the panel is not showing.
    A template branch nothing renders is a sentence that can be deleted
    without a single test noticing, on the screen whose whole job is telling
    the user what a tick is about to do.
    """

    _true_up = staticmethod(TestTheReconcileRoute._true_up)

    @staticmethod
    def _outstanding_transfer(seed_user, period, amount="75.00"):
        """Return a committed, Projected transfer out of the reconciled account.

        Dated into the past through the period it sits in, so the shared
        attribution bound admits it against a statement asserted today.  It
        returned the transfer's shadow on the reconciled account beside it
        until plan step ``balance:X-bi-6-4d-2``: a transfer is one row since
        then (ruling R-BAL167 class 4).
        """
        savings = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                name="Savings",
                account_type_id=seed_user["account"].account_type_id,
                anchor_balance=Decimal("100.00"),
            ),
        )
        db.session.flush()
        # **Its BOOKS open before the transfer below** (plan step X-f3c-2b,
        # ruling **R-HG**).  ``create_account`` opens them on the assertion's
        # own day, which is today, while this transfer is dated into the PAST
        # on purpose -- so its far leg would land inside the $100.00 declared,
        # and the route would answer 400 rather than the tick it is grading.
        open_books_before_the_first_assertion(db.session, savings)
        transfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings, period,
            amount=Decimal(amount),
        )
        db.session.commit()
        return transfer

    def test_the_panel_prints_the_section_and_says_a_SECOND_account_moves(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The TRANSFER section renders its heading AND its note.

        Both are template branches that no test reached: the heading comes
        from ``group.section.label`` and the note from ``group.section.note``,
        which :class:`OfferKind.TRANSFER` supplies (and, since plan step
        credit_card:CC-5-4b, ``OfferKind.SETTLEMENT``).  Shown to FIRE:
        deleting the ``group.section.note`` block fails this.
        """
        with app.app_context():
            transfer = self._outstanding_transfer(
                seed_user, seed_periods_today[0],
            )
            transfer_id = transfer.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            body = auth_client.get(
                f"/accounts/{seed_user['account'].id}/reconcile",
            ).data.decode()

            assert f'value="{transfer_id}"' in body, (
                "the transfer must be offered at all"
            )
            assert "Transfers" in body
            assert "settles both sides" in body
            assert "the matching row on the other account" in body

    def test_a_posted_tick_settles_the_parent_and_BOTH_legs(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The arm end to end: one form POST settles the transfer and both its sides.

        The service case proves the verb does it; this proves the ROUTE
        reaches the verb -- that the transfer's id posted in its own
        ``transfer_ids`` field (ruling R-BAL145) lands in the transfer arm
        rather than the transaction one, whose ids name rows, not transfers.

        The transfer's status is the one status, and each leg is its side's
        payment RECORD since plan step ``balance:X-bi-6-4d-2``; each shadow
        said Paid and carried the record until then (ruling R-BAL167 class 1,
        plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            transfer = self._outstanding_transfer(
                seed_user, seed_periods_today[0],
            )
            transfer_id = transfer.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={"transfer_ids": [str(transfer_id)]},
            )
            assert response.status_code == 200
            assert b"already been settled" not in response.data

            db.session.expire_all()
            # The status is compared by ID, not by its display name -- the
            # project's reference-table rule ("IDs for logic, strings for
            # display only"), which the DONE member makes concrete: its
            # ``name`` reads "Paid".
            done_id = ref_cache.status_id(StatusEnum.DONE)
            parent = db.session.get(Transfer, transfer_id)
            assert parent.status_id == done_id
            resolved = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.RESOLVED,
            )
            for account_id in (parent.from_account_id, parent.to_account_id):
                record = transfer_side_record(
                    db.session, transfer_id, account_id,
                )
                assert record is not None, (
                    f"the tick recorded no payment on account {account_id}"
                )
                assert record.settled_on == display_today()
            # Nobody typed a figure, so the transfer's figure source is
            # ``resolved`` -- which is where "did a human correct this" lives
            # since plan step X-au-c3 (on the covering movement since X-bi-4b-2,
            # and on the transfer, the figure's one home, since X-bi-6-4d-2's
            # 4d-4 half), rather than in the figure's NULL-ness.
            assert parent.settled_figure_source_id == resolved

    def test_a_correction_typed_on_a_transfer_lands_on_BOTH_legs(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A figure read off the statement is a fact about the whole act.

        Both legs carry it, because a transfer states ONE amount -- a
        correction recorded on one side only would leave the two accounts
        disagreeing about how much money moved between them.
        Each leg is its side's payment RECORD since plan step
        ``balance:X-bi-6-4d-2``, valued as the side the readers fold; the
        shadows held it until then (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            transfer = self._outstanding_transfer(
                seed_user, seed_periods_today[0],
            )
            transfer_id = transfer.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "transfer_ids": [str(transfer_id)],
                    f"transfer_amount-{transfer_id}": "80.25",
                },
            )
            assert response.status_code == 200

            db.session.expire_all()
            parent = db.session.get(Transfer, transfer_id)
            for account_id in (parent.from_account_id, parent.to_account_id):
                assert transfer_side_figure(
                    db.session, transfer_id, account_id,
                ) == Decimal("80.25")
                assert settled_contribution(
                    parent_of(leg_of(parent, account_id)),
                ) == Decimal("80.25")

    def test_an_ECHOED_prefill_on_a_transfer_records_no_correction(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The negative control for the case above, at the ROUTE.

        The panel PREFILLS every correctable box, so an untouched tick posts
        the figure the row would book anyway.  Without this case the
        correction test is satisfied by a verb that writes whatever it is
        handed, which would record every settled transfer's figure as typed
        and destroy the signal that says a human typed one: the transfer's
        figure source, read below, its one home since plan step
        ``balance:X-bi-6-4d-2``'s 4d-4 half (ruling **R-BAL366**), where each
        side record carried a copy until then.  Each
        leg is its side's payment RECORD since plan step
        ``balance:X-bi-6-4d-2``; the shadows held it until then (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            transfer = self._outstanding_transfer(
                seed_user, seed_periods_today[0],
            )
            transfer_id = transfer.id
            self._true_up(auth_client, seed_user["account"].id, "4537.66")

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/reconcile",
                data={
                    "transfer_ids": [str(transfer_id)],
                    f"transfer_amount-{transfer_id}": "75.00",
                },
            )
            assert response.status_code == 200

            db.session.expire_all()
            parent = db.session.get(Transfer, transfer_id)
            resolved = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.RESOLVED,
            )
            # An ECHOED prefill is not a correction: the transfer says
            # ``resolved`` and states the figure the settle booked.
            assert parent.settled_figure_source_id == resolved
            for account_id in (parent.from_account_id, parent.to_account_id):
                record = transfer_side_record(
                    db.session, transfer_id, account_id,
                )
                assert record is not None, (
                    f"the tick recorded no payment on account {account_id}"
                )
                assert transfer_side_figure(
                    db.session, transfer_id, account_id,
                ) == Decimal("75.00")
                assert settled_contribution(
                    parent_of(leg_of(parent, account_id)),
                ) == Decimal("75.00")
