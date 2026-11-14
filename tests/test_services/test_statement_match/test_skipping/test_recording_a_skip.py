"""What a skip records, a line paying a held account, and the exclusivity both ways."""

from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.models.statement_line_skip import StatementLineSkip
from app.models.statement_match import StatementMatch
from app.services import statement_match
from app.services.statement_match import (
    MintedEnvelopes,
    PurchaseCreation,
    Tab,
    reconcile_page,
    skip_line,
    unskip_line,
)

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    a_transaction,
    an_answers,
    an_envelope,
    an_import,
    an_unexplained_outflow,
)

from ._helpers import _owner, _skip, _money_snapshot


class TestRecordingASkip:
    """What :func:`skip_line` writes, and what it refuses to write."""

    def test_it_records_the_decision_with_who_and_when(
        self, app, db, seed_user,
    ):
        """The three facts the table exists to hold.

        ``user_id`` is the one that is not derivable from anything else here,
        which is why the column exists at all -- the same argument ruling
        **R-GT** makes for ``statement_matches.applied_by_rule``.
        """
        line = an_unexplained_outflow(seed_user)
        db.session.flush()

        recorded = _skip(seed_user, line)

        # **Read as COLUMNS out of the database, not off the instance the door
        # added.**  ``session.get`` answers from the identity map, so three of
        # these assertions would be "the object the door built holds what the
        # door set" -- a tautology at the ORM tier, and the shape
        # ``test_undo``'s own ``_rows`` helper exists to refuse.  Named by
        # adversarial test-quality review 2026-09-02.
        stored = db.session.execute(
            db.text(
                "SELECT bank_statement_line_id, account_id, user_id, "
                "created_at FROM budget.statement_line_skips WHERE id = :id"
            ),
            {"id": recorded.skip_id},
        ).one()

        assert recorded.line_id == line.id
        assert recorded.was_already_skipped is False
        assert stored.bank_statement_line_id == line.id
        assert stored.account_id == seed_user["account"].id
        assert stored.user_id == seed_user["user"].id
        assert stored.created_at is not None

    def test_a_repeat_returns_the_standing_row_and_writes_nothing(
        self, app, db, seed_user,
    ):
        """A stale double-submit states the same decision, so it is absorbed.

        **The SECOND call is the one under test**, which is the shape a
        producer correct on the first write can be wrong on: a door that
        inserted again would raise ``IntegrityError`` on a press the owner is
        entitled to make twice, and one that returned a fresh id would let the
        page offer an undo for a row that is not there.
        """
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        first = _skip(seed_user, line)

        second = _skip(seed_user, line)

        assert second.skip_id == first.skip_id
        assert second.line_id == line.id
        assert second.was_already_skipped is True
        assert db.session.query(StatementLineSkip).count() == 1

    def test_a_line_on_ANOTHER_account_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """The security response rule at the service tier.

        The refusal names no reason, so "not there" and "not yours" answer
        alike.  **It is a FIRING CONTROL for the account filter**: the
        composite foreign key would refuse the write anyway, but as an
        ``IntegrityError`` naming a constraint, which is not an answer a screen
        can render.
        """
        statement = an_import(seed_second_user)
        theirs = a_bank_line(seed_second_user, statement)
        db.session.flush()
        owner_id, account_id = _owner(seed_user)

        with pytest.raises(ValidationError):
            skip_line(theirs.id, owner_id, account_id)

        assert db.session.query(StatementLineSkip).count() == 0

    def test_ANOTHER_owners_line_AND_account_is_refused_by_NAME(
        self, app, db, seed_second_user, seed_user,
    ):
        """FIRING CONTROL for the OWNER join, which nothing else reaches.

        The sibling case above passes THIS account with THEIR line, which the
        pre-existing ``account_id`` filter catches.  This passes their line AND
        their account with the caller's own id -- the shape that gets past that
        filter and is otherwise stopped only by
        ``fk_statement_line_skips_owner`` at flush, as an ``IntegrityError``:
        a 500 and an aborted transaction rather than an answer a screen can
        render.  Delete the ``Account.user_id == owner_id`` join and this case
        fails on the exception TYPE, which is the whole difference.  Named by
        adversarial design review 2026-09-02.
        """
        statement = an_import(seed_second_user)
        theirs = a_bank_line(seed_second_user, statement)
        db.session.flush()

        with pytest.raises(ValidationError):
            skip_line(
                theirs.id, seed_user["user"].id,
                seed_second_user["account"].id,
            )

        assert db.session.query(StatementLineSkip).count() == 0

    def test_a_line_that_does_not_exist_is_refused(self, app, db, seed_user):
        """The same answer for a line id nothing has ever recorded."""
        owner_id, account_id = _owner(seed_user)

        with pytest.raises(ValidationError):
            skip_line(999_999, owner_id, account_id)

    def test_a_line_an_accepted_MATCH_explains_is_refused(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: ruling **R-HP**'s "exactly one of the four".

        A line carrying both a match and a skip would be answered twice, and
        the two answers contradict: one says it IS a row the books hold, the
        other that it is explained by nothing.  The refusal names the remedy,
        because there is one -- undo the match.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-180.00")
        txn = a_transaction(seed_user, amount="180.00")
        scope = a_scope(seed_user)
        statement_match.accept_match(
            a_submission(scope, lines=[line], transactions=[txn]), scope,
        )
        db.session.flush()
        owner_id, account_id = _owner(seed_user)

        with pytest.raises(ValidationError) as caught:
            skip_line(line.id, owner_id, account_id)

        assert "already explained by a match" in str(caught.value)
        assert db.session.query(StatementLineSkip).count() == 0


class TestALinePayingAnAccountTheyHoldIsNotExplainedByNothing:
    """Ruling **bank_import:R-JI** (developer, 2026-09-02), at the DOOR.

    A line the source files under its card-payment category is money that moved
    between two accounts the owner holds; the card arc will pair the two sides.
    Recording *explained by nothing* for it would store a decision the app
    already knows to be false.

    **It is a door and not a shut tab**, which is ruling **R-GJ**'s lesson one
    verb over -- that ruling cost `$7,412.94` to learn that a warning paragraph
    above a working control is not a refusal.  ``X-gj-4b`` renders SKIP shut
    for these lines; without this the shut tab would sit over an open door.

    **And it moves a rendered MONEY figure**, which is what makes it this
    leaf's business rather than the tab's: such a line is counted on the
    Reconcile hero's *waiting for the account they paid* chip, whose label
    carries a magnitude, so skipping one drops the chip's count AND its dollar
    total.  Named by adversarial review 2026-09-02.
    """

    #: What SECU files a card payment under, verbatim, which is the one string
    #: :data:`~app.services.statement_match._vocabulary.ACCOUNT_PAYMENT_CATEGORIES`
    #: holds for that adapter.
    CARD_PAYMENT = "Financial Services/Credit Card Payment"

    def test_the_door_refuses_it(self, app, db, seed_user):
        """FIRING CONTROL: delete the refusal and the skip lands."""
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=self.CARD_PAYMENT,
        )
        db.session.commit()
        owner_id, account_id = _owner(seed_user)

        with pytest.raises(ValidationError) as caught:
            skip_line(line.id, owner_id, account_id)

        assert "another account you hold" in str(caught.value)
        assert db.session.query(StatementLineSkip).count() == 0

    def test_the_transfers_chips_money_figure_is_unmoved(
        self, app, db, seed_user,
    ):
        """The consequence, asserted as the FIGURE rather than as the refusal.

        Arithmetic: one parked card payment of `-$793.23` gives a chip
        reading 1 and `$793.23` -- a MAGNITUDE, because every parked line is
        an outflow and the label supplies the direction.  A door that admitted
        the skip would take both to zero and delete the chip.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=self.CARD_PAYMENT,
        )
        db.session.commit()
        owner_id, account_id = _owner(seed_user)
        chip = reconcile_page(
            a_scope(seed_user), None, Tab.TO_EXPLAIN,
        ).chips[0]
        assert chip.count == 1
        assert chip.amount == Decimal("793.23")

        with pytest.raises(ValidationError):
            skip_line(line.id, owner_id, account_id)
        db.session.commit()

        after = reconcile_page(
            a_scope(seed_user), None, Tab.TO_EXPLAIN,
        ).chips[0]
        assert after.count == 1
        assert after.amount == Decimal("793.23")

    def test_a_line_whose_source_names_no_such_category_is_skippable(
        self, app, db, seed_user,
    ):
        """The refusal is about the BANK's filing, not about every outflow.

        Paired with the two above deliberately: a guard that refused every
        line, or every line with a merchant, would pass both of them and fail
        here -- which is the only way to tell a narrow refusal from a broad
        one.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Amazon", amount="-57.96",
        )
        db.session.commit()

        recorded = _skip(seed_user, line)

        assert recorded.was_already_skipped is False


class TestTheExclusivityRunsBOTHWays:
    """Ruling **R-HP**: a bank line ends on EXACTLY ONE of the four verbs.

    :class:`TestRecordingASkip` grades the direction the skip door owns -- a
    line a live match answers may not be skipped.  This grades the mirror,
    which no key can hold because the rule spans two tables: a line the owner
    has skipped may not then be MATCHED.

    **Without the mirror the exclusivity is one-directional and the state it
    admits is silent**: the line carries a match AND a skip, renders a card on
    the Explained tab and another on the Skipped tab, is absent from the inbox
    for two independent reasons, and nothing raises.  It is refused in
    :func:`~app.services.statement_match._resolve.load_lines`, which is the ONE
    place all three match doors resolve a submitted line, so a fourth door
    inherits it by calling that function.
    """

    def _skipped_line_and_a_row(self, db, seed_user):
        """Stage one skipped bank line and one app row it could pair with.

        Args:
            db: The session fixture.
            seed_user: The seeded user bundle.

        Returns:
            ``(line, transaction)``.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-180.00")
        txn = a_transaction(seed_user, amount="180.00")
        db.session.flush()
        _skip(seed_user, line)
        return line, txn

    def test_matching_a_skipped_line_is_refused(self, app, db, seed_user):
        """FIRING CONTROL: delete the ``load_lines`` term and this passes.

        The submission is one a browser can produce -- a tab held open across
        a skip made in another -- so the refusal has to be a sentence rather
        than an ``IntegrityError``, which no key would raise here anyway.
        """
        line, txn = self._skipped_line_and_a_row(db, seed_user)
        scope = a_scope(seed_user)

        with pytest.raises(ValidationError) as caught:
            statement_match.accept_match(
                a_submission(scope, lines=[line], transactions=[txn]), scope,
            )

        assert "already skipped" in str(caught.value)

    def test_the_refusal_leaves_the_skip_and_writes_no_match(
        self, app, db, seed_user,
    ):
        """It fires before anything is written, so the state is unchanged."""
        line, txn = self._skipped_line_and_a_row(db, seed_user)
        scope = a_scope(seed_user)
        before = _money_snapshot(db, seed_user)

        with pytest.raises(ValidationError):
            statement_match.accept_match(
                a_submission(scope, lines=[line], transactions=[txn]), scope,
            )

        assert db.session.query(StatementLineSkip).count() == 1
        # **The act table itself, which the money counts do not cover.**  The
        # refusal fires inside ``load_lines``, which ``_accept`` evaluates as
        # an argument BEFORE ``record_match`` runs -- so nothing is staged
        # today.  Counting the acts is what would catch that refusal being
        # relocated below the INSERT, which is the one mutation this case is
        # named for.  Named by adversarial test-quality review 2026-09-02.
        assert db.session.query(StatementMatch).count() == 0
        assert _money_snapshot(db, seed_user) == before

    def test_the_ADD_door_inherits_the_same_refusal(
        self, app, db, seed_user,
    ):
        """The claim that this lives in ONE place, MEASURED at a second door.

        :func:`~app.services.statement_match._resolve.load_lines` is called by
        :func:`~app.services.statement_match._accept.accept_match`, by
        :func:`~app.services.statement_match._create.create_purchase_from_line`,
        by the income door and by the preview.  The class docstring says a door
        inherits the refusal by calling it; asserting that of only
        ``accept_match`` leaves the claim untested at every other door.  ADD is
        one of ruling **R-HP**'s own four verbs, so *a skipped line becomes a
        purchase* is exactly the two-answers state this class forbids.  Named by
        adversarial test-quality review 2026-09-02.
        """
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        _skip(seed_user, line)

        with pytest.raises(ValidationError) as caught:
            statement_match.create_purchase_from_line(
                PurchaseCreation(
                    line_id=line.id, transaction_id=envelope.id,
                ),
                a_scope(seed_user),
                MintedEnvelopes.none_yet(),
                an_answers(seed_user),
                applied_by_rule=False,
            )

        assert "already skipped" in str(caught.value)

    def test_undoing_the_skip_makes_the_line_matchable_again(
        self, app, db, seed_user,
    ):
        """The refusal is about the STANDING answer, not about the line.

        Paired with the case above deliberately: a guard that refused the line
        forever -- because of something the skip left behind -- would pass the
        refusal cases and fail here, which is the only way to tell the two
        apart.
        """
        line, txn = self._skipped_line_and_a_row(db, seed_user)
        owner_id, account_id = _owner(seed_user)
        standing = db.session.query(StatementLineSkip).one()
        unskip_line(standing.id, owner_id, account_id)
        db.session.flush()
        scope = a_scope(seed_user)

        accepted = statement_match.accept_match(
            a_submission(scope, lines=[line], transactions=[txn]), scope,
        )

        assert accepted.match_id is not None
