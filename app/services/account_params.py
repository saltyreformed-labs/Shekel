"""
Shekel Budget App -- the rule that a parameterised account carries its satellite

**One statement of "an account of a parameterised kind has its params row", for
every door that can establish that kind.**  Plan step **balance:X-i3**, finding
**N-359**.

**FIVE doors set an account's projection kind, and until this module only the
first held the rule**:

1. creating an account (``routes.accounts.crud.create_account``);
2. re-classing one onto a different type (``crud.update_account``);
3. editing the TYPE itself, so every account already on it changes kind
   (``routes.accounts.types.update_account_type``, which may flip
   ``has_interest`` and ``has_parameters`` on an owner's own custom type, with
   no account row touched at all);
4. :func:`app.ref_seeds.seed_reference_data`, which rewrites every BUILT-IN
   type's flags in place on every deploy -- so an edit to ``ACCT_TYPE_SEEDS``
   re-classes those accounts for every owner at deploy;
5. a data migration doing the same by hand, which has shipped both ways
   already: ``2c1115378030`` flipped ``has_interest`` on Money Market and CD
   and backfilled, at a `0.04500` default that ``E-12`` / ``HIGH-06`` later
   banned, while ``98b1adb05030`` flipped ``has_parameters`` on 529 Plan and
   backfilled nothing.

**Doors 1-3 hold it here. Doors 4 and 5 do NOT, and that is N-359's**, along
with ``account_service.create_account`` -- the canonical factory, which builds a
parameterised account with no satellite row and whose contract a dozen
docstrings describe.

**It lives in the SERVICE tier for a reason that is a gate rather than a
preference.**  Its first home was ``app/routes/accounts/_type_params.py``, and
``shekel-private-module-import`` (W9910) forbids anything outside that package
from importing a private module of it in any spelling -- so ``ref_seeds`` and
``account_service``, doors 4 and 6, could not have reached the rule at all.  A
home that makes the remaining doors unfixable is the wrong home; an adversarial
review of this step made that argument and it is correct.

**What it cost while only door 1 held it.**  Two detail pages and the settings
dashboard each carried an auto-create for the missing row, repairing data on a
GET -- a write inside a read, which cost those pages the one snapshot every
figure on them is computed against (:mod:`app.db_transaction`) and hid the doors
that owed the row.  Deleting those repairs is only honest once the rule holds
where the kind is decided.

**Idempotent by construction, and that is what lets door 3 be simple.**  Each
arm asks whether the row exists before adding one, so a caller may run it over
every account of a type without first working out which of them changed kind --
and working that out would mean a second copy of
:func:`~app.services.account_projection.classify_account`'s branch order, which
is the "second answer to one question" these arcs keep deleting.

Flask-isolated, and it does not commit: the caller owns the transaction
boundary, as all three do.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import CompoundingFrequencyEnum, EmployerContributionTypeEnum
from app.extensions import db
from app.models.asset_appreciation_params import AssetAppreciationParams
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.services.account_projection import AccountProjectionKind


def ensure_type_params(account, kind):
    """Give *account* the params row its *kind* requires, if it has none.

    Dispatches on the account's :class:`AccountProjectionKind`: interest
    accounts get an ``apy=0`` :class:`InterestParams`, investment/retirement
    accounts a default :class:`InvestmentParams`, and Property (appreciating)
    accounts an ``annual_appreciation_rate=0``
    :class:`AssetAppreciationParams`.  Each row carries an explicit zero
    sentinel (E-12: zero is a value, not missing) so no projection runs on a
    silent server-default before the user configures the real value on the
    type-specific setup page.  ``PLAIN`` and ``AMORTIZING`` need nothing: a
    loan's terms are :class:`~app.models.loan_params.LoanParams`, written by
    its own setup flow.

    **Nothing is REMOVED when a kind changes away from a parameterised one**,
    deliberately: a re-class back restores the settings the owner had, and an
    unread satellite costs nothing but a row.

    Does not commit; the caller owns the transaction boundary.

    Args:
        account: The :class:`~app.models.account.Account` to hold the rule for.
        kind: Its :class:`AccountProjectionKind`, resolved by the caller
            through :func:`~app.services.account_projection.classify_account`
            so this module states no second copy of that taxonomy.
    """
    if kind is AccountProjectionKind.INTEREST:
        if not db.session.query(InterestParams).filter_by(account_id=account.id).first():
            # #38: compounding frequency is a ref FK, so the auto-create
            # supplies the DAILY id explicitly (an FK id is not a static
            # literal).  HIGH-06: explicit ``apy=0`` (no server_default) so
            # no ghost 4.5% interest is ever projected.
            db.session.add(InterestParams(
                account_id=account.id, apy=Decimal("0"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
    elif kind is AccountProjectionKind.INVESTMENT:
        if not db.session.query(InvestmentParams).filter_by(account_id=account.id).first():
            # #38: employer-contribution type is a ref FK, so the
            # auto-create supplies the NONE id explicitly.
            db.session.add(InvestmentParams(
                account_id=account.id,
                employer_contribution_type_id=(
                    ref_cache.employer_contribution_type_id(
                        EmployerContributionTypeEnum.NONE,
                    )
                ),
            ))
    elif kind is AccountProjectionKind.APPRECIATING:
        # Property: the user sets the real rate on the property detail page.
        if not db.session.query(AssetAppreciationParams).filter_by(
            account_id=account.id,
        ).first():
            db.session.add(AssetAppreciationParams(
                account_id=account.id,
                annual_appreciation_rate=Decimal("0"),
            ))
