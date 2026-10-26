"""Transfer route tests: the PATCH's settle-day correction, and the popover's two day boxes, one
per side.
"""

import re
from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.routes._render_helpers import transfer_side_boxes
from app.services import balance_at, transfer_legs
from app.services.balance_at import BalanceContext
from app.services import transfer_service
from app.utils.dates import display_today
from tests._test_helpers import (
    an_asserted_day,
    an_entered_day,
    an_observed_day,
    field_is_disabled,
    net_posted_by_day,
    on_both_sides,
    transfer_family_journal_filter,
    transfer_side_record,
    transfer_side_settle_day,
    typed,
)

from ._helpers import _create_savings_account, _create_transfer


class TestTransferSettleDayEditDoor:
    """The transfer PATCH's settle-day correction (rulings R-ED / R-EG).

    **This door is not a convenience twin of the transaction one.**  It is the
    ONLY correction path for the rows finding **N-181** names: all eight settled
    rows whose day the X-f1b backfill had to invent from a pay period's
    ``start_date`` are transfer SHADOWS (four pairs, measured on the 2026-08-03
    production clone), and a shadow's full-edit popover is the TRANSFER form --
    ``routes/transactions/forms.get_full_edit`` redirects a shadow to it rather
    than rendering the transaction popover.  Without this door X-f1c would close
    N-181 in principle and zero rows in fact.
    """

    @staticmethod
    def _settled_transfer(seed_user, seed_periods_today, day):
        """Return a settled transfer whose money moved on *day*, ledger in step.

        Settled and then back-dated THROUGH the service, so the fixture's own
        journal entry carries *day*.  A bare attribute write would leave the
        ledger at today and let a "the ledger followed" assertion pass on a
        stale comparison instead of on the edit under test.  Back-dated as ONE
        day TYPED for both sides -- what the one-box popover wrote before plan
        step ``balance:X-bi-6-4c-3`` -- so each side holds *day* as its own.
        """
        savings = _create_savings_account(seed_user)
        xfer = _create_transfer(seed_user, seed_periods_today, savings)
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id,
            status_id=ref_cache.status_id(StatusEnum.DONE),
        )
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id,
            side_days=on_both_sides(
                xfer.from_account_id, xfer.to_account_id, an_entered_day(day),
            ),
        )
        db.session.commit()
        return xfer

    @staticmethod
    def _both_boxes(day):
        """Return the popover's two day boxes, both carrying *day*.

        The form posts every control it renders, and a settled transfer's
        popover renders one box per side (plan step ``balance:X-bi-6-4c-3``,
        ruling **R-BAL108**) -- so an untouched Save posts both, and a day
        the owner means for the whole transfer is typed into both.
        """
        return {
            "settled_on_from": day.isoformat(),
            "settled_on_to": day.isoformat(),
        }

    @staticmethod
    def _state_both(xfer, owner_id, settle_day):
        """State *settle_day* for both sides through the service.

        Through the service rather than a bare write, so the day lands where
        the popover prefills each box from (``transfer_side_boxes``): each
        side's payment RECORD, which hangs off the transfer since plan step
        ``balance:X-bi-6-4d-2``; the twins take nothing since.  The record
        follows the arm's rule (``status_seam._side._writes_day``): another
        civil day is written with its basis, but on the SAME civil day the
        basis is written only where it raises the record's -- ``observed``
        over any, any over ``borrowed`` -- so an ``asserted`` day stated over
        an ``entered`` record on that record's own day leaves it ``entered``.
        """
        transfer_service.update_transfer(
            xfer.id, owner_id,
            side_days=on_both_sides(
                xfer.from_account_id, xfer.to_account_id, settle_day,
            ),
        )

    @staticmethod
    def _net_by_day(xfer_id):
        """Return ``{entry_date: net posted magnitude}`` for one transfer.

        Thin wrapper over the shared
        :func:`tests._test_helpers.net_posted_by_day`; see it for why the NET
        rather than the raw ``entry_date`` list is what grades a correction.
        Over the transfer's FAMILY -- its two per-movement entries since plan
        step ``balance:X-bi-6-3`` -- whose per-account magnitude on a day is
        the figure either side moved.
        """
        return net_posted_by_day(transfer_family_journal_filter(xfer_id))

    @staticmethod
    def _side_days(xfer_id):
        """Return both sides' recorded settle days, as a set.

        Each read off the side's payment RECORD, which hangs off the transfer
        since plan step ``balance:X-bi-6-4d-2`` -- where a ``_shadow_days``
        beside it read the twins, which kept no day since that step and are
        born no more since ``balance:X-bi-6-4d-2`` (ruling R-BAL167 class 4,
        plan step balance:X-bi-6-4d-2).  ``None`` for a side holding no dated
        record.
        """
        xfer = db.session.get(Transfer, xfer_id)
        days = (
            transfer_side_settle_day(db.session, xfer_id, account_id)
            for account_id in (xfer.from_account_id, xfer.to_account_id)
        )
        return {None if day is None else day.day for day in days}

    def test_a_RE_SUBMITTED_day_does_not_restate_its_basis(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Plan step **X-az**: the ECHO rule at the TRANSFER PATCH.

        This form prefills each side's day box and posts both on Save, so an
        untouched Save re-submits the days the sides already carry.  Stamping
        those ``entered`` rewrites what the legs knew about their own day -- a
        reconcile-panel BOUND, or a day the bank stated, becomes the owner's own
        typing, with the day unchanged so nothing releases the clearing link.

        A transfer carries neither settle column, so the rule grades each box
        against what THAT box was prefilled from, read off its leg's record
        (``transfer_side_boxes``; one box per side since plan step
        ``balance:X-bi-6-4c-3``, where it read the pair off the income shadow).
        Drop the ``recorded`` argument at this route's ``settle_day_for_status``
        call and this fails.

        **The transfer is paid ONE DAY EARLIER than the reconciled day**, so
        the reconciled day really lands (a setup change only, approved by the
        developer 2026-10-09 under rule 5): since plan step
        ``balance:X-bi-6-4d-2`` each side's day lives on its payment RECORD
        alone, and a reconciled day stated on the record's own day does not
        replace an ``entered`` one (``_state_both``).  Paid on that day, the
        record kept ``entered`` and this passed only by reading the twins,
        which no reader reads (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).  Each side's record is read before the Save and
        after it, both ``asserted`` on *day*.
        """
        with app.app_context():
            day = display_today() - timedelta(days=6)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, day - timedelta(days=1),
            )
            self._state_both(xfer, seed_user["user"].id, an_asserted_day(day))
            db.session.commit()

            def _bases():
                """Return both sides' recorded days, freshly read."""
                db.session.expire_all()
                return {
                    transfer_side_settle_day(db.session, xfer.id, account_id)
                    for account_id in (xfer.from_account_id, xfer.to_account_id)
                }

            assert _bases() == {an_asserted_day(day)}, (
                "fixture: the reconciled day did not land on both sides"
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data=self._both_boxes(day),
            )
            assert response.status_code == 200, response.get_data(
                as_text=True,
            )[:300]

            assert _bases() == {an_asserted_day(day)}, (
                "an untouched Save laundered the pair's BOUND into the owner's "
                "own day"
            )

    def test_a_transfer_day_the_owner_MOVED_is_their_own(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The firing half of the rule above, so it is not "never restate".

        Each side's day read off its payment RECORD, not its twin (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            day = display_today() - timedelta(days=6)
            corrected = day + timedelta(days=2)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, day,
            )
            self._state_both(xfer, seed_user["user"].id, an_asserted_day(day))
            db.session.commit()

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data=self._both_boxes(corrected),
            )
            assert response.status_code == 200, response.get_data(
                as_text=True,
            )[:300]

            db.session.expire_all()
            bases = {
                transfer_side_settle_day(db.session, xfer.id, account_id)
                for account_id in (xfer.from_account_id, xfer.to_account_id)
            }
            assert bases == {an_entered_day(corrected)}

    def test_the_LEG_door_of_the_transfer_PATCH_echoes_too(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Plan step **X-az**: the ECHO rule at the grid's status door.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87)**: the
        SHADOW door this graded (``PATCH /transactions/<shadow>``, re-expressed
        by ``_shadow_mutations`` as a transfer update) is deleted; the grid's
        door is the transfer PATCH carrying ``leg_account_id``, and the claim
        is graded there, with the response the LEG's cell.
        The transfer PATCH grades a leg's request by the same reading the
        transfers page's takes, so this and the two doors above share one
        spelling of the rule; what a leg's request adds is the response
        surface, and a laundered basis would still be visible here.  The
        leg's side is read off its payment RECORD, which hangs off the
        transfer since plan step ``balance:X-bi-6-4d-2``, not off its twin
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  The leg is
        the from-side, where the first twin this took its account from sat
        until plan step ``balance:X-bi-6-4d-2``, which births none (ruling
        R-BAL167 class 1).
        """
        with app.app_context():
            day = display_today() - timedelta(days=6)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, day,
            )
            self._state_both(xfer, seed_user["user"].id, an_observed_day(day))
            db.session.commit()
            leg_account_id = xfer.from_account_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    **self._both_boxes(day),
                    "leg_account_id": str(leg_account_id),
                },
            )
            assert response.status_code == 200, response.get_data(
                as_text=True,
            )[:300]

            db.session.expire_all()
            assert transfer_side_settle_day(
                db.session, xfer.id, leg_account_id,
            ) == an_observed_day(day), (
                "the leg door laundered a bank OBSERVATION into the "
                "owner's own day"
            )

    def test_correcting_the_day_moves_both_shadows_and_the_ledger(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """PATCHing both sides' day boxes re-dates the pair and its postings (R-ED).

        The gate ruling R-ED names for this half in terms: a test that EDITS a
        settled row's day and asserts the LEDGER followed.  Both sides' payment
        records take the corrected day (the posting writer files each side's
        entry under that side's covering movement's day since plan step
        ``balance:X-bi-6-3``), and the reconcile reverses the stale-dated
        entries and re-posts at the corrected day (finding **N-13**).  The
        record hangs off the transfer, not its twin (ruling R-BAL167 class 1,
        plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            original = display_today() - timedelta(days=9)
            corrected = display_today() - timedelta(days=4)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, original,
            )
            assert self._net_by_day(xfer.id) == {original: Decimal("200.00")}, (
                "fixture did not leave its net posted effect at the settle day: "
                f"{self._net_by_day(xfer.id)}"
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data=self._both_boxes(corrected),
            )
            assert response.status_code == 200, response.get_data(as_text=True)[:300]

            db.session.expire_all()
            assert self._side_days(xfer.id) == {corrected}, (
                "the correction did not reach both sides equally"
            )
            assert self._net_by_day(xfer.id) == {corrected: Decimal("200.00")}, (
                "the settle day moved but the posted ledger did not follow: "
                f"net effect by day is {self._net_by_day(xfer.id)}, expected "
                f"the whole $200.00 at {corrected} and nothing left at "
                f"{original}"
            )

    def test_reverting_to_projected_ignores_the_submitted_day(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The unlock path survives the form re-submitting the transfer's day.

        Ruling **R-EG**, the transfer half.  The service raises
        ``ValidationError`` for a day stated on an unsettled transfer, and the
        full-edit form re-submits the days its boxes carry when the user sets
        Status to Projected to unlock the amount -- so without the route
        dropping them, the documented unlock path would 400 on every settled
        transfer.  Graded on the 400 NOT happening, on both sides'
        payment records being undated (read off the records, not the twins,
        which keep no day since plan step ``balance:X-bi-6-4d-2``: ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2), AND on the ledger
        being reversed.

        **That last clause was a docstring overclaim until a neutral review
        measured it.**  The body asserted only the status and the shadows, so a
        planted mutant that left the settled effect POSTED through a revert --
        the balance keeping money the user just said never moved -- survived
        this test.  Its transaction-side sibling graded the ledger from the
        start; the asymmetry was an omission, not a design choice.
        """
        with app.app_context():
            settled_day = display_today() - timedelta(days=5)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, settled_day,
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                    **self._both_boxes(settled_day),
                },
            )
            assert response.status_code == 200, (
                "the unlock path (Status -> Projected) was refused because the "
                "form re-submitted the transfer's own settle day: "
                f"{response.get_data(as_text=True)[:300]}"
            )

            db.session.expire_all()
            xfer = db.session.get(Transfer, xfer.id)
            assert xfer.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert self._side_days(xfer.id) == {None}, (
                "the revert left a settle day on a side, breaking the "
                "settled-iff-dated invariant"
            )
            assert self._net_by_day(xfer.id) == {}, (
                "the revert left the settled effect POSTED -- the balance keeps "
                f"money the user just said never moved: {self._net_by_day(xfer.id)}"
            )

    def test_a_future_settle_day_is_refused_and_moves_no_money(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A day that has not happened is refused, and the balance holds.

        Ruling **R-EJ**, the transfer half.  A settled source counts from its
        own ``settled_on``, and ``walk_cash_ledger`` absorbs one into an
        assertion only when the assertion is dated ON OR AFTER it -- so a
        future-dated settle rides on top of every assertion until that day
        arrives, putting already-spent money back in the rendered balance.
        Measured on the live route before the guard existed: a ``$200``
        transfer out of Checking read ``$800`` against a ``$1,000`` anchor, and
        PATCHing its day forward answered **200** with Checking back at
        ``$1,000``.

        The BALANCE is asserted, not just the status code: a 400 that still let
        the write through would pass a status-only check, and the balance is
        the thing the defect actually moved.  The day is read off each side's
        payment record, not its twin (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            settled_day = display_today() - timedelta(days=3)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, settled_day,
            )
            ctx = BalanceContext.build(seed_user["user"].id)
            before = balance_at.cash_balance_at(
                seed_user["account"], ctx, display_today(),
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "settled_on_from": (
                        display_today() + timedelta(days=400)
                    ).isoformat(),
                    "settled_on_to": settled_day.isoformat(),
                },
            )
            assert response.status_code == 400, (
                "a settle day 400 days out was accepted: "
                f"{response.status_code}"
            )

            db.session.expire_all()
            assert self._side_days(xfer.id) == {settled_day}, (
                "the refused day was written anyway"
            )
            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.cash_balance_at(
                seed_user["account"], ctx, display_today(),
            ) == before, (
                "the refused future settle moved the rendered balance -- "
                "already-spent money came back"
            )

    def test_full_edit_offers_the_correction_only_on_a_settled_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The correction input renders for a settled transfer and not before.

        A Projected transfer's money has not moved, so there is no day to
        state.  Asserted from BOTH openers -- the transfers page and a grid
        leg's cell -- because the grid opener is the one N-181's rows are
        reached through.  The leg is Savings', the side whose twin this took
        its account from until plan step ``balance:X-bi-6-4d-2``, which
        births none (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            for url in (
                f"/transfers/{xfer.id}/full-edit",
                f"/transfers/{xfer.id}/full-edit?leg_account_id={savings.id}",
            ):
                response = auth_client.get(url)
                assert response.status_code == 200, (
                    f"{url} did not render: a 500 would satisfy the negative "
                    "assertion below without the condition holding"
                )
                body = response.get_data(as_text=True)
                for field in ("settled_on_from", "settled_on_to"):
                    assert f'name="{field}"' not in body, (
                        f"{url} offered a settle day on a Projected transfer"
                    )

            settled_day = display_today() - timedelta(days=2)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(settled_day),
                ),
            )
            db.session.commit()

            for url in (
                f"/transfers/{xfer.id}/full-edit",
                f"/transfers/{xfer.id}/full-edit?leg_account_id={savings.id}",
            ):
                response = auth_client.get(url)
                assert response.status_code == 200, f"{url} did not render"
                body = response.get_data(as_text=True)
                for field in ("settled_on_from", "settled_on_to"):
                    assert f'name="{field}"' in body, (
                        f"{url} offered no settle day on a settled transfer"
                    )
                assert f'value="{settled_day.isoformat()}"' in body, (
                    f"{url} did not pre-fill the stored settle day"
                )
                # The browser half of ruling R-EJ, and it must be the USER's
                # today: a ``date.today()`` here would refuse, in the evening
                # Eastern, a day the seam accepts.  ``settled_day`` is two days
                # back, so neither assertion can satisfy the other.
                assert f'max="{display_today().isoformat()}"' in body, (
                    f"{url} did not bound the settle day at the user's today"
                )
                for field in ("settled_on_from", "settled_on_to"):
                    assert not field_is_disabled(body, field), (
                        f"{url} locked the settle day on a finalised transfer, "
                        "so the correction R-ED exists for is unreachable"
                    )

    def test_an_undated_settled_transfer_still_offers_the_repair_box(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A settled transfer carrying NO day still gets both day boxes, empty.

        The shape the template's condition is written for, and the reason it
        keys on the STATUS rather than on a side's day: a settled transfer
        with no day is the one that most needs a way to state the day its
        money really moved.  Keying the condition on the day instead would
        hide the boxes from precisely that row, and a neutral review found
        nothing grading the difference: ``{% if xfer.settled_on %}`` passed
        the whole suite.  ``Transfer`` has no ``settled_on`` since plan step
        ``balance:X-bi-6-4c-3``, so that spelling now hides the boxes on EVERY
        settled transfer and ``test_full_edit_offers_the_correction_only_on_a_settled_transfer``
        fails on it too; what only this case grades is a guard keyed on a
        side's record or day, which a ``$0.00`` close lacks.

        **Its plant moved at plan step ``balance:X-bi-6-4d-2``** (ruling
        R-BAL167 class 1, onto ruling **R-BAL141**'s shape; the coordinator's
        ruling of 2026-10-15 under R-BAL207).  It cleared the day off a
        settled transfer's twins and their records behind the seam (the
        legacy shape of finding **N-181**).  That state is unrepresentable
        since plan step ``balance:X-bi-6-4d-2``: a settled transfer holds a
        DATED record on each side or none, and the database refuses an
        un-dated one at commit (``budget.refuse_a_side_record_off_its_band``,
        pinned by ``test_an_undated_record_under_a_paid_transfer_is_refused``
        in ``tests/test_models/test_a_transfer_side_s_record_follows_its_status.py``'s
        ``TestASettledTransferRecordsBothSidesOrNeither``).
        The one settled-and-dayless state the database admits is a ``$0.00``
        close, which holds no record and no day (R-BAL141), so the transfer
        is settled and then closed at ``$0.00`` through the service.  The old
        body cleared the twins' days while the records it meant to clear hung
        off the transfer, so it likely graded nothing since 4d-2; that is
        UNMEASURED.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, figure=typed(Decimal("0")),
            )
            db.session.commit()
            db.session.expire_all()

            # PRECONDITION: settled, no record on either side, so no day.
            assert db.session.get(Transfer, xfer.id).status.is_settled
            assert [
                transfer_side_record(db.session, xfer.id, account_id)
                for account_id in (xfer.from_account_id, xfer.to_account_id)
            ] == [None, None]
            assert self._side_days(xfer.id) == {None}

            for url in (
                f"/transfers/{xfer.id}/full-edit",
                f"/transfers/{xfer.id}/full-edit?leg_account_id={savings.id}",
            ):
                response = auth_client.get(url)
                assert response.status_code == 200, (
                    f"{url} 500'd on a settled transfer with no day -- the "
                    "row that most needs the repair box"
                )
                body = response.get_data(as_text=True)
                for field in ("settled_on_from", "settled_on_to"):
                    box = re.search(rf'<input[^>]*name="{field}"[^>]*>', body)
                    assert box is not None, (
                        f"{url} hid the {field} box from a settled transfer "
                        "with no day, leaving it no way to state the real day"
                    )
                    assert 'value=""' in box.group(0), (
                        f"{url} pre-filled a day into {field} on a transfer "
                        "that carries none: " + box.group(0)
                    )

    def test_the_LEG_door_corrects_the_pair_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """PATCHing a settle day from a grid LEG corrects both sides and the ledger.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87)**: the
        SHADOW door this graded (``PATCH /transactions/<shadow>``, re-expressed
        by ``_shadow_mutations`` as a transfer update) is deleted; the grid's
        door is the transfer PATCH carrying ``leg_account_id``, and the claim
        is graded there, with the response the LEG's cell.
        The leg's popover is the transfer form, so this is the request that
        form makes: the day, and the leg it was opened from.  The pair and
        the ledger follow, as the transfers page's request makes them.  The
        pair's days are read off both sides' payment records, not the twins
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  The leg is
        the from-side, named off the transfer where it was read off that
        side's twin until plan step ``balance:X-bi-6-4d-2``, which births
        none (ruling R-BAL167 class 1).
        """
        with app.app_context():
            original = display_today() - timedelta(days=8)
            corrected = display_today() - timedelta(days=5)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, original,
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    **self._both_boxes(corrected),
                    "leg_account_id": str(xfer.from_account_id),
                },
            )
            assert response.status_code == 200, response.get_data(as_text=True)[:300]

            db.session.expire_all()
            assert self._side_days(xfer.id) == {corrected}, (
                "the leg door dropped the settle day: the request "
                "answered 200 and changed nothing"
            )
            assert self._net_by_day(xfer.id) == {corrected: Decimal("200.00")}, (
                "the leg door moved the day without the ledger: "
                f"{self._net_by_day(xfer.id)}"
            )

    def test_the_LEG_door_drops_a_day_beside_a_revert(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A revert from a grid LEG drops the day rather than 400ing.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87)**: the
        SHADOW door this graded (``PATCH /transactions/<shadow>``, re-expressed
        by ``_shadow_mutations`` as a transfer update) is deleted; the grid's
        door is the transfer PATCH carrying ``leg_account_id``, and the claim
        is graded there, with the response the LEG's cell.
        Ruling **R-EG** through the grid's door.  Without the drop this request
        reaches the service with a day stated for a Projected transfer and
        raises.  What is graded here is the drop, and that the
        refusal-free response is the leg's cell.  Both sides' days are read
        off their payment records, not the twins (ruling R-BAL167 class 1,
        plan step balance:X-bi-6-4d-2).  The leg is the from-side, named off
        the transfer where it was read off that side's twin until plan step
        ``balance:X-bi-6-4d-2``, which births none (ruling R-BAL167 class 1).
        """
        with app.app_context():
            settled_day = display_today() - timedelta(days=6)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, settled_day,
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "leg_account_id": str(xfer.from_account_id),
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                    **self._both_boxes(settled_day),
                },
            )
            assert response.status_code == 200, (
                "the leg door refused a revert because the payload carried "
                f"the row's own settle day: {response.get_data(as_text=True)[:300]}"
            )

            db.session.expire_all()
            xfer = db.session.get(Transfer, xfer.id)
            assert xfer.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert self._side_days(xfer.id) == {None}
            assert self._net_by_day(xfer.id) == {}, (
                "the revert through the leg door left the effect posted"
            )


class TestTheTransferSideBoxes:
    """The popover's two day boxes, one per side (ruling **R-BAL108**).

    ``_render_helpers.transfer_side_boxes`` is the ONE producer the popover
    renders its boxes from AND the PATCH grades a submission against, so what
    a box shows and what an untouched Save states cannot part.  Each box reads
    ITS side's day off that side's leg record (plan step
    ``balance:X-bi-6-4c-3``; ``Transfer.settled_on`` read one day for the pair
    off the income shadow until then, and these cases replace that property's
    tests).  A side holding its own day shows it with how it is known; a side
    borrowing the other's renders EMPTY with the borrowed day in its caption
    (ruling **R-BAL164**).
    """

    @staticmethod
    def _boxes(xfer_id):
        """Return both boxes for *xfer_id*, drawn from a fresh load."""
        db.session.expire_all()
        xfer = db.session.get(Transfer, xfer_id)
        return transfer_side_boxes(
            xfer, transfer_legs.covering_movements_by_leg([xfer_id]),
        )

    @staticmethod
    def _transfer(seed_user, seed_periods_today):
        """Return a Projected Checking -> Savings transfer, committed."""
        savings = _create_savings_account(seed_user)
        xfer = _create_transfer(seed_user, seed_periods_today, savings)
        db.session.commit()
        return xfer

    def test_a_projected_transfer_offers_no_day_on_either_side(
        self, app, seed_user, seed_periods_today,
    ):
        """No money moved, so neither box holds a day or a caption."""
        with app.app_context():
            xfer = self._transfer(seed_user, seed_periods_today)

            from_box, to_box = self._boxes(xfer.id)

            assert (from_box.field, to_box.field) == (
                "settled_on_from", "settled_on_to",
            )
            assert (from_box.account_id, to_box.account_id) == (
                xfer.from_account_id, xfer.to_account_id,
            )
            for box in (from_box, to_box):
                assert box.prefill is None
                assert box.caption is None

    def test_a_paid_press_leaves_both_boxes_empty_and_names_the_guess(
        self, app, seed_user, seed_periods_today,
    ):
        """Neither side has a day of its own after Paid: both boxes empty.

        Both sides borrow the day Paid was pressed (ruling **R-BAL163**), so
        neither box is prefilled -- a day typed into either is that side's own
        -- and each caption says the day is a guess.
        """
        with app.app_context():
            xfer = self._transfer(seed_user, seed_periods_today)
            transfer_service.settle_transfer(xfer.id, seed_user["user"].id)
            db.session.commit()

            from_box, to_box = self._boxes(xfer.id)

            guess = f"{display_today():%m-%d} (a guess)"
            assert (from_box.prefill, from_box.caption) == (None, guess)
            assert (to_box.prefill, to_box.caption) == (None, guess)

    def test_a_side_with_its_own_day_shows_it_and_the_other_borrows(
        self, app, seed_user, seed_periods_today,
    ):
        """The bank showed Checking's side; Savings' box is empty and says whose day it has."""
        with app.app_context():
            xfer = self._transfer(seed_user, seed_periods_today)
            day = display_today() - timedelta(days=4)
            transfer_service.settle_transfer(
                xfer.id, seed_user["user"].id,
                side_days=(transfer_service.SideDay(
                    xfer.from_account_id, an_observed_day(day),
                ),),
            )
            db.session.commit()

            from_box, to_box = self._boxes(xfer.id)

            assert from_box.prefill == an_observed_day(day)
            assert from_box.caption == "bank-shown"
            assert to_box.prefill is None
            assert to_box.caption == (
                f"same as {from_box.account_name}, {day:%m-%d} (a guess)"
            )

    def test_each_box_reads_its_OWN_side(
        self, app, seed_user, seed_periods_today,
    ):
        """Two sides on two days, each box answering for its own, both ways round.

        The per-side successor of ``Transfer.settled_on``'s "it reads the
        income shadow": a box that read the OTHER side, or one fixed side for
        both, shows the wrong day in one of the two arrangements.  The days are
        swapped between the arrangements, so a box that answered with a value
        the fixture happened to write last cannot pass both.
        """
        with app.app_context():
            xfer = self._transfer(seed_user, seed_periods_today)
            early = display_today() - timedelta(days=6)
            late = display_today() - timedelta(days=4)
            transfer_service.settle_transfer(
                xfer.id, seed_user["user"].id,
                side_days=(
                    transfer_service.SideDay(
                        xfer.from_account_id, an_observed_day(early),
                    ),
                    transfer_service.SideDay(
                        xfer.to_account_id, an_entered_day(late),
                    ),
                ),
            )
            db.session.commit()

            from_box, to_box = self._boxes(xfer.id)
            assert (from_box.prefill, from_box.caption) == (
                an_observed_day(early), "bank-shown",
            )
            assert (to_box.prefill, to_box.caption) == (
                an_entered_day(late), "you typed it",
            )

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                side_days=(
                    transfer_service.SideDay(
                        xfer.from_account_id, an_entered_day(late),
                    ),
                    transfer_service.SideDay(
                        xfer.to_account_id, an_entered_day(early),
                    ),
                ),
            )
            db.session.commit()

            from_box, to_box = self._boxes(xfer.id)
            assert from_box.prefill == an_entered_day(late)
            assert to_box.prefill == an_entered_day(early)

    def test_a_soft_deleted_transfer_offers_no_day(
        self, app, seed_user, seed_periods_today,
    ):
        """A deleted transfer moves no money, so neither box answers for it.

        The successor of ``Transfer.settled_on``'s soft-deleted-shadow case:
        each box reads its leg's record through the grid's loader, and a
        deleted transfer holds none.

        **Deleted through the transfer's own door since plan step
        ``balance:X-bi-6-4d-2``** (ruling R-BAL167 class 2, plan step
        balance:X-bi-6-4d-2): it hid the twins on the ORM rows, which no
        longer hold the payments (they hang off the transfer), and a
        transfer hidden holding them is refused at commit (the deleted-row
        rule's transfer arm).  The door hides the transfer and its twins and
        takes both payments off in the same save (ruling
        **credit_card:R-CC75**).
        """
        with app.app_context():
            xfer = self._transfer(seed_user, seed_periods_today)
            transfer_service.settle_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(display_today() - timedelta(days=3)),
                ),
            )
            db.session.commit()
            assert self._boxes(xfer.id)[0].prefill is not None, (
                "the fixture's transfer showed no day before the delete, so "
                "the assertion below would pass on nothing"
            )
            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            assert db.session.get(Transfer, xfer.id).is_deleted is True

            for box in self._boxes(xfer.id):
                assert box.prefill is None
                assert box.caption is None
