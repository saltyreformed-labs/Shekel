"""The transaction PATCH's figure and day: a settled amount arriving alone is recorded, a changed
figure beside a revert is refused, and a re-submitted day does not restate its basis (plan step
X-az).
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam
from app.services.row_valuation import settled_figure
from app.services.settle_day import record_settle_day, recorded_settle_day
from tests._ledger_reads import family_movement_ids, net_posted_by_day
from tests._test_helpers import an_asserted_day, an_entered_day, create_envelope_txn

from ._helpers import _gas_envelope, _full_edit_save


class TestAFigureArrivingALONEAtTheTransactionPATCH:
    """A PATCH carrying ONLY ``settled_amount`` must still record it.

    **The dispatch clause this grades had no firing control** (neutral review,
    2026-08-18).  ``routes/transactions/mutations`` enters the status arm when a
    status, a day OR a figure arrives; without the third term a PATCH carrying
    the figure alone fell to the ``elif _POSTING_RELEVANT_FIELDS & data.keys()``
    branch -- ``settled_amount`` is in that set -- and answered 200 having
    discarded the user's typed money value.

    Every other route test that submits a figure also submits a status, because
    the popover's Save posts the whole form; the case that reaches this clause
    is the box edited on its own, which is what the transfer door already grades
    and this one did not.
    """

    def test_a_settled_row_records_a_figure_submitted_with_no_status(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Correct the Actual, touch nothing else, and the record moves.

        Hand arithmetic: a `$500.00` bill settles at its plan, so the cash
        account is `$500.00` down. The statement says `$245.32`, so the
        corrected record re-books that and the ledger nets to `$245.32` on the
        day the money moved -- not `$500.00`, and not `$745.32`.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Electricity", Decimal("500.00"),
            )
            txn.template.is_envelope = False
            db.session.commit()
            txn_id = txn.id

            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200
            db.session.expire_all()
            settled = db.session.get(Transaction, txn_id)
            assert settled_figure(settled) == Decimal("500.00")
            day = settled.settled_on

            response = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "settled_amount": "245.32",
                    "version_id": str(settled.version_id),
                },
            )

            assert response.status_code == 200, response.get_data(as_text=True)
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert settled_figure(reloaded) == Decimal("245.32"), (
                "a figure submitted alone was discarded and the save still "
                "answered 200"
            )
            assert status_seam.recorded_settlement(reloaded).source is (
                MovementFigureSourceEnum.TYPED
            )
            assert reloaded.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert reloaded.settled_on == day, (
                "a figure correction moved the day the money moved"
            )
            assert net_posted_by_day(
                family_movement_ids(txn_id),
            ) == {day: Decimal("245.32")}

    def test_a_SECOND_correction_replaces_the_first(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Correcting an already-CORRECTED record, which no test covered.

        Every echo test starts from a ``derived`` record, so nothing graded the
        comparison when the row already holds a human's figure -- the state a
        user is in the moment they re-open the popover after correcting once.
        The second figure must displace the first rather than be read as an echo
        of the PLAN, and the ledger must land on the second, not the sum.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Electricity", Decimal("500.00"),
            )
            txn.template.is_envelope = False
            db.session.commit()
            txn_id = txn.id
            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"settled_amount": "245.32"},
            ).status_code == 200

            db.session.expire_all()
            first = db.session.get(Transaction, txn_id)
            assert settled_figure(first) == Decimal("245.32")
            day = first.settled_on

            assert auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "settled_amount": "251.08",
                    "version_id": str(first.version_id),
                },
            ).status_code == 200

            db.session.expire_all()
            second = db.session.get(Transaction, txn_id)
            assert status_seam.recorded_settlement(second) == status_seam.Settlement(
                Decimal("251.08"), MovementFigureSourceEnum.TYPED,
            )
            assert net_posted_by_day(
                family_movement_ids(txn_id),
            ) == {day: Decimal("251.08")}

    def test_re_posting_a_CORRECTED_figure_unchanged_writes_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The echo rule, asked of a record that is already a correction.

        The firing control for the test above: the box is prefilled with what
        the row records, so re-saving must be a no-op even when what it records
        is itself a human's figure.  Graded on the version counter, because the
        record looks identical either way -- a write that changed nothing is
        still a write, and it is what a lost-update race is made of.  It is
        also the firing control for the test BELOW: the counter moves for a
        correction and not for an echo, and a mark that moved it for both
        would turn every second tab into a spurious 409.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Electricity", Decimal("500.00"),
            )
            txn.template.is_envelope = False
            db.session.commit()
            txn_id = txn.id
            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"settled_amount": "245.32"},
            ).status_code == 200

            db.session.expire_all()
            before = db.session.get(Transaction, txn_id)
            version_before = before.version_id

            assert auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "settled_amount": "245.32",
                    "version_id": str(version_before),
                },
            ).status_code == 200

            db.session.expire_all()
            after = db.session.get(Transaction, txn_id)
            assert settled_figure(after) == Decimal("245.32")
            assert after.version_id == version_before, (
                "an echoed prefill wrote the row anyway"
            )

    def test_a_stale_tab_cannot_overwrite_a_figure_correction(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two tabs, one pin: a figure correction moves the row's counter.

        **The plain row's half of the transfer's two-tab lost update**
        (``transfer_service._update._bump_parent_version_if_a_leg_moved``),
        armed by plan step ``balance:X-bi-4b-2``.  A figure correction writes
        the covering movement and nothing of the row: through ``X-bi-4b-1``
        the seam wrote the figure onto the row's own ``settled_amount`` too,
        so the row was dirty and its counter moved for free; with those
        columns gone the row stayed clean, tab B's stale pin still matched,
        and B's prefilled ``$200.00`` overwrote A's ``$214.37`` as a 200.
        ``status_seam._covering._record_moved`` marks the row whenever its
        record nets a change, so the pin the popover holds is stale the
        moment another tab corrects the figure.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Electricity", Decimal("200.00"),
            )
            txn.template.is_envelope = False
            db.session.commit()
            txn_id = txn.id
            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200
            db.session.expire_all()
            shared_pin = db.session.get(Transaction, txn_id).version_id

            first = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"settled_amount": "214.37", "version_id": str(shared_pin)},
            )
            assert first.status_code == 200, first.get_data(as_text=True)
            db.session.expire_all()
            assert db.session.get(Transaction, txn_id).version_id > shared_pin, (
                "a figure correction left the row's counter behind"
            )

            second = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"settled_amount": "200.00", "version_id": str(shared_pin)},
            )

            assert second.status_code == 409, (
                "a stale tab overwrote a figure correction and reported success"
            )
            db.session.expire_all()
            assert settled_figure(db.session.get(Transaction, txn_id)) == (
                Decimal("214.37")
            ), "the stale save landed anyway"


class TestAChangedFigureBesideARevert:
    """A retyped Actual submitted with Projected is REFUSED, not discarded.

    **The measured defect** (neutral review, 2026-08-18): the submission rule
    read the STATUS alone, so it could not tell an untouched prefill from a
    number the user had just typed and dropped both.  End to end, exactly as a
    user meets it: a bill settled at a hand-typed `$245.32`, re-read off the
    statement as `$214.37`, corrected and reverted in one Save -- HTTP 200, no
    message, and the row still recording `$245.32`.

    That is worse than the settle DAY's analogue ruling **R-EG** was written
    for.  A revert CLEARS the day, so a dropped day changes nothing; a revert
    RETAINS the figure, and ``Settlement.from_settle`` deliberately re-books a
    retained correction -- so the dropped figure became a silently WRONG
    booking on the next settle, promised on the grid by the re-book notice.

    The two halves of the rule are graded together here, because it is the
    DIFFERENCE between them that was missing: an echo still drops (the unlock
    path must keep working) and a change is refused.
    """

    @staticmethod
    def _settled_at(auth_client, seed_user, session, period, figure):
        """Return the id of a plain bill settled at a hand-typed *figure*."""
        txn = create_envelope_txn(
            seed_user, session, period, "Electricity", Decimal("500.00"),
        )
        txn.template.is_envelope = False
        session.commit()
        txn_id = txn.id
        assert auth_client.post(
            f"/transactions/{txn_id}/mark-done",
            data={"settled_amount": figure},
        ).status_code == 200
        session.expire_all()
        return txn_id

    def test_a_CHANGED_figure_beside_a_revert_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The user typed $214.37 and picked Projected. Neither is guessed at.

        Refused with the sentence that names both acts, and the row is left
        exactly as it was -- still Paid, still recording `$245.32` -- so the
        user can correct the figure and then revert, in that order.
        """
        with app.app_context():
            txn_id = self._settled_at(
                auth_client, seed_user, db.session,
                seed_periods_today[3], "245.32",
            )
            settled = db.session.get(Transaction, txn_id)

            response = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                    "settled_amount": "214.37",
                    "version_id": str(settled.version_id),
                },
            )

            assert response.status_code == 400, (
                "a figure the user CHANGED was swallowed by the revert"
            )
            assert "has nothing to record" in response.get_data(as_text=True)
            db.session.expire_all()
            after = db.session.get(Transaction, txn_id)
            assert after.status_id == ref_cache.status_id(StatusEnum.DONE), (
                "a refused request reverted the row anyway"
            )
            assert settled_figure(after) == Decimal("245.32")

    def test_an_UNTOUCHED_box_beside_a_revert_still_unlocks(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The firing control, and ruling R-EG's whole point.

        The popover PREFILLS the box with what the row records and submits every
        input it renders, so the ordinary revert carries `$245.32` back.  That
        is not an assertion that this much moved -- the user picked Projected --
        and refusing it would break the documented unlock path on every settled
        row.  Without this case the test above passes against a rule that
        refuses every figure.
        """
        with app.app_context():
            txn_id = self._settled_at(
                auth_client, seed_user, db.session,
                seed_periods_today[3], "245.32",
            )
            settled = db.session.get(Transaction, txn_id)

            response = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                    "settled_amount": "245.32",
                    "version_id": str(settled.version_id),
                },
            )

            assert response.status_code == 200, response.get_data(as_text=True)
            db.session.expire_all()
            after = db.session.get(Transaction, txn_id)
            assert after.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            # The ASSERTION is withdrawn and WHAT MOVED is kept, so the
            # revert / edit / re-settle round trip stays lossless.
            assert after.settled_on is None
            assert status_seam.recorded_settlement(after).amount == Decimal("245.32")


class TestAReSubmittedDayDoesNotRestateItsBASIS:
    """Plan step **X-az**: the ECHO rule at the two STATUS doors.

    **The whole form posts on Save**, which is this suite's own premise, and the
    settle-day box is part of it -- so an untouched Save re-submits the day the
    row already carries.  Stamping that ``entered`` rewrites what the row knew
    about its own day: a reconcile-panel UPPER BOUND, or a day the bank stated,
    becomes the owner's own typing, with the day unchanged so nothing releases
    the clearing link and nothing tells anyone.

    The entry PATCH has its own controls (``test_entries.py``) and it is where
    the cost was measured -- **59 of 66 linked purchases, `$4,173.07`** on
    production.  These are the two doors that share the rule through
    ``status_seam.settle_day_for_status``: the transaction PATCH here, and the
    SHADOW branch below, which grades the transfer PATCH's copy of it.
    """

    @staticmethod
    def _settled_on_a_bound(auth_client, seed_user, period):
        """Return a Paid row whose day is an ``asserted`` BOUND, as the panel leaves it."""
        txn = _gas_envelope(seed_user, period)
        txn_id = txn.id
        assert auth_client.post(
            f"/transactions/{txn_id}/mark-done",
        ).status_code == 200
        db.session.expire_all()
        paid = db.session.get(Transaction, txn_id)
        record_settle_day(paid, an_asserted_day(paid.settled_on))
        db.session.commit()
        return db.session.get(Transaction, txn_id)

    def test_an_untouched_save_keeps_the_asserted_basis(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The defect, at the transaction PATCH.

        Drop the ``recorded`` argument at
        ``routes/transactions/mutations.py``'s ``settle_day_for_status`` call
        and this fails: every submission is then a fresh assertion, so the bound
        is reported as a day the owner typed.
        """
        with app.app_context():
            paid = self._settled_on_a_bound(
                auth_client, seed_user, seed_periods_today[3],
            )
            txn_id, day = paid.id, paid.settled_on

            resp = _full_edit_save(
                auth_client, paid, paid.status_id,
                settled_on=day.isoformat(),
            )

            assert resp.status_code == 200
            db.session.expire_all()
            saved = db.session.get(Transaction, txn_id)
            assert saved.settled_on == day
            assert recorded_settle_day(saved) == an_asserted_day(day)

    def test_a_day_the_owner_really_MOVED_is_their_own(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The firing half: a genuine correction still records ``entered``.

        Without it the rule is satisfied by one that never restates the basis,
        which would report a day the owner typed as one a balance bounded --
        the same laundering in the other direction.
        """
        with app.app_context():
            paid = self._settled_on_a_bound(
                auth_client, seed_user, seed_periods_today[3],
            )
            txn_id = paid.id
            corrected = paid.settled_on - timedelta(days=2)

            resp = _full_edit_save(
                auth_client, paid, paid.status_id,
                settled_on=corrected.isoformat(),
            )

            assert resp.status_code == 200
            db.session.expire_all()
            saved = db.session.get(Transaction, txn_id)
            assert recorded_settle_day(saved) == an_entered_day(corrected)
