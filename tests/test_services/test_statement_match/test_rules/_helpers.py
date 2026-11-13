"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_statement_match/test_rules.py``, moved verbatim by ``tools/split``.
"""

from app.services.pay_calendar import calendar_for
from app.services.statement_match import PurchaseDestination, RuleView


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
