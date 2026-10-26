"""Where a group's difference lands: a lone row's old answer exactly, nowhere until the owner names
a member, on the member the owner names, and never as a figure that member's own row cannot
hold.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.models.statement_match import StatementMatchMember
from app.services import statement_match, status_seam
from app.services.row_valuation import settled_figure
from app.services.statement_match import RowKind
from app.services.statement_match._landing import DifferenceLanding
from app.services.statement_match._sides import MatchSides

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_purchase,
    a_scope,
    a_submission,
    a_transaction,
    an_import,
)
from tests._test_helpers import resolved_amount

from ._helpers import _submit, _minted, _ALine, a_named_row, _a_cc_payback


class TestALoneRowsLandingIsTheOldAnswerEXACTLY:
    """The algebra plan step **bank_import:X-gj-3a**'s generalisation rests on.

    ``bank_cash_for`` answered :attr:`MatchSides.bank` for a match naming one
    row and ``None`` otherwise.  :class:`DifferenceLanding` answers *the named
    member's own cash plus the whole difference*, and for a lone row those are
    the same number by definition -- ``row.cash + (bank - row.cash) == bank``.
    **These cases are what makes that an assertion rather than a remark**: if
    the two ever disagreed, every one-row correction the app has ever made
    would move, and the whole widening would be a rewrite wearing a
    generalisation's clothes.

    Graded through the constructor with plain values, exactly as
    :class:`TestTheTwoSidesAreRoundedBeforeTheyAreCompared` is: the identity is
    about arithmetic and not about any row the database can hold, so staging
    one would only narrow what is covered.
    """

    @pytest.mark.parametrize(
        ("bank", "row"),
        [
            # The payroll shape: an income row a few cents under the deposit.
            ("2573.43", "2473.38"),
            # An outflow, which is where every shipped correction lives.
            ("-178.29", "-178.32"),
            # The bank BELOW the app, so the difference is negative.
            ("100.00", "180.06"),
            # No difference at all, where the landing must still answer the
            # row's own figure -- ``corrected_figure`` is what turns that into
            # "write nothing", and it can only do so if it is handed the
            # figure the row already holds.
            ("50.00", "50.00"),
            # A row worth nothing, where ``row.cash + difference`` and the
            # bank total are the same number only if the identity is exact.
            ("42.00", "0.00"),
        ],
    )
    def test_the_figure_is_the_banks_own_total(self, bank, row):
        """Whatever the sign and whatever the gap."""
        member = a_named_row(RowKind.TRANSACTION, 1, row)
        sides = MatchSides.of([_ALine(Decimal(bank))], [member])

        landing = DifferenceLanding.of(sides, [member], None)

        assert landing.bank_cash == sides.bank

    def test_the_identity_survives_a_row_whose_cash_is_SUB_CENT(self):
        """The case that separates the two ways of spelling the same figure.

        ``sides.app`` is ROUNDED, so *the row's cash plus the difference* is
        the bank's figure plus that row's own rounding error, while *the bank
        less the other members* is the bank's figure exactly.  A first version
        of :class:`DifferenceLanding` used the first spelling, and on a row
        carrying ``-100.005`` it writes ``200.01`` where the door has always
        written ``200.00`` -- a cent invented out of a rounding step, on the
        one figure the whole correction is.

        Every figure the app produces today descends from ``Numeric(12, 2)``,
        so nothing reachable hits this; :class:`~._sides.MatchSides`' own
        docstring is what says a derived price with more places is
        expressible, and this is what keeps the rule true when one arrives.
        It is graded through the constructor for that reason, exactly as
        :class:`TestTheTwoSidesAreRoundedBeforeTheyAreCompared` is.
        """
        row = a_named_row(RowKind.TRANSACTION, 1, "-100.005")
        sides = MatchSides.of([_ALine(Decimal("200.00"))], [row])

        landing = DifferenceLanding.of(sides, [row], None)

        assert sides.app == Decimal("-100.01")
        assert landing.bank_cash == Decimal("200.00")

    def test_a_lone_row_mints_nothing_even_with_no_attribution_submitted(self):
        """Ruling **R-GD(a)**'s determinacy, and it is not a default.

        A match naming one row attributes its difference to that row BY
        CONSTRUCTION, so the pane offers no control and no body sends one.
        Were this arm to answer ``None`` instead, every near-miss correction
        the app offers would silently become a minted uncategorized row.
        """
        row = a_named_row(RowKind.TRANSACTION, 1, "-178.32")
        sides = MatchSides.of([_ALine(Decimal("-178.29"))], [row])

        landing = DifferenceLanding.of(sides, [row], None)

        assert landing.on_row is row
        assert landing.mints_a_row is False


class TestAGroupLandsNOWHEREUntilTheOwnerNamesAMember:
    """Plan step **bank_import:X-gj-3a**, ruling **R-HT(b)**'s residue half.

    The module's own argument is unamended: *three rows summing to one deposit,
    five cents short, is not five cents of error in one of them*.  Nothing here
    derives which member the bank varied.  What is new is that the owner may
    SAY, and these cases pin both halves -- silence still mints, and a stated
    member is honoured exactly.
    """

    def test_a_group_the_owner_said_nothing_about_names_no_member(self):
        """Which is what every group did before this step."""
        salary = a_named_row(RowKind.TRANSACTION, 1, "2473.38")
        allowance = a_named_row(RowKind.TRANSACTION, 2, "100.00")
        sides = MatchSides.of([_ALine(Decimal("2573.43"))], [salary, allowance])

        landing = DifferenceLanding.of(sides, [salary, allowance], None)

        assert landing.on_row is None
        assert landing.bank_cash is None
        assert landing.mints_a_row is True

    def test_a_named_member_takes_its_own_figure_plus_the_WHOLE_difference(
        self,
    ):
        """`$2,473.38` plus `$0.05`, and not the `$2,573.43` the bank moved.

        **The distinction is the whole reason the sums are not what travels.**
        A first shape of this step would have handed the member
        :attr:`MatchSides.bank`, which is what a lone row takes -- and on this
        group that writes the entire deposit onto the salary row, a `$100.00`
        error from a `$0.05` one.
        """
        salary = a_named_row(RowKind.TRANSACTION, 1, "2473.38")
        allowance = a_named_row(RowKind.TRANSACTION, 2, "100.00")
        sides = MatchSides.of([_ALine(Decimal("2573.43"))], [salary, allowance])

        landing = DifferenceLanding.of(
            sides, [salary, allowance], (RowKind.TRANSACTION, 1),
        )

        assert landing.on_row is salary
        assert landing.bank_cash == Decimal("2473.43")
        assert landing.mints_a_row is False

    def test_the_members_the_owner_did_NOT_name_are_offered_no_figure(self):
        """The rule about WHICH row moves lives in the value, not in a loop.

        ``_move_members`` asks this of every member and re-prices the ones it
        answers for.  If it answered a figure for all of them, a group would
        rewrite every row it names to the same number.
        """
        salary = a_named_row(RowKind.TRANSACTION, 1, "2473.38")
        allowance = a_named_row(RowKind.TRANSACTION, 2, "100.00")
        sides = MatchSides.of([_ALine(Decimal("2573.43"))], [salary, allowance])

        landing = DifferenceLanding.of(
            sides, [salary, allowance], (RowKind.TRANSACTION, 1),
        )

        assert landing.figure_for(allowance) is None

    def test_a_member_of_a_DIFFERENT_KIND_and_the_same_id_is_not_it(self):
        """The key is the PAIR, which is what every subject in this package is.

        A purchase and a transaction may share an id, so a landing that
        compared ``row_id`` alone would attribute a deposit's difference to
        whichever of the two the loop reached first.
        """
        txn = a_named_row(RowKind.TRANSACTION, 7, "2473.38")
        purchase = a_named_row(RowKind.PURCHASE, 7, "100.00")
        sides = MatchSides.of([_ALine(Decimal("2573.43"))], [txn, purchase])

        landing = DifferenceLanding.of(
            sides, [txn, purchase], (RowKind.PURCHASE, 7),
        )

        assert landing.on_row is purchase
        assert landing.figure_for(txn) is None

    def test_an_attribution_naming_no_member_RAISES_rather_than_minting(self):
        """The guard on the door's other caller, which builds its own content.

        ``resolve_rows`` refuses this for every SUBMISSION, so it is
        unconstructible from the wire.  What it must never do is fall through
        to the mint, because that would let a caller choose the remedy by
        naming a row that is not there.
        """
        salary = a_named_row(RowKind.TRANSACTION, 1, "2473.38")
        allowance = a_named_row(RowKind.TRANSACTION, 2, "100.00")
        sides = MatchSides.of([_ALine(Decimal("2573.43"))], [salary, allowance])

        with pytest.raises(ValueError):
            DifferenceLanding.of(
                sides, [salary, allowance], (RowKind.TRANSACTION, 99),
            )


class TestAGroupsDifferenceLandsOnTheMemberTheOwnerNames:
    """Plan step **bank_import:X-gj-3a**, through the real door.

    **The staged shape is finding salary:N-391's, measured 2026-09-01.**  The
    owner's `TOWN OF CLAYTON PAYROLL` deposit is two or three app rows summing
    `$0.04`-`$0.06` under it, seven times over the span -- and re-running his
    own paycheck through ``paycheck_calculator`` for each of the seven showed
    the two allowances matching the bank EXACTLY in all eleven of their
    occurrences while the salary row carried the whole residue, because the
    allowances are flat stored figures and the salary is computed.  So *which
    member the bank varied* has an answer on this data, the app cannot derive
    it, and the owner naming it is what this step adds.

    Before it, the ONLY thing this door could do with that difference was mint
    an uncategorized row -- measured by running ``preview_hand_build`` over all
    seven, which answered ``remedy='records'`` every time.
    """

    @staticmethod
    def _a_payroll_deposit(seed_user):
        """Stage the shape: one deposit, a salary row and a flat allowance.

        Args:
            seed_user: The seeded user bundle.

        Returns:
            ``(line, salary, allowance)``.  The two rows sum to `$2,573.38`
            against a `$2,573.43` line, which is the `$0.05` gap.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.43",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        salary = a_transaction(
            seed_user, name="Data Manager", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Health Insurance Allowance", amount="100.00",
            income=True,
        )
        return line, salary, allowance

    def test_the_named_member_takes_the_banks_figure_and_NOTHING_is_minted(
        self, app, db, seed_user,
    ):
        """`$2,473.38` becomes `$2,473.43`; no uncategorized row appears."""
        line, salary, allowance = self._a_payroll_deposit(seed_user)

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
            attributed=(RowKind.TRANSACTION, salary),
        )

        assert settled_figure(salary) == Decimal("2473.43")
        assert accepted.repriced_count == 1
        assert accepted.residual is None
        assert not _minted(seed_user)
        # ...and the member nobody named is untouched, which is what
        # ``figure_for`` answering ``None`` for it buys.  A version that
        # handed every member the same figure would put `$2,473.43` here too.
        assert settled_figure(allowance) == Decimal("100.00")

    def test_the_OTHER_member_can_be_the_named_one(
        self, app, db, seed_user,
    ):
        """The mutation the case above cannot catch on its own.

        With one figure and two rows, *re-price the first member* and *re-price
        the member the owner named* agree on every case that names the first.
        This names the SECOND, so the two answers separate: the allowance
        carries the gap and the salary row does not move.
        """
        line, salary, allowance = self._a_payroll_deposit(seed_user)

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
            attributed=(RowKind.TRANSACTION, allowance),
        )

        assert settled_figure(allowance) == Decimal("100.05")
        assert settled_figure(salary) == Decimal("2473.38")
        assert not _minted(seed_user)

    def test_naming_NO_member_still_mints_the_ordinary_row(
        self, app, db, seed_user,
    ):
        """Ruling **R-FN** is untouched where the owner says nothing.

        This is the behaviour every group had before the step, and it is the
        arm that must not have moved: a widening that quietly stopped minting
        would leave ``Sigma(lines) == Sigma(members)`` false on every group the
        owner does not attribute.
        """
        line, salary, allowance = self._a_payroll_deposit(seed_user)

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        assert accepted.residual == Decimal("0.05")
        assert accepted.repriced_count == 0
        assert settled_figure(salary) == Decimal("2473.38")
        assert settled_figure(allowance) == Decimal("100.00")
        minted = _minted(seed_user)
        assert len(minted) == 1
        assert resolved_amount(minted[0]) == Decimal("0.05")

    def test_the_two_remedies_are_EXCLUSIVE(self, app, db, seed_user):
        """Correcting a member and minting one for the same gap cannot both.

        The property :func:`record_match` used to get from ``bank_cash_for``
        and which a first version of plan step ``bank_import:X-f6d-4`` lost by
        gating the mint on the owner's consent alone: a one-row match carrying
        a consent then did BOTH.  It is stated over the widened shape here --
        the gap is `$0.05` and the books may move by `$0.05` and no more.
        """
        line, salary, allowance = self._a_payroll_deposit(seed_user)
        before = Decimal("2473.38") + Decimal("100.00")

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
            attributed=(RowKind.TRANSACTION, salary),
        )

        after = (
            settled_figure(salary)
            + settled_figure(allowance)
            + sum(
                (resolved_amount(row) for row in _minted(seed_user)),
                Decimal("0.00"),
            )
        )
        assert after - before == Decimal("0.05")
        assert after == Decimal("2573.43")

    def test_the_members_sum_to_the_lines_after_an_attributed_match(
        self, app, db, seed_user,
    ):
        """``Sigma(lines) == Sigma(members)``, the identity of plan step X-f6d-4.

        It held BY CONSTRUCTION because the minted row closed the gap.  Under
        an attribution nothing is minted, so it has to hold because the named
        member MOVED -- a different mechanism reaching the same invariant, and
        one nothing else in this file grades.
        """
        line, salary, allowance = self._a_payroll_deposit(seed_user)

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
            attributed=(RowKind.TRANSACTION, salary),
        )

        # The members name each row's PAYMENT (plan step
        # ``credit_card:CC-5-4a-1``, ruling **R-CC43**), and the row is
        # reached through it; the sum is the rows' settled figures, as before.
        # Developer confirmation 2026-09-21 (rule 5): "Confirm A, B and C as rule-5 re-expressions
        # under R-CC43 -- the member names the payment; a day moves through the seam that mirrors
        # it; a settled row is ticked as its payment."
        members = (
            db.session.query(StatementMatchMember)
            .filter(
                StatementMatchMember.match_id == accepted.match_id,
                StatementMatchMember.transaction_entry_id.isnot(None),
            )
            .all()
        )
        total = sum(
            (
                settled_figure(member.entry.transaction)
                for member in members
            ),
            Decimal("0.00"),
        )
        assert len(members) == 2
        assert all(member.entry.covers_settlement for member in members)
        assert total == line.amount

    def test_naming_a_row_this_match_does_NOT_carry_is_refused(
        self, app, db, seed_user,
    ):
        """A crafted body may not pick the remedy by pointing at nothing.

        Read as *no member*, this would silently mint instead of correcting --
        the sender choosing which of two money acts happens.  ``resolve_rows``
        refuses it before any arithmetic, so nothing is written either way.
        """
        line, salary, allowance = self._a_payroll_deposit(seed_user)
        elsewhere = a_transaction(
            seed_user, name="Not in this match", amount="40.00",
        )

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[salary, allowance],
                residual="0.05",
                attributed=(RowKind.TRANSACTION, elsewhere),
            )

        assert "does not include" in str(caught.value)
        assert status_seam.recorded_settlement(salary) is None
        assert status_seam.recorded_settlement(allowance) is None
        assert not _minted(seed_user)

    def test_an_attribution_disagreeing_with_ITS_OWN_ROW_is_refused(
        self, app, db, seed_user,
    ):
        """The pointer is compared as a WHOLE reviewed value, not by subject.

        A body whose consent names the salary row at a figure its own
        ``rows`` entry does not carry is describing two states of one row --
        finding **N-336**'s shape with both halves inside one submission -- and
        a subject-only comparison would accept it and re-price against
        whichever half it happened to read.
        """
        line, salary, allowance = self._a_payroll_deposit(seed_user)
        scope = a_scope(seed_user)
        submission = a_submission(
            scope, lines=[line], transactions=[salary, allowance],
            residual="0.05",
            attributed=(RowKind.TRANSACTION, salary),
        )
        crafted = replace(
            submission,
            consent=replace(
                submission.consent,
                on_row=replace(
                    submission.consent.on_row, cash_amount=Decimal("9999.99"),
                ),
            ),
        )

        with pytest.raises(ValidationError) as caught:
            statement_match.accept_match(crafted, scope)

        assert "does not include" in str(caught.value)
        assert status_seam.recorded_settlement(salary) is None
        assert not _minted(seed_user)

    def test_the_consent_gate_still_applies_to_an_attributed_group(
        self, app, db, seed_user,
    ):
        """Naming a member is not agreeing to a figure (ruling **R-IA**).

        The two are different questions -- *whose* and *how much* -- and a
        widening that let the first stand in for the second would put a
        re-price of any size one press away with nothing reviewed.

        **Since plan step ``bank_import:X-gp`` the two travel as ONE value**,
        so *named a member, agreed to nothing* is a body the wire cannot
        express (``a_submission`` refuses to build it, and the schema refuses
        the token).  What is expressible, and what this now grades, is a
        member named beside the WRONG figure: the gate compares the figure
        half whatever the member half says, so a re-price cannot ride in on
        a stale consent because the owner also said whose it was.
        """
        line, salary, allowance = self._a_payroll_deposit(seed_user)

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[salary, allowance],
                residual="0.04",
                attributed=(RowKind.TRANSACTION, salary),
            )

        assert "reviewed against a difference of +0.04" in str(caught.value)
        assert "now comes to +0.05" in str(caught.value)
        assert status_seam.recorded_settlement(salary) is None
        assert not _minted(seed_user)

    def test_an_UNNAMED_member_with_no_figure_of_its_own_still_refuses(
        self, app, db, seed_user,
    ):
        """``_reject_uncorrectable_row`` is asked of EVERY member, still.

        A CC payback's figure is a fact about the card spend it repays, so a
        difference on a group holding one is a different repair on a
        different row, and one this door must not paper over.  (Through plan
        step ``balance:X-bi-4a``'s first cut the member was an envelope
        holding a purchase, whose figure is its purchases; ruling **R-BAL81**
        keeps such a row out of the offer altogether, so the payback is the
        census's one member the matcher still offers.)

        **The attribution here points at the OTHER member**, which is what
        makes this a firing control for the widening rather than a restatement
        of the refusal: narrowing the census to *the member that absorbs the
        gap* -- the obvious simplification once one member is named -- passes
        every case that attributes to the bad row and drops this one, booking
        `$0.06` to Uncategorized beside an envelope whose own sentence says
        what to fix.  Plan step ``bank_import:X-f6d-4`` measured that
        substitution at `$180.00` when the dispatch was on ``len(rows) == 1``.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-280.06",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        payback = _a_cc_payback(seed_user, amount="180.00")
        priced = a_transaction(seed_user, name="Electricity", amount="100.00")

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[payback, priced],
                residual="-0.06",
                attributed=(RowKind.TRANSACTION, priced),
            )

        assert "no figure of its own" in str(caught.value)
        assert status_seam.recorded_settlement(priced) is None
        assert not _minted(seed_user)

    def test_the_UNDO_of_an_attributed_group_removes_NOTHING(
        self, app, db, seed_user,
    ):
        """Ruling **R-GG**'s direction, reached by a shape it predates.

        *Deleting the record does not put the days back* (:mod:`._release`),
        and a FIGURE the bank stated is the same kind of fact as a day: the
        undo restores the QUESTION -- the line becomes unexplained and the
        rows matchable -- and leaves the correction standing.  A one-row match
        has always behaved that way; this pins that an attributed GROUP is not
        a new exception, which it would be if the widening had made the
        re-price look like a creation.

        **The contrast is the point.** An UNATTRIBUTED group's difference is a
        row this act CREATED, so the undo takes it back
        (:class:`TestTheMintedRowIsAMemberOfTheMatch`); an attributed one
        creates nothing, so there is nothing to take back and the figure the
        bank stated stays.
        """
        line, salary, allowance = self._a_payroll_deposit(seed_user)
        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
            attributed=(RowKind.TRANSACTION, salary),
        )
        assert settled_figure(salary) == Decimal("2473.43")

        released = statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert released.removed_rows == 0
        assert settled_figure(salary) == Decimal("2473.43"), (
            "the bank's figure is evidence, and unlinking the record is not a "
            "reason to throw it away"
        )
        assert db.session.query(StatementMatchMember).filter(
            StatementMatchMember.match_id == accepted.match_id,
        ).count() == 0


class TestAMemberCannotBeGivenAFigureItsOwnRowCannotHold:
    """The refusal the LONE-ROW shape never needed, and the widening did.

    A transaction stores a non-negative GROSS whose direction comes from its
    ``transaction_type_id``, so the only cash it can be moved to is cash of the
    direction it already has.  For a match naming ONE row that was guaranteed
    for free: :func:`_reject_opposed_movements` compares the two SUMS, and with
    one row the app side IS that row's cash.  A group breaks the guarantee --
    this module's own docstring says mixed signs within a group stay legal,
    *because a net deposit really is a gross income row less a deduction* --
    and the select offers the negative member too.

    **Found by adversarial code review 2026-09-01, and it was not silent
    money**: the settle door writes a magnitude, the row lands on the wrong
    side, and :func:`~._accept._reject_drifted_under_the_act` catches the
    arithmetic and reports *"Applying this match moved one of its own rows"*.
    Nothing drifted.  A guard that catches the right state under the wrong name
    sends the owner to do something that cannot help.
    """

    @staticmethod
    def _a_net_deposit(seed_user):
        """Stage a deposit whose group holds a member of each direction.

        Returns:
            ``(line, salary, deduction)`` -- a `+$2,060.00` line against a
            `+$2,050.00` income row and a `-$50.00` expense row, which come to
            `+$2,000.00` and leave `$60.00`.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2060.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        salary = a_transaction(
            seed_user, name="Data Manager", amount="2050.00", income=True,
        )
        deduction = a_transaction(
            seed_user, name="Union Dues", amount="50.00",
        )
        return line, salary, deduction

    def test_a_member_the_bank_leaves_on_the_OTHER_SIDE_is_refused(
        self, app, db, seed_user,
    ):
        """`+$10.00` on an expense row, which no expense row can be worth."""
        line, salary, deduction = self._a_net_deposit(seed_user)

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[salary, deduction],
                residual="60.00",
                attributed=(RowKind.TRANSACTION, deduction),
            )

        assert "the other way" in str(caught.value)
        assert "+10.00" in str(caught.value)
        assert status_seam.recorded_settlement(salary) is None
        assert status_seam.recorded_settlement(deduction) is None
        assert not _minted(seed_user)

    def test_a_member_the_bank_leaves_at_NOTHING_is_refused(
        self, app, db, seed_user,
    ):
        """A row worth `$0.00` is not a row this app can hold.

        The sub-case one cent away from the arm above, and it fails
        DIFFERENTLY without this refusal: the figure is storable, the
        candidate constructor then declines to price a zero-valued row, and
        the owner is told *"One of the rows in this match stopped being
        priceable while it was being applied"* -- which is equally false.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2050.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        salary = a_transaction(
            seed_user, name="Data Manager", amount="2050.00", income=True,
        )
        deduction = a_transaction(
            seed_user, name="Union Dues", amount="50.00",
        )

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[salary, deduction],
                residual="50.00",
                attributed=(RowKind.TRANSACTION, deduction),
            )

        assert "worth nothing" in str(caught.value)
        assert status_seam.recorded_settlement(salary) is None
        assert not _minted(seed_user)

    def test_the_SAME_group_lands_on_the_member_that_CAN_hold_it(
        self, app, db, seed_user,
    ):
        """The paired arm, so the refusal is not simply refusing everything.

        Same three rows, same figures, the OTHER member named: the bank leaves
        the income row `+$2,110.00`, which an income row can be worth, and the
        act lands.
        """
        line, salary, deduction = self._a_net_deposit(seed_user)

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, deduction],
            residual="60.00",
            attributed=(RowKind.TRANSACTION, salary),
        )

        assert settled_figure(salary) == Decimal("2110.00")
        assert settled_figure(deduction) == Decimal("50.00")
        assert accepted.repriced_count == 1
        assert not _minted(seed_user)

    def test_a_PURCHASE_member_may_take_EITHER_direction(
        self, app, db, seed_user,
    ):
        """A purchase stores a SIGNED amount since ruling **R-II**.

        A refund is a negative purchase, so a purchase member is asked only
        about ZERO -- and this is the one shape where the sign arm must NOT
        fire.  It also grades :func:`corrected_figure`'s purchase arm
        (``-bank_cash``, the R-II inversion) in the shape this step created,
        which no lone-row case reaches.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-95.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        first = a_purchase(seed_user, envelope, amount="60.00")
        second = a_purchase(seed_user, envelope, amount="40.00")

        accepted = _submit(
            seed_user, lines=[line], entries=[first, second],
            residual="5.00",
            attributed=(RowKind.PURCHASE, second),
        )

        assert second.amount == Decimal("35.00"), (
            "the bank leaves this purchase -95.00 less the other's -60.00, "
            "which is -35.00 of cash and 35.00 stored"
        )
        assert first.amount == Decimal("60.00")
        assert accepted.repriced_count == 1
        assert not _minted(seed_user)

    def test_a_PURCHASE_the_bank_leaves_on_the_OTHER_SIDE_is_a_REFUND(
        self, app, db, seed_user,
    ):
        """The arm that makes the purchase exemption load-bearing.

        **The case above does NOT reach it**, and a mutation sweep is how that
        was found: there the bank leaves the named purchase on its own side, so
        asking a purchase the sign question would have changed nothing and the
        exemption was untested.  Here the bank leaves it on the OTHER side, and
        ruling **R-II** is what makes that a legal row rather than a refusal --
        a refund is a negative purchase, so a purchase member may cross zero
        where a transaction member may not.

        `-$95.00` against a `$100.00` purchase and a `$10.00` one: the bank
        leaves the second `+$5.00` of cash, which stores as `-$5.00` -- a `$5`
        refund inside Groceries -- and the group then comes to the line.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-95.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        envelope = a_transaction(
            seed_user, name="Groceries", amount="110.00", is_envelope=True,
        )
        spend = a_purchase(seed_user, envelope, amount="100.00")
        refunded = a_purchase(seed_user, envelope, amount="10.00")

        accepted = _submit(
            seed_user, lines=[line], entries=[spend, refunded],
            residual="15.00",
            attributed=(RowKind.PURCHASE, refunded),
        )

        assert refunded.amount == Decimal("-5.00")
        assert spend.amount == Decimal("100.00")
        assert accepted.repriced_count == 1
        assert not _minted(seed_user)
