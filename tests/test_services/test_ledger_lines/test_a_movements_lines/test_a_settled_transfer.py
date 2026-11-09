"""A settled transfer's lines: each side against transit, the settled figure, the day each is
dated, a revert and re-settle, and the per-account oracle.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.services import transfer_service
from app.services.ledger_lines import linked_key, transit_key
from tests._ledger_reads import (
    account_net,
    ledger_net,
    net_posted_by_day,
    scenario_lines,
    transfer_family_movement_ids,
)
from tests._records_oracle import settled_transfer_effect
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
    transfer_side_record,
)
from app.models.amount_ownership import AmountOwnership

from ._helpers import _period_nets_for_transfer, _scenario_id


# ---------------------------------------------------------------------------
# Settle: two lines per side, each against transit
# ---------------------------------------------------------------------------


class TestASettledTransferBooksEachSideAgainstTransit:
    """A settled transfer books each side's account against transit."""

    def test_asset_to_asset_signs_balance_and_metadata(
        self, app, db, seed_user, savings,
    ):
        """Checking -> Savings $100 books Chk -100 / T +100 and Sav +100 / T -100.

        Arithmetic (ruling **R-BAL45**'s shape C, built at plan step
        ``balance:X-bi-6-3``): the from-side is -100.00 on Checking (a credit:
        money leaving) against +100.00 on Transfers in transit; the to-side is
        +100.00 on Savings (a debit: money entering) against -100.00 on
        transit.  Transit nets 0.00 across the pair.  Both accounts are Asset
        class, but the rule never branches on class -- the sign follows
        direction.  Also pins each line's filing: the ``transfer_movement``
        source kind, the ``transfer`` posting kind, and the side's own record
        and its recorded day.
        """
        with app.app_context():
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                period, amount=Decimal("100.00"),
            )
            _db.session.commit()

            assert _period_nets_for_transfer(transfer.id, _scenario_id(seed_user)) == {
                period.id: {
                    linked_key(checking): Decimal("-100.00"),
                    linked_key(savings): Decimal("100.00"),
                    transit_key(): Decimal("0.00"),
                },
            }
            records = transfer_family_movement_ids(transfer.id)
            lines = [
                line
                for line in scenario_lines(_db.session, _scenario_id(seed_user))
                if line.movement_id in records
            ]
            # Two lines per side: its account and transit.
            assert len(lines) == 4
            assert {line.source_kind_id for line in lines} == {
                ref_cache.posting_source_id(PostingSourceEnum.TRANSFER_MOVEMENT),
            }
            assert {line.posting_kind_id for line in lines} == {
                ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
            }
            assert {line.pay_period_id for line in lines} == {period.id}
            # Each side's two lines on its own record's recorded day.
            for account in (checking, savings):
                record = transfer_side_record(_db.session, transfer.id, account.id)
                assert {
                    line.entry_date for line in lines
                    if line.movement_id == record.id
                } == {record.settled_on}

    def test_asset_to_liability_signs(self, app, db, seed_user):
        """Checking -> Mortgage $250 books -250 / +250 (pay-down), via transit.

        Arithmetic (plan Section 1, second worked example): paying down a
        liability is still from=-amount / to=+amount.  -250.00 on the Asset
        Checking line against +250.00 transit, +250.00 on the Liability
        Mortgage line against -250.00 transit; transit nets to zero -- the
        sign rule is class-independent.
        """
        with app.app_context():
            mortgage = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Posting Mortgage",
            )
            _db.session.commit()
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], mortgage,
                seed_user["bootstrap_period"], amount=Decimal("250.00"),
            )
            _db.session.commit()

            assert _period_nets_for_transfer(transfer.id, _scenario_id(seed_user)) == {
                seed_user["bootstrap_period"].id: {
                    linked_key(seed_user["account"]): Decimal("-250.00"),
                    linked_key(mortgage): Decimal("250.00"),
                    transit_key(): Decimal("0.00"),
                },
            }

    def test_settle_uses_effective_amount_not_transfer_amount(
        self, app, db, seed_user, savings,
    ):
        """A settled transfer's FIGURE overrides the transfer amount.

        The transfer's nominal amount is $100, but its settled figure is
        $97.50 (stored once on the transfer; neither side's record holds
        one), so each side's line is $97.50 -- the value the balance seam and
        the oracle use: -97.50 / +97.50 on the real accounts, NOT -100 /
        +100.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_amount=Decimal("97.50"),
            )
            _db.session.commit()

            assert _period_nets_for_transfer(transfer.id, _scenario_id(seed_user)) == {
                seed_user["bootstrap_period"].id: {
                    linked_key(seed_user["account"]): Decimal("-97.50"),
                    linked_key(savings): Decimal("97.50"),
                    transit_key(): Decimal("0.00"),
                },
            }


#: A settle day in the PAST of this suite's frozen today (2026-03-20, set by
#: ``tests/test_services/conftest.py``), and inside the seeded period range.
#: Ruling **R-EJ** refuses a settle dated ahead of the clock, so a fixture that
#: means "this money moved" has to name a day that has happened.
_A_RECORDED_SETTLE_DAY = date(2026, 3, 9)


class TestASettledTransfersDay:
    """Each line is dated by its side's recorded settle DAY.

    ``test_a_settled_transfer_cannot_be_left_without_a_day`` was deleted by
    ruling **R-BAL342** (the developer, 2026-10-20, finding **BAL-581**): it
    asked :class:`~app.services.transfer_service.SideDay` to refuse ``None``
    and wrote nothing, the refusal
    ``test_the_due_date_and_settle_day.py``'s
    ``test_a_settled_transfer_refuses_to_have_its_day_CLEARED`` asks.
    """

    def test_entry_date_is_the_rows_recorded_settle_day(
        self, app, db, seed_user, savings,
    ):
        """A settled transfer's lines are dated by its recorded settle day.

        The line's day is each side's record's stored ``settled_on``,
        unmodified: no conversion, no fallback.

        **The rule it grades has moved twice.**  Ruling R-DH (b) inverted it in
        2026-07-31: a posted entry's date had been ``paid_at``'s UTC civil date
        and became the USER's, together with the cash fold and the loan fold,
        because a ledger day is compared against and bucketed by plain ``DATE``
        columns that mean the user's civil days.  Plan step X-f1 then deleted
        the derivation entirely: the day is stored, so there is no zone left to
        get wrong HERE.  The zone rule now lives at the write door and is
        pinned there (``test_status_seam.py``); what this case still owns is
        that the read takes the recorded day rather than re-deriving one.

        The day is a PAST one relative to this suite's frozen today, and since
        ruling **R-EJ** it has to be: a settled row asserts that money has
        already moved, so the write door refuses a settle dated ahead of the
        clock.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_on=_A_RECORDED_SETTLE_DAY,
            )
            _db.session.commit()
            # Each side's lines are dated by ITS record's recorded day; the
            # helper presses Paid on the one day, which both sides then borrow.
            # The recorded day, verbatim -- no conversion, no fallback.
            assert net_posted_by_day(transfer_family_movement_ids(transfer.id)) == {
                _A_RECORDED_SETTLE_DAY: Decimal("100.00"),
            }


# ---------------------------------------------------------------------------
# A revert and a re-settle
# ---------------------------------------------------------------------------


class TestARevertAndAReSettle:
    """A revert takes the pair's lines off; a re-settle books the new figure."""

    def test_revert_edit_amount_resettle_posts_new_amount(
        self, app, db, seed_user, savings,
    ):
        """A revert -> edit-amount -> re-settle books the new amount.

        Arithmetic: settle $100 (+100 on Savings), revert (the door un-dates
        both records, ruling **R-BAL61**: net 0), edit the amount to $150,
        re-settle: the pair books +150 on Savings, so the Savings net is its
        $100.00 opening + 150 = 250.00, and transit nets to zero.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            scenario_id = _scenario_id(seed_user)

            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            _db.session.commit()
            # The revert leaves Savings on its opening.
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("100.00")

            # Edit the amount while Projected, then re-settle.
            transfer_service.update_transfer(
                transfer.id, user_id, amount_ownership=AmountOwnership.own(Decimal("150.00")),
            )
            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            _db.session.commit()

            # 100 (opening) + 150 (re-settle) = +250.
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("250.00")
            assert settled_transfer_effect(
                savings.id, scenario_id,
            ) == Decimal("150.00")
            assert ledger_net(_db.session, transit_key(), scenario_id) == Decimal("0.00")


# ---------------------------------------------------------------------------
# The records oracle
# ---------------------------------------------------------------------------


class TestTheOracleAgreesWithTheRead:
    """The per-account read equals the settled transfer effect over the records."""

    def test_helpers_match_after_settle_both_accounts(
        self, app, db, seed_user, savings,
    ):
        """After a $100 settle, the read and the oracle agree on both accounts.

        Arithmetic: Savings (the to-account, the income side) is +100;
        Checking (the from-account, the expense side) is -100.
        ``settled_transfer_effect`` (the sum over each side's dated record)
        reports exactly those; ``account_net`` (the worked-out ledger) reports
        each on top of the account's opening -- Savings 100 + 100 = 200.00,
        Checking 1000 - 100 = 900.00 -- so the two independent reads agree on
        the transfer's contribution.
        """
        with app.app_context():
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            checking = seed_user["account"]
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("200.00")
            assert settled_transfer_effect(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            assert account_net(
                _db.session, checking.id, scenario_id,
            ) == Decimal("900.00")
            assert settled_transfer_effect(
                checking.id, scenario_id,
            ) == Decimal("-100.00")

    def test_helpers_net_to_zero_after_reverse(
        self, app, db, seed_user, savings,
    ):
        """A settled-then-reverted transfer contributes zero on both sides.

        Arithmetic: the revert un-dates each side's record (ruling
        **R-BAL61**), so the settled transfer effect drops it to zero, and
        each account's net lands back exactly on its opening (Savings 100.00,
        Checking 1000.00) -- the transfer contributes nothing.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()

            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            for account, opening in (
                (savings, Decimal("100.00")),
                (seed_user["account"], Decimal("1000.00")),
            ):
                assert account_net(
                    _db.session, account.id, scenario_id,
                ) == opening
                assert settled_transfer_effect(
                    account.id, scenario_id,
                ) == Decimal("0.00")

    def test_settled_effect_uses_effective_amount(
        self, app, db, seed_user, savings,
    ):
        """The settled transfer effect honours a divergent settled figure.

        Arithmetic: nominal $100, settled actual $97.50, so the effect
        reader reports +97.50 and the read carries it on top of the Savings
        opening: 100 + 97.50 = 197.50.
        """
        with app.app_context():
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_amount=Decimal("97.50"),
            )
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            assert settled_transfer_effect(
                savings.id, scenario_id,
            ) == Decimal("97.50")
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("197.50")
