"""Ruling R-IO at the one door that states a transfer's amount: who owns the figure after each kind
of edit, on a generated and an ad-hoc transfer.
"""

import re
from decimal import Decimal

import pytest
import sqlalchemy.exc

from app.enums import AmountSourceEnum
from app.extensions import db as _db
from app.models.escrow_line import EscrowComponentVersion, EscrowLine
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import account_service, transfer_service
from app.services.amount_ownership import owns_its_amount
from tests._test_helpers import (
    constraint_name_from,
    generate_transfer_of,
    make_every_period_rule,
    open_books_before_the_first_assertion,
    transfer_amount,
)
from tests.test_integration.test_transfer_settle_freeze import _derived_loan_transfer
from app.models.amount_ownership import AmountOwnership
from app.services.amount_ownership import declare_derived, derived_ownership
from tests._test_helpers import rendered_transfer_amount
from datetime import date
from app.services import template_amount_service

from ._helpers import _CONTRACT, _TYPED, _plain_transfer, _reread


def _generated_transfer(seed_user, seed_periods, series="500.00"):
    """A TEMPLATE-LINKED transfer, priced by its definition.

    It was ``_generated_pair`` and returned the transfer AND its two shadow
    legs until plan step balance:X-bi-6-4d-2, where a transfer became one row
    (ruling **R-BAL167** class 4).

    **The sibling of :func:`_plain_transfer`, and the difference is load-bearing
    rather than cosmetic since plan step X-au-f.**  An ad-hoc transfer may not
    declare a relation at all -- ``ck_transfers_adhoc_owns_amount`` refuses it,
    because nobody generated it and no definition states its price -- so a case
    whose subject is *the definition speaking* cannot be built on one.  Before
    X-au-f the definition spoke by sending a FIGURE, which an ad-hoc row could
    accept; it speaks by stating an ``AmountOwnership`` now (ruling
    **R-BAL11**), and the schema refuses that on a row with no template.  The
    three cases that used ``_plain_pair`` (now :func:`_plain_transfer`) for it
    were therefore asserting against a shape the app cannot produce.

    The price is stated through ``template_amount_service.set_amount``, the one
    write door, which is what every app-side template create calls.
    """
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            name="Sinking Fund",
            account_type_id=seed_user["account"].account_type_id,
            anchor_balance=Decimal("100.00"),
        ),
    )
    _db.session.flush()
    # RE-EXPRESSED at plan step pay_calendar:C18-a under CLAUDE.md rule 5,
    # developer-confirmed 2026-09-22 ("Yes: open books before rows"): the
    # destination's books open before the schedule, as the seeded account's
    # do.  ``create_account`` opens them TODAY, after the first paycheck this
    # case generates into, and since ruling R-PC85 a definition's occurrences
    # start above the books of every account it moves money in.
    open_books_before_the_first_assertion(_db.session, savings)
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        name="Sinking Fund Contribution",
        default_amount=Decimal(series),
    )
    _db.session.add(template)
    _db.session.flush()
    template_amount_service.set_amount(
        template, Decimal(series), effective_on=date(2000, 1, 1),
    )
    # The transfer is the ENGINE's (:func:`generate_transfer_of`, plan step
    # balance:X-ch): derived, parent and both legs, on the definition's own
    # date.  A hand-built one owned its figure and was linked afterwards, a
    # shape no producer writes since X-au-f.
    make_every_period_rule(_db.session, template)
    return generate_transfer_of(template, seed_periods[0])


class TestWhoOwnsTheFigureAfterAnEdit:
    """Ruling R-IO at the one door that states a transfer's amount.

    **Asked of the TRANSFER since plan step balance:X-bi-6-4d-2** (ruling
    **R-BAL167** class 1).  Each case asked its two shadow legs whether they
    owned the figure, what they stored and what they were worth; a transfer is
    one row since then and both its legs are read off it (**R-BAL13**), so the
    same three questions are asked of the transfer -- ``owns_its_amount``,
    ``amount`` and :func:`~tests._test_helpers.transfer_amount` -- with every
    figure unchanged.  A leg's ownership was a COPY of the transfer's:
    ``transfer_service._amount.apply_amount_ownership`` wrote the parent's
    stated ownership onto each leg (at ``5f46d6973``), so asking the transfer
    asks what the legs copied.  The one state where the two differed is an
    AD-HOC transfer no edit that STATED an amount has touched (that function
    ran only when an ownership was stated, so a bare period move or a
    notes-only save left it too) -- it owns its figure from birth while its
    legs were born derived -- and the case that read that difference,
    ``test_an_AD_HOC_retype_lands_on_the_transfer_and_raises_no_flag``, says
    so.
    """

    def test_a_definition_driven_amount_moves_both_legs_and_writes_neither(
        self, app, db, seed_user, seed_periods,
    ):
        """The DEFINITION's ownership leaves both legs derived, reading its series.

        The recurrence maintain pass sends exactly this -- an
        ``AmountOwnership`` naming TEMPLATE, no figure and no ownership claim
        for the operator -- and it must not un-declare anything.  The legs
        follow the new price because they READ it, which is the copy and its
        two repairs replaced by one arrow.

        **The caller states no FIGURE since plan step X-au-f** (ruling
        **R-BAL11**): it sent ``amount=$400.00`` with an ``amount_authored``
        of ``False``, which said *the definition re-priced this* by handing the
        service the definition's own number to store.  The definition's number
        lives in its SERIES now, so the act is to state WHO prices the row and
        let the series answer -- and this case re-states the series to
        ``$400.00`` first, so the figure it asserts is the same one it always
        asserted and reaches the legs by the mechanism that actually carries it.
        """
        with app.app_context():
            xfer = _generated_transfer(seed_user, seed_periods)

            # The definition re-prices itself, through the one write door.
            template_amount_service.set_amount(
                xfer.template, Decimal("400.00"),
                effective_on=xfer.due_date,
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount_ownership=derived_ownership(AmountSourceEnum.TEMPLATE),
            )
            db.session.commit()

            edited = _reread(xfer.id)
            assert edited.amount is None
            assert owns_its_amount(edited) is False
            assert transfer_amount(edited) == Decimal("400.00")

    def test_an_owner_stated_amount_is_what_the_transfer_stores_and_resolves(
        self, app, db, seed_user, seed_periods,
    ):
        """An owner-stated amount, ``is_override`` in the same call, lands on the transfer.

        Ruling **R-IO**: the figure a human types always wins.  The door
        translates the caller's own statement into the model's column -- it does
        not READ the stored flag to decide a price, which is finding N-262's
        rule; ``is_override`` here is the kwarg this call carried.  The same
        call took the figure back for both shadow legs until plan step
        balance:X-bi-6-4d-2.

        **Renamed at plan step balance:X-bi-6-4d-2's checkpoint 2d-a** (it was
        ``test_an_owner_stated_amount_makes_both_legs_own_it``, a claim about
        the legs it no longer asserts).  **Its ownership read went at
        checkpoint 2c-a** (ruling **R-BAL167** class 1, the cp2c review's L-4
        applied to this case).
        The fixture is AD-HOC, whose legs were born derived and were TAKEN by
        this statement; the transfer owns its figure from birth
        (``ck_transfers_adhoc_owns_amount``), so ``owns_its_amount`` asked of
        it could not fail.  The refusal that grades that CHECK is the AD-HOC
        retype case's, and a TAKE that changes ownership is graded on a
        template-linked transfer by
        ``test_stating_the_definitions_ownership_hands_a_taken_leg_back``.
        The `$400.00` stored and resolved stands.
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods)

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount_ownership=AmountOwnership.own(Decimal("400.00")),
                is_override=True,
            )
            db.session.commit()

            edited = _reread(xfer.id)
            assert edited.amount == Decimal("400.00")
            assert transfer_amount(edited) == Decimal("400.00")

    def test_stating_the_definitions_ownership_hands_a_taken_leg_back(
        self, app, db, seed_user, seed_periods,
    ):
        """The hand-back is an OWNERSHIP now, and the bare flag no longer is one.

        The conflict resolver's *use the definition* action sends exactly this
        (``transfer_recurrence.resolve_conflicts``).  **It sent
        ``is_override=False`` and, when it had one, a figure, until plan step
        X-au-f** -- and this case was named for that spelling.  Ruling
        **R-BAL11** replaced the three parameters with one: the resolver states
        ``derived_ownership(TEMPLATE)``, and a bare flag says nothing about the
        amount at all, because the flag means *this row is the OWNER's, not the
        rule's* (X-au-h) and no longer decides a price.

        **That is the whole of what changed, and the ACT is unchanged**: the
        pair goes back to being priced by its definition, both legs stop owning
        the figure they were frozen at, and the drift this step exists to make
        unconstructible stays unconstructible.  The behaviour this all replaces
        resumed deriving the moment the flag cleared, because pricing READ the
        flag; ownership is declared now, so somebody has to write it back, and
        this is the case that says who.

        **A first draft of ``apply_amount_ownership`` ran only under
        ``"amount" in updates`` and did not reach this at all**, leaving both
        legs owning the figure they had been frozen at -- for ever, since no
        later act would look at them again.

        The pair is TEMPLATE-linked because the act requires it: a definition
        cannot speak for an ad-hoc transfer, and
        ``ck_transfers_adhoc_owns_amount`` refuses the declaration outright.
        """
        with app.app_context():
            xfer = _generated_transfer(seed_user, seed_periods)
            user_id = seed_user["user"].id
            xfer_id = xfer.id

            transfer_service.update_transfer(
                xfer_id, user_id,
                amount_ownership=AmountOwnership.own(Decimal("400.00")),
                is_override=True,
            )
            db.session.commit()
            assert owns_its_amount(_reread(xfer_id)) is True

            # A BARE flag states nothing about the amount, so it leaves the
            # transfer exactly as it stands -- asserted, because "the
            # hand-back moved to the ownership" is only true if the old
            # spelling stopped performing it.
            transfer_service.update_transfer(
                xfer_id, user_id, is_override=False,
            )
            db.session.commit()
            assert owns_its_amount(_reread(xfer_id)) is True

            transfer_service.update_transfer(
                xfer_id, user_id,
                amount_ownership=derived_ownership(AmountSourceEnum.TEMPLATE),
            )
            db.session.commit()

            edited = _reread(xfer_id)
            assert edited.amount is None
            assert owns_its_amount(edited) is False
            assert transfer_amount(edited) == Decimal("500.00")

    def test_a_bare_period_move_does_NOT_freeze_a_legs_derivation(
        self, app, db, seed_user, seed_periods,
    ):
        """``is_override=True`` with no amount leaves both legs deriving.

        A period move sets the flag too -- carry-forward and the transfers page
        both do it -- and the flag froze a leg's live figure against every later
        derivation, which is finding **N-238**'s recorded exposure.  It cannot
        here: ownership is DECLARED, and a move states no figure to own.

        The loan is the shape where it is visible, because a leg's derivation is
        not its parent's amount there.  The escrow is raised after the move: a
        frozen leg would still read ``$1,499.10``.
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            loan_id = xfer.to_account_id
            xfer_id = xfer.id

            transfer_service.update_transfer(
                xfer_id, seed_user["user"].id,
                pay_period_id=seed_periods[1].id,
                is_override=True,
            )
            db.session.commit()

            assert owns_its_amount(_reread(xfer_id)) is False

            # The VERSION carries the figure, not the line: ``EscrowLine`` has
            # no ``annual_amount`` at all, so writing one there sets an unmapped
            # attribute and changes nothing -- which is how a first draft of
            # this case "passed".
            version = (
                db.session.query(EscrowComponentVersion)
                .join(EscrowLine, EscrowLine.id == EscrowComponentVersion.line_id)
                .filter(EscrowLine.account_id == loan_id)
                .one()
            )
            version.annual_amount = Decimal("4800.00")
            db.session.commit()

            # P&I 1,199.10 + the NEW escrow 400.00.  A frozen transfer reads
            # the old 1,499.10, so this figure is what says the derivation
            # survived.
            assert transfer_amount(_reread(xfer_id)) == Decimal("1599.10")

    def test_a_bare_period_move_does_not_TAKE_a_leg_back_either(
        self, app, db, seed_user, seed_periods,
    ):
        """A move states no figure, so it changes no ownership in EITHER direction.

        The complement of the case above, and the one that cost money in a
        draft.  ``carry_forward_service`` moves a transfer with
        ``is_override=True`` and NO amount; an earlier
        ``apply_amount_ownership`` re-declared both legs whenever it was reached
        without a figure, so carrying a hand-priced payment forward silently
        handed it back to its parent.

        On a plain transfer that is invisible -- the leg's derivation IS its
        parent's amount -- so the shape that shows it is a DERIVE-mode loan
        payment, where handing back means reverting ``$1,325.00`` to the
        contract's ``$1,499.10``.  ``$174.10`` of a real payment, discarded by a
        period move.
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            user_id = seed_user["user"].id
            xfer_id = xfer.id

            transfer_service.update_transfer(
                xfer_id, user_id, amount_ownership=AmountOwnership.own(_TYPED),
                is_override=True,
            )
            db.session.commit()
            assert transfer_amount(_reread(xfer_id)) == _TYPED

            transfer_service.update_transfer(
                xfer_id, user_id,
                pay_period_id=seed_periods[1].id,
                is_override=True,
            )
            db.session.commit()

            moved = _reread(xfer_id)
            assert owns_its_amount(moved) is True
            assert moved.amount == _TYPED
            assert transfer_amount(moved) == _TYPED

    def test_the_FORM_s_period_move_echoes_the_amount_and_takes_nothing(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The BROWSER's payload, not a hand-picked one -- and it is the arm that bites.

        **The two cases above call the service with the CARRY-FORWARD payload**
        (``pay_period_id`` and ``is_override``, no ``amount``), and an
        adversarial review of this step pointed out that no browser sends that.
        ``routes/transfers/mutations.py`` records as a MEASURED fact that "this
        form renders the Amount input on every editable row and an HTML form
        posts every input it renders", and it raises ``is_override`` when the
        amount changed **or the PERIOD did** -- so a period-only save arrives
        with the amount ECHOED BACK UNCHANGED beside a raised flag.

        Taking ownership on the flag alone would convert this derive-mode loan
        payment's two legs to OWN, frozen for ever at ``transfers.amount`` --
        the stale creation-time ``$1.00``, against a ``$1,499.10`` installment.
        That is finding **N-238**'s exposure being ADDED by the step whose
        docstring claims to remove it, and it is why ownership requires the
        stated figure to have MOVED rather than merely to have been posted.

        `feedback_a_route_test_must_post_what_the_template_emits`: a payload
        assembled by hand grades an arm a browser never takes.
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id
            assert transfer_amount(xfer) == _CONTRACT

            # Every field the edit form renders for an editable row, with the
            # amount echoed at its CURRENT value and only the period moved.
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": rendered_transfer_amount(xfer),
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "pay_period_id":str(seed_periods[1].id),
                    "status_id": str(xfer.status_id),
                    "notes": "",
                },
            )
            assert resp.status_code == 200, resp.data

            db.session.expire_all()
            # The PREMISE of this case: the period really did move.  Without
            # it, a regression that silently dropped ``pay_period_id`` would
            # leave the case green while it graded a no-op save.
            moved = db.session.get(Transfer, xfer_id)
            assert moved.pay_period_id == seed_periods[1].id
            assert owns_its_amount(moved) is False, (
                "an echoed amount is not an authored one"
            )
            assert moved.amount is None
            assert transfer_amount(moved) == _CONTRACT

    def test_the_FORMs_period_move_does_not_HAND_BACK_a_taken_leg(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """PROBE: the intersection neither period-move case above covers.

        ``test_a_bare_period_move_does_not_TAKE_a_leg_back_either`` starts from
        a TAKEN leg but sends the CARRY-FORWARD payload (no ``amount``).
        ``test_the_FORM_s_period_move_echoes_the_amount_and_takes_nothing``
        sends the BROWSER's payload but starts from a DERIVED leg.  Neither
        grades a taken leg moved by the form.
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id

            # 1. The owner types $1,325.00 through the real form.
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": str(_TYPED),
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "pay_period_id":str(xfer.pay_period_id),
                    "status_id": str(xfer.status_id),
                    "notes": "",
                },
            )
            assert resp.status_code == 200, resp.data
            typed_over = _reread(xfer_id)
            assert owns_its_amount(typed_over) is True, "the typed figure is owned"
            assert transfer_amount(typed_over) == _TYPED

            # 2. The owner moves ONLY the period, through the same form -- so
            #    the amount box echoes the $1,325.00 it now renders.
            xfer = db.session.get(Transfer, xfer_id)
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": rendered_transfer_amount(xfer),
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "pay_period_id":str(seed_periods[1].id),
                    "status_id": str(xfer.status_id),
                    "notes": "",
                },
            )
            assert resp.status_code == 200, resp.data

            db.session.expire_all()
            # The PREMISE of this case: the period really did move.  Without
            # it, a regression that silently dropped ``pay_period_id`` would
            # leave the case green while it graded a no-op save.
            moved = db.session.get(Transfer, xfer_id)
            assert moved.pay_period_id == seed_periods[1].id
            assert transfer_amount(moved) == _TYPED, (
                "a period move must not revert the owner's typed figure "
                "to the contract"
            )
            assert owns_its_amount(moved) is True

    def test_an_UNRELATED_field_save_does_not_HAND_BACK_a_taken_leg(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """PROBE: the echo WITHOUT the flag -- the door the period move does not use.

        The route raises ``is_override`` only on an amount or period DELTA
        (``mutations.py``), so a save that changes NOTES, CATEGORY or STATUS
        carries the echoed amount and NO flag at all.
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id

            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": str(_TYPED),
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "pay_period_id":str(xfer.pay_period_id),
                    "status_id": str(xfer.status_id),
                    "notes": "",
                },
            )
            assert resp.status_code == 200, resp.data
            assert transfer_amount(_reread(xfer_id)) == _TYPED

            # A NOTES-only save: same amount, same period, same status.
            xfer = db.session.get(Transfer, xfer_id)
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": rendered_transfer_amount(xfer),
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "pay_period_id":str(xfer.pay_period_id),
                    "status_id": str(xfer.status_id),
                    "notes": "escrow went up in March",
                },
            )
            assert resp.status_code == 200, resp.data

            assert transfer_amount(_reread(xfer_id)) == _TYPED, (
                "a notes-only save must not revert the owner's figure"
            )

    def test_the_LEG_door_echo_leaves_the_parent_alone(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The grid's door onto a transfer's amount, echoing what it rendered.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87).**  It
        graded the SHADOW door -- ``/transactions/<shadow>/quick-edit`` priming
        a box with the RESOLVED amount and ``PATCH /transactions/<shadow>``
        re-expressed as a transfer update -- which is deleted: the grid draws
        a transfer's LEG off its parent, and the leg's popover is the
        TRANSFER form asked with ``?leg_account_id=``, whose box and companion
        the transfer PATCH grades.  So the door the grid uses renders the SAME
        figure the transfers page's popover renders, and an untouched save
        through it -- the payload read out of the rendered fragment, which is
        the only way to grade a door whose rendered value may not be the
        column -- must leave the parent's stored amount and both legs'
        derivation alone.  On a derive-mode loan payment the contract's figure
        and the stored column differ, which is what made the old shadow door
        cost real money (it rewrote the parent to the resolved figure and
        called it a human's); the leg's door is graded on the same row.

        The leg asked from is the transfer's from-side, where it was the
        expense twin's account, and "still the contract's" is asked of the
        transfer, where it was asked of each twin, until plan step
        balance:X-bi-6-4d-2 stopped the twins being born (ruling R-BAL167
        class 1; the account is the same one).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id, leg_account_id = xfer.id, xfer.from_account_id
            stored_before = xfer.amount

            edit = auth_client.get(
                f"/transfers/{xfer_id}/full-edit?leg_account_id={leg_account_id}",
            )
            assert edit.status_code == 200
            body = edit.data.decode()
            shown = re.search(r'name="amount"[^>]*value="([^"]*)"', body)
            companion = re.search(
                r'name="amount_as_rendered"[^>]*value="([^"]*)"', body,
            )
            assert shown is not None, "the leg's popover renders a figure"
            assert companion is not None, (
                "the leg's door owes the companion like every other door"
            )
            assert companion.group(1) == shown.group(1)
            assert f'name="leg_account_id" value="{leg_account_id}"' in body

            resp = auth_client.patch(f"/transfers/instance/{xfer_id}", data={
                "amount": shown.group(1),
                "amount_as_rendered": companion.group(1),
                "version_id": str(xfer.version_id),
                "leg_account_id": str(leg_account_id),
            })
            assert resp.status_code == 200, resp.data
            assert f'data-leg-account-id="{leg_account_id}"' in resp.data.decode()

            db.session.expire_all()
            echoed = db.session.get(Transfer, xfer_id)
            assert echoed.amount == stored_before, (
                "an echo through the leg door must not rewrite the parent"
            )
            assert owns_its_amount(echoed) is False, "still the contract's"

    def test_an_AD_HOC_retype_lands_on_the_transfer_and_raises_no_flag(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """An ad-hoc retype lands on the transfer as typed and raises no flag.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-2** (it was
        ``test_an_AD_HOC_retype_now_TAKES_both_legs``; ruling **R-BAL167**
        class 1).  What it was written to state -- an ad-hoc retype TAKES both
        legs, where it once left them derived, the arm that changed at plan
        step X-au-h -- was a change in the legs' ROWS, and no leg row is born
        since that step.  An ad-hoc transfer owns its figure from birth and
        both legs are read off it (**R-BAL13**), so there is no derived leg
        left to take and nothing for the X-au-h arm to change.  What stands is
        the rest of the case: the retype lands on the one row as `$400.00` and
        the route raises no ``is_override`` for a row with no template.  The
        paragraphs below describe the three-row shape this case was written
        against.

        **"The row still owns it" is a REFUSAL, not an ``owns_its_amount``
        read** (ruling **R-BAL167** class 1 with a refusal ADDED, the cp2c
        review's L-4: the case planted nothing, so the refusal is a shape it
        adds, not one it planted; labelled class 2 until checkpoint 2d-a).
        ``ck_transfers_adhoc_owns_amount`` refuses an ad-hoc transfer that
        declares a derived figure, so ``owns_its_amount`` is ``True`` for every
        ad-hoc row the database holds and the two reads it took could not
        fail.  The case asserts the database refuses the other state instead,
        on the retyped row.  Where the legs were rows the precondition
        discriminated -- they were born derived beside a parent that owned
        its figure -- and that difference is what the transfer cannot have.

        The old discriminator was ``is_override``, and
        ``routes/transfers/mutations.py`` raises that flag only for a
        template-linked row -- so an AD-HOC transfer's retype fell to the
        definition arm and left both legs DERIVED.  Authorship is now stated by
        the door for every row, so an ad-hoc retype takes them.

        **It moves no money and cannot**: an ad-hoc transfer has no template,
        ``loan_payment_settings`` is keyed by ``transfer_template_id``, so it
        can never be a derive-mode loan payment -- the arm where a leg's
        derivation differs from its parent's figure.  Both arms answer the
        typed figure.  What changed was that three rows STORED it where one
        had: the copy ``X-au-m`` was to remove until ruling **R-BAL91**
        dissolved that step into ``X-bi-6-4d``, which deleted the twin rows
        and the loop that wrote them, so one row stores it again.

        Graded because an adversarial review found the paragraph documenting the
        old arm had been deleted along with the comparison it described, leaving
        the change undocumented AND untested in either direction.
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods)
            xfer_id = xfer.id
            assert xfer.transfer_template_id is None, "the ad-hoc precondition"

            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "amount": "400.00",
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "status_id": str(xfer.status_id),
                    "notes": "",
                },
            )
            assert resp.status_code == 200, resp.data

            db.session.expire_all()
            retyped = db.session.get(Transfer, xfer_id)
            assert retyped.is_override is False, (
                "the route raises the flag only for a template-linked row"
            )
            assert retyped.amount == Decimal("400.00")
            assert transfer_amount(retyped) == Decimal("400.00")

            # It cannot stop owning the figure: no definition prices an
            # ad-hoc transfer, so the database refuses the declaration.
            declare_derived(retyped, AmountSourceEnum.TEMPLATE)
            with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert constraint_name_from(excinfo.value) == (
                "ck_transfers_adhoc_owns_amount"
            ), f"the derived ad-hoc transfer was refused by: {excinfo.value}"

    def test_a_later_definition_write_hands_a_taken_leg_back(
        self, app, db, seed_user, seed_periods,
    ):
        """The re-declaration is what makes drift unconstructible over time.

        A leg an owner took once must not be left behind at a stale figure when
        its definition later moves.  Without the ``else`` arm in
        ``_apply_amount`` this pair would read ``$400.00`` for ever while the
        transfer said ``$500.00`` -- the exact drift the deleted corrector
        existed to repair, reintroduced by the step that removed it.
        """
        with app.app_context():
            xfer = _generated_transfer(seed_user, seed_periods)
            user_id = seed_user["user"].id

            transfer_service.update_transfer(
                xfer.id, user_id, amount_ownership=AmountOwnership.own(Decimal("400.00")),
                is_override=True,
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, user_id, amount_ownership=derived_ownership(AmountSourceEnum.TEMPLATE),
                # The definition re-prices the pair (R-JR): not a human, so the
                # taken leg is handed back rather than left at a stale figure.

            )
            db.session.commit()

            handed_back = _reread(xfer.id)
            assert handed_back.amount is None
            assert owns_its_amount(handed_back) is False
            assert transfer_amount(handed_back) == Decimal("500.00")
