"""Shekel Budget App -- the pay stub entry door's printed-GROSS check.

What a stub's figures must add up to against the gross it prints (ruling
**R-SAL99**, "Check the gross too", closing finding **SAL-590**; on a job
whose stub's gross also holds the after-tax earnings, ruling **R-SAL102**,
closing **SAL-592**), and how a miss is worded (ruling **R-SAL106**, one rule
read off the net, amending R-SAL104; R-SAL108, R-SAL110 and R-SAL111 name
causes it had left out, R-SAL112 asks for the figure that tells two of them
apart, R-SAL119 drops the question where that figure cannot be and R-SAL120
has both typed totals checked first, and R-SAL114 to R-SAL117 put every
message in plain words).  One rule of
:mod:`app.services.pay_stub_service`'s door, held in its own module because
that module reached pylint's 1000-line cap (plan step salary:S11-c-2b): the
door asks :func:`gross_refusals` from ``_refuse`` beside the printed-net
check, and the entry form's route reads :func:`gross_counts` for the line
under the gross box, so the two say what the check counts in one spelling.

Flask-free and query-free: it takes the stub's derived totals, its printed
ones and the job's yes/no, and returns words.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from app.enums import PaycheckLineKindEnum
from app.services.paycheck_line_kinds import LABELS
from app.utils.money import ZERO

if TYPE_CHECKING:
    from app.services.pay_stub_service import PrintedTotals
    from app.services.paycheck_calculator import StubTotals


def gross_counts(includes_after_tax: bool) -> str:
    """Word which earnings the printed-gross check adds to base pay.

    ``"taxable earnings"`` (ruling **R-SAL99**), or ``"earnings, taxable and
    untaxed,"`` on a job whose stub's gross also holds its after-tax earnings
    (ruling **R-SAL102**), in the plain words of rulings **R-SAL114** and
    **R-SAL117**.  The one spelling the check's refusals and the entry form's
    line under the gross box both read, each after "Base pay plus your".

    Args:
        includes_after_tax: The job's ``stub_gross_includes_after_tax``.

    Returns:
        The phrase, to stand between "Base pay plus your" and its verb; the
        second carries its own closing comma.
    """
    return "earnings, taxable and untaxed," if includes_after_tax else "taxable earnings"


def checked_gross(totals: StubTotals, *, includes_after_tax: bool) -> Decimal:
    """Return the Gross Pay a stub's figures add up to, as the printed-gross check reads it.

    Base pay plus every taxable earning (ruling **R-SAL99**), and on a job whose
    stub's gross also holds its after-tax earnings, those too (ruling
    **R-SAL102**): the words :func:`gross_counts` gives.  The ONE spelling of
    that rule (ruling **R-SAL127**): :func:`gross_refusals` checks a typed stub
    with it, and the rehearsal of plan step salary:S11-d's old-calibration drop
    adds up a saved stub's Gross Pay with it.

    Args:
        totals: What the stub's figures add up to.
        includes_after_tax: The job's ``stub_gross_includes_after_tax``.

    Returns:
        The Gross Pay the figures make.
    """
    return totals.gross + (totals.after_tax if includes_after_tax else ZERO)


#: The refusal key that names the job's setting beside a printed-gross refusal
#: (rulings R-SAL104 and R-SAL106): the setting's own field name.  Its message
#: is the opening of the sentence that ends in a link to the setting, which
#: the entry form completes with "change that on your salary profile" and a
#: full stop (rulings R-SAL114 and R-SAL115), because this module cannot build
#: a URL.
SETTING_KEY = "stub_gross_includes_after_tax"


def gross_refusals(
    base_pay: Decimal, totals: StubTotals, printed: PrintedTotals,
    *, includes_after_tax: bool,
) -> dict[str, str]:
    """Return why the stub's figures miss its printed gross: empty when they make it.

    Ruling **R-SAL99**, "Check the gross too" (finding **SAL-590**), reads the
    printed gross as base pay plus every taxable earning, a line's and a
    one-off's alike (:func:`~app.services.pay_stub_service.totals_of`).  On a
    job whose stub's gross also holds the after-tax earnings (the profile's
    ``stub_gross_includes_after_tax``, ruling **R-SAL102**, closing finding
    **SAL-592**) the check adds those too.  **What "yes" costs, which the
    ruling accepted:** on such a job the gross cannot tell a taxable earning
    from an after-tax one, and the net never could (an earning joins it
    either way), so an earning entered under the wrong one of the two passes
    both checks.  Only the saved stub's page shows it: a line's kind beside
    its paycheck line's, flagged when they differ, and a one-off's kind as
    entered, with nothing to compare it against.

    **How a miss is worded: ONE rule, read off the net** (ruling
    **R-SAL106**, "One rule by the net", which amends R-SAL99's wording and
    R-SAL104's trigger; **R-SAL108** and **R-SAL110** name the typed gross
    wherever the net is exact, **R-SAL111** the earning the stub does not
    print beside the Base pay box, and **R-SAL112** asks the stub's base pay
    to tell those two apart; **R-SAL114** to **R-SAL117** word every message
    in plain words, changing neither what it checks nor when it appears).
    The door checks two printed totals, and
    each single typing mistake moves them as a pair -- the gross miss (what
    the checked lines make less the printed gross) and the net miss (the
    lines' net less the printed net).  A refusal names the single-mistake
    causes its pair allows:

    * **net exact** -- if one mistake is all there is, every amount is
      right, so the gross is off in what it holds or in how it was typed,
      and the typed gross is named every time (R-SAL108, R-SAL110: a typo in
      the gross box makes this pair at any amount).  On a "no" job short by
      exactly the after-tax total, R-SAL104's question beside it (an earning
      the stub taxes entered as after-tax) and the setting (a gross that
      holds the after-tax earnings: the job should be "yes"); on a "yes" job
      over by exactly that total, the setting (a gross that leaves them out:
      on a "yes" job no heading moves the gross); otherwise, on a "no" job,
      which earnings are taxable and which after-tax;
    * **net off by the same** -- an amount the gross counts is off: base pay,
      an earning's amount, an earning left out or added.  When base pay
      equals the printed gross -- the gross can then only be over -- the two
      single mistakes that make that pair are both asked: the gross typed
      into the Base pay box (R-SAL99's slip) and an earning of a kind the
      check counts, entered on a stub that prints none (R-SAL111).  Once the
      two typed totals are checked (R-SAL120), they are told apart by the
      base pay the stub shows (R-SAL112): base pay less the miss for the
      first, base pay itself for the second.  Where base pay less the miss
      is ``$0.00`` or less the first cannot be (a stub's base pay is above
      zero), so only the second is named (R-SAL119);
    * **net off by twice** -- one amount on the wrong side: a deduction
      entered as an earning (gross over) or an earning entered as a
      deduction (gross short);
    * **anything else** -- no single mistake makes it: the difference alone.

    **A pair can belong to more than one cause, and two mistakes can make a
    single mistake's pair.**  Where one pair has several single-mistake
    causes, the wording asks after each and the owner tells which.  Where
    two mistakes offset into a single mistake's pair, the wording names that
    mistake's causes and not theirs, so a wording that states a cause --
    "One amount is wrong" (R-SAL106, worded by R-SAL114), R-SAL112's two
    answers and R-SAL119's "you entered extra pay this stub doesn't list" --
    can then be false:

    * base pay and a tax off by the same amount read as net exact, and no
      cause a net exact wording names is theirs;
    * both printed totals typed off by the same read as an amount.  Where
      the gross box then holds the base pay -- and the net box is off by the
      earnings the check counts -- those earnings read as extra pay.  Each
      such wording first has the owner check both typed totals (R-SAL120),
      which finds both slips; only an owner who passes them as right goes on
      to remove the earnings the check counts, real ones the stub DOES list
      among them, and that SAVES the stub without them (R-SAL112's second
      answer, read with the stub's base pay right, and R-SAL119's only one);
    * base pay and a tax both off by exactly the after-tax total make the
      pair the setting's exit answers, and taking that exit then saves both
      mistakes.

    The submit with one of them fixed reads true.
    **A tax left out** is no gross figure but shifts the net miss by its
    amount: ``$0.00`` left out changes nothing, so every wording is as right
    as with every tax typed; a non-zero one moves the pair, which then reads
    as "anything else" unless the shift happens to match, and the submit
    with the tax typed reads true.

    Args:
        base_pay: The base pay typed.
        totals: What the stub's figures add up to.
        printed: The gross and net the stub prints.
        includes_after_tax: The job's ``stub_gross_includes_after_tax``.

    Returns:
        ``{"printed_gross": message}``, with a :data:`SETTING_KEY` message
        beside it when the wording names the setting; empty when the
        figures make the printed gross.
    """
    checked = checked_gross(totals, includes_after_tax=includes_after_tax)
    gross_miss = checked - printed.gross
    if gross_miss == ZERO:
        return {}
    opening = (
        f"Base pay plus your {gross_counts(includes_after_tax)} come to "
        f"${checked:,.2f}, but the stub's Gross Pay is ${printed.gross:,.2f}"
    )
    net_miss = totals.net - printed.net
    if net_miss == ZERO:
        return _net_exact(opening, gross_miss, totals.after_tax, includes_after_tax)
    return {"printed_gross": _amount_miss(
        opening, gross_miss, net_miss, base_pay=base_pay, printed_gross=printed.gross,
    )}


def _net_exact(
    opening: str, gross_miss: Decimal, after_tax: Decimal, includes_after_tax: bool,
) -> dict[str, str]:
    """Word a gross miss beside an exact net: how the gross was typed, or what it holds.

    The first bullet of :func:`gross_refusals`' rule (rulings R-SAL104,
    R-SAL106, R-SAL108, R-SAL110; worded by R-SAL114 to R-SAL116).  The two
    choices it names are the kind select's own labels (R-SAL116: "the exact
    words on the form"), read from their one home.
    """
    taxable = LABELS[PaycheckLineKindEnum.TAXABLE_EARNING]
    if not includes_after_tax and gross_miss == -after_tax:
        return {
            "printed_gross": (
                f"{opening}: ${-gross_miss:,.2f} less, the same as your untaxed "
                f"earnings.  Check the Gross Pay you typed.  If it's right, is one of "
                f"those earnings taxed on your stub?  Then choose {taxable} for it."
            ),
            SETTING_KEY: "Or, if your stub counts untaxed pay in its Gross Pay,",
        }
    if includes_after_tax and gross_miss == after_tax:
        return {
            "printed_gross": (
                f"{opening}: ${gross_miss:,.2f} more, the same as your untaxed "
                f"earnings.  Check the Gross Pay you typed."
            ),
            SETTING_KEY: "If it's right, your stub leaves untaxed pay out of its Gross Pay:",
        }
    headings = "" if includes_after_tax else (
        f", and which earnings you marked {taxable} and which "
        f"{LABELS[PaycheckLineKindEnum.AFTER_TAX_EARNING]}"
    )
    return {"printed_gross": (
        f"{opening} (${abs(gross_miss):,.2f} apart).  Your amounts match the stub's "
        f"Net Pay, so check the Gross Pay you typed{headings}."
    )}


def _amount_miss(
    opening: str, gross_miss: Decimal, net_miss: Decimal, *,
    base_pay: Decimal, printed_gross: Decimal,
) -> str:
    """Word a gross miss beside a net miss: an amount off, on the wrong side, or several.

    The last three bullets of :func:`gross_refusals`' rule (rulings R-SAL99,
    R-SAL106, R-SAL111, R-SAL112, R-SAL119, R-SAL120; worded by R-SAL114 and
    R-SAL117).
    """
    if net_miss == gross_miss:
        if base_pay == printed_gross:
            totals_first = (
                f"{opening}.  Check the Gross Pay and Net Pay you typed.  If they're right,"
            )
            stub_base = base_pay - gross_miss
            if stub_base <= ZERO:
                return f"{totals_first} you entered extra pay this stub doesn't list.  Remove it."
            return (
                f"{totals_first} what base pay does the stub show?  ${stub_base:,.2f}: "
                f"you typed the Gross Pay into Base pay.  Type ${stub_base:,.2f} "
                f"there instead.  ${base_pay:,.2f}: you entered extra pay this stub "
                f"doesn't list.  Remove it."
            )
        return (
            f"{opening} (${abs(gross_miss):,.2f} apart), and your amounts are "
            f"${abs(net_miss):,.2f} apart from its Net Pay too.  One amount is wrong: "
            f"check Base pay and each earning."
        )
    if net_miss == 2 * gross_miss:
        if gross_miss > ZERO:
            return (
                f"{opening}: ${gross_miss:,.2f} more, and your amounts come to "
                f"${net_miss:,.2f} more than its Net Pay.  Is a deduction marked as "
                f"an earning?  Check the kind chosen beside each amount."
            )
        return (
            f"{opening}: ${-gross_miss:,.2f} less, and your amounts come to "
            f"${-net_miss:,.2f} less than its Net Pay.  Is an earning marked as a "
            f"deduction?  Check the kind chosen beside each amount."
        )
    return f"{opening} (${abs(gross_miss):,.2f} apart).  Check each amount against the stub."

__all__ = [
    "SETTING_KEY",
    "checked_gross",
    "gross_counts",
    "gross_refusals",
]
