"""A CC payback's figure is not its own to type -- finding **N-252**.

A payback exists to repay the card spend of the row it names, so what it is
worth is a fact about THAT row.  Typing over it is a lie by one of two
mechanisms, and this suite grades both doors that used to let it happen:

* the full-edit popover, which rendered an Estimated box on a payback and took
  a figure;
* the PATCH handler behind it, which wrote one.

**The measured cost is production payback 2590**: edited to ``$123.18`` against
``$181.58`` of credit purchases on 2026-06-02 and settled at the edited figure,
so ``$58.40`` of card spend went unreported and no screen said anything.
``is_override`` cannot express the state -- ``mutations._apply_field_updates``
sets it only for a TEMPLATE-linked row, and a payback carries neither a
template nor a transfer link -- which is why a refusal is needed rather than
the flag.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db as _db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import credit_workflow
from app.services.entry_credit_workflow import sync_entry_payback
from tests._test_helpers import figure_source_columns, one_off_row_of, resolved_amount
from tests._legacy_credit import plant_old_credit


@pytest.fixture(name="payback_pair")
def _payback_pair(app, seed_user, seed_periods):
    """Return ``(source, payback)`` for a one-off expense marked Credit.

    The PAYBACK is the row under test: it carries no definition -- its one
    pricing link is the row it repays -- which is precisely the state
    ``is_override`` cannot describe and the reason this refusal exists.  The
    source is a one-off (a rule-less definition's placed row, plan step
    balance:X-bi-7c), as the grid writes one.

    Yields:
        The source :class:`~app.models.transaction.Transaction` and the payback
        the old Credit shape holds for it (``tests._legacy_credit``; no door
        writes one since plan task plan#2929).
    """
    with app.app_context():
        source = one_off_row_of(
            seed_periods[0],
            name="Card purchase",
            amount=Decimal("181.58"),
            user_id=seed_periods[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            category_id=seed_user["categories"]["Groceries"].id,
        )
        _db.session.flush()
        payback = plant_old_credit(source)
        _db.session.commit()
        yield source.id, payback.id


class TestThePopoverWithdrawsTheEstimate:
    """The screen does not offer a box for a figure it cannot honour."""

    def test_a_payback_gets_no_estimated_input(
        self, app, auth_client, payback_pair,
    ):
        """The Estimated INPUT is gone, and the figure is still shown.

        Withdrawn rather than ``disabled``: a disabled input still renders as
        an editable-looking control, and the row's budget is a real fact the
        popover should keep showing.  The copy has to say where to change it,
        or the refusal is a dead end.
        """
        _, payback_id = payback_pair
        with app.app_context():
            resp = auth_client.get(f"/transactions/{payback_id}/full-edit")

            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert 'name="estimated_amount"' not in body, (
                "the popover still offers an Estimated input on a payback, "
                "which is the door finding N-252 came through"
            )
            assert "181.58" in body, (
                "the figure itself must still be shown -- withdrawing the "
                "INPUT is not the same as hiding what the row is worth"
            )
            # **The repair NAMED must be the one this payback actually has.**
            # Its source is a whole transaction marked Credit and carries no
            # purchases at all, so "change the purchases it repays" would send
            # the owner to an act with no target -- a dead end that turns a
            # silently wrong figure into a permanently wrong one.  The real
            # repair is Undo CC on the source, correct it, re-mark it Credit.
            # A first version of this suite pinned the entry-language copy on
            # exactly this row-backed fixture (adversarial review 2026-08-20).
            assert "Undo" in body and "Credit again" in body, body[-700:]
            assert "purchases it repays" not in body

    def test_an_ordinary_row_still_gets_one(
        self, app, auth_client, seed_user, seed_periods, payback_pair,
    ):
        """The control: the refusal is about PAYBACKS, not about every row.

        Without this the case above would pass for a popover that had stopped
        rendering the Estimated input for everyone.
        """
        source_id, _ = payback_pair
        with app.app_context():
            resp = auth_client.get(f"/transactions/{source_id}/full-edit")

            assert resp.status_code == 200
            assert 'name="estimated_amount"' in resp.get_data(as_text=True)


class TestTheCopyNamesTheRepairTHISPaybackHas:
    """The two payback kinds are corrected by DIFFERENT acts.

    ``transaction_service.repays_tracked_purchases`` is the fork, and it is a
    money-adjacent one rather than a wording choice: naming the wrong repair
    leaves the owner with a figure they cannot correct at all.
    """

    def test_an_entry_backed_payback_is_sent_to_its_purchases(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """An envelope source HAS purchases, so that is the repair named."""
        with app.app_context():
            envelope = one_off_row_of(
                seed_periods[0],
                name="Card envelope",
                amount=Decimal("500.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
                is_envelope=True,
            )
            _db.session.flush()
            _db.session.add(TransactionEntry(
                **figure_source_columns(),
                transaction_id=envelope.id, account_id=envelope.account_id, owner_id=envelope.user_id,
                user_id=seed_user["user"].id, description="Card purchase",
                amount=Decimal("181.58"), is_credit=True,
            ))
            _db.session.flush()
            payback = sync_entry_payback(envelope.id, seed_user["user"].id)
            payback_id = payback.id
            _db.session.commit()

            body = auth_client.get(
                f"/transactions/{payback_id}/full-edit",
            ).get_data(as_text=True)

            assert 'name="estimated_amount"' not in body
            assert "purchases it repays" in body
            assert "Undo" not in body


class TestTheDeleteRefusalNamesTheRepairTHISPaybackHas:
    """The SAME fork, on the delete control the card grew at ``X-gb``.

    A payback may not be deleted on its own: its source stays Credit and out of
    the balance, so the spending it repays would sit in the books with nothing
    paying it off.  Which REPAIR the refusal names is
    ``transaction_service.repays_tracked_purchases`` again -- and *Undo CC* is
    a control on a row whose OWN status is Credit, so it does not exist for an
    entry-backed payback at all.  A first draft named it for both, and the
    class above is what caught it.
    """

    def test_a_row_backed_payback_is_sent_to_Undo_CC(
        self, app, auth_client, payback_pair,
    ):
        """Its source IS a Credit row, so the button is on that row's card."""
        _, payback_id = payback_pair
        with app.app_context():
            body = auth_client.get(
                f"/transactions/{payback_id}/full-edit",
            ).get_data(as_text=True)

            assert "Delete this row" not in body
            assert "Press Undo CC on the row it repays" in body

    def test_an_entry_backed_payback_is_sent_to_its_card_purchases(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Its source is an ordinary envelope, which has NO Undo CC button.

        The envelope's own ``status_id`` is Projected -- credit is per ENTRY on
        a tracked row -- so the card renders no Undo CC control for it, and a
        refusal naming one would send the owner to a button that is not on the
        screen.
        """
        with app.app_context():
            envelope = one_off_row_of(
                seed_periods[0],
                name="Card envelope",
                amount=Decimal("500.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
                is_envelope=True,
            )
            _db.session.flush()
            _db.session.add(TransactionEntry(
                **figure_source_columns(),
                transaction_id=envelope.id, account_id=envelope.account_id, owner_id=envelope.user_id,
                user_id=seed_user["user"].id, description="Card purchase",
                amount=Decimal("181.58"), is_credit=True,
            ))
            _db.session.flush()
            payback_id = sync_entry_payback(
                envelope.id, seed_user["user"].id,
            ).id
            _db.session.commit()

            body = auth_client.get(
                f"/transactions/{payback_id}/full-edit",
            ).get_data(as_text=True)

            assert "Delete this row" not in body
            assert "Remove the card purchases it repays" in body
            assert "Press Undo CC" not in body

            source = auth_client.get(
                f"/transactions/{envelope.id}/full-edit",
            ).get_data(as_text=True)
            assert "Undo CC" not in source, (
                "the repair named must be one the source's own card offers"
            )


class TestThePatchDoorRefusesATypedFigure:
    """The crafted-request and stale-form backstop behind the popover."""

    def test_a_submitted_estimate_is_refused_and_nothing_is_written(
        self, app, auth_client, payback_pair,
    ):
        """400, and the row keeps the figure it had.

        Asserting the WRITE did not land as well as the status code: a refusal
        that reports 400 after staging the mutation would leave the typed
        figure in the session for the next flush to commit.
        """
        _, payback_id = payback_pair
        with app.app_context():
            before = _db.session.get(Transaction, payback_id)
            version = before.version_id

            resp = auth_client.patch(f"/transactions/{payback_id}", data={
                "estimated_amount": "123.18",
                # A CRAFTED payload, so it is well-formed (R-JR) and the
                # refusal under test is the payback gate, not the schema.
                "estimated_amount_as_rendered": "181.58",
                "version_id": version,
            })

            assert resp.status_code == 400
            assert "card spend it repays" in resp.get_data(as_text=True)
            _db.session.expire_all()
            after = _db.session.get(Transaction, payback_id)
            assert after.estimated_amount == Decimal("181.58"), (
                "the typed figure was written despite the refusal -- this is "
                "N-252 itself, $58.40 of it on production payback 2590"
            )

    def test_an_EMPTY_submitted_estimate_writes_NOTHING(
        self, app, auth_client, payback_pair,
    ):
        """A crafted empty box cannot NULL the column, and does not 500.

        **Asserted because a first draft of this suite expected a 400 and was
        wrong about why.**  ``TransactionUpdateSchema.estimated_amount`` is not
        ``allow_none``, so ``_normalize_empty_inputs`` DROPS an empty submit
        instead of loading it as ``None`` -- the key never reaches the handler
        and the refusal never has to fire.  What matters is the outcome the
        refusal exists to protect: the row keeps its figure, and the NULL that
        ``ck_transactions_amount_ownership`` forbids on a source-less row is
        never staged.  If the schema ever gains ``allow_none``, this case turns
        red and the presence-keyed refusal above is what catches it.
        """
        _, payback_id = payback_pair
        with app.app_context():
            version = _db.session.get(Transaction, payback_id).version_id

            resp = auth_client.patch(f"/transactions/{payback_id}", data={
                "estimated_amount": "",
                "version_id": version,
            })

            # 200, not 400: the key never reaches the handler, so the gate
            # never fires.  Asserted EXACTLY rather than as "200 or 400" --
            # a first version admitted both, which cannot fail when the branch
            # flips and is the one event this case exists to notice.
            assert resp.status_code == 200
            _db.session.expire_all()
            assert _db.session.get(
                Transaction, payback_id,
            ).estimated_amount == Decimal("181.58")

    def test_an_ordinary_rows_estimate_still_writes(
        self, app, auth_client, payback_pair,
    ):
        """The control: a one-off that repays nothing is still editable.

        The source here is a one-off and carries no payback link of its own,
        so it is the nearest neighbour to a payback the guard must NOT catch.
        """
        source_id, _ = payback_pair
        with app.app_context():
            source = _db.session.get(Transaction, source_id)
            # Back to Projected: a Credit row is immutable, and what is under
            # test is the payback guard rather than the finalised-field lock.
            credit_workflow.unmark_credit(source_id, source.account.user_id)
            _db.session.commit()
            _db.session.expire_all()
            version = _db.session.get(Transaction, source_id).version_id

            resp = auth_client.patch(f"/transactions/{source_id}", data={
                "estimated_amount": "200.00",
                "estimated_amount_as_rendered": "500.00",
                "version_id": version,
            })

            assert resp.status_code == 200
            _db.session.expire_all()
            # Read through the resolver: the row is a ONE-OFF (plan step
            # balance:X-bi-7c), priced by its definition, so the raw column is None
            # and the app's resolver is the reader (ruling R-BAL60).
            assert resolved_amount(
                _db.session.get(Transaction, source_id),
            ) == Decimal("200.00")
