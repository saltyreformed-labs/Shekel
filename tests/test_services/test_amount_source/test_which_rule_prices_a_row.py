"""Which rule prices a row: the dispatch's two answer tables, the classification and the order two
of its arms depend on, and the declared column that alone decides (finding N-262).
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum, TxnTypeEnum
from app.models.transaction import Transaction
from app.services import status_seam
from app.services.cash_ledger import AmountRule, amount_rule
from app.services.cash_ledger import transfer_amount_rule
from app.services.cash_ledger._amount_rule import _RELATION_RULES, _TRANSFER_RELATION_RULES
from app.services.cash_ledger._amount_source import _RULE_ANSWERS, _TRANSFER_RULE_ANSWERS
from tests._test_helpers import add_txn, one_off_row_of, payback_row_of, settlement_if_settling

from ._helpers import (
    _OLD_PRICE,
    _NOT_AN_ANSWER,
    _resolve,
    _priced_template,
    _salary_template,
    _template_row,
    _loan_payment,
)


class TestTheDispatchIsTotal:
    """Every rule has an answer, and the enum is the list of rules."""

    def test_every_amount_rule_has_an_answer_in_one_of_the_two_tables(self):
        """The two dispatch tables cover the enum exactly -- no rule, no orphan.

        The predicate behind the module's "TOTAL dispatch" claim.  A member
        added to :class:`AmountRule` without an answer would raise a ``KeyError``
        at the lookup rather than silently taking whichever branch happened to
        be last, and this is what says so before a row does.

        **It became a UNION at plan step X-au-f-2**, where ruling **R-BAL10**
        moved rule 4 off the transaction dispatch onto the transfer's.  Written
        as a union rather than as ``set(AmountRule) - {LOAN_PAYMENT}`` on the
        transaction table: a set defined by SUBTRACTION claims members nobody
        censused, and the union states the property that is actually wanted --
        every rule is answerable somewhere.
        """
        assert set(_RULE_ANSWERS) | set(_TRANSFER_RULE_ANSWERS) == set(
            AmountRule,
        )

    def test_each_table_answers_exactly_the_rules_its_rows_can_take(self):
        """And the union does not let a rule land in only ONE table by accident.

        The union above is satisfied by a rule wired into either table, so on
        its own it would pass a LOAN_PAYMENT wired only into the transaction
        dispatch -- which is the state plan step X-au-f-2 exists to leave.  The
        two key sets are therefore named, so a member added to
        :class:`AmountRule` arrives with a decision about BOTH tables rather
        than defaulting into whichever one someone edited.

        A TRANSACTION cannot take rule 4 (its parent transfer answers it) and a
        TRANSFER cannot take rule 2 (a salary profile names a transaction
        template).  A fifth rule, a shadow's *= my parent*, was the
        transaction table's alone until plan step balance:X-bi-6-5 deleted it
        (ruling plan#1850 trimmed it from this set).
        """
        assert set(_RULE_ANSWERS) == {
            AmountRule.OWN,
            AmountRule.SALARY,
            AmountRule.TEMPLATE,
        }
        assert set(_TRANSFER_RULE_ANSWERS) == {
            AmountRule.OWN,
            AmountRule.TEMPLATE,
            AmountRule.LOAN_PAYMENT,
        }

    def test_every_declarable_relation_has_a_rule(self):
        """Every relation a row may DECLARE refines into one of the four rules.

        The second half of the same totality claim, and the one plan step
        X-au-c2 added: :func:`amount_rule` now branches on
        ``amount_source_id``, so a member added to
        :class:`~app.enums.AmountSourceEnum` -- ``credit_card:CC4c``'s finance
        charge is the one already known to need one (finding **N-264**) --
        must arrive with a rule beside it.  Without this it would raise a
        ``KeyError`` on the first row that declared it, in production, instead
        of here.
        """
        assert set(_RELATION_RULES) == set(AmountSourceEnum)

    def test_every_relation_a_TRANSFER_can_declare_has_a_rule(self):
        """The transfer classifier's half, added with its table at X-au-f-2.

        A transfer may declare TEMPLATE, the one member, so its refinement
        table holds one entry.  Asserting the set both ways is what makes a NEW
        member arrive with a decision here too: a finance charge that can
        price a transfer needs an entry, and one that cannot fails the second
        assertion until it is named there with a refusal in
        :func:`transfer_amount_rule` to say so.  PARENT_TRANSFER was named
        there, refused by name, until plan step balance:X-bi-6-5 deleted the
        member and the refusal (ruling plan#1850 trimmed it from this set).
        """
        assert set(_TRANSFER_RELATION_RULES) == {AmountSourceEnum.TEMPLATE}
        assert set(AmountSourceEnum) - set(_TRANSFER_RELATION_RULES) == set()


class TestWhichRulePricesARow:
    """The classification, and the order two of its arms depend on."""

    def test_a_one_off_placed_today_is_priced_by_its_definition(
        self, app, db, seed_user, seed_periods,
    ):
        """An ordinary row since ``balance:X-bi-7b``: a rule-less definition prices it."""
        txn = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        assert txn.recurs is False and txn.is_placed is True
        assert amount_rule(txn) is AmountRule.TEMPLATE

    def test_a_cc_payback_owns_its_amount(self, app, db, seed_user, seed_periods):
        """A payback carries NEITHER link, so today's rules can only call it OWN.

        The kind ruling R-FI names and this step does not move: its figure is
        DERIVED (the sum of the source row's credit entries) and stored, with no
        link for a discriminator to key on -- which is why R-FI's discriminator
        is an explicit column and why finding **N-243** stays open.  21 such rows
        on the production clone, all classified here.
        """
        source = one_off_row_of(
            seed_periods[0], name="Groceries", amount="500.00",
            user_id=seed_user["user"].id, account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            category_id=seed_user["categories"]["Groceries"].id,
            is_envelope=True,
        )
        payback = payback_row_of(
            db.session, seed_user, source, Decimal("123.18"),
            seed_periods[0].start_date,
        )
        assert payback.pay_period_id == seed_periods[1].id
        assert amount_rule(payback) is AmountRule.OWN

    def test_a_template_row_is_priced_by_its_definition(
        self, app, db, seed_user, seed_periods,
    ):
        """A generated row's price comes from the template's series."""
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        assert amount_rule(txn) is AmountRule.TEMPLATE

    def test_a_salary_row_beats_the_template_rule(
        self, app, db, seed_user, seed_periods,
    ):
        """SALARY is tested first, because a profile names an ordinary template.

        The precedence control: this row satisfies BOTH arms -- it has a
        template, and that template is salary-linked -- so a dispatch that
        tested TEMPLATE first would price every paycheck off a price series
        nobody ever stated.
        """
        template, _profile = _salary_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        assert amount_rule(txn) is AmountRule.SALARY

    def test_a_loan_payment_TRANSFER_beats_the_series_rule(
        self, app, db, seed_user, seed_periods,
    ):
        """LOAN_PAYMENT is tested first, because a loan payment HAS a series.

        The precedence control R-BAL10 moved here from the shadow, and its
        inverse is finding **N-263**: a loan-payment transfer that placed as a
        plain template row would be priced from its definition's series --
        which in DERIVE mode is refused outright (``owns_its_amount`` is False)
        and in MANUAL mode silently drops the standing extra.

        The parent is DECLARED here, which is what makes the refinement
        reachable: a transfer carrying its own figure takes rule 1 first,
        whatever its template says (**R-IO**).
        """
        xfer = _loan_payment(seed_user, derive=True)
        assert transfer_amount_rule(xfer) is AmountRule.LOAN_PAYMENT

    def test_a_row_that_took_its_figure_back_owns_it_again(
        self, app, db, seed_user, seed_periods,
    ):
        """A template-generated row that carries a figure owns it, definition or not.

        The state a hand re-price leaves and the state the freeze leaves are the
        same state, and it is the one the CHECK describes: a figure and no
        source.  ``is_override`` is not consulted -- see
        :class:`TestTheDeclarationDecides` for the inverse, which is the half
        finding **N-262** was about.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template, owns=True)
        assert amount_rule(txn) is AmountRule.OWN
        assert _resolve(seed_user, txn) == Decimal(_NOT_AN_ANSWER)

    def test_soft_deleting_a_row_does_not_change_which_rule_prices_it(
        self, app, db, seed_user, seed_periods,
    ):
        """Deletion says whether a row counts, never who owns its figure.

        Making it flip the rule would force ``amount_source_id`` to be rewritten
        on every delete and restore -- a stored value beside a second writer,
        which is the shape this arc removes.  102 of 330 transfer shadows were
        soft-deleted on the 2026-08-12 production clone: the arm is ordinary.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        txn.is_deleted = True
        db.session.flush()
        assert amount_rule(txn) is AmountRule.TEMPLATE
        assert _resolve(seed_user, txn) == _OLD_PRICE


class TestTheDeclarationDecides:
    """Finding **N-262**: the COLUMN says who owns a figure, and nothing else does.

    Until plan step X-au-c2 the OWN arm was inferred from ``is_override`` and
    from having left Projected -- neither of which
    ``ck_transactions_amount_ownership`` can see -- so four live doors could
    write a row the schema admits and the resolver refuses.  Each of the four is
    reached here on a DECLARED row and shown to resolve through its relation
    rather than falling into OWN and raising.
    """

    @pytest.mark.parametrize("status", [
        StatusEnum.DONE, StatusEnum.RECEIVED, StatusEnum.CREDIT,
        StatusEnum.CANCELLED,
    ])
    def test_leaving_projected_does_not_take_a_declared_row_back(
        self, app, db, seed_user, seed_periods, status,
    ):
        """A declared row keeps its relation in every status the machine admits.

        Credit and Cancelled are the reachable half and they are why this is a
        parametrize rather than a settled-only case: both leave Projected WITHOUT
        entering the settled band, so no freeze fires and no figure is written
        back.  Under the inference such a row took the OWN arm and
        ``_own_figure`` raised on its empty column -- and production carries 7
        Cancelled and 2 Credit template-linked rows against a grid route that
        loads every row in the window with no status predicate
        (``routes/grid/_items.py``'s ``load_grid_items``), so the first
        bucket to derive would have taken out the whole screen.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        status_id = ref_cache.status_id(status)
        # Through the seam, and the two settling arms record a figure no rule
        # may answer: a resolver that read the settlement record instead of
        # the series would answer 999.99 here rather than the old price.
        status_seam.apply_status_change(
            txn, status_id,
            settlement=settlement_if_settling(
                txn, status_id, submitted=Decimal(_NOT_AN_ANSWER),
            ),
        )
        db.session.flush()
        assert amount_rule(txn) is AmountRule.TEMPLATE
        assert _resolve(seed_user, txn) == _OLD_PRICE

    def test_the_override_flag_does_not_take_a_declared_row_back(
        self, app, db, seed_user, seed_periods,
    ):
        """The flag carries four facts and pricing is not one of them.

        ``routes/transactions/mutations.py:295`` sets it for a pay-period MOVE
        with no re-price at all, and finding **N-238** (plan step X-au-h) is the
        split of the other three.  A row that was moved and not re-priced still
        has its definition's price, and a rule reading the flag would refuse it.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        txn.is_override = True
        db.session.flush()
        assert amount_rule(txn) is AmountRule.TEMPLATE
        assert _resolve(seed_user, txn) == _OLD_PRICE

    def test_a_bulk_flag_update_does_not_take_a_declared_row_back(
        self, app, db, seed_user, seed_periods,
    ):
        """The carry-forward shape: the flag set by SQL, past every ORM hook.

        ``carry_forward_service/_execute.py:157`` sets ``is_override`` in a bulk
        ``query.update``, which no validator and no session event can see.  It is
        written that way for the partial unique index rather than for pricing
        (the fourth fact finding **N-238** names), so a row it touched must price
        exactly as it did before.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        (
            db.session.query(Transaction)
            .filter(Transaction.id == txn.id)
            .update({"is_override": True}, synchronize_session="fetch")
        )
        db.session.flush()
        assert amount_rule(txn) is AmountRule.TEMPLATE
        assert _resolve(seed_user, txn) == _OLD_PRICE
