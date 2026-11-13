"""A pass carries the skip verb (plan step bank_import:X-gj-4b, ruling bank_import:R-JG), and a
rule-consented pass may not skip.
"""

from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.statement_line_skip import StatementLineSkip
from app.services.statement_match import Consent, ReviewedBatch, SkipRequest

from tests.test_services.test_statement_match._builders import an_unexplained_outflow, an_envelope

from ._helpers import _batch


class TestAPassCarriesTheSKIPVerb:
    """Plan step ``bank_import:X-gj-4b``, ruling **bank_import:R-JG**.

    The fourth act class a reviewed pass can hold, and the only one that
    writes no money at all.  What these cases hold is that the receipt tells
    the truth about it: an act that moved nothing still CHANGED something, a
    repeat changed nothing and says so, and the one sentence on this screen
    that is easiest to misread -- *this closed no difference* -- is actually
    on the receipt.
    """

    def test_it_records_the_decision_and_counts_it(self, app, db, seed_user):
        """The ordinary press."""
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Kroger", amount="-41.18",
        )
        db.session.commit()

        outcome = _batch(seed_user, skips=(SkipRequest(line_id=line.id),))

        assert outcome.skipped_count == 1
        assert outcome.refused_count == 0
        assert _db.session.query(StatementLineSkip).count() == 1

    def test_a_skip_only_pass_does_NOT_render_nothing_moved(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for :attr:`BatchOutcome.moved_nothing`.

        Drop ``skipped_count`` from that predicate and this fails: the receipt
        renders *"Nothing moved.  Everything that was applied confirmed a day
        you already had"*, whose second sentence is false of an act that
        confirms no day at all.  The skip moved no MONEY, which is why the
        arithmetic below asserts the difference is untouched -- the two facts
        are both true and the receipt has to carry both.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Kroger", amount="-41.18",
        )
        db.session.commit()

        outcome = _batch(seed_user, skips=(SkipRequest(line_id=line.id),))

        assert not outcome.moved_nothing
        assert outcome.settled_count == 0
        assert outcome.recorded_count == 0
        assert outcome.deposited_count == 0
        assert outcome.residual_total == Decimal("0.00")

    def test_the_receipt_names_the_figure_the_day_and_what_it_did_NOT_do(
        self, app, db, seed_user,
    ):
        """Ruling **R-GD(a)**: a consent naming a count and no figure is a
        consent to an amount nobody stated.

        The line is `-$41.18` on the bank's convention, so the sentence states
        the magnitude and says the bank TOOK it.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Kroger", amount="-41.18",
        )
        db.session.commit()

        outcome = _batch(seed_user, skips=(SkipRequest(line_id=line.id),))

        (item,) = outcome.applied
        assert item.line_ids == (line.id,)
        assert "$41.18" in item.summary
        # The DAY, which this case's own name promises and an earlier draft
        # did not grade (adversarial review 2026-09-04).
        assert str(line.posted_on) in item.summary
        assert "took" in item.summary
        assert "closes no difference" in item.summary
        # The BANK's own signed figure, unflipped: a skip has no app-side
        # amount to convert from.
        assert item.amount == Decimal("-41.18")

    def test_a_REPEAT_is_applied_reports_itself_and_counts_NOTHING(
        self, app, db, seed_user,
    ):
        """``was_already_skipped`` is a fact about a PRESS, not about a row.

        A second press states the same decision, so it is an outcome rather
        than a refusal -- and it wrote nothing, so counting it would claim a
        record changed when none did.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Kroger", amount="-41.18",
        )
        db.session.commit()
        _batch(seed_user, skips=(SkipRequest(line_id=line.id),))

        again = _batch(seed_user, skips=(SkipRequest(line_id=line.id),))

        assert again.refused_count == 0
        assert again.applied_count == 1
        assert again.skipped_count == 0
        assert again.already_skipped_count == 1
        assert "already recorded" in again.applied[0].summary
        assert _db.session.query(StatementLineSkip).count() == 1
        # FIRING CONTROL for the second counter (adversarial review
        # 2026-09-04): with only ``skipped_count`` in ``moved_nothing`` this
        # pass rendered *"Nothing moved.  Everything that was applied
        # confirmed a day you already had"* over an act that confirmed no day.
        assert not again.moved_nothing

    def test_a_line_the_door_refuses_is_ONE_refusal_and_the_rest_land(
        self, app, db, seed_user,
    ):
        """The savepoint policy, on the fourth act class.

        A card payment (**R-JI**) and an ordinary line in one press: the first
        is refused in the door's own words, the second still lands.
        """
        an_envelope(seed_user)
        barred = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category="Financial Services/Credit Card Payment",
        )
        ordinary = an_unexplained_outflow(
            seed_user, merchant="Kroger", amount="-41.18",
        )
        db.session.commit()

        outcome = _batch(seed_user, skips=(
            SkipRequest(line_id=barred.id),
            SkipRequest(line_id=ordinary.id),
        ))

        assert outcome.skipped_count == 1
        assert outcome.refused_count == 1
        assert "another account you hold" in outcome.refused[0].reason
        assert _db.session.query(StatementLineSkip).count() == 1


class TestARuleConsentedPassMayNotSkip:
    """Plan step ``bank_import:X-gj-4b``, and the refusal is the STORE's.

    Ruling **R-GH** would permit it on its own terms -- a skip creates a
    decision and modifies no row the owner made by hand.  What refuses it is
    that ``budget.statement_line_skips`` carries no ``applied_by_rule``
    column, so nothing could record that a rule performed the act.
    """

    def test_it_is_refused_at_the_VALUE(self, app, db, seed_user):
        """A programming error, so a ``ValueError`` and not a refusal
        sentence: no wire value reaches the consent field."""
        with pytest.raises(ValueError) as caught:
            ReviewedBatch(
                consent=Consent.STANDING_RULE,
                matches=(), creations=(), incomes=(),
                skips=(SkipRequest(line_id=1),),
            )

        assert "applied_by_rule" in str(caught.value)

    def test_a_TICKED_pass_carrying_the_same_skip_is_legal(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the refusal is keyed on the CONSENT and not on the
        act class, so the identical batch under a tick constructs."""
        batch = ReviewedBatch(
            consent=Consent.TICKED,
            matches=(), creations=(), incomes=(),
            skips=(SkipRequest(line_id=1),),
        )

        assert batch.item_count == 1
