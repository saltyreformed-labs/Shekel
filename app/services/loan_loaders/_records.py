"""Many loans' records at once: each kind a loan's walk folds, loaded once.

The owner's half of :mod:`app.services.loan_loaders` (plan task plan#2649,
ruling **plan#2623**: "load each kind of record once for the owner ... then
work every line out in memory, each walk over its own account's slice").  A
loan's walk folds four kinds of record -- its standing statements, its rate
history, its escrow lines and its settled payments -- and each loader of a
kind is ONE body over a set of loans
(:func:`._terms._standing_assertions_by_account`,
:func:`._terms._rate_history_by_account`,
:func:`._terms._escrow_lines_by_account`,
:func:`._legs._settled_legs_by_account`), with the one-loan door every
per-loan caller keeps.  :func:`load_loan_records` runs each body once over
every loan it is handed, so the statements do not grow with the number of
loans, and hands each loan back the very values its doors answer.

The loans' TERMS are the caller's: it hands in the
:class:`~app.models.loan_params.LoanParams` rows it already holds (the
worked-out read's account load joins ``Account.loan_params``), so no term is
loaded here a second time.

A LEAF beside :mod:`._terms` and :mod:`._legs`, which it reads and which
read nothing back.  Flask-isolated, reads only, no commits.
"""

from dataclasses import dataclass

from app.models.escrow_line import EscrowLine
from app.models.loan_params import LoanParams
from app.services.amortization_engine import RateChangeRecord
from app.services.transfer_legs import TransferLeg

from ._legs import _settled_legs_by_account
from ._terms import (
    LoanAnchorFact,
    _escrow_lines_by_account,
    _merged_anchor_facts,
    _rate_change_records_from,
    _rate_history_by_account,
    _standing_assertions_by_account,
)


@dataclass(frozen=True)
class LoanRecords:
    """One configured loan's records, each as its one-loan door answers it.

    Attributes:
        anchor_facts: Its opening and standing statements, in the loan's
            chronology (:func:`._terms.load_loan_anchor_facts`' list).
        rate_changes: Its rate-change feed, or ``None`` for a loan carrying
            no rate history (:func:`._terms.load_rate_changes`' answer).
        escrow_lines: Its escrow lines with every version
            (:func:`._terms.load_escrow_lines`' list).
        settled: Its settled payments' legs, each record held
            (:func:`._legs.settled_income_legs`' list, under no caller
            option).
    """

    anchor_facts: list[LoanAnchorFact]
    rate_changes: list[RateChangeRecord] | None
    escrow_lines: list[EscrowLine]
    settled: list[TransferLeg]


def load_loan_records(
    loans: list[LoanParams], scenario_id: int,
) -> dict[int, LoanRecords]:
    """Return each of *loans*' records, every kind loaded once for all of them.

    Statements: the standing statements 1, the rate history 1, the escrow
    lines 1 and their versions 1 when any line exists, the settled payments
    1, their pay periods 1 when any exists, and their records 1 when one is
    not already in the session -- whatever the number of loans.  NONE for no
    loan: an owner without a loan pays nothing for the loan half of a read.

    Args:
        loans: The configured loans' terms, each a loaded
            :class:`~app.models.loan_params.LoanParams` row.
        scenario_id: The budget scenario the payments live in.

    Returns:
        ``{loan account id: its LoanRecords}``, one per loan handed in.

    Raises:
        ValueError: A transfer into one of the loans carries a status that
            is neither settled nor ``Projected``
            (:func:`._legs.settled_income_legs`' refusal).
    """
    if not loans:
        return {}
    account_ids = [params.account_id for params in loans]
    standing = _standing_assertions_by_account(account_ids)
    rates = _rate_history_by_account(account_ids)
    escrow = _escrow_lines_by_account(account_ids)
    settled = _settled_legs_by_account(account_ids, scenario_id, options=())
    return {
        params.account_id: LoanRecords(
            anchor_facts=_merged_anchor_facts(
                params, standing.get(params.account_id, []),
            ),
            rate_changes=_rate_change_records_from(
                rates.get(params.account_id, []),
            ),
            escrow_lines=escrow.get(params.account_id, []),
            settled=settled.get(params.account_id, []),
        )
        for params in loans
    }
