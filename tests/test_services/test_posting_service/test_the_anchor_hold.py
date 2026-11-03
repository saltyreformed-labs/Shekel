"""The anchor hold refuses a second hold and keeps the first (plan task plan#2614).

``posting_service.holding_the_anchor_re_check`` queues every anchor re-check
its block's syncs owe and runs them once at its close (ruling plan#2574).  The
session holds one re-check, so a hold opened inside another replaced it until
plan task plan#2614: the inner close ran only its own block's re-check and
took the key, and the outer close raised ``KeyError`` without running what its
block had queued (review plan#2521, finding A2).  A second hold is refused now,
naming both acts, before it touches the open one.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.services import posting_service
from tests._test_helpers import create_account_of_type, freeze_today

from ._helpers import _TODAY, _bill, _opening_day, _settle_paid_from


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    """Pin the owner's today, which every new account's opening day reads."""
    freeze_today(monkeypatch, _TODAY)


class TestASecondHoldIsRefused:
    """A hold inside a hold is refused, and the open hold still re-checks."""

    def test_a_second_hold_is_refused_and_the_first_still_re_checks(
        self, app, db, seed_user, seed_periods,
    ):
        """A second hold raises naming both acts; the first's close re-checks both cards.

        Two cards open, each asserted on 2026-05-14: Rewards at -$500.00 and
        Travel at -$300.00.  Inside one hold a $120.00 bill is settled from
        Rewards that day, a second hold is refused, and a $120.00 bill is
        settled from Travel that day.  Each settle's re-check is queued, so
        each card reads its leg on top of its opening until the close books
        the assertion's true-up around it:

            inside the hold   Rewards -500.00 - 120.00                = -620.00
                              Travel  -300.00 - 120.00                = -420.00
            after the close   Rewards -500.00 - 120.00 + 120.00 trueup = -500.00
                              Travel  -300.00 - 120.00 + 120.00 trueup = -300.00

        Travel's settle comes after the refusal and posts on another account
        than Rewards', so only the open hold, left as it was, re-checks it.
        Each mutation of the refusal fails here: deleted, the second hold
        opens, its close takes the key and the first's close raises
        ``KeyError``; emptying the open hold leaves Rewards at -620.00;
        putting a hold of its own in the open one's place leaves Travel at
        -420.00.
        """
        with app.app_context():
            rewards = create_account_of_type(
                seed_user, _db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            travel = create_account_of_type(
                seed_user, _db.session, "Credit Card", "Travel Card",
                anchor_balance=Decimal("-300.00"),
            )
            _db.session.commit()
            first = _bill(seed_user, seed_periods[0])
            second = _bill(seed_user, seed_periods[0])
            scenario_id = first.scenario_id

            def total(card):
                """Return *card*'s linked-ledger total in the scenario."""
                return posting_service.account_posting_total(
                    card.id, scenario_id,
                )

            with posting_service.holding_the_anchor_re_check("the outer act"):
                _settle_paid_from(first, rewards, _opening_day(rewards))
                assert total(rewards) == Decimal("-620.00"), (
                    "CONTROL: the settle's re-check is held"
                )

                with pytest.raises(
                    RuntimeError,
                    match=(
                        "^the inner act cannot hold the anchor re-check: "
                        "the outer act already holds it on this session"
                    ),
                ):
                    with posting_service.holding_the_anchor_re_check(
                        "the inner act",
                    ):
                        pass

                _settle_paid_from(second, travel, _opening_day(travel))
                assert total(rewards) == Decimal("-620.00")
                assert total(travel) == Decimal("-420.00")

            assert total(rewards) == Decimal("-500.00")
            assert total(travel) == Decimal("-300.00")
