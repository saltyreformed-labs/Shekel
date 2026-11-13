"""The basis a read pass builds and the maps it answers: the batch tier, one derivation per read
pass, a row's budget against its contribution (ruling E-21), and one displayed figure per row.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.services import income_service, transaction_service, transfer_legs
from app.services.cash_ledger import (
    AmountRule,
    derived_amount_basis,
    amount_rule,
    amounts_by_key,
    contributions_by_key,
    resolve_transaction_amount,
    resolve_transfer_amount,
)
from app.services.cash_ledger import transfer_amount_rule
from tests._test_helpers import add_txn, capture_sql_statements
from app.services.balance_at import BalanceContext
from app.routes.grid._items import build_amount_maps
from app.services.row_valuation import settled_contribution
from app.services.transfer_legs import parent_of

from ._helpers import (
    _OLD_PRICE,
    _NOT_AN_ANSWER,
    _basis_for,
    _priced_template,
    _salary_template,
    _template_row,
    _legs_of,
    _loan_payment,
)


class TestTheBatchTier:
    """``amount_basis``, and the merged map that is now derived from it."""

    def test_the_basis_keeps_the_two_derivations_apart(
        self, app, db, seed_user, seed_periods,
    ):
        """Salary answers and loan answers come from their own derivations.

        Which rule prices a row is a fact about the row; merging the two first
        would make it a question about which map the id turned up in, which is
        the discriminator ruling R-FI refuted.

        **The loan payment is asked as its TRANSFER since plan step
        balance:X-bi-6-4d-2** (ruling **R-BAL167** class 1), and one clause
        is gone with no row to ask: ``salary_net_for(shadow, ...) is None``
        asked the salary derivation about the loan payment's shadow, and the
        payment is a transfer now, which that derivation (it takes INCOME
        transaction rows) is never handed.  The claim is narrower by that
        clause.
        """
        template, _profile = _salary_template(seed_user)
        paycheck = _template_row(
            seed_periods[0], template,
        )
        xfer = _loan_payment(seed_user, derive=True)
        basis = _basis_for(seed_user)

        # WHICH RULE prices each row is decided by the row, and the two rows
        # reach different rules.  This asked ``basis.loans.live_cash`` on both
        # until plan step X-au-g-2c-2 deleted that method; the dispatch is the
        # thing the assertion was always about, so it is asserted directly.
        # The loan payment's rule is asked of its PARENT since plan step
        # X-au-f-2 (ruling R-BAL10).
        assert amount_rule(paycheck) is AmountRule.SALARY
        assert transfer_amount_rule(xfer) is AmountRule.LOAN_PAYMENT
        # And each derivation answers its own row.  "Apart" also meant that
        # the two do not CROSS -- the salary derivation answered nothing for
        # the loan payment's shadow -- and that clause went with the shadow
        # (the docstring), so this asserts each answers, not that they part.
        assert income_service.salary_net_for(
            paycheck, basis.salary,
        ) != Decimal(_NOT_AN_ANSWER)
        assert resolve_transfer_amount(xfer, basis) == Decimal("1499.10")

    def test_a_basis_answers_for_a_row_it_was_not_built_over(
        self, app, db, seed_user, seed_periods,
    ):
        """A basis is pinned to an OWNER and a SCENARIO, never to a row set.

        This replaces the membership refusal plan step X-au-c2b deleted.  That
        guard existed because a basis stored per-row ANSWERS, so a MISS was
        indistinguishable from a producer's deliberate omission -- an
        adversarial review reproduced a manual loan payment resolved outside its
        own basis answering ``$1,250.00`` where ``$1,400.00`` was right,
        silently dropping a standing ``$150.00`` extra.  A basis holds the
        DERIVATIONS now, so nothing is ever absent: the payment's cash is
        computed from its own config whenever it is asked.  The control is the
        MANUAL-with-extra shape the review used, resolved against a basis built
        before the row existed at all.
        """
        stale = _basis_for(seed_user)
        xfer = _loan_payment(
            seed_user, derive=False, extra=Decimal("150.00"),
        )
        # Built before this payment existed, and it prices it: the definition's
        # $1,300.00 plus the standing $150.00.  The review's failing figure was
        # $1,250.00 -- the parent's stored column with the extra dropped -- and
        # it is named so the assertion cannot pass on a dropped term.  Asked
        # of the TRANSFER, whose rule the shadow answered, since plan step
        # balance:X-bi-6-4d-2 (ruling R-BAL167 class 1).
        assert resolve_transfer_amount(xfer, stale) == Decimal("1450.00")
        other = add_txn(
            db.session, seed_user, seed_periods[0], "Fuel", "60.00",
        )
        assert resolve_transaction_amount(other, stale) == Decimal("60.00")

    def test_ONE_map_prices_a_paycheck_a_loan_payment_and_an_owner_together(
        self, app, db, seed_user, seed_periods,
    ):
        """The map a screen reads covers every row, each by its own rule.

        **This case asserted a UNION of two maps until plan step X-au-d.**
        ``live_amounts`` held the read-time repair's answers and
        ``display_amounts_by_id`` laid them over the resolved ones; both
        cutovers deleted the repair (the loan half at X-au-g-2c-2, the salary
        half at X-au-d), so there is one map and the composition has no second
        half left to drop.  What the case still has to show is that the ONE map
        is TOTAL over kinds -- a map that had quietly stopped pricing loan
        payments, or paychecks, would satisfy a per-kind assertion perfectly.

        Three rows, three rules, three distinguishable figures: a declared
        paycheck answered by its profile, a derive-mode loan payment answered
        by the loan, and a paycheck a HUMAN re-priced answered by its own
        column.  The third is what stops the first from passing on a resolver
        that answers rule 2 for every salary-linked row whatever its ownership.

        **The map is the GRID's merged budgets map since plan step
        balance:X-bi-6-4d-2** (ruling **R-BAL167** class 1).  The loan
        payment's two shadow rows sat in ``amounts_by_key`` beside the
        paychecks; no shadow is born, and a screen holds a transfer's LEGS in
        the same map by ``leg.cell_key`` (``amounts_by_key``, since plan
        step ``balance:X-bi-6-1``).  The case asks the GRID's merge,
        ``build_amount_maps``, rather than restating it here.  It is not the
        only spelling: the dashboard pulse builds the same rows-plus-legs map
        inline (``dashboard_service/_pulse.py``), a second home for one
        derived map (CLAUDE.md rule 14), reported at plan step
        balance:X-bi-6-4d-2 rather than moved in this tests-only step.
        """
        template, _profile = _salary_template(seed_user)
        paycheck = _template_row(
            seed_periods[0], template,
        )
        repriced = _template_row(
            seed_periods[1], template, owns=True,
        )
        xfer = _loan_payment(
            seed_user, derive=True,
        )
        legs = _legs_of(xfer)
        basis = _basis_for(seed_user)

        shown = build_amount_maps([paycheck, repriced, *legs], basis).budgets

        assert set(shown) == {
            paycheck.id, repriced.id, *(leg.cell_key for leg in legs),
        }
        assert shown[legs[0].cell_key] == Decimal("1499.10")
        assert shown[legs[1].cell_key] == Decimal("1499.10")
        assert shown[repriced.id] == Decimal(_NOT_AN_ANSWER)
        assert shown[paycheck.id] > Decimal("0")
        assert shown[paycheck.id] != Decimal(_NOT_AN_ANSWER), (
            "a DECLARED paycheck is priced by its profile; answering the "
            "figure the row beside it owns would mean the rule read a column"
        )


def _touch(*rows):
    """Refresh *rows* from the database, outside any statement capture.

    ``db.session.commit()`` EXPIRES every instance, so the next attribute read
    emits a PK SELECT.  The query-count controls below are about whether a
    DERIVATION re-runs, and a refresh landing inside their capture would make
    them assertions about SQLAlchemy's expiry policy instead.

    Args:
        rows: The ORM instances to load back.
    """
    for row in rows:
        _ = row.estimated_amount


class TestTheBasisIsOneDerivationPerReadPass:
    """The property plan step X-au-c2b's restructure exists to make structural.

    A basis held per-row ANSWERS for one row SET until that step, so a request
    that loaded two row sets ran the paycheck engine and the loan resolver twice
    -- findings **N-268** and **N-269**, two filings of that one cause.  Holding
    the DERIVATIONS instead makes "one pricing pass per read pass" a property of
    the object rather than a discipline every surface has to remember, and these
    are the controls that say so.

    Each is a QUERY-COUNT assertion, because that is the only thing that can
    tell a memo from a re-derivation: both answer the same figure, which is
    exactly why the duplication went unnoticed long enough to be filed twice.
    The one exception is the no-derivation case below, which reads the two
    memos directly since plan step balance:X-bi-7c (an ordinary row prices
    through its definition now, so "no statement at all" stopped being the
    fact) and bounds the row's own reads beside it.
    """

    def test_no_paycheck_and_no_loan_payment_means_neither_derivation_resolves(
        self, app, db, seed_user, seed_periods,
    ):
        """A row set with neither kind resolves neither derivation.

        The "fast no-op when there are no candidates" property the row-set
        producers had, KEPT rather than traded away for the sharing: both
        derivations are lazy and each half of :func:`live_override` answers
        ``None`` from the row's own columns before it touches one.  Without that
        the salary projection would run on every read pass in the app.

        **The BASIS is built inside the capture, and that is what makes this a
        control.**  Built outside it, an eagerly-loading derivation issues its
        query before the capture opens and this passes anyway -- which a
        mutation check caught it doing: making ``LoanPricing`` load its config
        map in its constructor left the assertion green.
        """
        row = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        db.session.commit()
        # The commit EXPIRED the row, so the first attribute read refreshes it
        # from the database.  Done here rather than inside the capture: that
        # SELECT is SQLAlchemy's, not the derivation's, and counting it would
        # make this assertion about the session rather than about laziness.
        _touch(row)
        # The two pins are read out HERE for the same reason: the commit
        # expired the seeded ``User`` and ``Scenario`` too, and their refresh
        # SELECTs are the session's rather than the amount model's.
        user_id = seed_user["user"].id
        scenario_id = seed_user["scenario"].id

        built = {}

        def _price():
            built["basis"] = derived_amount_basis(user_id, scenario_id)
            return amounts_by_key([row], built["basis"])

        _answer, statements = capture_sql_statements(_price)

        assert _answer == {row.id: Decimal("35.00")}
        # **Graded on the two derivations' own memos since plan step
        # balance:X-bi-7c** (ruling **R-BAL59**, the rule-5 batch).  The three
        # statements are finding **BAL-511** (closed at X-bi-7d-2: the grid read path is flat across the cutover).
        # An ordinary expense row is a ONE-OFF -- a rule-less definition's
        # placed row, TEMPLATE-priced (rule 3) -- so pricing it reads the row's
        # OWN relations: its definition, that definition's price series, and
        # the one salary-profile row rule 2's refinement asks about (measured
        # 2026-09-16: three statements per row loaded bare).  This asserted
        # ``statements == []`` while the row OWNED its figure and rule 1
        # answered from the row alone; what the control has always been about
        # is the two DERIVATIONS of the basis, and neither may have resolved.
        # The memos are read directly because a statement's text cannot tell
        # the refinement's one-profile read from the projection's.
        basis = built["basis"]
        touched = [text for text, _params in statements]
        # Pylint: ``protected-access`` -- the derivation's memo IS the fact.
        assert basis.salary._profiles is None, (  # pylint: disable=protected-access
            "pricing an ordinary expense row must not resolve the paycheck "
            f"derivation; got {touched}"
        )
        # The loan derivation's memo is the pass's ``LoanCalendars`` since plan
        # step recurrence:R25 (it was ``LoanPricing._loans``); its params memo
        # is the first thing any loan price loads, and the calendar is built
        # over it, so both empty is "no loan resolved".
        # Pylint: ``protected-access`` -- the loan derivation's memo IS the fact.
        terms = basis.loans._terms  # pylint: disable=protected-access
        # Pylint: ``protected-access`` -- its two memos ARE the fact.
        assert not terms._params and not terms._calendars, (  # pylint: disable=protected-access
            "pricing an ordinary expense row must not resolve the loan "
            f"derivation; got {touched}"
        )
        # The row's own reads, BOUNDED so growth is seen: the definition, its
        # one salary-profile check, its version series -- finding BAL-511's
        # three, per definition.  A fourth here is a new per-row read.
        assert len(statements) <= 3, touched

    def test_the_salary_projection_runs_ONCE_however_many_row_sets_ask(
        self, app, db, seed_user, seed_periods,
    ):
        """Two row sets, one basis, one paycheck engine run.

        The direct control on **N-268**: the dashboard pulse priced rows the
        cash fold had already priced, and paid a second full projection for it.
        The second ask must issue no statement at all -- not merely fewer.
        """
        template, _profile = _salary_template(seed_user)
        # DERIVED, not owning: an OWN paycheck is answered by rule 1 off its own
        # column and touches no projection at all, so the first ask would issue
        # no statement and this control would measure nothing (plan step
        # X-au-d, where the map became ``amounts_by_key``).
        first = _template_row(
            seed_periods[0], template,
        )
        second = _template_row(
            seed_periods[1], template,
        )
        db.session.commit()
        basis = _basis_for(seed_user)
        _touch(first, second)

        _first, first_statements = capture_sql_statements(
            lambda: amounts_by_key([first], basis),
        )
        _second, second_statements = capture_sql_statements(
            lambda: amounts_by_key([second], basis),
        )

        assert first_statements, "the first ask must resolve the projection"
        assert second_statements == [], (
            "the second row set must answer from the derivation the first "
            f"resolved; got {[text for text, _params in second_statements]}"
        )
        # The ANSWER, not just the query count.  A memo that answers correctly
        # once and empty afterwards satisfies a count assertion perfectly, and
        # an adversarial review of this file caught exactly that omission: the
        # second ask must be the same live net, not merely cheap.
        assert _first[first.id] == _second[second.id] > Decimal("0")

    def test_the_loan_resolve_runs_ONCE_however_many_legs_ask(
        self, app, db, seed_user, seed_periods,
    ):
        """Both legs of one payment, one basis, one loan resolve.

        The direct control on **N-269**: the transfer settle door re-queried the
        transfer and re-resolved the loan for every offered row, because each
        offered row built its own basis.  The two legs of one payment are the
        smallest shape that shows it -- they share the transfer, the config and
        the destination loan, so everything the second ask needs is what the
        first resolved.

        **It asks the RESOLVER since plan step X-au-g-2c-2**, where it asked
        ``basis.loans.live_cash``.  That is a wider claim rather than a
        translation: the memo it grades is still ``LoanPricing._loan``, but the
        path ran through ``amount_rule`` and rule 4, so a second shadow that
        re-read its transfer or re-resolved its template showed up here too.
        Both shadows were ``_touch``-ed first, so the relationship loads were
        not what the count measured.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-2** (it was
        ``test_the_loan_resolve_runs_ONCE_however_many_shadows_ask``; ruling
        **R-BAL167** class 1).  The two asks are the payment's two LEGS, each
        priced by ``amounts_by_key``, where they were its two shadow rows
        priced by the resolver.  Both legs hold ONE transfer, so the second
        cannot re-read it.  What the empty second count can see is a
        STATEMENT: a loan resolved again, or any explicit query.  The
        definition and its settings are relationships the first ask loaded,
        so reading them again issues none, and this count cannot say whether
        the second ask re-read them.  The legs are built after the commit and
        before either capture, which reads the transfer's columns back where
        ``_touch`` read each row's.
        """
        xfer = _loan_payment(seed_user, derive=True)
        db.session.commit()
        basis = _basis_for(seed_user)
        first, second = _legs_of(xfer)

        _first, first_statements = capture_sql_statements(
            lambda: amounts_by_key([first], basis)[first.cell_key],
        )
        _second, second_statements = capture_sql_statements(
            lambda: amounts_by_key([second], basis)[second.cell_key],
        )

        assert first_statements, "the first ask must resolve the loan"
        assert second_statements == [], (
            "the second leg must answer from the loan already resolved; got "
            f"{[text for text, _params in second_statements]}"
        )
        # The FIGURE: both legs of one payment are priced off its one
        # transfer, so a memo that answered ``None`` the second time would
        # show two figures for one transfer while passing every query-count
        # assertion.
        assert _first == _second == Decimal("1499.10")

    def test_the_settle_freeze_and_the_display_ask_ONE_rule(
        self, app, db, seed_user, seed_periods,
    ):
        """The AMOUNT MODEL is the single rule, where there were two functions.

        ``live_loan_transfer_amounts`` (the display) and
        ``live_loan_payment_amount`` (the settle freeze) were two
        implementations of one rule, the second's docstring stating that it
        "mirrors" the first's candidate filter -- kept in step by hand, which is
        the shape that eventually disagrees.  They became one method
        (``LoanPricing.live_cash``) at plan step X-au-c2b and NO method at plan
        step X-au-g-2c-2: a derived payment is priced by rule 4, so the settle
        books what the display shows because both ask the resolver.

        The control is unchanged in kind and stronger in reach -- it now spans
        two packages rather than two callers of one method: what the screen
        publishes (``amounts_by_key``) and what a tick would book
        (``transaction_service.settle_amount`` of the leg's transfer, the one
        composition since ruling plan#1983's settle fold deleted
        ``transfer_service.leg_settle_amount``) must be the same figure.
        Both are asked of the checking-side LEG since plan step
        ``balance:X-bi-6-4d-2``, which prices a leg off its transfer and its
        side's record and deleted ``settle_amount(shadow)``; they were asked
        of the checking-side twin row until then (``amounts_by_key``,
        ``settle_amount``), and the figure is unchanged (ruling R-BAL167
        class 1, plan step balance:X-bi-6-4d-2).
        """
        # The payment is the transfer, and the leg is its checking side, where
        # it was the checking-side shadow's transfer and account until plan
        # step balance:X-bi-6-4d-2 (ruling R-BAL167 class 4).
        xfer = _loan_payment(seed_user, derive=True)
        db.session.commit()
        basis = _basis_for(seed_user)
        leg = transfer_legs.leg_of(xfer, xfer.from_account_id)

        displayed = amounts_by_key([leg], basis)[leg.cell_key]
        booked = transaction_service.settle_amount(parent_of(leg), basis)

        assert booked == displayed == Decimal("1499.10")

    def test_one_read_pass_hands_out_the_SAME_basis(  # pylint: disable=unused-argument
        self, app, db, seed_user, seed_periods,
    ):
        """``BalanceContext.amounts()`` memoizes, so a render holds one.

        Identity, not equality: two bases with the same pins ARE equal (the
        derivations are excluded from the comparison, exactly as
        ``BalanceContext``'s own memo caches are), so an equality assertion
        would pass on two objects that each resolve the projection separately.

        Pylint: ``unused-argument`` -- ``app``, ``db`` and ``seed_periods``
        are requested for the state they set up (the app context, the
        database, the periods the read pass is built over), never read.
        """
        ctx = BalanceContext.build(seed_user["user"].id)

        assert ctx.amounts() is ctx.amounts()
        # And that it memoized the RIGHT one.  A defect passing the wrong ids
        # -- ``derived_amount_basis(self.user_id, self.user_id)``, or a hardcoded
        # baseline -- memoizes just as well, and on a single-user single-
        # scenario seed the two ids are often equal, so identity alone cannot
        # see it.  The pins are public fields precisely so this is assertable.
        assert ctx.amounts().user_id == ctx.user_id
        assert ctx.amounts().scenario_id == ctx.scenario_id


class TestABudgetIsNotAContribution:
    """Ruling E-21's base, and the two accessors that answer it.

    A row's BUDGET and what it CONTRIBUTES are different questions, and plan
    step X-au-c2b's reader routing turns on their being different: an
    entry-tracked row's remaining, its over-budget flag and its amount cell all
    answer on the budget, which E-21 fixes on the row's own amount
    unconditionally -- never the entered actual, never status-dependent.  A
    reader handed a contribution instead would answer ``$0.00`` for a Cancelled
    envelope whose budget is still its budget, and would re-base a settled row's
    variance on the very number it is being compared against.
    """

    def test_a_cancelled_envelopes_budget_is_still_its_budget(
        self, app, db, seed_user, seed_periods,
    ):
        """The batch answers the amount; the contribution answers zero.

        Both are correct answers to their own question, which is why the reader
        has to pick.  ``$0.00`` on a Cancelled row is what a balance should
        count it as; it is not what the envelope was budgeted.
        """
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "Groceries", "400.00",
        )
        txn.status_id = ref_cache.status_id(StatusEnum.CANCELLED)
        db.session.flush()
        basis = _basis_for(seed_user)

        assert amounts_by_key([txn], basis) == {txn.id: Decimal("400.00")}
        assert contributions_by_key([txn], basis) == {txn.id: Decimal("0")}

    def test_an_entered_actual_does_not_move_the_budget(
        self, app, db, seed_user, seed_periods,
    ):
        """What a row RECORDED is what it cost, never what it was budgeted.

        The control on the surprises list: its two terms are the plan and the
        record, so a plan producer that answered the record would make every
        delta zero and the list empty.  This is the row that separates them.

        **The plan half names the RESOLVER since plan step X-bu.**  It read
        ``owned_amount``, the accessor that answered the plan from the row's own
        column -- a second spelling of the question ``resolve_transaction_amount``
        answers, and the pairing this docstring named until that step deleted it
        (finding **BAL-462**).  The contribution half is unchanged, and so are
        both figures: what the pair separates was never the accessor's doing.

        **The row is SETTLED since plan step X-au-c3.**  A figure records a
        settle, so the projected row this used to build -- ``$60.00`` budgeted,
        ``$81.40`` typed -- is a state the CHECKs refuse.  The separation under
        test is unchanged and so are both figures.
        """
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "Fuel", "60.00",
            status_enum=StatusEnum.DONE, settled_amount="81.40",
        )
        db.session.flush()
        basis = _basis_for(seed_user)

        assert resolve_transaction_amount(txn, basis) == Decimal("60.00")
        assert settled_contribution(txn) == Decimal("81.40")
        assert amounts_by_key([txn], basis) == {txn.id: Decimal("60.00")}

    def test_a_derived_rows_budget_is_ANSWERED_rather_than_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A row carrying no figure of its own still has a budget.

        **This test asserted the opposite until plan step X-bu, and the
        inversion is the step.**  It read ``owned_amount(txn)`` inside a
        ``pytest.raises`` and was named for the refusal, on the ground that a
        settled-only reader handed a derived row should fail loudly rather than
        subtract a ``None``.  The refusal was real and it FIRED -- on
        ``/analytics/spending``, for every window, on production-shaped data
        (finding **BAL-462**) -- because the accessor was a SECOND answer to a
        question ``resolve_transaction_amount`` already answers, and the two
        part exactly here: on a row an amount-source cutover has declared
        DERIVED.  What the deletion removed is the ACCESSOR -- a public,
        one-token answer to the plan question a reader could take instead of
        asking the amount model -- and NOT the refusal, which survives inside
        the amount model's own arms.  *Plan step X-bx moved that refusal into
        ``cash_ledger._amount_source`` as the private ``_own_figure``, so it is
        no longer reachable by a caller that spells the column read itself:
        ``shekel-private-module-import`` blocks the spelling and the module
        defines no public one.  This paragraph said it WAS reachable, which was
        true when written.*  So the surviving claim is the positive one below.

        **The two assertions guard DIFFERENT things, and an adversarial review
        corrected this paragraph's account of the first.**  It said the second
        alone would pass against a producer that had gone back to reading the
        column; it would not -- ``declare_derived`` EMPTIES the column, so such
        a producer refuses and the second assertion fails, which is exactly what
        a mutation of rule 3 measured.  What ``estimated_amount is None`` guards
        is the FIXTURE: a later change to ``_template_row`` or to the engine's
        row that left a figure on the row would keep the second assertion green
        while it graded nothing at all.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)

        assert txn.estimated_amount is None
        assert amounts_by_key(
            [txn], _basis_for(seed_user),
        ) == {txn.id: _OLD_PRICE}


class TestOneRowHasOneDisplayedFigure:
    """Every surface shows a row's amount by ONE rule (``amounts_by_key``).

    An adversarial review found that rule written twice and differently: the
    grid merged the seam's live-override map over its resolved one, while every
    HTMX fragment and the companion view published the resolved map ALONE under
    the same context key.  So a projected salary row whose profile had moved
    past its cached column showed the live net on the grid and the stale column
    in the quick-edit box the same click opened -- and that box is what a save
    posts back from, which is how a figure nobody saw gets booked.

    **The composition that defect was in is GONE as of plan step X-au-d**, and
    the class stays because the property does not: a screen and a balance still
    have to show one figure per row.  What makes that structural now is that
    the second answer has no home -- a declared row stores no column for a map
    to disagree with.
    """

    def test_a_declared_paycheck_displays_its_profiles_net(
        self, app, db, seed_user, seed_periods,
    ):
        """A paycheck the definition prices shows what the definition pays.

        The discriminator is the ROW's own column: it is empty, so a resolver
        that read it would answer ``None`` into a money map rather than a
        figure.
        """
        template, _profile = _salary_template(seed_user)
        paycheck = _template_row(
            seed_periods[0], template,
        )
        basis = _basis_for(seed_user)

        displayed = amounts_by_key([paycheck], basis)[paycheck.id]

        assert paycheck.estimated_amount is None
        assert displayed > Decimal("0")

    def test_a_paycheck_a_HUMAN_repriced_displays_the_typed_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """Ownership decides, not the template's kind (finding **N-262**).

        The partner the case above needs: without it, a resolver answering
        rule 2 for every salary-linked row whatever its ownership would pass --
        and that resolver overwrites the figure a human typed, on the screen
        that same human types it into.
        """
        template, _profile = _salary_template(seed_user)
        paycheck = _template_row(
            seed_periods[0], template, owns=True,
        )
        basis = _basis_for(seed_user)

        assert amounts_by_key([paycheck], basis) == {
            paycheck.id: Decimal(_NOT_AN_ANSWER),
        }

    def test_an_ordinary_row_displays_what_it_resolves_to(
        self, app, db, seed_user, seed_periods,
    ):
        """The non-salary, non-derived case, so the map is total over kinds."""
        txn = add_txn(db.session, seed_user, seed_periods[0], "Rent", "1200.00")
        db.session.flush()
        basis = _basis_for(seed_user)

        assert amounts_by_key([txn], basis) == {txn.id: Decimal("1200.00")}
