"""The MERCHANT RULE: where the owner says a merchant's spending goes.

Plan step **bank_import:X-f6a-3d**.  **It MOVES NO MONEY and the first class
below is what pins that**, because it is the property the whole design rests
on: a rule is read to SUGGEST a destination, and the only thing that records
a purchase is an explicit destination submitted for one specific line.  Ruling
**R-FZ**'s *the destination select IS the tick* survives only while that holds.

Measured on the developer's own 2026-08-16 statement against a 2026-08-18
production clone: the 91 unexplained outflows are **21 merchants**, and six
stated answers place **48 of the 91** -- so the sweep those placements feed
turns 48 decisions into one press.  Two of those answers are the ones a
per-line screen could never express: Capital One Credit Card is *never a
purchase* (9 lines, `-$7,412.94` the app already holds as payback rows), and
``Amazon -> Groceries`` reaches ten different pay periods through one template.

Every refusal below is a FIRING CONTROL, written to fail if the refusal were
deleted.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.statement_import import BankStatementLine
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services.pay_calendar import calendar_for
from app.services.statement_import import delete_import
from app.services.statement_match import (
    account_merchants,
    LinePipeline,
    merchant_directory,
    pipeline_for,
    PlacementKind,
    PurchaseDestination,
    review_set,
    RuleAnswer,
    RuleSubmission,
    RuleView,
    StandingRule,
    state_rules,
)
from app.services.statement_match._placement import (
    placements_for,
)
from app.services.statement_match._bars import (
    CreationBars,
)
from app.services.statement_match._section import (
    merchant_summary,
)
from app.services.statement_match._rules import (
    _named_templates,
    active_category_names,
    is_inflow,
    offerable_templates,
    rules_for,
)
from app.services.statement_match._stating import (
    _columns_of,
    _refuse_unknown_merchants,
)

from tests._test_helpers import generate_row_of, moved_by_the_owner
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_merchant,
    a_one_off_envelope,
    a_rule,
    a_scope,
    a_statement,
    a_transaction,
    the_merchant_id,
    an_import,
)


#: A stand-in merchant ROW ID for the cases that build a
#: :class:`~app.services.statement_match.StandingRule` by hand.
#:
#: Those cases grade the RESOLVER, which never reads ``budget.merchants`` --
#: it takes a merchant id and a view and returns a placement -- so staging a
#: real merchant row would arrange a database the code under test does not
#: look at.  The literal is what the view is keyed by AND what the resolver is
#: asked about, so the two cannot drift: a case that changed one and not the
#: other would resolve nothing and fail loudly.
_MERCHANT = 4001


def _view(*rules, templates=None, categories=frozenset(), stale=None,
          stale_categories=None, placeable=frozenset(), placed=None):
    """Return the rule view these resolvers read, built by hand.

    Built here rather than through :meth:`RuleView.build` because these cases
    grade the RESOLVER: stating the inputs literally is what lets a case pin
    one of them (an archived category, a template with no row) without
    arranging the database into that shape first.  The reads themselves are
    graded by the cases that go through ``review_set``.  *placeable* and
    *placed* are the PLACE arm's two reads (leaf 7b-3 of ``balance:X-bi-7b``),
    empty unless a case states them: no definition is placeable, so a
    TEMPLATE answer with no row in the paycheck stays UNRESOLVED here, as it
    did before that leaf.
    """
    return RuleView(
        rules={rule.merchant_id: rule for rule in rules},
        template_names=templates or {},
        active_categories=categories,
        stale_templates=stale or {},
        stale_categories=stale_categories or {},
        placeable_templates=placeable,
        placed_periods=placed or {},
    )


def _destination(txn, *, is_settled=False):
    """Return *txn* as the offer value a placement resolves against.

    **The paycheck comes off the owner's CALENDAR, exactly as
    ``destinations_for`` reads it** (pay-calendar plan step C4-a-4), and this
    helper stands in for that producer -- so it derives the span rather than
    reading the ``pay_periods.end_date`` column plan step C4-c dropped.
    """
    return PurchaseDestination(
        transaction_id=txn.id,
        name=txn.name,
        category_id=txn.category_id,
        period=calendar_for(txn.pay_period.user_id).saved_by_id()[
            txn.pay_period_id
        ],
        is_settled=is_settled,
        template_id=txn.template_id,
        # Whether it is a rule-less definition's row, as ``destinations_for``
        # reads it (plan step balance:X-bi-7b, leaf 7b-3).
        is_placed=txn.is_placed,
    )


class TestStatingARuleMovesNoMoney:
    """The property the whole design rests on, asserted rather than assumed."""

    def test_a_stated_rule_records_no_purchase_and_no_row(
        self, app, db, seed_user,
    ):
        """Answering for every merchant writes rules and nothing else.

        THE central claim.  If a rule could record a purchase, then a
        remembered answer would be a default that moves money -- which is
        exactly what ruling R-FZ removed from the destination select, and the
        reason this is a separate door rather than a third kind of batch item.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        db.session.flush()
        before_entries = db.session.query(TransactionEntry).count()
        before_txns = db.session.query(Transaction).count()

        state_rules(
            (a_statement(
                seed_user, "Amazon", RuleAnswer.TEMPLATE,
                template_id=envelope.template_id,
            ),),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert db.session.query(TransactionEntry).count() == before_entries
        assert db.session.query(Transaction).count() == before_txns
        assert db.session.query(MerchantRule).count() == 1

    def test_a_placement_does_not_TICK_the_line_it_places(
        self, app, db, seed_user,
    ):
        """The select still opens on "leave this line alone".

        A placement is rendered BESIDE the control, never into it.  The screen
        proved the rendered default separately, in the review queue's own
        ``TestTheCreateArm``; plan step ``bank_import:X-gi-2`` deleted that page
        and the case with it, and the Reconcile card has no *leave this line
        alone* default to grade -- its OK checkbox is the consent, so the
        rendered-default half is MOOT rather than lost.  What this pins is the
        service half: a placed line is still an offer, and nothing about it
        says the owner accepted anything.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.commit()

        review = review_set(a_scope(seed_user))

        placed = [
            item for item in review.creatable
            if item.placement is not None
        ]
        assert len(placed) == 1
        assert placed[0].placement.kind is PlacementKind.RECORD_IN
        # ...and the account still holds no purchase at all.
        assert db.session.query(TransactionEntry).count() == 0


class TestWhatARuleResolvesTo:
    """The four answers a rule comes to for one line, and their reasons."""

    def test_a_TEMPLATE_answer_finds_that_periods_own_row(
        self, app, db, seed_user,
    ):
        """The whole point of keying on a template rather than a row.

        An envelope belongs to ONE pay period -- the 24 unexplained Amazon
        lines on the developer's own statement fall in ten of them -- so the
        answer has to resolve per period, and the template is what does it.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Amazon", answer=RuleAnswer.TEMPLATE,
            template_id=envelope.template_id,
        )

        placement = placements_for(
            _MERCHANT,
            _view(rule, templates={envelope.template_id: "Groceries"}),
            [_destination(envelope)],
        )

        assert placement.kind is PlacementKind.RECORD_IN
        assert placement.destination.transaction_id == envelope.id
        assert placement.select_value == str(envelope.id)

    def test_a_TEMPLATE_answer_with_NO_row_here_says_so(
        self, app, db, seed_user,
    ):
        """Reported, never substituted for.

        Measured on the developer's own clone: template 5 (``Gas``) is
        offerable in 9 of the 11 pay periods their creatable lines fall in, and
        template 38 (``Groceries``) in 10 -- so this is the ordinary state of a
        real statement rather than an edge.  The tempting substitution, falling
        back to a new envelope, would file money somewhere the owner never
        named.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Amazon", answer=RuleAnswer.TEMPLATE,
            template_id=envelope.template_id,
        )

        placement = placements_for(
            _MERCHANT,
            _view(rule, templates={envelope.template_id: "Groceries"}), [],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "Groceries" in placement.unresolved_reason
        assert placement.select_value is None

    def test_a_TEMPLATE_answer_with_TWO_rows_here_refuses_to_guess(
        self, app, db, seed_user, seed_periods,
    ):
        """A template does NOT always make exactly one row in a period.

        Measured on a 2026-08-18 production clone: template 22 (``Kayla's
        Spending Money``) generated two rows in pay period 3, ids 2388 and
        2389.  Picking either would file money in a row the owner did not pick,
        so the placement says which pay period holds two and stops.
        """
        first = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
            period=seed_periods[0],
        )
        # Two rows from ONE template in ONE period, which is what production
        # holds: an override row beside the generated one (2026-08-18 clone,
        # transactions 2388 and 2389, both template 22 in pay period 3).  The
        # app produces that pair two ways -- a carried-forward leftover, or a
        # row the owner MOVED in from a neighbouring paycheck -- and this is
        # the second: the engine's own row of the next paycheck, moved here
        # by the two acts the move door performs (plan step balance:X-cf).
        # Hand-linking a second engine row to this template would give two
        # rows ONE occurrence, which the occurrence index refuses.
        second = moved_by_the_owner(
            generate_row_of(first.template, seed_periods[1]),
            into=seed_periods[0],
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Amazon", answer=RuleAnswer.TEMPLATE,
            template_id=first.template_id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, templates={first.template_id: "Groceries"}),
            [_destination(first), _destination(second)],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "2 of them" in placement.unresolved_reason

    def test_a_NEW_ENVELOPE_answer_carries_its_name_and_category(
        self, app, db, seed_user,
    ):
        """The arm that always resolves, because it depends on no existing row.

        On the developer's own data it is the only arm open to the 10 lines in
        a pay period whose every envelope closed at a fixed figure.
        """
        category = seed_user["categories"]["Groceries"]
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Lowe's", category_id=category.id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, categories=frozenset({category.id})), [],
        )

        assert placement.kind is PlacementKind.CREATE_NEW
        assert placement.new_envelope.name == "Lowe's"
        assert placement.new_envelope.category_id == category.id
        assert placement.select_value == "new"

    def test_a_NEW_ENVELOPE_answer_whose_CATEGORY_was_archived_says_so(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for the active-category clause.

        ``_create._owned_category`` refuses an archived category, because the
        picker renders only active ones -- so a placement naming one would tick
        a line whose submission can never succeed, which is the shape finding
        **N-325** was closed for one field over and which this arc has now
        shipped four times.  Delete the clause and the placement comes back
        ``CREATE_NEW``, the sweep ticks it, and the item is refused a tier
        deeper with a sentence about the category not being the owner's.
        """
        category = seed_user["categories"]["Groceries"]
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Lowe's", category_id=category.id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, categories=frozenset()), [],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "archived" in placement.unresolved_reason
        assert placement.select_value is None

    def test_the_active_category_set_is_the_pickers_own(
        self, app, db, seed_user,
    ):
        """The premise for the clause above, read from the database.

        The set is not an assumption about which ids are active: it is the
        same predicate ``category_service.list_active_categories`` renders by.
        """
        archived = seed_user["categories"]["Groceries"]
        archived.is_active = False
        db.session.flush()

        active = active_category_names(seed_user["user"].id)

        assert archived.id not in active
        assert seed_user["categories"]["Rent"].id in active

    def test_a_NEVER_answer_PLACES_NOTHING_because_it_is_a_bar(
        self, app, db, seed_user,
    ):
        """Ruling **R-GJ**: *never a purchase* stopped being a suggestion.

        Capital One Credit Card is 9 of the developer's 91 unexplained outflows
        and `-$7,412.94` of the `-$11,336.36` in that list, and every one of
        them must never become a purchase -- the app holds that money as CC
        Payback rows already.  Until plan step ``bank_import:X-ga`` this answer
        produced a ``NOT_A_PURCHASE`` placement, which withheld a sweep value
        and printed a sentence -- beside a destination select that still
        offered every envelope in the period, and above a create door that read
        no rule at all.  One YTD pass recorded all nine through it.

        **A placement is a suggested DESTINATION, and for this answer there is
        none**, so the honest return is nothing: what the answer now produces
        is a :class:`~app.services.statement_match.CreationBar`, graded in
        ``test_bars``.  This case pins that the placement seam no longer speaks
        for it -- a second statement of a refusal is what the last one cost.

        It is a FIRING CONTROL for the arm that returns early: delete it and
        this stored answer falls through into ``_template_placement`` with a
        ``NULL`` template id, which names every offerable row in the period.
        """
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Capital One Credit Card", answer=RuleAnswer.NEVER,
        )

        assert placements_for(
            _MERCHANT, _view(rule), [],
        ) is None

    def test_a_stored_NEVER_does_not_fall_through_to_a_TEMPLATE_placement(
        self, app, db, seed_user,
    ):
        """The same arm, shown against a period that HAS offerable rows.

        The case above passes an empty offer set, so an early return and a
        fall-through are indistinguishable in it: ``_template_placement`` would
        find no match either way.  This one hands the resolver a real
        destination, which a fall-through WOULD name -- and one whose
        ``template_id`` is ``None``, exactly what a ``NULL`` rule template
        would compare equal to.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True, template=False,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Capital One Credit Card", answer=RuleAnswer.NEVER,
        )

        assert placements_for(
            _MERCHANT, _view(rule), [_destination(envelope)],
        ) is None

    def test_a_line_naming_NO_merchant_reaches_no_rule_at_all(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for the nullable merchant column.

        A source that names no merchant records ``NULL``, and this is what that
        NULL buys: no rule can key on it.  Delete the ``merchant is None``
        arm and a second adapter's truncated descriptions -- SECU's own OFX
        cuts 326 of 361 to the same 32 characters -- would each key one rule
        and fire it on every merchant behind them.
        """
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Amazon",
            answer=RuleAnswer.NEVER,
        )

        assert placements_for(None, _view(rule), []) is None


class TestStatingAndRestatingARule:
    """The write door: record, restate, and say only what changed.

    **There is no withdrawal** as of ruling **R-GS** (plan step
    ``bank_import:X-gd-2``): a rule row, once made, is only ever restated, and
    *ask me every time* is the answer that replaced taking one back.
    """

    def _state(self, db, seed_user, *statements):
        """Run the door for this owner's checking account.

        **The precondition is a MERCHANT ROW, and :func:`a_statement` stages
        it** (plan step ``bank_import:X-gd-1``).  ``state_rules`` refuses a
        statement about a merchant this account has never seen, and that scope
        is ``budget.merchants``.  This helper used to record a bank line per
        merchant, because the scope was a DISTINCT over recorded lines and a
        merchant with no line was outside it.  A merchant row OUTLIVES its
        lines now, so a row with no line is an ordinary production state -- the
        import that first recorded it was deleted -- and staging one is no
        longer a fixture shortcut past a guard.  The guard itself is graded by
        ``TestWhatTheWriteDoorRefuses``, on an id this account does not hold.
        """
        db.session.flush()
        return state_rules(
            tuple(statements),
            seed_user["user"].id, seed_user["account"].id,
        )

    def test_it_records_each_of_the_four_answers(
        self, app, db, seed_user,
    ):
        """One row per merchant, carrying the columns its answer sets."""
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        category = seed_user["categories"]["Groceries"]

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=envelope.template_id),
            a_statement(seed_user, "Lowe's", RuleAnswer.NEW_ENVELOPE,
                            envelope_name="Lowe's", category_id=category.id),
            a_statement(seed_user, "Capital One Credit Card", RuleAnswer.NEVER),
        )
        db.session.flush()

        assert len(recorded.stated) == 3
        assert recorded.refused == ()
        held = rules_for(
            seed_user["user"].id, seed_user["account"].id,
        )
        by_name = {rule.merchant: rule for rule in held.values()}
        assert by_name["Amazon"].answer is RuleAnswer.TEMPLATE
        assert by_name["Amazon"].template_id == envelope.template_id
        assert by_name["Lowe's"].answer is RuleAnswer.NEW_ENVELOPE
        assert by_name["Lowe's"].envelope_name == "Lowe's"
        assert by_name["Capital One Credit Card"].answer is RuleAnswer.NEVER

    def test_restating_the_SAME_answer_writes_nothing_and_says_so(
        self, app, db, seed_user,
    ):
        """The section submits every merchant it renders, so most is no-ops.

        The receipt has to carry the denominator: "0 recorded" with nothing
        beside it would read as though every answer had failed, when in fact
        every one was already what the owner had said.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=envelope.template_id),
        )

        assert recorded.stated == ()
        assert recorded.unchanged_count == 1

    def test_a_DIFFERENT_answer_replaces_the_row_rather_than_adding_one(
        self, app, db, seed_user,
    ):
        """One answer per merchant is structural, so restating is an UPDATE.

        Two rows would be two answers to one question, which is what
        ``uq_merchant_rules_account_merchant`` makes unwritable.
        """
        first = a_transaction(seed_user, name="Groceries", is_envelope=True)
        second = a_transaction(seed_user, name="Gas", is_envelope=True)
        a_rule(seed_user, "Amazon", template_id=first.template_id)
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=second.template_id),
        )
        db.session.flush()

        assert len(recorded.stated) == 1
        rows = db.session.query(MerchantRule).all()
        assert len(rows) == 1
        assert rows[0].template_id == second.template_id

    def test_ALWAYS_ASK_replaces_a_rule_rather_than_deleting_it(
        self, app, db, seed_user,
    ):
        """Ruling **R-GS**: a rule is restated, never un-stated.

        **This case replaced ``test_answering_NOT_SAID_WITHDRAWS_a_rule``**,
        which asserted the row COUNT went to zero.  The behaviour it graded was
        withdrawn by the developer on 2026-08-25: the control's do-nothing
        option used to delete the row and return the merchant to *you have not
        said*, and *ask me every time* is what replaced it.  The reason the
        withdrawal existed is unchanged and is still met -- when the credit-card
        arc gives Capital One its own account the Checking-side answer stops
        being right -- but taking an answer BACK and saying *I want no standing
        answer* are different statements, and only the second survives a screen
        that has to know which merchants it may still ask about.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.ALWAYS_ASK),
        )
        db.session.flush()

        assert len(recorded.stated) == 1
        row = db.session.query(MerchantRule).one()
        assert row.template_id is None
        assert row.envelope_name is None
        assert row.category_id is None
        # ...and NOT the other container-less answer, which is the whole of
        # what the flag is for: *never a purchase* would BAR every line this
        # merchant ever shows.
        assert row.never_a_purchase is False

    def test_ALWAYS_ASK_on_an_unanswered_merchant_STATES_it(
        self, app, db, seed_user,
    ):
        """It is an answer even where there was nothing to replace.

        The other half of the fourth answer, and the one that separates it from
        the absence of a row: an owner who has never answered for a merchant
        and picks *ask me every time* has DECIDED something, and the exception
        queue ``bank_import:X-gf`` builds reads exactly that difference to
        decide whether to prompt them again.
        """
        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.ALWAYS_ASK),
        )
        db.session.flush()

        assert recorded.stated == ("Amazon: ask me every time.",)
        row = db.session.query(MerchantRule).one()
        assert row.never_a_purchase is False
        assert RuleAnswer.of(row) is RuleAnswer.ALWAYS_ASK

    def test_restating_ALWAYS_ASK_is_reported_as_UNCHANGED(
        self, app, db, seed_user,
    ):
        """The fourth answer restates like the other three.

        The section submits every merchant it renders, so an ordinary pass is
        mostly no-ops; a receipt reading "1 recorded" with no denominator would
        read as though the rest had failed.  This is the arm that would have
        been missed by writing the flag but not comparing it -- the row would
        be rewritten to the value it already held and reported as a change on
        every single Save.
        """
        a_rule(seed_user, "Amazon", always_ask=True)
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.ALWAYS_ASK),
        )

        assert recorded.stated == ()
        assert recorded.unchanged_count == 1

    def test_NEVER_and_ALWAYS_ASK_are_DIFFERENT_answers_to_the_door(
        self, app, db, seed_user,
    ):
        """The two share every column, so only the flag tells them apart.

        **The case a "same answer" comparison written over the three container
        columns would pass while being wrong**: it would find nothing changed
        between *never a purchase* and *ask me every time* and leave a bar
        standing that the owner had just lifted.  Both directions, because a
        comparison can be blind in one.
        """
        a_rule(seed_user, "Amazon")
        db.session.flush()

        lifted = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.ALWAYS_ASK),
        )
        db.session.flush()

        assert lifted.unchanged_count == 0
        assert db.session.query(MerchantRule).one().never_a_purchase is False

        restored = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.NEVER),
        )
        db.session.flush()

        assert restored.stated == ("Amazon is never a purchase.",)
        assert db.session.query(MerchantRule).one().never_a_purchase is True


class TestWhatTheWriteDoorRefuses:
    """Each refusal, written to fail if the refusal were deleted."""

    def _state(self, db, seed_user, *statements):
        """Run the door for this owner's checking account.

        :func:`a_statement` stages the merchant these submissions are about,
        which is the precondition ``state_rules`` checks -- see
        :meth:`TestStatingAndRestatingARule._state` for why that is no longer
        a bank line.
        """
        db.session.flush()
        return state_rules(
            tuple(statements),
            seed_user["user"].id, seed_user["account"].id,
        )

    def test_a_template_on_ANOTHER_ACCOUNT_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """A statement is one bank's record of ONE account.

        ``fk_merchant_rules_template_account`` makes it unwritable
        anyway, and this is what turns that into a sentence rather than an
        ``IntegrityError`` reaching the owner as "Something went wrong".
        """
        other = a_transaction(
            seed_second_user, name="Groceries", is_envelope=True,
        )
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=other.template_id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1
        assert "no recurring envelope on this account" in recorded.refused[0]
        assert db.session.query(MerchantRule).count() == 0

    def test_a_template_that_does_NOT_TRACK_PURCHASES_is_refused(
        self, app, db, seed_user,
    ):
        """The create door's own refusal, applied where the answer is stated.

        ``entry_service.create_entry`` refuses a parent that does not track
        purchases, so a rule naming a plain budget line would be an answer
        every one of whose placements is refused -- the chooser-that-always-
        fails shape, moved one tier back.
        """
        plain = a_transaction(seed_user, name="Electricity", is_envelope=False)

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=plain.template_id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1
        assert db.session.query(MerchantRule).count() == 0

    def test_an_INCOME_template_is_refused(self, app, db, seed_user):
        """Money coming in is not a purchase.

        ``destinations_for`` excludes an income row for the same reason, so
        offering one here would be the offer-versus-accept drift this arc keeps
        closing.
        """
        income = a_transaction(
            seed_user, name="Paycheck", income=True, is_envelope=True,
        )

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=income.template_id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1

    def test_an_INACTIVE_template_is_refused(self, app, db, seed_user):
        """A definition the owner has turned off generates no more rows."""
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        db.session.query(TransactionTemplate).filter(
            TransactionTemplate.id == envelope.template_id,
        ).update({"is_active": False})
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=envelope.template_id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1

    def test_ANOTHER_OWNERS_category_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """The IDOR probe every create door in this project performs.

        A foreign ``category_id`` satisfies a bare foreign key perfectly well.
        ``fk_merchant_rules_category_owner`` makes it unwritable and
        this makes the refusal a sentence.
        """
        foreign = seed_second_user["categories"]["Groceries"]

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Lowe's", RuleAnswer.NEW_ENVELOPE,
                            envelope_name="Lowe's", category_id=foreign.id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1
        assert "not one of yours" in recorded.refused[0]
        assert db.session.query(MerchantRule).count() == 0

    def test_an_ARCHIVED_category_is_refused(self, app, db, seed_user):
        """The picker renders only active categories, so the door takes only those."""
        category = seed_user["categories"]["Groceries"]
        category.is_active = False
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Lowe's", RuleAnswer.NEW_ENVELOPE,
                            envelope_name="Lowe's", category_id=category.id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1

    def test_a_REFUSED_statement_costs_only_ITSELF(
        self, app, db, seed_user,
    ):
        """Per-item isolation, and here it protects work rather than money.

        Refusing the whole submission would re-render the section from the
        DATABASE and so discard the other answers the owner had just picked.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        plain = a_transaction(seed_user, name="Electricity", is_envelope=False)

        recorded = self._state(
            db, seed_user,
            a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                            template_id=envelope.template_id),
            a_statement(seed_user, "Walmart", RuleAnswer.TEMPLATE,
                            template_id=plain.template_id),
            a_statement(seed_user, "Capital One Credit Card", RuleAnswer.NEVER),
        )
        db.session.flush()

        assert len(recorded.stated) == 2
        assert len(recorded.refused) == 1
        held = rules_for(seed_user["user"].id, seed_user["account"].id)
        assert sorted(rule.merchant for rule in held.values()) == [
            "Amazon", "Capital One Credit Card",
        ]

    def test_a_merchant_this_account_NEVER_SAW_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """The scope check, as the SENTENCE a stale page gets.

        Since plan step ``bank_import:X-gd-1`` this is no longer what makes a
        stored answer correct -- that is
        ``fk_merchant_rules_merchant_account``, graded by
        :meth:`TestTheTableRefusesWhatIsNotAnAnswer
        .test_ANOTHER_ACCOUNTS_merchant_is_unwritable` -- so what it buys is a
        sentence rather than an ``IntegrityError`` and a logged traceback for
        what is ordinarily a stale page.  The whole submission is refused,
        because the section renders only this account's merchants and a
        statement about another cannot have come from the screen.

        **The id is ANOTHER ACCOUNT'S real merchant**, not an invented number:
        an id no row carries would also be refused by the foreign key, and the
        case that matters is the one a crafted request would actually try.
        """
        theirs = a_merchant(
            seed_second_user, "Nowhere Ltd",
            account=seed_second_user["account"],
        )
        db.session.flush()

        with pytest.raises(ValidationError) as caught:
            _refuse_unknown_merchants(
                (RuleSubmission(theirs.id, RuleAnswer.NEVER),),
                account_merchants(seed_user["account"].id),
            )

        assert "not ones your bank has shown" in str(caught.value)

    def test_a_merchant_this_account_HAS_seen_is_admitted(
        self, app, db, seed_user,
    ):
        """The other side of the scope check, so the refusal is not vacuous."""
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        db.session.flush()

        _refuse_unknown_merchants(
            (a_statement(seed_user, "Amazon", RuleAnswer.NEVER),),
            account_merchants(seed_user["account"].id),
        )

    def test_a_merchant_from_a_line_that_is_already_MATCHED_is_admitted(
        self, app, db, seed_user,
    ):
        """The scope is every merchant SEEN, not the ones still outstanding.

        A merchant whose every line is explained today is still one the owner
        may want to answer for -- the next statement brings more of it -- and a
        scope narrowed to the leftovers would refuse an answer the section
        itself renders.  The line staged here is matched by nothing, which is
        the point: what puts a merchant in scope is that it EXISTS, and no
        reader has to remember to widen past this pass's leftovers.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        db.session.flush()

        assert sorted(
            account_merchants(seed_user["account"].id).values(),
        ) == ["Amazon"]


class TestWhatARuleMayNAME:
    """The option list, and that it is the same set the door checks against."""

    def test_it_offers_only_purchase_tracking_expense_definitions(
        self, app, db, seed_user,
    ):
        """One set, read by the control that renders and the door that writes.

        A template this does not return cannot be reached by crafting a
        request, and one it does return cannot be refused by the write door --
        the property ``destinations_for`` rests on, applied one tier up.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        a_transaction(seed_user, name="Electricity", is_envelope=False)
        a_transaction(
            seed_user, name="Paycheck", income=True, is_envelope=True,
        )

        offered = offerable_templates(seed_user["account"].id)

        assert offered == {envelope.template_id: "Groceries"}

    def test_a_template_on_another_account_is_not_offered(
        self, app, db, seed_user, seed_second_user,
    ):
        """A statement is one bank's record of ONE account."""
        a_transaction(seed_second_user, name="Groceries", is_envelope=True)
        db.session.flush()

        assert offerable_templates(seed_user["account"].id) == {}


class TestTheSectionTheScreenRenders:
    """What each rule control lists, and what the inbox's rows count.

    **The membership rule changed at plan step ``bank_import:X-gf-2``** (ruling
    **bank_import:R-GX**): one control was every merchant with pending work
    PLUS every merchant answered for, and it became two -- the inbox asked
    about the merchants with no answer, and the register showed the answers.

    **Plan step ``bank_import:X-gi-3`` deleted the register's half**, and with
    it the PARTITION these cases graded: the MERCHANTS surface
    (:func:`~app.services.statement_match.merchant_directory`, ruling
    **bank_import:R-IC**) lists every merchant the account has seen, answered
    or not, so *reaches exactly one of the two* is no longer a property anyone
    holds.  What survives is the half that is still a rule -- the inbox asks
    only where there is work AND no answer -- plus the reach that mattered: an
    answer whose lines are all explained is still editable, which is the case
    below.
    """

    def test_the_queue_lists_only_the_merchants_with_no_answer(
        self, app, db, seed_user,
    ):
        """A decision already made is not a question the queue asks.

        Measured on the developer's own data 2026-08-27: this control was 30
        rows of which 29 were answers he had already given, and 225,472 bytes
        of a 578,523-byte page.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant="Walmart",
            sequence_in_group=1,
        )
        # Answered for, and its line is still waiting: the answer is a
        # decision made, so the row is the register's whatever is pending.
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert [
            row.summary.merchant for row in review.merchants.merchants
        ] == ["Walmart"]

    def test_the_directory_lists_an_answer_whose_lines_are_all_explained(
        self, app, db, seed_user,
    ):
        """This is what makes an answer changeable once its work is done.

        Without it a rule could only be changed while there was still an
        unexplained line for that merchant.

        **It asked the REGISTER until plan step ``bank_import:X-gi-3``**, whose
        membership was the answers themselves.  The register's model went with
        the screen ``bank_import:X-gi-2`` deleted, and the reach survives
        because :func:`~app.services.statement_match.account_merchants` IS that
        union: an answered ``Merchant`` row outlives its lines, and deleting an
        import sweeps only the merchants no line names AND no answer is about.
        So a merchant with no line at all is in the directory exactly as one
        with ten is, and this case is what says so.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-30.00", merchant="Walmart")
        a_rule(seed_user, "Old Merchant", template_id=envelope.template_id)
        db.session.commit()

        listed = {
            entry.summary.merchant: entry.summary
            for entry in merchant_directory(
                seed_user["user"].id, seed_user["account"].id, {},
            ).entries
        }

        assert "Old Merchant" in listed, (
            "an answer whose merchant has no line left is out of reach"
        )
        assert listed["Old Merchant"].rule is not None

    def test_a_queue_row_carries_how_many_lines_and_how_much_money(
        self, app, db, seed_user,
    ):
        """The queue decides several lines at once, so it says how much.

        On the developer's own statement one row of it covers `-$7,412.94`.
        **A surface with no pass carries no such figure and must not**: it has
        not measured one -- see
        ``TestASurfaceWithNoPassStatesNoFigureItHasNotMeasured``, which this
        step renamed from ``TestTheRegisterStatesNoFigureItHasNotMeasured``
        when the register went.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant="Amazon",
            sequence_in_group=1,
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        row = review.merchants.merchants[0]
        assert row.summary.merchant == "Amazon"
        assert row.line_count == 2
        assert row.total == Decimal("-55.00")

    def test_a_NEVER_line_leaves_the_creatable_list_ENTIRELY(
        self, app, db, seed_user,
    ):
        """Ruling **R-GJ**, end to end through the reader.

        The caption cannot promise a number the control does not deliver -- and
        since plan step ``bank_import:X-ga`` it cannot render a control either.
        A merchant answered *never a purchase* is not one line of the create
        card with its select disabled: it is not in that card at all, and it is
        listed among the pass's ``answered_never`` lines instead -- which is
        where ruling **bank_import:R-JH** put it at plan step
        ``bank_import:X-gj-4c``, having been in ``parked`` before that.  Both
        lists are out of ``creatable``, which is the fact this case grades:
        what changed is where such a line RENDERS, never whether the create
        door would take it.

        The other half of the old sweep rule -- a rule that does not reach
        this line's pay period places nothing either -- is graded by
        ``TestWhatARuleResolvesTo``'s two UNRESOLVED cases, which is where
        that partition lives.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant="Capital One",
            sequence_in_group=1,
        )
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        a_rule(seed_user, "Capital One")
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert [item.line.merchant for item in review.creatable] == ["Amazon"]
        assert [
            item.line.merchant for item in review.answered_never
        ] == ["Capital One"]
        # **And in NEITHER of the other two**, which is what makes the line
        # above a partition claim rather than a membership one: a line counted
        # twice would put it on a holding tab AND in the inbox.
        assert review.parked == ()
        # ...and the ANSWER that parked it is still reachable, on the MERCHANTS
        # surface: both of these merchants have been answered for, so the inbox
        # asks about neither and the directory shows both (rulings
        # **bank_import:R-GX**, **R-IC**).  A parked line whose answer had no
        # row anywhere would be ruling R-GJ's own dead end -- an act refused
        # with the door that permits it hidden.
        #
        # *The sweep-per-evidence-group assertions that stood here went with
        # the queue at plan step ``bank_import:X-gi-3``; the live coupling they
        # were about -- no SWEPT card carries a sentence -- is
        # ``test_reconcile.test_the_tabs.TestOnlyTheInboxSweeps``, which stages a
        # non-empty swept set and a non-empty withheld set so it can fire.*
        assert review.merchants.merchants == ()
        assert {
            entry.summary.merchant for entry in merchant_directory(
                seed_user["user"].id, seed_user["account"].id, {},
            ).entries
        } == {"Amazon", "Capital One"}


class TestASurfaceWithNoPassStatesNoFigureItHasNotMeasured:
    """The directory runs no pass, so it carries no count of waiting lines.

    Plan step ``bank_import:X-gf-2``.  ``line_count`` and ``total`` used to sit
    on :class:`~app.services.statement_match.MerchantSummary`, which both
    surfaces share -- so the register would have carried a ``0`` for a merchant
    whose lines are waiting, and printed *none right now* over the developer's
    own ``Capital One``, whose *never a purchase* answer parks 9 lines worth
    `-$7,412.94`.  A figure that is FALSE rather than absent.

    The remedy is the composition: the pass's share is
    :class:`~app.services.statement_match.WaitingMerchant`'s and only the queue
    builds one, so the surface with no pass cannot state one.
    """

    def test_the_register_row_has_no_waiting_figure_to_state(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the fields are not reachable, not merely unset.

        An assertion that a count READ zero would pass against exactly the
        defect this exists to prevent, so what is asserted is that the value
        has no such attribute at all.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.commit()

        row = merchant_directory(
            seed_user["user"].id, seed_user["account"].id, {},
        ).entries[0].summary

        assert row.merchant == "Amazon"
        assert not hasattr(row, "line_count")
        assert not hasattr(row, "total")

    def test_the_QUEUE_row_states_it_because_the_queue_measured_it(
        self, app, db, seed_user,
    ):
        """The other side, so the absence above is a boundary and not a hole."""
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        db.session.commit()

        row = review_set(a_scope(seed_user)).merchants.merchants[0]

        assert row.summary.merchant == "Amazon"
        assert row.line_count == 1
        assert row.total == Decimal("-25.00")


class TestALineDatedMadeAfterItPosted:
    """Finding **N-325**, ruled 2026-08-19: the ADD is withheld, and SAID.

    **The rule is unchanged and its PLACEMENT is what plan step
    ``bank_import:X-gm`` moved** (developer 2026-09-05).  The app still refuses
    to choose between the bank's two days; what it stopped doing is dropping
    the line out of every list to say so, which left it on no tab of the
    Reconcile page at all.
    """

    def test_it_is_offered_with_its_ADD_withheld(self, app, db, seed_user):
        """The line is on the page, and the control that cannot succeed is not.

        ``entry_service.create_entry`` refuses a purchase whose money left
        before it was spent -- correctly, because money cannot -- so a line the
        bank dates MADE after it POSTED has no day a purchase could happen on.
        The line is still WORK: it keeps MATCH, so it is in ``creatable``
        carrying the refusal and carrying NO placement, which is what a sweep
        would otherwise press.  0 of the developer's 378 recorded lines are
        this shape; the OFX adapter's own measurement found 2 of 361.
        """
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=day,
            transaction_on=day + timedelta(days=1), merchant="Amazon",
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert len(review.creatable) == 1
        offered = review.creatable[0]
        assert offered.placement is None
        assert offered.withheld is not None
        # The sentence names BOTH of the bank's own days, because a refusal
        # the owner cannot check against their statement is not actionable.
        assert day.isoformat() in offered.withheld
        assert (day + timedelta(days=1)).isoformat() in offered.withheld
        # It is not a BOUND: nothing here was left unexamined.  *It asked
        # ``bounds.any_limit`` until plan step ``bank_import:X-gi-3`` deleted
        # that property with its last reader; these are the two bounds this
        # fixture could set, and they are the two the page states for itself
        # (``ReconcilePage.unexamined``).*
        assert review.bounds.crowded_days == ()
        assert review.bounds.unpriceable_count == 0
        assert review.bounds.before_calendar_count == 0
        assert review.bounds.books is None

    def test_a_line_dated_made_BEFORE_it_posted_keeps_its_placement(
        self, app, db, seed_user,
    ):
        """The firing control's other side, so the withholding is not vacuous."""
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date + timedelta(days=2)
        a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=day,
            transaction_on=day - timedelta(days=1), merchant="Amazon",
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert len(review.creatable) == 1
        assert review.creatable[0].withheld is None


class TestTheTableRefusesWhatIsNotAnAnswer:
    """The CHECKs, each shown refusing a row the ORM would happily write."""

    def _row(self, db, seed_user, **columns):
        """Stage a rule row with these columns and flush it.

        ``never_a_purchase`` is NOT NULL with no default, so it is stated here
        or nothing reaches the CHECK under test at all -- ``False`` unless the
        case names it, which is what every container answer carries.
        """
        columns.setdefault("never_a_purchase", False)
        row = MerchantRule(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            merchant_id=a_merchant(seed_user, "Amazon").id,
            **columns,
        )
        db.session.add(row)
        db.session.flush()
        return row

    def test_a_template_AND_a_new_envelope_is_unwritable(
        self, app, db, seed_user,
    ):
        """Two answers to one question.

        ``ck_merchant_rules_one_answer`` spells the three legal shapes
        as three shapes, because a count-the-NULLs form cannot say this: one
        answer sets two columns and one sets none.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        with pytest.raises(Exception) as caught:
            self._row(
                db, seed_user, template_id=envelope.template_id,
                envelope_name="Lowe's",
                category_id=seed_user["categories"]["Groceries"].id,
            )

        assert "ck_merchant_rules_one_answer" in str(caught.value)

    def test_a_new_envelope_stated_by_HALVES_is_unwritable(
        self, app, db, seed_user,
    ):
        """A name with no category, or a category with no name, is neither answer.

        ``transactions.category_id`` is what every spending report groups by,
        so a row created without one would be invisible to the analysis the
        purchase exists to feed.
        """
        with pytest.raises(Exception) as caught:
            self._row(db, seed_user, envelope_name="Lowe's")

        assert "ck_merchant_rules_one_answer" in str(caught.value)

    def test_a_BLANK_merchant_is_unwritable(self, app, db, seed_user):
        """A blank name is a merchant the owner could neither read nor restate.

        **The rule moved to where the string now lives once** (plan step
        ``bank_import:X-gd-1``): it was stated on the answer table AND on the
        line table, two CHECKs over two copies of one value, and it is
        ``ck_merchants_name_not_blank`` alone now.  The adapter answers ``None``
        for the same input (``_secu_csv._stated_merchant``), so the two cannot
        drift.
        """
        row = Merchant(account_id=seed_user["account"].id, name="   ")
        db.session.add(row)
        with pytest.raises(Exception) as caught:
            db.session.flush()

        assert "ck_merchants_name_not_blank" in str(caught.value)

    def test_ANOTHER_ACCOUNTS_merchant_is_unwritable(
        self, app, db, seed_user, seed_second_user,
    ):
        """THE guard the scope check used to be, now structural.

        A stated answer keyed on a merchant belonging to somebody else's
        account is what ``_refuse_unknown_merchants`` was the only thing
        refusing.  ``fk_merchant_rules_merchant_account`` is composite
        -- ``(merchant_id, account_id)`` against ``uq_merchants_id_account`` --
        so the row is unwritable rather than merely unreached, which is what
        demotes that Python check to a sentence.
        """
        theirs = a_merchant(
            seed_second_user, "Theirs",
            account=seed_second_user["account"],
        )
        db.session.flush()
        row = MerchantRule(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            merchant_id=theirs.id,
            never_a_purchase=True,
        )
        db.session.add(row)
        with pytest.raises(Exception) as caught:
            db.session.flush()

        assert "fk_merchant_rules_merchant_account" in str(caught.value)

    def test_TWO_answers_for_ONE_merchant_are_unwritable(
        self, app, db, seed_user,
    ):
        """One answer per merchant per account, structurally."""
        a_rule(seed_user, "Amazon")
        with pytest.raises(Exception) as caught:
            self._row(db, seed_user)

        assert "uq_merchant_rules_account_merchant" in str(
            caught.value,
        )


class TestAMerchantOutLIVESTheLinesThatNamedIt:
    """A merchant with an answer stays answerable when its lines are gone.

    **This was the second half of a UNION and it is now the table** (plan step
    ``bank_import:X-gd-1``).  The scope used to be *every merchant this
    account's recorded lines name*, unioned with *every merchant already
    answered for*, and the second half existed because deleting an import took
    a merchant's lines and would otherwise have made its answer unwithdrawable.
    A merchant ROW survives its lines, so what these cases grade is that
    survival rather than a second clause someone has to remember to keep.
    """

    def test_a_merchant_stays_in_scope_when_its_IMPORT_is_deleted(
        self, app, db, seed_user,
    ):
        """The real event, through the real door.

        ``statement_import.delete_import`` is what removes an import and its
        lines (plan step ``bank_import:X-f6a-4``).  Before the merchant was a
        row, that deletion took the ONLY evidence the merchant had ever been
        seen, and the answer stated about it became unrestatable -- the door
        refuses a merchant outside the scope, and the whole submission with it.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        db.session.flush()
        a_rule(seed_user, "Amazon")
        db.session.flush()

        delete_import(
            statement.id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert db.session.query(BankStatementLine).count() == 0
        held = account_merchants(seed_user["account"].id)
        assert sorted(held.values()) == ["Amazon"]
        # ...and the door still admits a restatement of the answer.
        state_rules(
            (a_statement(seed_user, "Amazon", RuleAnswer.NEVER),),
            seed_user["user"].id, seed_user["account"].id,
        )

    def test_ANOTHER_ACCOUNTS_merchant_is_not_in_this_ones_scope(
        self, app, db, seed_user, seed_second_user,
    ):
        """THE security-relevant filter, and it is one clause on one table.

        Delete ``Merchant.account_id == account_id`` from
        :func:`~app.services.statement_match.account_merchants` and every
        merchant on every account in the database widens this account's scope.
        The stored row is unwritable either way since
        ``fk_merchant_rules_merchant_account`` -- this is the reader's
        half, which is what decides whether the SCREEN offers the question.
        """
        theirs = an_import(
            seed_second_user, account=seed_second_user["account"],
        )
        a_bank_line(
            seed_second_user, theirs, amount="-9.99", merchant="Theirs Only",
        )
        db.session.flush()

        assert account_merchants(seed_user["account"].id) == {}


class TestEveryReadFilterHasAControlThatFiresOnItsOwn:
    """One guard per case, because a case that needs two to fail grades neither.

    **The owner/account pair below was measured to survive its own deletion**
    by an adversarial test-quality review on 2026-08-19: the suite stayed green
    with each filter removed one at a time, and the one case that looked like a
    control for two of them differed on both, so it fired only when both went.
    That sentence is scoped to that member on purpose -- it is a MEASUREMENT
    with a date, and the name-join case beside it was written on 2026-08-25 and
    is not covered by it.  A false provenance line is worse than none, because
    the next reader trusts it instead of re-measuring.

    The scope filters this class also used to cover are now
    :class:`TestAMerchantOutLIVESTheLinesThatNamedIt`'s, because the scope
    became one clause on one table.
    """

    def test_rules_are_read_for_THIS_OWNER_and_THIS_ACCOUNT(
        self, app, db, seed_user, seed_second_user,
    ):
        """Both filters on ``rules_for``, each observed on its own.

        The OWNER filter protects against a CALLER, not against a data state:
        ``fk_merchant_rules_owner`` already holds a row's owner equal to
        its account's, so no row with the wrong pair exists to be found.  What
        it refuses is a producer asked for one owner's answers with another
        owner's id -- which is why the case passes a mismatched pair rather
        than staging an impossible row.  A first version of this test staged
        the other owner's rule on the OTHER owner's account, where the
        account filter alone answers correctly, so it graded nothing.

        The ACCOUNT filter is the one a screen can hit: a Checking answer
        suggested on a card statement resolves to nothing at best, and the
        credit-card arc is about to give this owner a second account.
        """
        mine = a_transaction(seed_user, name="Groceries", is_envelope=True)
        a_rule(seed_user, "Amazon", template_id=mine.template_id)
        theirs = a_transaction(
            seed_second_user, name="Groceries", is_envelope=True,
        )
        a_rule(seed_second_user, "Theirs", template_id=theirs.template_id,
                 account=seed_second_user["account"])
        db.session.flush()

        # Asked for the WRONG owner of a real account: nothing.
        assert rules_for(
            seed_second_user["user"].id, seed_user["account"].id,
        ) == {}
        # Asked for the right owner and the WRONG account: nothing.
        assert rules_for(
            seed_user["user"].id, seed_second_user["account"].id,
        ) == {}
        # ...and the right pair finds exactly its own.
        assert sorted(
            rule.merchant for rule in rules_for(
                seed_user["user"].id, seed_user["account"].id,
            ).values()
        ) == ["Amazon"]

    def test_the_NAME_a_rule_carries_is_ITS_OWN_merchants(
        self, app, db, seed_user,
    ):
        """The join in ``rules_for``, which two answers can tell apart.

        The name travels with the answer so every sentence the door writes can
        print it without a second read.  Join on ``merchant_id`` alone and one
        account's two answers still resolve correctly; drop the join's
        ``account_id`` term and nothing observable changes here either, which
        is why the composite is stated for the reason the FOREIGN KEY is and
        not because a case could catch it.  What this DOES catch is the join
        pairing an answer with the wrong merchant's name.
        """
        a_rule(seed_user, "Amazon")
        a_rule(seed_user, "Walmart")
        db.session.flush()

        held = rules_for(seed_user["user"].id, seed_user["account"].id)
        by_id = {
            merchant_id: rule.merchant
            for merchant_id, rule in held.items()
        }
        assert by_id == {
            the_merchant_id(seed_user, "Amazon"): "Amazon",
            the_merchant_id(seed_user, "Walmart"): "Walmart",
        }



class TestRestatingOneCOLUMNIsStillAChange:
    """`_same_answer` compares three columns, and two graded nothing."""

    def _record(self, db, seed_user, merchant, **columns):
        """Stage a line for *merchant* and a rule row carrying *columns*."""
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant=merchant)
        a_rule(seed_user, merchant, **columns)
        db.session.flush()

    def test_changing_only_the_NAME_is_written(self, app, db, seed_user):
        """Delete the ``envelope_name`` term and this restatement is dropped.

        The owner renames the envelope a merchant should get, the receipt says
        nothing changed, and the next statement still creates it under the old
        name.
        """
        category = seed_user["categories"]["Groceries"]
        self._record(
            db, seed_user, "Lowe's", envelope_name="Lowe's",
            category_id=category.id,
        )

        recorded = state_rules(
            (a_statement(seed_user, "Lowe's", RuleAnswer.NEW_ENVELOPE,
                             envelope_name="Yard & Garden",
                             category_id=category.id),),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert len(recorded.stated) == 1
        assert rules_for(
            seed_user["user"].id, seed_user["account"].id,
        )[a_merchant(seed_user, "Lowe's").id].envelope_name == "Yard & Garden"

    def test_changing_only_the_CATEGORY_is_written(self, app, db, seed_user):
        """Delete the ``category_id`` term and this restatement is dropped.

        The envelope keeps being created under the category the owner moved it
        away from, which is what every spending report groups by.
        """
        was = seed_user["categories"]["Groceries"]
        now = seed_user["categories"]["Rent"]
        self._record(
            db, seed_user, "Lowe's", envelope_name="Lowe's", category_id=was.id,
        )

        recorded = state_rules(
            (a_statement(seed_user, "Lowe's", RuleAnswer.NEW_ENVELOPE,
                             envelope_name="Lowe's", category_id=now.id),),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert len(recorded.stated) == 1
        assert rules_for(
            seed_user["user"].id, seed_user["account"].id,
        )[a_merchant(seed_user, "Lowe's").id].category_id == now.id

    def test_a_NEW_answer_is_stored_TRIMMED(self, app, db, seed_user):
        """The write-path trim, which the no-op case above cannot observe.

        That case is decided by the unchanged check, which trims before it
        compares; this is the first statement, where there is nothing to
        compare against.  Stored untrimmed, the name differs from what the
        database CHECK reads (``btrim``) and from what the owner typed next
        time, so every later Save rewrites the row.
        """
        category = seed_user["categories"]["Groceries"]
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant="Lowe's")
        db.session.flush()

        state_rules(
            (a_statement(seed_user, "Lowe's", RuleAnswer.NEW_ENVELOPE,
                             envelope_name="  Lowe's  ",
                             category_id=category.id),),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert rules_for(
            seed_user["user"].id, seed_user["account"].id,
        )[a_merchant(seed_user, "Lowe's").id].envelope_name == "Lowe's"

    def test_a_name_differing_only_by_WHITESPACE_is_not_a_change(
        self, app, db, seed_user,
    ):
        """`_trimmed` had no control, and the database CHECK compares btrim.

        Without it the section rewrites that row on every Save, and the stored
        name drifts from the one the CHECK would accept.
        """
        category = seed_user["categories"]["Groceries"]
        self._record(
            db, seed_user, "Lowe's", envelope_name="Lowe's",
            category_id=category.id,
        )

        recorded = state_rules(
            (a_statement(seed_user, "Lowe's", RuleAnswer.NEW_ENVELOPE,
                             envelope_name="  Lowe's  ",
                             category_id=category.id),),
            seed_user["user"].id, seed_user["account"].id,
        )

        assert recorded.stated == ()
        assert recorded.unchanged_count == 1


class TestOneSubmISSIONNamingOneMerchantTwice:
    """The crafted shape whose blast radius is the whole pass."""

    def test_the_second_statement_RESTATES_rather_than_inserting(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for keeping ``stored`` in step.

        Without it both statements take the insert branch,
        ``uq_merchant_rules_account_merchant`` raises an
        ``IntegrityError`` -- which is not a designed refusal, so it escapes
        the per-item savepoint into the route's database arm and rolls back
        every answer that had already landed.  Unreachable from the rendered
        form, which emits one row per merchant; reachable by a crafted body.
        """
        first = a_transaction(seed_user, name="Groceries", is_envelope=True)
        second = a_transaction(seed_user, name="Gas", is_envelope=True)
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant="Amazon")
        db.session.flush()

        recorded = state_rules(
            (
                a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                                template_id=first.template_id),
                a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                                template_id=second.template_id),
            ),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert recorded.refused == ()
        rows = db.session.query(MerchantRule).all()
        assert len(rows) == 1
        # The LAST statement wins, which is what restating means.
        assert rows[0].template_id == second.template_id


class TestAStoredAnswerThatStoppedBeingOfferable:
    """A template deactivated under a rule's feet (finding from review)."""

    def test_the_view_can_still_NAME_it(self, app, db, seed_user):
        """THE FIRING CONTROL for the whole ``stale_templates`` derivation.

        Without it the section has no option carrying the stored value, so the
        select shows and submits its FIRST -- *I have not said* -- and the next
        Save silently WITHDRAWS a rule the owner never touched.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.query(TransactionTemplate).filter(
            TransactionTemplate.id == envelope.template_id,
        ).update({"is_active": False})
        db.session.flush()

        view = RuleView.build(
            seed_user["user"].id, seed_user["account"].id,
        )

        assert view.template_names == {}
        assert view.stale_templates == {envelope.template_id: "Groceries"}
        assert view.label_for(envelope.template_id) == "Groceries"

    def test_restating_it_UNCHANGED_is_a_no_op_rather_than_a_refusal(
        self, app, db, seed_user,
    ):
        """The order of the unchanged check is the fix, not a saving.

        The section renders the stale answer back, so Save submits it.
        Validating before comparing would refuse it -- reporting a refusal for
        a merchant the owner never touched, on a press about a different one.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant="Amazon")
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.query(TransactionTemplate).filter(
            TransactionTemplate.id == envelope.template_id,
        ).update({"is_active": False})
        db.session.flush()

        recorded = state_rules(
            (a_statement(seed_user, "Amazon", RuleAnswer.TEMPLATE,
                             template_id=envelope.template_id),),
            seed_user["user"].id, seed_user["account"].id,
        )

        assert recorded.refused == ()
        assert recorded.stated == ()
        assert recorded.unchanged_count == 1
        assert db.session.query(MerchantRule).count() == 1


class TestAMerchantSectionOverMixedLines:
    """The NULL-merchant guard is reachable and was graded by nothing."""

    def test_a_NULL_merchant_line_contributes_no_row(
        self, app, db, seed_user,
    ):
        """Remove the guard and ``sorted()`` raises on a set holding ``None``.

        Both shapes in one account is the ordinary state of a second adapter:
        one source names merchants and the other does not.
        """
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant=None,
            sequence_in_group=1,
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert len(review.creatable) == 2
        assert [
            row.summary.merchant for row in review.merchants.merchants
        ] == ["Amazon"]

    def test_two_spellings_of_one_merchant_are_two_rules(
        self, app, db, seed_user,
    ):
        """The model's own load-bearing claim, which nothing graded.

        ``merchant_rules`` does not case-fold, on the ground that
        deciding two bank strings name one merchant is a guess.  That is a real
        consequence rather than a detail: the owner answers twice, and the
        screen has to show both rows so they can.
        """
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant="amazon",
            sequence_in_group=1,
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert [
            row.summary.merchant for row in review.merchants.merchants
        ] == ["Amazon", "amazon"]


class TestANewEnvelopeAnswerReusesOneOfThatNameHere:
    """Finding **N-327**, developer ruling 2026-08-20 (plan step X-f6a-4).

    A ``new envelope called X`` answer used to mint unconditionally, so a
    rule fragmented its own budget line: measured on the developer's own
    statement, a ``Lowe's`` answer places 4 lines over 3 pay periods, so ONE
    press made 4 envelopes -- two of them in the same period -- and the next
    statement made more beside them, because an ad-hoc row carries no identity
    across periods for anything to converge on.

    **The suggestion is what changed, never the tick.**  The placement prints
    beside the line's own destination select, which still opens on *leave this
    line alone* (ruling **R-FZ**), so the owner sees the envelope it would
    reuse and may pick another -- including "a new envelope" again.
    """

    def test_a_LEGACY_link_less_envelope_of_that_name_HERE_is_recorded_into(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the cross-STATEMENT half of the convergence, on a legacy row.

        The envelope a previous statement created before plan step
        balance:X-bi-7b carries no definition, and until the family's cutover
        mints it one it is what the answer's first firing converges on
        (``PurchaseDestination.names_no_cadence``) -- a first build of leaf
        7b-3 keyed the convergence on ``is_placed`` and minted a second
        "Home Improvement" beside every legacy one (found by 7b-3's
        adversarial review; finding N-327's fragmentation back).  What such a
        row does NOT do is take the rule's NAME: the flip is keyed on
        ``is_placed``, graded in ``test_placing.py``.
        """
        category = seed_user["categories"]["Groceries"]
        existing = a_transaction(
            seed_user, name="Home Improvement", is_envelope=True,
            template=False,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, categories={category.id}), [_destination(existing)],
        )

        assert placement.kind is PlacementKind.RECORD_IN
        assert placement.destination.transaction_id == existing.id
        assert placement.select_value == str(existing.id)

    def test_a_ONE_OFF_envelope_of_that_name_HERE_is_recorded_into(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the cross-STATEMENT half of the convergence.

        An envelope the owner made at the grid carries a rule-less DEFINITION
        (plan step balance:X-bi-7b), and a new-envelope answer's first firing
        converges on it rather than minting a second "Home Improvement"
        beside it in the same period -- finding N-327's fragmentation --
        after which the answer names that definition (leaf 7b-3, ruling
        R-BAL24).  Keyed on ``is_placed``, read off the REAL destinations scan
        here (``a_scope``), so this fires if the scan stops setting it or the
        placement reverts to the link or to ``recurs``.
        """
        category = seed_user["categories"]["Groceries"]
        existing = a_one_off_envelope(
            seed_user, name="Home Improvement", category=category,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )
        offered = [
            one for one in a_scope(seed_user).destinations
            if one.transaction_id == existing.id
        ]
        assert len(offered) == 1 and offered[0].template_id is not None

        placement = placements_for(
            _MERCHANT, _view(rule, categories={category.id}), offered,
        )

        assert placement.kind is PlacementKind.RECORD_IN
        assert placement.destination.transaction_id == existing.id

    def test_a_period_holding_NONE_of_that_name_still_creates(
        self, app, db, seed_user,
    ):
        """The arm the answer exists for, unchanged.

        Several of the developer's merchants -- a hardware store, a parks fee,
        two subscriptions -- have no envelope in ANY period, which is why
        ruling R-FX made creating one an answer at all.
        """
        category = seed_user["categories"]["Groceries"]
        other = a_transaction(
            seed_user, name="Groceries", is_envelope=True, template=False,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, categories={category.id}), [_destination(other)],
        )

        assert placement.kind is PlacementKind.CREATE_NEW
        assert placement.new_envelope.name == "Home Improvement"
        assert placement.select_value == "new"

    def test_TWO_of_that_name_here_is_reported_rather_than_guessed(
        self, app, db, seed_user,
    ):
        """The same rule, and the same sentence shape, a template answer takes.

        Reachable on data the defect itself produced -- two same-named
        envelopes in one period is exactly what one press used to make -- which
        is why it may not be papered over by picking the first.
        """
        category = seed_user["categories"]["Groceries"]
        one = a_one_off_envelope(
            seed_user, name="Home Improvement", category=category,
        )
        two = a_one_off_envelope(
            seed_user, name="Home Improvement", category=category,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, categories={category.id}),
            [_destination(one), _destination(two)],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "already holds 2 of them" in placement.unresolved_reason
        assert placement.select_value is None

    def test_a_same_named_envelope_under_ANOTHER_category_is_NOT_reused(
        self, app, db, seed_user,
    ):
        """MONEY-ADJACENT FIRING CONTROL: a rule names a name AND a category.

        Reusing on the name alone would file this merchant's spending under a
        category the owner did not pick -- which is what every spending report
        groups by, and what the within-press registry already keys on.  A first
        draft of this arm compared the name alone, so the two halves of one
        rule disagreed; found by adversarial design review 2026-08-20.
        """
        answered = seed_user["categories"]["Groceries"]
        other = next(
            category for name, category in seed_user["categories"].items()
            if name != "Groceries"
        )
        existing = a_transaction(
            seed_user, name="Home Improvement", is_envelope=True,
            template=False, category=other,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=answered.id,
        )

        placement = placements_for(
            _MERCHANT,
            _view(rule, categories={answered.id, other.id}),
            [_destination(existing)],
        )

        assert placement.kind is PlacementKind.CREATE_NEW
        assert placement.new_envelope.category_id == answered.id

    def test_a_same_named_TEMPLATE_row_is_NOT_reused(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: naming a template is a DIFFERENT answer.

        It has its own resolution beside this one, including the "this pay
        period holds two of them, pick the one you mean" report that a
        recurring definition needs.  An owner who means the recurring envelope
        has that answer available and did not choose it, so converging onto it
        here would make the two answers indistinguishable in effect -- and
        would silently bypass the reporting.
        """
        category = seed_user["categories"]["Groceries"]
        generated = a_transaction(
            seed_user, name="Home Improvement", is_envelope=True,
        )
        assert generated.template_id is not None
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, categories={category.id}),
            [_destination(generated)],
        )

        assert placement.kind is PlacementKind.CREATE_NEW

    def test_it_matches_the_NAME_and_not_the_label(
        self, app, db, seed_user,
    ):
        """The label appends the pay-period span for a reader.

        A rule comparing against the label would be comparing against the span
        too, so it would never match -- and the convergence would silently do
        nothing while every test above still passed if they compared labels.
        """
        category = seed_user["categories"]["Groceries"]
        existing = a_one_off_envelope(
            seed_user, name="Home Improvement", category=category,
        )
        offered = _destination(existing)
        assert offered.label != offered.name

        placement = placements_for(
            _MERCHANT,
            _view(
                StandingRule(
                    merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
                    envelope_name="Home Improvement",
                    category_id=category.id,
                ),
                categories={category.id},
            ),
            [offered],
        )

        assert placement.kind is PlacementKind.RECORD_IN

    def test_an_ARCHIVED_category_still_refuses_before_any_of_this(
        self, app, db, seed_user,
    ):
        """The order of the refusals: an unusable answer resolves to nothing.

        Reusing an envelope of that name would look like a repair, and it would
        be applying an answer the owner can no longer restate.
        """
        category = seed_user["categories"]["Groceries"]
        existing = a_transaction(
            seed_user, name="Home Improvement", is_envelope=True,
            template=False,
        )
        rule = StandingRule(
            merchant_id=_MERCHANT, merchant="Lowe's", answer=RuleAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            _MERCHANT, _view(rule, categories=frozenset()),
            [_destination(existing)],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "archived" in placement.unresolved_reason


class TestTheScreenSaysWhichLineCREATESTheEnvelope:
    """Finding **N-327**: one press mints one envelope per answer per period.

    ``placements_for`` resolves ONE line against its own period and cannot know
    what another line in the same pass will do, so the flag that says *an
    earlier line here already creates this* is set by the reader -- the only
    thing that sees more than one line at a time.

    **It is about the SENTENCE, not the act.**  Both lines still carry the same
    select value (``new``), because the write converges; what this buys is that
    the screen says so BEFORE the press rather than the receipt saying it after.
    """

    def test_the_SECOND_line_of_one_answer_says_it_joins(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: without it both lines read as making their own."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        category = seed_user["categories"]["Groceries"]
        for amount in ("-30.00", "-45.00"):
            a_bank_line(
                seed_user, statement, amount=amount, posted_on=day,
                description=f"LOWES {amount}", merchant="Lowe's",
            )
        a_rule(
            seed_user, "Lowe's", envelope_name="Home Improvement",
            category_id=category.id,
        )

        review = review_set(a_scope(seed_user))

        joining = [
            line.placement.joins_new for line in review.creatable
            if line.placement is not None and line.placement.creates
        ]
        assert joining == [False, True]

    def test_lines_in_DIFFERENT_periods_join_one_definition(
        self, app, db, seed_user,
    ):
        """The key is the ANSWER: the second line joins the definition the first mints.

        **It asserted ``[False, False]`` until leaf 7b-3 of balance:X-bi-7b**
        (ruling R-BAL24): the key carried the period, so a line in another
        paycheck created its own envelope -- a second definition of one
        answer.  One press mints ONE rule-less definition per answer now and
        places a row of it per paycheck, so the later line joins.
        """
        statement = an_import(seed_user)
        category = seed_user["categories"]["Groceries"]
        first_day = seed_user["bootstrap_period"].start_date
        later_day = a_later_period(seed_user).start_date
        for amount, day in (("-30.00", first_day), ("-45.00", later_day)):
            a_bank_line(
                seed_user, statement, amount=amount, posted_on=day,
                description=f"LOWES {day}", merchant="Lowe's",
            )
        a_rule(
            seed_user, "Lowe's", envelope_name="Home Improvement",
            category_id=category.id,
        )

        review = review_set(a_scope(seed_user))

        joining = [
            line.placement.joins_new for line in review.creatable
            if line.placement is not None and line.placement.creates
        ]
        assert joining == [False, True]


class TestTheFiveAnswersRoundTrip:
    """What an answer WRITES and what a row READS BACK are one mapping.

    ``_columns_of`` turns an answer into five columns and ``RuleAnswer.of``
    turns five columns back into an answer.  They are inverse functions stated
    twice, which is the shape a new answer breaks by being added to one side
    only -- so what is graded is the round trip over EVERY member rather than
    one hand-picked case each, and a member added to the enum without a column
    mapping fails here on the day it is added.

    **It did exactly that on 2026-08-31**, when ruling **R-HT(a)**'s
    :attr:`~._rules.RuleAnswer.INCOME_CATEGORY` was added: all three cases
    failed on their ``set(fields) == set(RuleAnswer)`` guard before any of them
    reached a database, which is the cheapest place to learn it.  That guard is
    why the maps below stay literal rather than derived from the enum.
    """

    def test_every_answer_reads_back_as_itself(self, app, db, seed_user):
        """All four, driven off the enum rather than off a list here.

        ``for answer in RuleAnswer`` is the point: a list re-typed in this file
        would go stale exactly when the mapping did.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        category = seed_user["categories"]["Groceries"]
        fields = {
            RuleAnswer.TEMPLATE: {"template_id": envelope.template_id},
            RuleAnswer.NEW_ENVELOPE: {
                "envelope_name": "Lowe's", "category_id": category.id,
            },
            RuleAnswer.INCOME_CATEGORY: {"income_category_id": category.id},
            RuleAnswer.NEVER: {},
            RuleAnswer.ALWAYS_ASK: {},
        }
        assert set(fields) == set(RuleAnswer), (
            "a RuleAnswer member with no case here is one this round trip "
            "does not grade"
        )

        for answer in RuleAnswer:
            merchant = a_merchant(seed_user, f"Merchant {answer.value}")
            row = MerchantRule(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                merchant_id=merchant.id,
                **_columns_of(RuleSubmission(
                    merchant_id=merchant.id, answer=answer, **fields[answer],
                )),
            )
            db.session.add(row)
            # Flushed one at a time so the CHECK grades each shape on its own:
            # a batch would report the first violation and say nothing about
            # the rest.
            db.session.flush()

            assert RuleAnswer.of(row) is answer

    def test_every_answer_gets_its_OWN_sentence(self, app, db, seed_user):
        """The receipt is a fourth place the answer set is enumerated.

        ``_columns_of`` derives the flag and ``_apply_one`` DISPATCHED on three
        members and fell through to the fourth, so a fifth member would be
        STORED correctly and RECEIPTED as *ask me every time* -- telling the
        owner they said something they did not.  **That fall-through is a raise
        since plan step ``bank_import:X-gj-2a``**, and this case is what makes
        it a test failure rather than a 500.  The round trip above catches the storage half; this catches the
        sentence, which is the half the owner reads.  Named by adversarial
        review 2026-08-26.

        Distinct sentences rather than four literals, because what has to hold
        is that no two answers report the same thing, not what any one of them
        says.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        category = seed_user["categories"]["Groceries"]
        fields = {
            RuleAnswer.TEMPLATE: {"template_id": envelope.template_id},
            RuleAnswer.NEW_ENVELOPE: {
                "envelope_name": "Lowe's", "category_id": category.id,
            },
            RuleAnswer.INCOME_CATEGORY: {"income_category_id": category.id},
            RuleAnswer.NEVER: {},
            RuleAnswer.ALWAYS_ASK: {},
        }
        assert set(fields) == set(RuleAnswer)

        said = []
        for answer in RuleAnswer:
            db.session.flush()
            recorded = state_rules(
                (a_statement(
                    seed_user, f"Merchant {answer.value}", answer,
                    **fields[answer],
                ),),
                seed_user["user"].id,
                seed_user["account"].id,
            )
            db.session.flush()
            assert recorded.refused == ()
            assert len(recorded.stated) == 1
            said.append(recorded.stated[0])

        assert len(set(said)) == len(RuleAnswer)

    def test_a_field_belonging_to_ANOTHER_answer_is_not_written(
        self, app, db, seed_user,
    ):
        """A submission that pairs an answer with the wrong arm states nothing.

        ``state_rules`` is exported from the package, so its input is a public
        contract: a caller that built *never a purchase* while leaving a
        ``template_id`` on the value would, under a mapping that copied all
        four columns, store a TEMPLATE row whose own stated answer said
        otherwise.  ``ck_merchant_rules_one_answer`` would not catch it --
        template-and-nothing-else is a legal shape -- so the refusal has to be
        that the field is never read.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )

        columns = _columns_of(RuleSubmission(
            merchant_id=1, answer=RuleAnswer.NEVER,
            template_id=envelope.template_id,
            envelope_name="Lowe's",
            category_id=seed_user["categories"]["Groceries"].id,
            income_category_id=seed_user["categories"]["Groceries"].id,
        ))

        assert columns == {
            "template_id": None,
            "envelope_name": None,
            "category_id": None,
            "income_category_id": None,
            "never_a_purchase": True,
        }


class TestTheFlagIsPinnedOnTheContainerAnswers:
    """``ck_merchant_rules_one_answer``'s new term, shown refusing a row.

    Without it a row could name a template AND claim *never a purchase*, and
    the two readers that ask in different orders would disagree:
    ``RuleAnswer.of`` looks at the container first and calls it a TEMPLATE,
    while ``CreationBars`` looks at the flag and bars the line.  One suggests
    a destination and the other forbids one, on the same row.
    """

    def _row(self, db, seed_user, **columns):
        """Stage a rule row with these columns and flush it."""
        row = MerchantRule(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            merchant_id=a_merchant(seed_user, "Amazon").id,
            **columns,
        )
        db.session.add(row)
        db.session.flush()
        return row

    def test_a_TEMPLATE_answer_claiming_never_is_unwritable(
        self, app, db, seed_user,
    ):
        """The contradiction, on the arm that names a recurring envelope."""
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )

        with pytest.raises(Exception) as caught:
            self._row(
                db, seed_user, template_id=envelope.template_id,
                never_a_purchase=True,
            )

        assert "ck_merchant_rules_one_answer" in str(caught.value)

    def test_a_NEW_ENVELOPE_answer_claiming_never_is_unwritable(
        self, app, db, seed_user,
    ):
        """...and on the arm that creates one."""
        with pytest.raises(Exception) as caught:
            self._row(
                db, seed_user, envelope_name="Lowe's",
                category_id=seed_user["categories"]["Groceries"].id,
                never_a_purchase=True,
            )

        assert "ck_merchant_rules_one_answer" in str(caught.value)

    def test_BOTH_container_less_shapes_are_writable(
        self, app, db, seed_user,
    ):
        """The firing control: the CHECK closes a contradiction, not an answer.

        Without this the two cases above would be satisfied by a constraint
        that refused the flag outright, which would make *never a purchase*
        unwritable -- the answer worth `-$7,412.94` on the developer's own
        statement.
        """
        # Read BACK from the database in both arms: asserting the attribute on
        # the instance just constructed re-reads what Python set and would pass
        # against a column that was never written.  Found by adversarial review
        # 2026-08-26.
        barred = self._row(db, seed_user, never_a_purchase=True).id
        db.session.expire_all()
        assert db.session.get(MerchantRule, barred).never_a_purchase is True

        db.session.rollback()

        asking = self._row(db, seed_user, never_a_purchase=False).id
        db.session.expire_all()
        assert db.session.get(MerchantRule, asking).never_a_purchase is False

    def test_a_row_that_STATES_no_flag_is_unwritable(
        self, app, db, seed_user,
    ):
        """NOT NULL with no default, and this is what that buys.

        A writer that does not state the answer gets a refusal rather than one
        the schema picked for it.  ``never_a_purchase`` is the only column here
        whose absence nothing else would catch: a row with no container is a
        legal answer, so the three columns beside it raise nothing on its
        behalf.
        """
        with pytest.raises(Exception) as caught:
            self._row(db, seed_user)

        assert "never_a_purchase" in str(caught.value)


class TestAlwaysAskPlacesNothing:
    """The fourth answer reaches the line's own control with no suggestion.

    It is the answer that means *I do not want a standing one*, so a placement
    would be the app answering a question the owner reserved for themselves.
    """

    def test_it_offers_no_placement(self, app, db, seed_user):
        """...and the line is still CREATABLE, which is the other half.

        *ask me every time* is not a bar: the line keeps its own destination
        select, exactly as an unanswered merchant's does.  A dispatch that
        treated the fourth answer like *never a purchase* would silently
        remove the very control this answer exists to send the owner back to.
        """
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-25.00", merchant="Amazon",
        )
        a_rule(seed_user, "Amazon", always_ask=True)
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert len(review.creatable) == 1
        assert review.creatable[0].placement is None

    def test_it_does_not_fall_through_to_the_TEMPLATE_arm(
        self, app, db, seed_user,
    ):
        """The dispatch asked directly, past the screen that renders it.

        ``placements_for`` used to name the answers that place NOTHING and fall
        through to the template arm, so a fourth answer resolved as a template
        with a ``NULL`` id.  Asked here rather than only through
        ``review_set`` because a raise inside a read pass is a 500, and a 500
        is not a value this control could assert about.
        """
        merchant = a_merchant(seed_user, "Amazon")
        a_rule(seed_user, "Amazon", always_ask=True)
        db.session.flush()
        view = RuleView.build(
            seed_user["user"].id, seed_user["account"].id,
        )

        assert placements_for(merchant.id, view, []) is None


class TestNamingAStoredTemplateIsScopedToTheAccount:
    """Finding **N-353**, closed here.

    ``_named_templates`` selected by id alone and the NAME it returns is
    rendered on this control.  Nothing reachable leaked -- a rule's template is
    held to its account by ``fk_merchant_rules_template_account`` -- but that
    is safety by derivation over an open set of future callers, and the caller
    already holds the account.
    """

    def test_ANOTHER_accounts_template_is_not_named(
        self, app, db, seed_user, seed_second_user,
    ):
        """The scope clause, shown withholding a name it would have returned.

        Asked at the function rather than through ``RuleView`` because a rule
        naming a foreign template is UNWRITABLE, so the leak is only reachable
        by a future caller -- which is exactly what the finding says, and
        exactly why the guard has to be local.
        """
        theirs = a_transaction(
            seed_second_user, name="Their Envelope", is_envelope=True,
        )
        db.session.flush()

        assert _named_templates(
            {theirs.template_id}, seed_user["account"].id,
        ) == {}

    def test_THIS_accounts_template_IS_named(
        self, app, db, seed_user,
    ):
        """The firing control: the clause narrows, it does not empty.

        Without it the case above would pass against a function that returned
        nothing at all -- and the name it returns is what a stale answer's
        option carries, so an empty answer here is a select that submits the
        wrong template.
        """
        mine = a_transaction(seed_user, name="Groceries", is_envelope=True)
        db.session.flush()

        assert _named_templates(
            {mine.template_id}, seed_user["account"].id,
        ) == {mine.template_id: "Groceries"}


class TestTheControlAlwaysCarriesTheAnswerItHOLDS:
    """A select with no option for its stored value submits its FIRST one.

    ``RuleView`` reads the offerable templates and the stale ones in two
    statements, so a template deleted between them is named by NEITHER: not
    offerable, and not in ``stale_templates``.  On every row the database can
    hold the two agree -- ``fk_merchant_rules_template_account`` keeps a rule's
    template on its account, and the hard-delete door cascades the rule away
    with it -- so the gap is the window between the two reads, which is why it
    is produced HERE and not through the screen.

    **Plan step ``bank_import:X-gd-2`` is what made it matter.**  The first
    option used to be *I have not said*, and falling onto it silently WITHDREW
    the rule; that option is no longer rendered for an answered merchant, so
    the first option is now a real recurring envelope and the silent outcome
    would be a rule RE-AIMED at a destination the owner never picked.
    """

    def test_a_template_named_by_NEITHER_read_still_gets_a_label(
        self, app, db, seed_user,
    ):
        """The totality property, asked of the row the screen renders.

        ``label_for`` is total by design; what is graded is that
        ``_merchant_summary`` ASKS it whenever the answer is not offerable,
        rather than only when the stale read happened to find a name.
        """
        merchant = a_merchant(seed_user, "Amazon")
        rule = StandingRule(
            merchant_id=merchant.id, merchant="Amazon",
            answer=RuleAnswer.TEMPLATE, template_id=9_999,
        )
        view = RuleView(
            rules={merchant.id: rule},
            template_names={},
            active_categories=frozenset(),
            stale_templates={},
            stale_categories={},
            placeable_templates=frozenset(),
            placed_periods={},
        )

        # THE SHARED ROW, because an answered merchant is where a stored
        # answer can be stale and the inbox asks about none (ruling
        # **bank_import:R-GX**).  *It went through ``answered_merchants``, the
        # register's control, until plan step ``bank_import:X-gi-3`` deleted
        # it; ``merchant_summary`` is the producer that control and the
        # MERCHANTS surface always shared, so the row graded is the same one.*
        row = merchant_summary(
            merchant.id, "Amazon", view,
            CreationBars(never=frozenset(), account_payments=frozenset()),
        )

        assert row.unofferable.template == "a recurring envelope"

    def test_an_OFFERABLE_template_gets_no_stale_label(
        self, app, db, seed_user,
    ):
        """The firing control: the label is for answers the list cannot show.

        Without it the case above would be satisfied by labelling every
        template answer, which would render a duplicate option beside the real
        one -- two options carrying the same value, one of them saying the
        envelope is no longer offered when it is.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        merchant = a_merchant(seed_user, "Amazon")
        rule = StandingRule(
            merchant_id=merchant.id, merchant="Amazon",
            answer=RuleAnswer.TEMPLATE, template_id=envelope.template_id,
        )
        view = RuleView(
            rules={merchant.id: rule},
            template_names={envelope.template_id: "Groceries"},
            active_categories=frozenset(),
            stale_templates={},
            stale_categories={},
            placeable_templates=frozenset(),
            placed_periods={},
        )

        row = merchant_summary(
            merchant.id, "Amazon", view,
            CreationBars(never=frozenset(), account_payments=frozenset()),
        )

        assert row.unofferable.template is None


class TestTheBanksSignConvention:
    """:func:`is_inflow` -- the ONE statement of which sign means ARRIVING.

    Plan step ``bank_import:X-gj-2b``.  Seven readers in this package need a
    bank line's direction, and each spelled the comparison for itself until
    that step's own adversarial review counted EIGHT such comparisons -- three
    of them added by the very change whose commit title read *the SIGN stops
    routing*.  A convention written eight times is eight places to write it
    backwards, and it decides which way real money is reported to have moved.

    **Graded on LITERALS, not on the constant it is derived from**, which is
    the whole value of the case: an assertion phrased against the function's
    own expression would pass with that expression inverted.
    """

    def test_a_CREDIT_is_money_arriving(self):
        """The bank states money INTO the account as positive."""
        assert is_inflow(Decimal("28.29")) is True

    def test_a_DEBIT_is_not(self):
        """And money out of it as negative."""
        assert is_inflow(Decimal("-57.96")) is False

    def test_the_smallest_representable_figures_take_the_same_arms(self):
        """One cent either way, which is where an off-by-one boundary shows.

        ``bank_statement_lines.amount`` is ``NUMERIC(12, 2)``, so a cent is the
        smallest step the column can hold and these are the two rows nearest
        the boundary that the table can actually store.
        """
        assert is_inflow(Decimal("0.01")) is True
        assert is_inflow(Decimal("-0.01")) is False


class TestWhichPipelineALineEnters:
    """:func:`pipeline_for` -- the ONE place the two acts are told apart.

    Plan step ``bank_import:X-gj-2b-2``.  What an unexplained bank line BECOMES
    was decided by its SIGN until this step, in two separate sign tests that
    ran before the owner's rule was consulted at all.  A merchant credit is
    money ARRIVING that must become a PURCHASE -- a refund back into the
    container the owner named -- so the sign could not decide it and never
    could have; it was standing in for the ANSWER.

    **Graded as a whole TABLE rather than as cases**, because the failure this
    shape invites is an uncovered cell rather than a wrong one: a sixth
    ``RuleAnswer`` added without a decision here would silently take whatever
    the fall-through does, and "no suggestion" is a plausible-looking answer
    that nobody chose.  The round-trip assertion below is the same
    ``set(...) == set(RuleAnswer)`` guard this module already uses for
    :meth:`RuleAnswer.of`.
    """

    #: Every cell of (answer x direction), stated rather than derived, so a new
    #: answer fails the coverage assertion below instead of inheriting a
    #: default.  ``None`` is the sixth key: no rule at all.
    EXPECTED = {
        (RuleAnswer.TEMPLATE, False): LinePipeline.PURCHASE,
        (RuleAnswer.TEMPLATE, True): LinePipeline.PURCHASE,
        (RuleAnswer.NEW_ENVELOPE, False): LinePipeline.PURCHASE,
        (RuleAnswer.NEW_ENVELOPE, True): LinePipeline.PURCHASE,
        (RuleAnswer.INCOME_CATEGORY, False): LinePipeline.PURCHASE,
        (RuleAnswer.INCOME_CATEGORY, True): LinePipeline.INCOME,
        (RuleAnswer.NEVER, False): LinePipeline.PURCHASE,
        (RuleAnswer.NEVER, True): LinePipeline.INCOME,
        (RuleAnswer.ALWAYS_ASK, False): LinePipeline.PURCHASE,
        (RuleAnswer.ALWAYS_ASK, True): LinePipeline.INCOME,
        (None, False): LinePipeline.PURCHASE,
        (None, True): LinePipeline.INCOME,
    }

    def test_the_table_covers_every_answer_in_both_directions(self):
        """A new ``RuleAnswer`` fails HERE rather than falling through.

        The guard, and the reason this class states a table at all: the
        dispatcher's own shape is *name what routes and fall through*, which is
        right for safety and silent for coverage.  This is what makes the
        silence visible.
        """
        answered = {answer for answer, _ in self.EXPECTED}

        assert answered == set(RuleAnswer) | {None}, (
            "every RuleAnswer needs a decision in BOTH directions -- a new "
            "answer must name its pipeline rather than inherit one"
        )
        assert len(self.EXPECTED) == (len(RuleAnswer) + 1) * 2

    #: One real figure per direction, on the bank's own convention.  Stated as
    #: DATA here rather than taken from :func:`~app.services.statement_match.
    #: _rules.is_inflow`, so this table grades that convention instead of
    #: inheriting it: a test that asked the production predicate which sign
    #: means *arriving* would pass with the predicate inverted.
    AMOUNTS = {True: Decimal("28.29"), False: Decimal("-57.96")}

    @pytest.mark.parametrize("answer,arriving", sorted(
        EXPECTED, key=lambda k: (k[0].value if k[0] else "", k[1]),
    ))
    def test_each_cell_routes_where_the_table_says(self, answer, arriving):
        """Every cell, one case each."""
        assert pipeline_for(
            amount=self.AMOUNTS[arriving], answer=answer,
        ) is self.EXPECTED[(answer, arriving)]

    def test_a_CONTAINER_answer_is_what_makes_an_inflow_a_purchase(self):
        """The whole content of the change, stated as one property.

        An inflow is a purchase exactly when its merchant's answer names a
        SPENDING CONTAINER.

        **Asserted against a LITERAL pair, not against ``CONTAINER_ANSWERS``**
        (plan step ``bank_import:X-gj-2b-3``, found by adversarial test-quality
        review).  It read ``answer in CONTAINER_ANSWERS`` -- the same constant
        ``pipeline_for`` branches on -- so both sides of the assertion came
        from one place and adding ``INCOME_CATEGORY`` to that constant left the
        case GREEN while every unclaimed deposit became a purchase.  Its own
        docstring claimed the opposite: that the property and the constant
        "cannot drift apart".  They cannot, because the case could not see
        drift.
        """
        for answer in list(RuleAnswer) + [None]:
            files_a_purchase = pipeline_for(
                amount=self.AMOUNTS[True], answer=answer,
            ) is LinePipeline.PURCHASE
            assert files_a_purchase is (
                answer in (RuleAnswer.TEMPLATE, RuleAnswer.NEW_ENVELOPE)
            )

    def test_an_OUTFLOW_is_always_a_purchase_candidate(self):
        """Whatever the answer -- the rule only decides what is SUGGESTED.

        ``NEVER`` is answered further down by
        :class:`~app.services.statement_match.CreationBars`, which is where
        ruling **R-GJ** put it; routing it away from the purchase pipeline here
        would be a second statement of that refusal.
        """
        for answer in list(RuleAnswer) + [None]:
            assert pipeline_for(
                amount=self.AMOUNTS[False], answer=answer,
            ) is LinePipeline.PURCHASE
