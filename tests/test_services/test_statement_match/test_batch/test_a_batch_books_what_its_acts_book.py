"""The firing control for the whole step: a batch books what the same acts book one at a time, to
the cent.
"""

from datetime import timedelta
from decimal import Decimal

from app.enums import LedgerAccountKindEnum, StatusEnum
from app.extensions import db as _db
from app.services import balance_at, statement_match
from app.services.ledger_lines import account_counter_key
from app.services.statement_match import Consent, ReviewedBatch
from app.services.statement_match import _create

from tests._ledger_reads import linked_key_of, nets_by_key_and_day
from tests._test_helpers import last_covered_day

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_purchase,
    a_scope,
    a_transaction,
    an_answers,
    an_import,
)

from ._helpers import _match, _creation


class TestABatchBooksWhatTheSameActsBookOneAtATime:
    """**The firing control for the whole step**, and it was missing.

    Everything else in this module grades COUNTS, refusal sentences and row
    existence.  None of that can see the thing the step actually risks: one
    derivation is shared across every act, and a candidate's price from that
    derivation feeds exactly one consumer -- ``_accept._reject_unbalanced``,
    which is a money gate.  A stale price admits a match a fresh derivation
    would refuse, or refuses one it would admit, and the difference shows up in
    a FIGURE rather than in a count.

    So this applies the same acts twice, once as a batch against one shared
    scope and once one at a time against a fresh scope each, and compares the
    money to the cent.  Each run is bracketed by its own SAVEPOINT so the
    second starts from the state the first did.

    Found missing by adversarial test-quality review 2026-08-19, which put it
    exactly right: *"That premise is graded by nothing."*
    """

    @staticmethod
    def _money(app_db, seed_user, account):
        """Return every figure the two runs must agree on.

        Not just a balance: a balance can agree while the rows underneath it
        disagree in offsetting ways, so the per-row settle facts travel with
        it.

        Args:
            app_db: The test's ``db`` fixture.
            seed_user: The seeded user bundle.
            account: The account being reviewed.

        Returns:
            A comparable dict.
        """
        ctx = balance_at.BalanceContext.build(seed_user["user"].id)
        period = seed_user["bootstrap_period"]
        days = [
            period.start_date,
            period.start_date + timedelta(days=7),
            last_covered_day(period),
            last_covered_day(period) + timedelta(days=30),
        ]
        # **No surrogate keys.**  A row this pass CREATES gets a sequence
        # value, and a rolled-back sequence does not rewind -- so the two runs
        # differ on ``id`` and on nothing else that matters.  An id is not
        # money; what the two runs must agree on is what each row RECORDS.
        rows = app_db.session.execute(_db.text(
            "SELECT name, settled_on, status_id, estimated_amount"
            "  FROM budget.transactions ORDER BY name, id"
        )).all()
        entries = app_db.session.execute(_db.text(
            "SELECT t.name, e.description, e.amount, e.purchased_on,"
            "       e.settled_on, e.is_credit"
            "  FROM budget.transaction_entries e"
            "  JOIN budget.transactions t ON t.id = e.transaction_id"
            " ORDER BY t.name, e.description, e.amount"
        )).all()
        # The LEDGER, not just the projection.  A balance can agree while the
        # lines underneath it differ, so the ledger is where a re-dated settle
        # leaving money on its old day would show up.
        return {
            "balances": {
                day.isoformat(): str(
                    balance_at.cash_balance_at(account, ctx, day),
                )
                for day in days
            },
            "transactions": [tuple(str(v) for v in row) for row in rows],
            "entries": [tuple(str(v) for v in row) for row in entries],
            "ledger": nets_by_key_and_day(app_db.session, seed_user["scenario"].id),
        }

    @staticmethod
    def _acts(db, seed_user):
        """Stage a pass that exercises all three effects, and return its items.

        One proposal that SETTLES a projected row, one that CORRECTS a settled
        row's day, one that re-dates a PURCHASE, and one line RECORDED into an
        existing envelope -- so the comparison covers every write the door
        performs rather than the cheapest one.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        matches, creations = [], []

        projected = a_transaction(
            seed_user, name="Electricity", amount="180.00",
        )
        settle_line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=day,
        )
        matches.append(_match(
            seed_user, lines=[settle_line], transactions=[projected],
        ))

        settled = a_transaction(
            seed_user, name="Water", amount="42.00", status=StatusEnum.DONE,
            settled_on=day + timedelta(days=4),
        )
        correct_line = a_bank_line(
            seed_user, statement, amount="-42.00", posted_on=day,
            sequence_in_group=1,
        )
        matches.append(_match(
            seed_user, lines=[correct_line], transactions=[settled],
        ))

        envelope = a_transaction(
            seed_user, name="Groceries", amount="300.00", is_envelope=True,
        )
        # **Recorded LATER than the bank posted it**, which is the only shape
        # ruling R-FW re-dates: the owner's own assertion that this line IS
        # this purchase refutes a purchase day after the day it cleared.
        late = a_purchase(
            seed_user, envelope, amount="31.00", description="Aldi",
            purchased_on=day + timedelta(days=5),
        )
        redate_line = a_bank_line(
            seed_user, statement, amount="-31.00",
            posted_on=day + timedelta(days=3),
            transaction_on=day + timedelta(days=2), sequence_in_group=2,
        )
        matches.append(_match(
            seed_user, lines=[redate_line], entries=[late],
        ))

        swipe = a_bank_line(
            seed_user, statement, amount="-57.96", posted_on=day,
            sequence_in_group=3,
        )
        creations.append(_creation(
            seed_user, swipe, transaction_id=envelope.id,
        ))
        db.session.flush()
        return tuple(matches), tuple(creations)

    def test_the_two_agree_to_the_cent(self, app, db, seed_user):
        """One shared derivation books what four fresh ones book."""
        with app.app_context():
            from app.models.account import Account  # pylint: disable=import-outside-toplevel

            matches, creations = self._acts(db, seed_user)
            account = db.session.get(Account, seed_user["account"].id)

            batched = _db.session.begin_nested()
            outcome = statement_match.apply_reviewed(
                ReviewedBatch(
                    consent=Consent.TICKED,
                    incomes=(),
                    skips=(),
                    matches=matches, creations=creations,
                ),
                a_scope(seed_user),
            )
            _db.session.flush()
            as_a_batch = self._money(db, seed_user, account)
            batched.rollback()
            # **EXPIRE, or the second run reads the first run's writes.**  A
            # savepoint rollback restores the DATABASE, and SQLAlchemy returns
            # the instance already in its identity map for a known primary key
            # -- so without this the second run saw the purchase the first run
            # had settled and reported "unchanged", booking nothing.  Two
            # REQUESTS have two sessions; this is what models that, and a
            # harness that does not is comparing a fresh run against a stale
            # one.  Measured while writing this test: the two runs differed by
            # exactly the `$31.00` purchase.
            _db.session.expire_all()

            one_at_a_time = _db.session.begin_nested()
            singly_did = []
            for submission in matches:
                singly_did.append(statement_match.accept_match(
                    submission, a_scope(seed_user),
                ))
                _db.session.flush()
            for creation in creations:
                # Its OWN registry per call, because acting one at a time is
                # several REQUESTS and a request is the registry's scope.
                # **This control does NOT measure convergence** and a comment
                # here once claimed it did: its fixture holds a single creation
                # naming an EXISTING envelope, so neither path consults the
                # registry and both create zero envelopes.  Two adversarial
                # reviews measured that independently 2026-08-20.  What does
                # measure it is
                # ``TestConvergingMovesTheSameMoneyAsNotConverging`` below,
                # which needs its own fixture and its own comparison because
                # the row-count comparison here is exactly what convergence is
                # SUPPOSED to change.
                singly_did.append(statement_match.create_purchase_from_line(
                    creation, a_scope(seed_user),
                    _create.MintedEnvelopes.none_yet(), an_answers(seed_user),
                    applied_by_rule=False,
                ))
                _db.session.flush()
            singly = self._money(db, seed_user, account)
            one_at_a_time.rollback()
            _db.session.expire_all()

            assert all(
                getattr(did, "match_id", None) is not None
                for did in singly_did
            ), "an act in the one-at-a-time run did not record a match"

            assert outcome.applied_count == 4, (
                "the pass must APPLY its items, or the comparison is between "
                "two states in which nothing happened"
            )
            # **TWO settles, and the second one is the point.**  The late
            # purchase was never marked as having happened, so the match
            # SETTLES it -- and it is re-dated in the same act, which is
            # exactly why ``redated_count`` cuts across the settled/corrected
            # partition rather than joining it.  A first draft of this fixture
            # expected one settle and one correction and was wrong about the
            # purchase; the counts are the code's, checked against the rows.
            assert outcome.settled_count == 2
            assert outcome.corrected_count == 1
            assert outcome.redated_count == 1
            assert outcome.recorded_count == 1

            assert as_a_batch["balances"] == singly["balances"]
            assert as_a_batch["transactions"] == singly["transactions"]
            assert as_a_batch["entries"] == singly["entries"]
            assert as_a_batch["ledger"] == singly["ledger"]
            # The figures, or that comparison could hold between two empty
            # ledgers.  The bank's day is the seed's assertion day, so Checking's
            # own line nets nothing there (the assertion takes the -279.96 back
            # as a true-up): the money shows on its counter, Electricity 180.00
            # + Water corrected onto it 42.00 + the swipe 57.96 = 279.96 moved
            # off Anchor equity.  Three days on, the purchase re-dated to where
            # it posted.  Water's old day holds nothing.
            cash = linked_key_of(_db.session, seed_user["account"].id)
            equity = account_counter_key(
                LedgerAccountKindEnum.ANCHOR_EQUITY, seed_user["account"].id,
            )
            day = seed_user["bootstrap_period"].start_date
            assert singly["ledger"][(equity, day)] == Decimal("-279.96")
            assert singly["ledger"][(cash, day + timedelta(days=3))] == Decimal("-31.00")
            assert (cash, day + timedelta(days=4)) not in singly["ledger"]
