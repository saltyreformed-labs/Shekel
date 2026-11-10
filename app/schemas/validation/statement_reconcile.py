"""
Shekel Budget App -- What the RECONCILE page's form submits

Plan step ``bank_import:X-gj-1b``.  One reader per act the page carries, and
the schemas that grade the one act :mod:`.statements` does not already have a
schema for.

**The FORM shape lives beside the schema that grades it**, which is the rule
:mod:`.statements` states for its own readers and the reason neither lives in
a route: a route that listed the field names itself is a route that can be
extended with a fourth kind of item and not updated.  This is a SIBLING of
that module rather than more of it, because the two are different surfaces --
and because one file holding both crossed pylint's 1,000-line ceiling, which
is finding **balance:N-365**: in a corpus where the docstrings ARE the design
record, meeting that ceiling by cutting prose is the harm that finding names.

**The money DOOR and its schemas are unchanged and shared.**  Everything here
produces payloads :class:`~.statements.StatementBatchSchema` and
:class:`~.statements.StatementMatchSchema` load, so the Reconcile page is
graded by the SAME set of rules the review queue and the hand-build workbench
were, and reaches the same applier.  A second schema would be free to grade
``consent`` less strictly than the one beside it, which is what that field's
own docstring records having cost once.  *Those two pages went at plan step
``bank_import:X-gi-2``, which left their FORM READERS -- ``batch_payload`` and
``hand_match_payload`` -- with no caller in ``app/`` (finding
``bank_import:BI-479``); plan step ``bank_import:X-gi-3`` deleted both, so the
readers below are the only ones a request reaches.*

**Nothing here validates.**  Every value moved is a raw submitted string, so a
forged id, an unparseable figure and a destination naming no row are all the
schemas' to refuse -- one grader, in one place, with one error structure the
route already knows how to render.
"""

from app.schemas.validation._helpers import order_token_key
from app.schemas.validation.statements import (
    CATEGORY_PREFIX,
    DESTINATION_PREFIX,
    ENVELOPE_NAME_PREFIX,
    LEAVE_ALONE,
)
from app.services.statement_match import RECORD_AS_INCOME, Verb


#: What a RECONCILE card's OK checkbox is named with, its value being the bank
#: line it consents to.  Plan step ``bank_import:X-gj-1b``, rulings **R-HS**
#: and **R-FP**.
#:
#: **Presence IS the consent, and that is what makes ruling R-HS structural.**
#: R-HS pre-fills a justified suggestion -- the destination a standing rule
#: names arrives selected -- and then says *an untouched card is not
#: submitted: OK per card and Apply are the consent*.  Those two cannot both
#: be true of the retired review queue's form, where the destination select
#: IS the tick, so a pre-filled select on an untouched card would write a
#: purchase.  Here the select is only ever READ for a line whose OK checkbox
#: is in the body, and a browser submits a checkbox only when it is ticked.
_OK_FIELD = "ok"

#: What a card's four verb tabs are named with, keyed by its BANK LINE.  Plan
#: step ``bank_import:X-gj-1b``, rulings **R-HP** and **R-HW**.
#:
#: **The tab a card is left on IS the verb it is OK'd with**, which is the
#: locked direction's own "one primary button NAMED BY THE VERB".  They are a
#: RADIO GROUP rather than JavaScript state, so the panel needs no script to
#: switch tabs and the act the owner chose is a submitted fact rather than an
#: inference from which parameters happen to be present.
_VERB_PREFIX = "verb-"

#: What a card's MATCH tab ticks a candidate row with, keyed by its BANK LINE.
#: One token per row carrying its kind, id, reviewed figure and reviewed
#: revision (:class:`ReviewedRowField`).  **One token and never two parallel
#: lists**: those are desynchronised by a body submitting different lengths,
#: and one token cannot be desynchronised from itself.
_ROWS_PREFIX = "rows-"

#: What a card's MATCH tab carries its BANK LINE in, AS THE SCREEN SHOWED IT:
#: one token per line carrying its id and the day the bank said it was made
#: (:class:`~app.schemas.validation.statements.ReviewedLineField`), since
#: plan step ``bank_import:X-f6b-2`` (ruling **bank_import:R-BI14**).  The
#: KEY beside it locates the card's controls; the token is the line's
#: identity and reviewed state as one value, on ``rows-``' terms.  A page
#: drawn before this step posts no such field, and the item then names no
#: line: fail-closed, refused by the door as an empty side rather than read
#: off the key as a line nobody reviewed.
#:
#: **Read as a LIST, and that is a declared widening of what the wire
#: admits.**  Until this step the item's line WAS the key -- one line per
#: card, structurally -- and plan step ``bank_import:X-gn`` (a match naming a
#: second bank line) recorded that single-line key as the one thing in its
#: way.  The token is a list here for ``rows-``' reason (a match names N
#: lines in the service's own shape, ruling **R-FS**), so a crafted body
#: carrying two tokens under one key reaches the accept door as a two-line
#: group: an act the door validates whole (sums, sides, days, the owner's own
#: lines only, through ``load_lines``' account scope) and the screen does not
#: yet draw.  Nothing is bounded here, because the bound would be a fence
#: X-gn deletes; that step's premise sentence is now false and is reported
#: with this leaf.  A token naming a line OTHER than the key's is the same
#: act as ticking that line's own card, and is not refused for the same
#: reason ``rows-`` tokens the pane did not draw are not.
_LINE_PREFIX = "line-"

#: What a card's MATCH tab carries the owner's CONSENT in, keyed by its BANK
#: LINE: the difference the match was reviewed against AND the member it
#: lands on, as one :class:`~app.services.statement_match.ReviewedDifference`
#: token the server itself spelled, which the door re-derives and refuses if
#: the figure disagrees.
#:
#: **ONE field since plan step ``bank_import:X-gp``** (ruling **R-BI2**).  It
#: was ``residual-<line>``, the figure, beside ``difference_on-<line>``, the
#: member, and the door compared only the figure -- so the consent shown for
#: one act could be submitted beside a member that performed another.  The
#: pane's control now has one option per act, each valued with both halves,
#: and this is the name every one of them submits under.  **Neither old name
#: is read**, and that is fail-closed on purpose: a page drawn before this
#: step still posts ``residual-<line>``, and a reader that honoured it would
#: mint a row where the owner had named a member.  Ignored, the stale body
#: states no consent and the door refuses it with the remedies named.
#:
#: **Sent as nothing, never as an empty string, when nothing is chosen**: the
#: control is a set of radios or one box, and a browser submits a checkbox or
#: radio only when it is ticked.  The reader below still turns an empty value
#: into an absence, so a body carrying one is untouched rather than malformed
#: and the schema's ``load_default`` stays the one statement of what *nothing
#: was said* means.
_CONSENT_PREFIX = "consent-"


def _reconcile_creation(form, key: str, destination: str) -> dict:
    """Return one OK'd ADD card as the creation item the schema loads.

    Args:
        form: The request's ``MultiDict``.
        key: The bank line's id, as submitted.
        destination: What that line's destination control named.

    Returns:
        The item.  Raw strings: the schema is what reads them.
    """
    return {
        "line_id": key,
        "destination": destination,
        "envelope_name": form.get(f"{ENVELOPE_NAME_PREFIX}{key}", ""),
        "category_id": form.get(f"{CATEGORY_PREFIX}{key}", ""),
    }


def reconcile_match_payload(form, key: str) -> dict:
    """Return one Reconcile card's MATCH tab as :class:`StatementMatchSchema`
    loads it.

    **Two callers, one reader**: :func:`reconcile_payload` reads it for a card
    the owner OK'd, and the panel's own live-difference fragment reads it for
    the card being ticked -- so the figure on screen and the figure the door
    compares against come from ONE reading of one body, which is the rule
    :func:`~app.services.statement_match.preview_hand_build` exists to keep.

    Args:
        form: The request's ``MultiDict``.
        key: The bank line's id, as submitted.

    Returns:
        The item, carrying ``"consent"`` only where the consent control
        submitted a value.  **Omitted rather than sent as ``None``**, so the
        schema's own ``load_default`` is the one statement of what absence
        means -- and an EMPTY consent is untouched rather than malformed: a
        body carrying an empty control must not 400 the act over a field
        nobody filled in.

    """
    item = {
        "lines": form.getlist(f"{_LINE_PREFIX}{key}"),
        "rows": form.getlist(f"{_ROWS_PREFIX}{key}"),
    }
    consent = form.get(f"{_CONSENT_PREFIX}{key}")
    if consent:
        item["consent"] = consent
    return item


def reconcile_payload(form) -> "tuple[dict, tuple[str, ...]]":
    """Return one Reconcile pass as :class:`StatementBatchSchema` loads it.

    Plan step ``bank_import:X-gj-1b``.  **A second reader for one schema and
    one door**, which is what the retired ``hand_match_payload`` was for the
    workbench: the Reconcile page and the review queue applied the same acts
    through :func:`~app.services.statement_match.apply_reviewed`, and a second
    SCHEMA would be free to grade ``consent`` less strictly than the one
    beside it.  Since plan step ``bank_import:X-gi-2`` this is the only reader
    of the three a request reaches, and since ``X-gi-3`` the only one there
    is.
    What differs is the FORM, and the form shape lives here beside the schema
    that grades it.

    **It is keyed by BANK LINE and carries no rendered position at all**,
    where the retired ``batch_payload`` keyed its ticks by the proposal's
    position on the page.  That is ruling **R-HC**'s own lesson taken one surface further:
    a position is a property of the DOCUMENT, and two acts that share a
    namespace are one ``hx-include`` away from being unioned into an act
    nobody assembled.  One card, one line id, everywhere.

    **The OK checkbox is the consent and the VERB radio is the act**, so
    nothing here infers an arm from an absence -- the defect that made the
    existing-envelope arm unreachable from a browser at plan step X-f6a-3b.
    A card OK'd on a verb this build has no door for (TRANSFER), or on ADD
    with nothing chosen, produces no item and is REPORTED rather than dropped:
    see the second half of the return value.  *SKIP was in that list until
    plan step ``bank_import:X-gj-4b`` lit it*, and it is out rather than
    reworded: a card OK'd on SKIP now produces an item.

    **A card OK'd on a SKIP the panel rendered SHUT still produces one**, and
    that is deliberate.  Ruling **bank_import:R-JI** shuts the verb for a line
    a source files as paying an account the owner holds, and the tab is a
    LABEL for a radio the browser will happily select -- so the honest answer
    is the DOOR's refusal, quoted on the receipt in its own words, rather than
    this reader silently dropping the item into ``ok_with_no_act``, whose
    sentence (*without choosing what to do with them*) would be false of an
    owner who chose.

    Args:
        form: The request's ``MultiDict``.

    Returns:
        ``(payload, ok_with_no_act)``.  The payload is
        ``{"matches": [...], "creations": [...], "incomes": [...],
        "skips": [...]}``, in
        ascending bank-line order through the same
        :func:`~app.schemas.validation._helpers.order_token_key` every other
        reader here uses, because the receipt this order becomes is meant to
        read down the page.  ``ok_with_no_act`` is every submitted OK that
        named no act at all, as the raw keys, so the screen can say which
        cards it did nothing for instead of leaving a press unanswered.

    """
    matches: list = []
    creations: list = []
    incomes: list = []
    skips: list = []
    silent: list = []
    for key in sorted(set(form.getlist(_OK_FIELD)), key=order_token_key):
        verb = form.get(f"{_VERB_PREFIX}{key}", "")
        if verb == Verb.MATCH.value:
            matches.append(reconcile_match_payload(form, key))
            continue
        if verb == Verb.SKIP.value:
            # **One id and nothing to unpack**, which is ruling
            # **bank_import:R-GW**'s argument for the income arm below taken
            # one verb further: a skip is filed against nothing at all, so the
            # verb the tab submitted is the whole of what the wire has to say.
            skips.append({"line_id": key})
            continue
        destination = (
            form.get(f"{DESTINATION_PREFIX}{key}", LEAVE_ALONE)
            if verb == Verb.ADD.value else LEAVE_ALONE
        )
        if destination == RECORD_AS_INCOME:
            # **One id and nothing to unpack** (ruling **bank_import:R-GW**):
            # an income row is filed against no container, so the arm the
            # control names is the whole of what the wire has to say.
            incomes.append({"line_id": key})
        elif destination != LEAVE_ALONE:
            creations.append(_reconcile_creation(form, key, destination))
        else:
            silent.append(key)
    return (
        {
            "matches": matches, "creations": creations, "incomes": incomes,
            "skips": skips,
        },
        tuple(silent),
    )


# **NOTHING HERE READS AN "always, for this merchant" TICK**, and the absence
# is ruling **bank_import:R-IB** (developer, 2026-08-30).  This module carried
# ``_ALWAYS_PREFIX``, ``ReconcileRuleSchema``, ``ReconcileRuleBatchSchema`` and
# ``reconcile_rules_payload`` until then: a per-LINE checkbox posting a
# ``merchant_id``, graded by a second schema beside the money one.
#
# All four are gone rather than fixed, because the grain was the defect.  A
# standing rule is ONE fact per merchant and the card is one line, so the page
# asked one question 86 times on the developer's own pass; the tick had to be
# read BEFORE the money door ran, so a per-item refusal rolled back in its
# savepoint while the rule was written anyway; and the wire carried a
# ``merchant_id`` the server can derive from the line, which nothing checked
# the line agreed with.
#
# The offer is on the RECEIPT now, once per merchant, about what the door
# APPLIED -- and it posts to the door the review queue and the register
# already use, at the field names
# :func:`~app.schemas.validation.merchant_rules.rule_payload` reads.  So there
# is no reconcile-specific rule schema to write: three surfaces, one grader.
