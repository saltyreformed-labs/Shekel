"""X-i4: a read pass BINDS the account it values, and memoizes what it derives.

Plan step **X-i4** (shipped at ``79a1730c``),
closing finding **N-354**.

**What the defect was.**  ``_cash_fold.assemble`` and its four siblings took the
account and the pass's own derivations -- ``ctx.amounts()``, ``ctx.as_of``,
``ctx.reported_periods()`` -- as INDEPENDENT arguments.  They agreed only
because every call site happened to name one ``ctx`` two or three times, and
:class:`~app.services.balance_at.BalanceContext` pinned a ``user_id`` it never
checked against the account handed alongside.  The seam cited "an argument a
caller can get wrong is a defect, not a contract" in three places and then
reproduced the shape.

**Where the rule lives now, and why THERE.**  ``_memoize._memoize_once`` (in ``_context`` until recurrence:R7d-f-2 moved it) is the
one primitive that creates state keyed by ``account.id`` on a pass, so the
binding is a precondition of creating that state rather than a guard repeated at
each of the five funnels.  A funnel added later cannot forget a check it never
had to remember, and the four readings in ``_cash_fold`` now take the assembled
record and carry no account and no clock at all -- there is nothing left for a
caller to pair wrongly.

**It is NOT a second ownership gate**, and these tests are written so that
distinction stays visible.  Whether a REQUESTER may see an account is decided
upstream, where an untrusted id becomes a row.  What is graded here is whether an
account and a pass describe ONE READ -- a question no route can ask, because no
route knows a context exists, and one that a service-tier caller with no
``current_user`` at all (``debt_strategy``, ``tax_report_service``,
``loan_recurrence_sync``) could get wrong with two perfectly ownership-checked
objects.

:class:`TestWhatAMisPairingWouldHavePublished` is the one to read first: it
builds the disclosure the refusal prevents, from the same fixtures, so the stake
is executable rather than asserted in prose.

The primitive's OWN cases -- the refusal on a warm cache, that a refused build
never runs, that nothing is stored -- live beside its membership and
raising-build cases in ``test_loan_plan_assembly.py``, where ``_memoize_once``
was already graded.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.exceptions import ForeignAccountError
from app.services import balance_at
from app.services.cash_flow_set import CashFlowSet
from app.services.balance_at import BalanceContext
from app.services.balance_at._cash_fold import assembled_fold
from app.services.balance_at import _asset_fold
from app.services.balance_at._asset_contributions import ContributionInputs
from app.services.balance_at._plan import memoized_plan
from app.services.balance_at._positions import memoized_payoff
from app.services.balance_at._resolution import resolved_loan
from tests._test_helpers import (
    counting_calls,
    create_loan_account,
    create_savings_account,
)

# A date inside both fixtures' generated schedules, pinned so nothing here
# depends on the day the suite runs (``.claude/rules/testing.md``).
_AS_OF = date(2026, 3, 1)


def _pass_for(seed, as_of=_AS_OF):
    """Return the read pass for a seed fixture's owner, pinned at *as_of*."""
    return BalanceContext.build(seed["user"].id, as_of=as_of)


@pytest.fixture()
def foreign_loan(db, seed_second_user, seed_second_periods):
    # pylint: disable=unused-argument
    """Return a CONFIGURED loan belonging to the second owner.

    The four loan funnels are graded against a real loan rather than against
    the second owner's Checking account, and the difference is what makes the
    tests sharp: handed a non-loan, ``resolved_loan`` answers ``None`` and
    ``memoized_payoff`` raises a ``ValueError`` of its own, so a binding test
    built on one would pass on an incidental refusal instead of on the rule.
    With a configured loan every funnel below would return the OTHER owner's
    resolved schedule were the binding removed -- which is the state being
    refused.
    """
    return create_loan_account(
        seed_second_user, db.session, name="Second Owner Mortgage",
        principal=Decimal("240000.00"), rate=Decimal("0.06"), term=360,
        origination_date=date(2026, 1, 1),
    )


class TestEveryPassFunnelRefusesAForeignAccount:
    """All five per-account derivations a pass holds refuse, from one rule.

    The five are the complete set of things a ``BalanceContext`` memoizes under
    an ``account.id``: the cash fold, the loan walk, the loan resolution, the
    forward plan and the derived payoff.  Each is asserted separately rather
    than in a loop, so a failure names WHICH funnel stopped binding.
    """

    def test_the_cash_fold_refuses(
        self, db, seed_user, seed_periods, seed_second_user,
    ):  # pylint: disable=unused-argument
        """The funnel X-i4 introduced: ``assembled_fold(account, ctx)``."""
        with pytest.raises(ForeignAccountError):
            assembled_fold(seed_second_user["account"], _pass_for(seed_user))

    def test_the_loan_walk_refuses(
        self, db, seed_user, seed_periods, foreign_loan,
    ):  # pylint: disable=unused-argument
        """``BalanceContext.loan_walk`` -- routed through the primitive at X-i4.

        It open-coded its own store-once lines before, which is why it was the
        one account-keyed cache on the object the binding could not reach.
        """
        with pytest.raises(ForeignAccountError):
            _pass_for(seed_user).loan_walk(foreign_loan)

    def test_the_loan_resolution_refuses(
        self, db, seed_user, seed_periods, foreign_loan,
    ):  # pylint: disable=unused-argument
        """``_resolution.resolved_loan`` -- fills ``ctx.loans``."""
        with pytest.raises(ForeignAccountError):
            resolved_loan(foreign_loan, _pass_for(seed_user))

    def test_the_forward_plan_refuses(
        self, db, seed_user, seed_periods, foreign_loan,
    ):  # pylint: disable=unused-argument
        """``_plan.memoized_plan`` -- fills ``ctx.plans``."""
        with pytest.raises(ForeignAccountError):
            memoized_plan(foreign_loan, _pass_for(seed_user))

    def test_the_derived_payoff_refuses(
        self, db, seed_user, seed_periods, foreign_loan,
    ):  # pylint: disable=unused-argument
        """``_positions.memoized_payoff`` -- fills ``ctx.payoffs``."""
        with pytest.raises(ForeignAccountError):
            memoized_payoff(foreign_loan, _pass_for(seed_user))

    def test_the_foreign_loan_really_does_resolve_on_its_own_pass(
        self, db, seed_second_user, seed_second_periods, foreign_loan,
    ):  # pylint: disable=unused-argument
        """The precondition every refusal above rests on -- stated, not assumed.

        Each test above asserts that a call RAISES.  A raise proves nothing on
        its own: the same four calls would raise for an account that simply is
        not a configured loan, which is what a first draft of this class
        accidentally graded.  Read by its OWN owner's pass, this loan resolves
        to a real schedule -- so the refusals above are refusals of a read that
        would otherwise have succeeded and returned another owner's figures.
        """
        own_pass = _pass_for(seed_second_user)
        assert resolved_loan(foreign_loan, own_pass) is not None
        assert memoized_plan(foreign_loan, own_pass), (
            "precondition: this loan has a non-empty forward plan"
        )


class TestThePassBindsTheSCENARIOItValues:
    """The pass pins THREE things, and X-i4 bound the two that can be crossed.

    Finding **N-354**'s sentence -- "``BalanceContext`` pins a ``user_id`` and
    never checks it against the account handed alongside" -- applies verbatim to
    the ``scenario`` handed alongside, one field over on the same object, and
    X-i4's first build left it (adversarial review 2026-08-26, which measured a
    pass carrying one owner's ``user_id`` and another's scenario answering
    ``cash_balance_at`` a real figure).

    It is refused at ``__post_init__`` rather than in
    :meth:`~app.services.balance_at.BalanceContext.build`, because ``build``
    resolves the baseline itself and cannot get it wrong -- the constructor is
    the door that can, and it is public, frozen and directly called by fixtures.
    """

    def test_a_pass_refuses_another_owners_scenario(
        self, db, seed_user, seed_second_user,
    ):  # pylint: disable=unused-argument
        """The crossing the review measured, refused at construction."""
        with pytest.raises(ForeignAccountError) as excinfo:
            BalanceContext(
                user_id=seed_user["user"].id,
                scenario=seed_second_user["scenario"],
                as_of=_AS_OF,
            )
        message = str(excinfo.value)
        assert str(seed_user["user"].id) in message
        assert str(seed_second_user["user"].id) in message

    def test_a_pass_with_no_scenario_is_legal(
        self, db, seed_user,
    ):  # pylint: disable=unused-argument
        """``None`` is the DEGRADED state, not a foreign one.

        ``require_scenario`` is what answers a pass with no baseline, and the
        unit-test helper ``read_pass_over_paydays`` builds exactly this shape --
        so a check that refused ``None`` would refuse a legal context.
        """
        ctx = BalanceContext(
            user_id=seed_user["user"].id, scenario=None, as_of=_AS_OF,
        )
        assert ctx.scenario is None

    def test_build_produces_a_bound_pass(
        self, db, seed_user, seed_second_user,
    ):  # pylint: disable=unused-argument
        """Each owner's own ``build`` resolves that owner's own baseline.

        The precondition the refusal above rests on: ``build`` is not merely
        unaffected by the new check, it is what makes the check unreachable
        through the door production uses.
        """
        for seed in (seed_user, seed_second_user):
            ctx = BalanceContext.build(seed["user"].id, as_of=_AS_OF)
            assert ctx.scenario is not None
            assert ctx.scenario.user_id == seed["user"].id


class TestTheAssembledFoldBindsItsOwnAccount:
    """A reader taking BOTH a fold and an account cannot be handed two.

    :func:`~app.services.balance_at._asset_fold.resolve` folds the account's own
    modelled rule, its latest assertion and its contribution feed onto the cash
    fold's steps -- three reads of *account* against *cash*'s running total. It
    memoizes nothing, so the pass's refusal never reaches it, and X-i4's first
    build edited its docstring to say otherwise. The record carries
    ``account_id`` now, beside the ``scenario_id`` it already carried for
    exactly this reason -- and, since pay-calendar plan step **C4-a-1**, the
    CALENDAR it was clamped by, which is why ``resolve`` no longer takes one:
    the third determinant stopped being an argument a caller supplies
    separately, so these calls pass four values where they passed five.
    """

    def test_resolve_refuses_a_fold_assembled_for_another_account(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Two accounts of the SAME owner, so only the pairing is wrong.

        Deliberately not a cross-OWNER case: the pass already refuses that one,
        and it would pass this test for the wrong reason. What is graded here is
        that a fold belongs to the account it was assembled for, which is a
        narrower and stronger claim.
        """
        ctx = _pass_for(seed_user)
        other = create_savings_account(
            seed_user, db.session, "Elsewhere", Decimal("1234.00"),
        )
        db.session.commit()

        with pytest.raises(ValueError) as excinfo:
            _asset_fold.resolve(
                seed_user["account"],
                assembled_fold(other, ctx),
                _AS_OF,
                ContributionInputs.absent(),
            )
        assert str(other.id) in str(excinfo.value)
        assert str(seed_user["account"].id) in str(excinfo.value)

    def test_the_record_refuses_directly_too(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The rule lives on the RECORD, so every reader asks the same one.

        ``resolve`` is the only caller today; pinning it here as well means a
        second reader that takes both a fold and an account inherits a checked
        pairing rather than writing its own -- which is what put this on the
        value instead of in ``resolve``'s body.
        """
        ctx = _pass_for(seed_user)
        other = create_savings_account(
            seed_user, db.session, "Second", Decimal("50.00"),
        )
        db.session.commit()

        fold = assembled_fold(seed_user["account"], ctx)
        fold.require_account(seed_user["account"])  # its own account: no raise
        with pytest.raises(ValueError):
            fold.require_account(other)

    def test_the_matched_pairing_resolves(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The precondition: the same call with the RIGHT fold answers."""
        ctx = _pass_for(seed_user)
        account = seed_user["account"]
        folded = _asset_fold.resolve(
            account, assembled_fold(account, ctx), _AS_OF,
            ContributionInputs.absent(),
        )
        assert folded.seed == assembled_fold(account, ctx).seed


class TestThePublicSeamInheritsTheBinding:
    """Nine public entries refuse, and NOT ONE of them carries a check.

    This is what makes X-i4 structural rather than a fence: each entry below
    holds no ownership test of its own and inherits the refusal by REACHING a
    funnel, which is the only way to obtain per-account state from a pass.

    **It does NOT generalise to every seam entry, and the honest scope is the
    point** (adversarial review, 2026-08-26, which refuted a first draft of this
    docstring claiming it did).  An entry that early-outs BEFORE the funnel
    never reaches the rule: `liability_owed_at_dates` short-circuits on account
    KIND, `secured_loan_series` binds the loans and not the property account,
    `investment_growth_since_anchor` and `records_balance_at` return ``None``
    on the classifier gate, and every per-period entry answers an owner with no
    pay periods from an empty window. Those are finding **N-362**, owned by
    `X-i6`, and they are why this class enumerates the nine it grades rather
    than asserting a property of the whole seam. A pass binds what it MEMOIZES.

    ``balance_map`` / ``balance_at`` are on the KIND-CORRECT family and the
    seven above them on the CASH-FLOW family, so both dispatch paths are
    covered.
    """

    @pytest.fixture()
    def foreign(self, seed_user, seed_periods, seed_second_user):
        # pylint: disable=unused-argument
        """Return ``(this owner's pass, the other owner's account)``."""
        return _pass_for(seed_user), seed_second_user["account"]

    def test_cash_balance_map_refuses(self, db, foreign):
        # pylint: disable=unused-argument
        """The per-period cash map."""
        ctx, account = foreign
        with pytest.raises(ForeignAccountError):
            balance_at.cash_balance_map(account, ctx)

    def test_cash_balance_at_refuses(self, db, foreign):
        # pylint: disable=unused-argument
        """The cash scalar."""
        ctx, account = foreign
        with pytest.raises(ForeignAccountError):
            balance_at.cash_balance_at(account, ctx, _AS_OF)

    def test_cash_daily_balance_series_refuses(self, db, foreign):
        # pylint: disable=unused-argument
        """The day-grain cash series."""
        ctx, account = foreign
        with pytest.raises(ForeignAccountError):
            balance_at.cash_daily_balance_series(
                account, ctx, _AS_OF, _AS_OF + timedelta(days=3),
            )

    def test_cash_daily_facts_series_refuses(self, db, foreign):
        # pylint: disable=unused-argument
        """The day-grain series with its three tiers split out."""
        ctx, account = foreign
        with pytest.raises(ForeignAccountError):
            balance_at.cash_daily_facts_series(
                account, ctx, _AS_OF, _AS_OF + timedelta(days=3),
            )

    def test_records_balance_at_refuses(self, db, foreign):
        # pylint: disable=unused-argument
        """The true-up form's "what do the records say" figure."""
        ctx, account = foreign
        with pytest.raises(ForeignAccountError):
            balance_at.records_balance_at(account, ctx, _AS_OF)

    def test_cash_anchor_history_refuses(self, db, foreign):
        # pylint: disable=unused-argument
        """The assertion LOG -- the entry that publishes the other owner's
        recorded balances directly, which is the disclosure below."""
        ctx, account = foreign
        with pytest.raises(ForeignAccountError):
            balance_at.cash_anchor_history(account, ctx)

    def test_grid_balance_view_refuses(self, db, foreign):
        # pylint: disable=unused-argument
        """The grid's whole column set."""
        ctx, account = foreign
        with pytest.raises(ForeignAccountError):
            balance_at.grid_balance_view(CashFlowSet.single(account), ctx)

    def test_balance_map_refuses(self, db, foreign):
        # pylint: disable=unused-argument
        """The KIND-CORRECT per-period map."""
        ctx, account = foreign
        with pytest.raises(ForeignAccountError):
            balance_at.balance_map(account, ctx)

    def test_balance_at_refuses(self, db, foreign):
        # pylint: disable=unused-argument
        """The KIND-CORRECT scalar."""
        ctx, account = foreign
        with pytest.raises(ForeignAccountError):
            balance_at.balance_at(account, ctx, _AS_OF)


class TestWhatAMisPairingWouldHavePublished:
    """The stake, built from the fixtures rather than asserted in prose.

    A foreign account's TRANSACTIONS are scenario-scoped, so a mis-paired pass
    folds none of them -- which is exactly what made the defect quiet.  Its
    balance ASSERTIONS are not: ``cash_ledger.cash_anchor_facts(account_id)``
    takes no scenario and no owner, so the other owner's recorded balances
    replay and the fold answers a confident figure built from them.

    The two tests below are one statement in two halves: the figure IS real and
    IS the other owner's, and the pass refuses to publish it.
    """

    def test_the_other_owners_assertion_is_a_real_figure(
        self, db, seed_second_user, seed_second_periods,
    ):  # pylint: disable=unused-argument
        """Read by its OWN pass, the second owner's Checking holds $2,000.00.

        Their fixture opens the account with that assertion, so this is the
        number a mis-paired read would have carried into the first owner's
        screen -- not a fabricated zero, and not an empty result that would look
        like a missing account.
        """
        assert balance_at.cash_balance_at(
            seed_second_user["account"],
            _pass_for(seed_second_user),
            _AS_OF,
        ) == Decimal("2000.00")

    def test_the_first_owners_pass_refuses_that_same_read(
        self, db, seed_user, seed_periods,
        seed_second_user, seed_second_periods,
    ):  # pylint: disable=unused-argument
        """The identical call, on the wrong pass, raises instead of answering.

        Same account, same date, same seam entry as the test above -- only the
        pass differs.  Before X-i4 this returned ``$2,000.00``.
        """
        with pytest.raises(ForeignAccountError):
            balance_at.cash_balance_at(
                seed_second_user["account"], _pass_for(seed_user), _AS_OF,
            )


class TestThePassAssemblesEachAccountOnce:
    """The memo half of X-i4: one walk, one plan load, one valuation per pass.

    The fold was the one account-keyed derivation a ``BalanceContext`` did not
    hold, so every reading assembled its own: the cash detail page walked one
    account twice in a render (its assertion log and its records figure), and a
    ``/savings`` render rebuilt one modelled account's cash base once per
    surface that asked (finding **N-115**).  Redundant derivation is not
    performance trivia here -- it is where a divergence hides, which is the
    lesson the read pass itself was built on.
    """

    def test_two_reads_of_one_account_share_one_assembly(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Identity, not equality: the second read returns the SAME record.

        Equality would pass against a second assembly that happened to agree,
        which is precisely the state this memo exists to make unrepresentable.
        """
        ctx = _pass_for(seed_user)
        first = assembled_fold(seed_user["account"], ctx)
        assert assembled_fold(seed_user["account"], ctx) is first

    def test_two_seam_entries_on_one_pass_walk_the_account_once(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The cash detail page's double walk, counted at its source.

        ``records_balance_at`` and ``cash_anchor_history`` each reached
        ``assemble`` for the same account's walk; their own comment named the
        duplication and deferred it.  On one pass they now issue ONE
        ``walk_cash_ledger``.
        """
        ctx = _pass_for(seed_user)
        account = seed_user["account"]
        with counting_calls(
            ("app.services.cash_ledger", "walk_cash_ledger"),
        ) as counts:
            balance_at.records_balance_at(account, ctx, _AS_OF)
            balance_at.cash_anchor_history(account, ctx)
        assert counts["walk_cash_ledger"] == 1

    def test_the_memo_is_per_account_not_per_pass(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A second account on one pass gets its OWN fold.

        The memo is keyed by ``account.id``, so this is the guard against the
        opposite defect -- one account's running total served for another's,
        which a bare single-slot memo would produce and which no balance
        assertion on either account would reveal.
        """
        ctx = _pass_for(seed_user)
        other = create_savings_account(
            seed_user, db.session, "Savings", Decimal("4321.00"),
        )
        db.session.commit()

        checking_fold = assembled_fold(seed_user["account"], ctx)
        savings_fold = assembled_fold(other, ctx)

        assert checking_fold is not savings_fold
        # Each record names the account it was assembled for, so this grades
        # the answer rather than the store behind it -- the cache itself is
        # PRIVATE (it carries a balance-at-T), and a test reaching into it
        # would be the fence bypass X-i4's review found and closed.
        assert checking_fold.account_id == seed_user["account"].id
        assert savings_fold.account_id == other.id
