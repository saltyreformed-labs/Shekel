"""
Shekel Budget App -- Grid designed error-fragment tests.

The marker-header convention (closeout plan session 4, ruled
2026-07-11): grid mutation rejections that used to return raw strings
or JSON -- bodies the app-wide htmx config silently drops -- now
re-render the requesting surface (desktop cell, mobile card, entry
list) with CURRENT data plus the rejection message, stamped with the
``Shekel-Designed-Fragment`` header so the one global listener in
``app.js`` swaps them.  These tests pin the response contract per
surface; the state-machine legality itself is covered by the C-21
suites.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.extensions import db
from app.models.ref import AccountType, Status, TransactionType
from app.models.settled_figure import SettledFigure
from app.services import account_service, status_seam, transfer_service
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER
from tests._test_helpers import one_off_row_of, resolved_amount, settlement_if_settling
from app.models.amount_ownership import AmountOwnership


def _create_expense(seed_user, seed_periods_today, status_name="Projected"):
    """Insert an expense in the given status for a rejection attack.

    Mirrors the ``test_c21_state_machine_routes`` helper so this suite
    shares the auth_client / seed_user wiring.  The status is written
    directly (bypassing the route layer) because the point is to START
    from a state the attacked action is illegal from.
    """
    status = db.session.query(Status).filter_by(name=status_name).one()
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    txn = one_off_row_of(
        seed_periods_today[0],
        name="Fragment Test Expense",
        amount=Decimal("55.00"),
        user_id=seed_periods_today[0].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense_type.id,
        category_id=seed_user["categories"]["Groceries"].id,
    )
    txn.status_id = status.id
    db.session.commit()
    return txn


class TestDesktopCellErrorFragment:
    """Cell-targeted rejections return the cell re-rendered + marker."""

    def test_illegal_patch_transition_renders_cell_with_names(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An illegal PATCH returns the marked cell naming both statuses.

        Paid -> Cancelled is unreachable (money that moved cannot be
        un-moved by re-labelling; the row must revert first).  The 400 body
        must be the cell fragment (not a raw string), carry the marker header,
        and name the two statuses in words -- the id-only message the state
        machine used to emit reads as noise in a user-facing hint.

        The specimen was ``Projected -> Settled`` until plan step
        **balance:X-am** deleted the terminal archive -- after which Projected
        reaches every status the enum has, so there is no illegal move FROM it
        to render.  What this case grades is the FRAGMENT, not the rule, so it
        moves to a refusal that still exists.
        """
        with app.app_context():
            txn = _create_expense(seed_user, seed_periods_today)
            paid_id = ref_cache.status_id(StatusEnum.DONE)
            cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)
            status_seam.apply_status_change(
                txn, paid_id,
                settlement=settlement_if_settling(txn, paid_id),
            )
            db.session.commit()

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(cancelled_id)},
            )
            assert resp.status_code == 400
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert "txn-chip" in body
            assert "bi-exclamation-octagon" in body
            # Status NAMES lead the message; the ids stay in parens.
            assert "Paid" in body
            assert "Cancelled" in body

            db.session.refresh(txn)
            assert txn.status_id == paid_id

    def test_mark_done_on_cancelled_renders_cell_error(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Mark Paid on a Cancelled row: designed 400, row untouched.

        The stale-surface race D2 names: another device cancels, then a
        still-open surface posts mark-done.  Cancelled -> Paid is
        illegal; before the convention the rejection body was silently
        dropped and the click did nothing visible.
        """
        with app.app_context():
            txn = _create_expense(
                seed_user, seed_periods_today, status_name="Cancelled",
            )
            cancelled_id = txn.status_id

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 400
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert "txn-chip" in body
            assert "Cancelled" in body

            db.session.refresh(txn)
            assert txn.status_id == cancelled_id

    def test_patch_schema_422_renders_cell_error(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A malformed PATCH field returns the marked cell at 422.

        The flattened Marshmallow message replaces the old JSON body
        the client dropped; the stored amount survives.
        """
        with app.app_context():
            txn = _create_expense(seed_user, seed_periods_today)

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "abc"},
            )
            assert resp.status_code == 422
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert "txn-chip" in body
            assert "estimated_amount" in body

            db.session.refresh(txn)
            # Unchanged, read through the resolver: the row is a ONE-OFF (plan step
            # balance:X-bi-7c), priced by its definition, so the raw column is None
            # and the app's resolver is the reader (ruling R-BAL60).
            assert resolved_amount(txn) == Decimal("55.00")


class TestMobileCardErrorFragment:
    """Card-targeted rejections return the card + banner, not the cell."""

    def test_mark_done_on_cancelled_renders_card_banner(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A mobile Mark Paid rejection re-renders THAT card with a banner.

        The request carries ``render=mobile_card`` + ``card_prefix``,
        so the 400 body must be the ``#card-tp-<id>`` wrapper (a cell
        fragment would not resolve against the card's hx-target) with
        the danger banner inside the wrapper -- outside it, the next
        successful swap would orphan the banner.
        """
        with app.app_context():
            txn = _create_expense(
                seed_user, seed_periods_today, status_name="Cancelled",
            )

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={
                    "render": "mobile_card",
                    "card_prefix": "tp",
                    "can_edit": "1",
                },
            )
            assert resp.status_code == 400
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert f'id="card-tp-{txn.id}"' in body
            assert "alert-danger" in body
            assert "Cancelled" in body


def _create_transfer(seed_user, seed_periods_today):
    """Create a projected transfer (with shadows) via the service.

    Mirrors the ``test_transfers._helpers`` helper: a second Savings account
    plus one ad-hoc transfer between it and the seeded checking
    account.
    """
    savings_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Fragment Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(savings)
    db.session.flush()
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(Decimal("200.00")),
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=seed_user["categories"]["Rent"].id,
            name="Fragment Transfer",
        ),
    )
    db.session.commit()
    return xfer


class TestTransferErrorFragment:
    """Transfer-route rejections return the marked cell fragment."""

    def test_illegal_patch_status_renders_transfer_cell(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """PATCH status_id=Credit returns the marked transfer cell.

        Credit is a transaction-only status (the transfer map excludes
        it); the 400 body used to be a JSON errors dict the client
        dropped -- now it is the transfer cell re-rendered with the
        rejection in its hint.
        """
        with app.app_context():
            xfer = _create_transfer(seed_user, seed_periods_today)
            credit_id = ref_cache.status_id(StatusEnum.CREDIT)

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"status_id": str(credit_id)},
            )
            assert resp.status_code == 400
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert "bi-exclamation-octagon" in body
            assert "Invalid transfer status transition" in body

            db.session.refresh(xfer)
            assert xfer.status_id == ref_cache.status_id(
                StatusEnum.PROJECTED,
            )

    def test_cancel_settled_transfer_designed_400_not_500(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Cancelling a settled transfer is a designed 400, not a crash.

        Before session 4 the transfer mark-done/cancel routes did not
        catch the state machine's ValidationError at all, so this
        stale-surface race crashed the request as a 500.
        """
        with app.app_context():
            xfer = _create_transfer(seed_user, seed_periods_today)
            # Walk the parent to Paid, bypassing the route layer; the cancel
            # below is verified against the PARENT's status before any shadow
            # is touched.  It walked one step further, to the terminal
            # ``Settled`` archive, until plan step **balance:X-am** deleted
            # that status -- and ``done -> cancelled`` is the refusal the
            # transfer map has always had underneath it.  Laid bare, the
            # Paid transfer holds no side record, the shape of a ``$0.00``
            # close, so it stores the figure that close stores, ``0.00`` and
            # typed (ruling R-BAL365): since plan step balance:X-bi-6-4d-2's
            # 4d-4b-2 leaf the band trigger's figure arm refuses a settled
            # transfer storing no figure.
            paid_id = ref_cache.status_id(StatusEnum.DONE)
            xfer.status_id = paid_id
            xfer.settled_figure_pair = SettledFigure(
                Decimal("0.00"),
                ref_cache.movement_figure_source_id(
                    MovementFigureSourceEnum.TYPED,
                ),
            )
            db.session.commit()

            resp = auth_client.post(f"/transfers/instance/{xfer.id}/cancel")
            assert resp.status_code == 400
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert "Invalid transfer status transition" in body
            assert "Paid" in body

            db.session.refresh(xfer)
            assert xfer.status_id == paid_id


class TestEntryListErrorFragment:
    """Entry-list rejections return the list + banner at the host id."""

    def test_create_entry_schema_422_renders_list_banner(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A bad entry amount re-renders the entry list with the reason.

        The 422 body must be the ``#entry-list-<id>`` fragment (the
        request's own hx-target) with a danger banner carrying the
        flattened field message, replacing the old raw ``str(errors)``
        dict repr the client dropped.
        """
        with app.app_context():
            txn = _create_expense(seed_user, seed_periods_today)

            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "abc",
                    "description": "Bad amount",
                    "purchased_on": "2026-07-11",
                },
            )
            assert resp.status_code == 422
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert f'id="entry-list-{txn.id}"' in body
            assert "alert-danger" in body
            assert "amount" in body
