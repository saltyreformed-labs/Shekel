"""Why a bank statement can never show one of the app's rows BY ITSELF.

The one caveat the MATCH pane chips a candidate row with, and the one
statement of it.  **Moved out of** :mod:`._offers` **at plan step
``bank_import:X-gz`` as a PURE move**, when that module's thirteen-field
candidate value took the pane's two date facts as fields and the old span as
two derived accessors, and stood past pylint's 1,000-line ceiling: the seam
is the subject -- this is a
sentence about a row, not the row -- and nothing outside that module read
either name, so the move changed no import but its own.

Services-boundary discipline (``CLAUDE.md`` Architecture): a frozen dataclass
and one instance of it, no Flask import, no query.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NotShownAlone:
    """Why a bank statement can never show one of the app's rows BY ITSELF.

    **The panel that lists the rows a statement did not explain asserts an
    INFERENCE, and for two row shapes that inference is false** (plan step
    ``bank_import:X-gc``, ruling **R-GO**, the 2026-08-24 adversarial design
    review).  Its
    caption claims of every row that the owner's records say its money moved
    and the statement never showed it moving, which only follows if that money
    would have reached the bank as a line of its own.  A CC PAYBACK's does
    not -- it leaves inside one lump
    payment to the card, so the bank shows the payment and never the payback --
    and an ENVELOPE whose figure is its purchases is the opposite shape with
    the same consequence: the bank showed the purchases, not the container.
    **The caption also named one DIRECTION until finding
    bank_import:N-380** -- it said *a payment ... your bank did not make*,
    which is a claim about an outflow, over a list that is 17 deposits in 49;
    plan step ``bank_import:X-gf-3b-2`` took the direction out of the sentence
    and left each row to state its own by its figure.

    Measured on the developer's own dev database 2026-08-25: **18 of the
    panel's 67 rows are CC Paybacks**, and the left-hand list beside them holds
    **9 unexplained ``ACH DEBIT CAPITAL ONE ... PMT`` lines** those paybacks
    are the counterpart of.

    **So the rows are ANNOTATED and never withheld.**  That panel is also the
    row-picker of the hand-build group form, and ruling **R-GJ** leaves the
    group match as the only ACT a parked card-payment line has -- its other arm
    is to PARK, which disposes of nothing -- so dropping the paybacks out of it
    would close the one path the ruling kept open.  What was false was the
    caption, not the membership.

    Attributes:
        label: The chip's own text, short enough to sit beside a row label.
        sentence: The whole reason, carried as the chip's title.  It ends in
            the ACT rather than in the diagnosis, which is
            :attr:`~._bars.BarredLine.reason`'s shape one card over: a row the
            owner cannot act on is a row they will read once.
    """

    label: str
    sentence: str


#: The one statement of it, because ONE property returns it and every surface
#: subscripts that property.  It does not fork on WHICH of the two shapes a row
#: is, and the measurement is why -- but the measurement is narrower than a
#: first draft of this comment claimed, and the difference matters.
#:
#: **The ENVELOPE arm is unreachable through PRICING, not merely unexercised.**
#: An envelope that derives its figure from entries values at
#: ``gross - Sigma(card entries) - Sigma(posted purchases)``
#: (:func:`~._settle_price.settle_price`), and
#: :func:`~._valuation.transaction_candidate` drops a row worth nothing --
#: measured 2026-08-25 on the developer's account, **63 of 63** such envelopes
#: price to ``Decimal("0")`` and all 63 are dropped, so none can reach a panel.
#: Every one of the 22 candidates stating no figure of its own is a CC payback.
#: One sentence covering both therefore costs nothing today, and a fourteenth
#: field on :class:`CandidateRow` to fork it would buy nothing.
#:
#: **What the panel DOES hold is a different envelope shape, and this
#: deliberately does not claim it**: 7 envelope-tracked containers carrying
#: ZERO entries (``Groceries`` `-163.95`, ``Gas`` `-40.00`, ``Mint Mobile`
#: `-132.69`, ``Father's Day`` `-100.00` and three more).  Their figure IS
#: their own -- there are no entries to derive it from, which is exactly what
#: ``settles_from_entries``' second half exists to say -- and whether the bank
#: showed such a row as ONE line or as several is a fact the app does not hold.
#: ``Mint Mobile`` is plainly one.  Chipping them on ``tracks_purchases`` would
#: withdraw the alarm from rows the bank may really have failed to show, which
#: is the one direction this caveat may not fail in.
NOT_SHOWN_ALONE = NotShownAlone(
    label="not a line of its own",
    sentence=(
        "This row's figure is not its own to state -- it is the purchases "
        "inside it, or the card spending it repays -- so your bank never "
        "shows it as a line by itself.  Tick it here together with the line "
        "that does carry its money, and match them."
    ),
)
