"""
Shekel Budget App -- Statement Import Validation Schema

The upload form's one schema.  ``docs/coding-standards.md``: *"Marshmallow
schema for every state-changing route... No manual ``request.form.get()`` with
inline try/except."*  A file upload is no exemption -- the source field is a
closed enumeration, and validating it here rather than in the route is what
keeps the route's job to ownership, the unit of work and the flash.

**The offer set is the ADAPTER REGISTRY, resolved at validation time.**  It is
not a literal list here and not a second copy of the enum: a source whose
parser has not been written must be unofferable AND unsubmittable from the same
fact, or the two drift and a tampered form reaches a parser that does not exist.
"""

from marshmallow import (
    RAISE,
    Schema,
    ValidationError,
    fields,
    pre_load,
    validate,
    validates_schema,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _normalize_empty_inputs,
)
from app.services.statement_import import supported_sources
from app.services.statement_match import (
    NEW_ENVELOPE,
    PlaceIn,
    ReviewedDifference,
    ReviewedLine,
    ReviewedRow,
    parse_place_token,
)
from app.utils.digit_strings import parse_row_id


def _source_values() -> list[str]:
    """Return the source values a file may actually be imported as.

    Read through the registry rather than the enum so a member without a
    parser is refused here exactly as it is hidden on the form.

    Returns:
        The submittable ``StatementSourceEnum`` values.
    """
    return [member.value for member in supported_sources()]


class StatementUploadSchema(BaseSchema):
    """Validate the statement upload form's non-file field.

    The FILE itself is not a Marshmallow field: ``request.files`` is a
    different mapping from ``request.form``, and Marshmallow validates data
    rather than streams.  The route checks the file's presence and hands the
    bytes to the adapter, which is the only thing that can say whether they are
    a statement.

    Inherits :class:`~app.schemas.validation._helpers.BaseSchema`, whose
    ``unknown = EXCLUDE`` is what drops the form's ``csrf_token`` -- the same
    rule every other schema here takes, rather than a second spelling of it.
    """

    source = fields.String(
        required=True,
        validate=validate.OneOf(_source_values()),
        error_messages={
            "required": "Choose which kind of export this file is.",
        },
    )


#: The most acts ONE reviewed pass may ask for (plan step
#: ``bank_import:X-f6a-3c-2``).  **Measured, not chosen**: applying the
#: developer's own statement -- 124 proposals and 91 recordable lines -- takes
#: 5.80 s in the door on a production clone, of which 3.65 s is the one shared
#: derivation, so an item costs about 10 ms; the whole REQUEST, which derives a
#: second time to render its answer, measured 13.37 s.  Gunicorn's request
#: timeout is 120 s and Nginx's ``proxy_read_timeout`` is the same, so 500
#: items is about 9 s of door plus the response's own re-derivation -- well
#: inside the budget, at 2.3 times the acts the developer's own statement
#: offers.
#:
#: **43 ms is an AVERAGE over acts naming one to four rows, not a bound on
#: one.**  An item may name any number of lines and rows, so a hostile pass is
#: bounded by ``MAX_CONTENT_LENGTH`` (512 KB, ``app/config.py``), the one bound
#: on a request body (ruling **R-BAL218**), rather than by this.  *Through
#: Werkzeug 3.1.8 Flask's ``MAX_FORM_MEMORY_SIZE`` (500,000) was the tighter
#: bound on such a body, so this sentence named the wrong one until plan step
#: ``balance:X-dk`` moved the pin (finding BAL-598).*  This ceiling is what
#: keeps an ORDINARY pass inside the budget; that one is what keeps a crafted
#: one out, and
#: :func:`~app.services.statement_match._resolve.resolve_rows` is what keeps
#: either from naming a row the pass never offered.  *It said
#: ``_MAX_MATCH_MEMBERS`` bounded a member at 100 until plan step
#: ``bank_import:X-go`` deleted that cap; see
#: :class:`StatementMatchSchema`'s own note for the measurement that
#: replaces the one this paragraph used to quote.*
#:
#: **A bound that fires is REFUSED and said, never silently truncated.**  An
#: import may carry ``_secu_csv.MAX_LINES`` = 20,000 lines, so an account can
#: in principle offer more acts than this; the owner is told to apply the pass
#: in two rather than having half of what they ticked dropped without a word.
MAX_BATCH_ITEMS: int = 500

#: What the destination select submits when the owner picks "a new envelope".
#: A NAMED arm rather than an absent id, and that is plan step X-f6a-3c-2's
#: correction to the shape: the arm was "``transaction_id`` is missing" until
#: then, so the always-rendered, always-prefilled name box read as a
#: destination and made the existing-envelope arm unreachable from a browser
#: (three adversarial reviews, 2026-08-19).  One control now states which of
#: the three things the owner meant, so nothing has to be inferred from an
#: absence.
#:
#: **Re-exported from the SERVICE rather than declared here** (plan step
#: X-f6a-3d), because the service produces this value as well as reading it:
#: ``Placement.select_value`` answers what a line's control would be set to.
#: Two literals would be one wire value spelled twice, which is this package's
#: own root cause 1 -- and the import direction is the one this module already
#: takes for ``supported_sources``.

#: What that select submits when the owner has not picked anything, which is
#: its DEFAULT.  The line is left alone: it is not an act, so it never reaches
#: the schema --
#: :func:`~app.schemas.validation.statement_reconcile.reconcile_payload`
#: drops it.  **The default is the
#: do-nothing arm on purpose** (developer ruling, 2026-08-19): the select used
#: to default to the first envelope in the line's pay period, which on the
#: developer's own data has already CLOSED at a fixed figure on 78 of 91
#: lines, and the category select defaulted to the first active category
#: ("Auto: Property Tax").  One press per line hid that; one press for forty
#: would not.
LEAVE_ALONE: str = ""


class PurchaseDestination(fields.Field):
    """Where one bank line is to be recorded: an envelope, a new one, or a placed one.

    **One field because the owner makes one choice.**  The review screen's
    destination control is a single ``<select>`` whose options are the pay
    period's envelopes plus :data:`NEW_ENVELOPE` and, where a standing rule
    names a one-off envelope's definition with no row in this paycheck, the
    PLACE option (plan step ``balance:X-bi-7b`` leaf 7b-3, ruling
    **R-BAL24**) -- so the submission carries one value and this reads it
    into one of three things: an ``int`` naming an envelope, the
    :data:`NEW_ENVELOPE` string, or a :class:`~app.services.statement_match.PlaceIn`
    naming the definition to place a row of.  Splitting it into an id plus
    an implied arm is what let a form name both destinations at once.  The
    PLACE token is spelled once, in the service that produces it
    (``place_token`` / ``parse_place_token``), so the two directions cannot
    drift.

    **The id half is exactly as strict as :class:`RowId`**, through the same
    :func:`~app.utils.digit_strings.parse_row_id`: ``'٧'``, ``' 7 '``, ``'+7'``,
    ``'0_7'``, ``'007'``, ``'-7'`` and ``'0'`` name no envelope here either.  A
    second, laxer reading of a row id on a money door is exactly what plan step
    X-ae removed.
    """

    default_error_messages = {
        "invalid": "That is not a place a purchase can go.",
    }

    def _deserialize(self, value, attr, data, **kwargs):
        """Return the destination *value* names.

        Args:
            value: The submitted value.
            attr: The field name being loaded (marshmallow's contract).
            data: The whole payload being loaded (marshmallow's contract).
            **kwargs: Marshmallow's contract, unused.

        Returns:
            :data:`NEW_ENVELOPE`, a :class:`PlaceIn`, or the ``int`` id of an
            existing envelope.

        Raises:
            ValidationError: When *value* is none of the three.
        """
        if value == NEW_ENVELOPE:
            return NEW_ENVELOPE
        placed = parse_place_token(value)
        if placed is not None:
            return PlaceIn(template_id=placed)
        row_id = parse_row_id(value) if isinstance(value, str) else None
        if row_id is None:
            raise self.make_error("invalid")
        return row_id


class ReviewedRowField(fields.Field):
    """One app row a match names, AS THE SCREEN SHOWED IT.

    **The format is the service's, read through the service's own reader**
    (:meth:`~app.services.statement_match.ReviewedRow.from_token`).  The review
    template WRITES this token and this field READS it, and a second parse
    living here would be two spellings of one format with nothing in the tree
    failing when they diverged -- which is this arc's own root cause 1, on the
    one pair where the halves are a template and a validator.

    **Everything strict about it is strict in there**, so this field is the
    thin adapter it looks like: the row id and the version counter go through
    the same :func:`~app.utils.digit_strings.parse_row_id` every other id on
    this screen does, and the figure is matched against an explicit pattern
    before it reaches ``Decimal`` -- because a bare ``Decimal(raw)`` accepts
    ``"NaN"``, and a ``NaN`` figure compares unequal to every row, which would
    turn the staleness guard into a no-op that always passes.

    What arrives is a value object, not a mapping: the door's parameter type
    is :class:`~app.services.statement_match.ReviewedRow`, and loading straight
    into it is what keeps the route from assembling one field by field.
    """

    default_error_messages = {
        "invalid": "That is not a row this page could have shown you.",
    }

    def _deserialize(self, value, attr, data, **kwargs):
        """Return the reviewed row *value* names.

        Args:
            value: The submitted token.
            attr: The field name being loaded (marshmallow's contract).
            data: The whole payload being loaded (marshmallow's contract).
            **kwargs: Marshmallow's contract, unused.

        Returns:
            The :class:`~app.services.statement_match.ReviewedRow`.

        Raises:
            ValidationError: When *value* is not a token this app emitted.
        """
        try:
            return ReviewedRow.from_token(value)
        except ValueError as exc:
            raise self.make_error("invalid") from exc


class ReviewedLineField(fields.Field):
    """One bank line a match names, AS THE SCREEN SHOWED IT.

    :class:`ReviewedRowField`'s twin for the line side of a match (plan step
    ``bank_import:X-f6b-2``, ruling **bank_import:R-BI14**), and on its
    terms: the format is the service's, read through the service's own
    reader (:meth:`~app.services.statement_match.ReviewedLine.from_token`),
    so the template that writes the token and this field that reads it
    cannot be two spellings of one format.  The line id inside it goes
    through :func:`~app.utils.digit_strings.parse_row_id` and the day through
    one anchored ``YYYY-MM-DD`` pattern before ``date.fromisoformat`` sees
    it, so a spelling that library also reads (``20260610``) is refused as a
    body this app never rendered.

    What arrives is a value object, for :class:`ReviewedRowField`'s reason:
    the door's parameter type is
    :class:`~app.services.statement_match.ReviewedLine`.
    """

    default_error_messages = {
        "invalid": "That is not a bank line this page could have shown you.",
    }

    def _deserialize(self, value, attr, data, **kwargs):
        """Return the reviewed line *value* names.

        Args:
            value: The submitted token.
            attr: The field name being loaded (marshmallow's contract).
            data: The whole payload being loaded (marshmallow's contract).
            **kwargs: Marshmallow's contract, unused.

        Returns:
            The :class:`~app.services.statement_match.ReviewedLine`.

        Raises:
            ValidationError: When *value* is not a token this app emitted.
        """
        try:
            return ReviewedLine.from_token(value)
        except ValueError as exc:
            raise self.make_error("invalid") from exc


class ReviewedDifferenceField(fields.Field):
    """What a match's consent control submitted: the difference it was
    reviewed against, and the member it lands on, AS THE SCREEN SHOWED THEM.

    :class:`ReviewedRowField`'s sibling and, since plan step
    ``bank_import:X-gp``, its co-reader for BOTH halves of the value: the
    figure goes through
    :func:`~app.services.statement_match._submission.parse_figure` and the
    row through :meth:`~app.services.statement_match.ReviewedRow.from_token`,
    inside :meth:`~app.services.statement_match.ReviewedDifference
    .from_token`, so nothing submitted here is graded more laxly than the same
    thing submitted in ``rows``.  *It was ``ReviewedFigureField`` and read the
    figure alone until that step, beside a second field reading the member;
    see :attr:`StatementMatchSchema.consent` for why one value replaced two.*
    See :attr:`StatementMatchSchema.consent` too for what having two
    strictnesses on one form measurably cost.

    ``None`` passes through untouched, because absence is a state the schema
    names (``load_default``) rather than a spelling this reads.
    """

    default_error_messages = {
        "invalid": "That is not a figure this page could have shown you.",
    }

    def _deserialize(self, value, attr, data, **kwargs):
        """Return the reviewed difference *value* names.

        Args:
            value: The submitted string.
            attr: The field name being loaded (marshmallow's contract).
            data: The whole payload being loaded (marshmallow's contract).
            **kwargs: Marshmallow's contract, unused.

        Returns:
            The :class:`~app.services.statement_match.ReviewedDifference`.

        Raises:
            ValidationError: When *value* is not a token this app emitted.
        """
        try:
            return ReviewedDifference.from_token(value)
        except ValueError as exc:
            raise self.make_error("invalid") from exc


class StatementMatchSchema(BaseSchema):
    """Validate ONE accepted match: its bank lines, and its reviewed rows.

    **The door still re-derives everything it WRITES**, inside the same
    transaction, so a stale page cannot commit a number the database no longer
    holds.  What :class:`ReviewedRowField` carries is a PRECONDITION -- the
    figure and the revision the owner was looking at -- and the door refuses an
    item whose row has moved since (finding **N-336**, plan step
    ``bank_import:X-f6d-3``).  The distinction is the whole reason this schema
    may accept an amount at all: nothing here is a value to write, and a field
    that fed one to a settle verb would still be the channel the old docstring
    refused.

    **``rows`` REPLACED ``transaction_ids`` and ``entry_ids``.**  Those were one
    fact discriminated by table, and carrying the reviewed state beside them
    would have meant a second list joined back on the row id -- a parallel
    array, whose halves a crafted body desynchronises by submitting different
    lengths.  One token per row cannot be desynchronised from itself.

    Both lists are ``required=False`` with an empty default: which is populated
    depends on R-FS's shape, and the door's own ``_reject_empty_side`` is what
    refuses a submission naming nothing.  A schema arm refusing it too would be
    a second statement of one rule -- and the wrong one, because "at least one
    line AND at least one row" is a relation between two fields rather than a
    fact about either.

    **``lines`` REPLACED ``line_ids`` at plan step ``bank_import:X-f6b-2``**
    (ruling **bank_import:R-BI14**, finding **N-338**), on ``rows``' own
    terms one side over: a line and the day the screen showed the bank
    saying it was made are one fact, so they travel as one
    :class:`ReviewedLineField` token and the ids are derived from it.  The
    id inside the token goes through the same
    :func:`~app.utils.digit_strings.parse_row_id` every id on this screen
    does (plan step X-ae, finding **N-141**: ``Integer`` reads ``'١٢'``,
    ``' 12 '``, ``'+12'``, ``'1_0'``, ``'007'``, ``'-5'`` and ``'0'`` as ids
    -- two of which name no row at all), and the day through one anchored
    pattern before ``date.fromisoformat`` sees it.  The completeness gate in
    ``tests/test_schemas`` asserts both directly, as it does for
    :class:`ReviewedRowField`'s two counters.

    **It is NESTED inside :class:`StatementBatchSchema` since plan step
    X-f6a-3c-2**, because one submission now carries many of these: the
    proposals the owner ticked, and the group they built by hand, are the same
    act and reach the same door.
    """

    #: **NEITHER LIST CARRIES A LENGTH CEILING**, and its absence is plan step
    #: ``bank_import:X-go`` (developer, 2026-09-06).  ``_MAX_MATCH_MEMBERS =
    #: 100`` stood on both until then, justified as stopping a crafted
    #: submission asking "the accept door to re-derive and settle an account's
    #: whole history in one request".  Both halves of that were already false:
    #:
    #: * the DOMAIN bound exists where the offer set does.
    #:   :func:`~app.services.statement_match._resolve.resolve_rows` looks
    #:   every submitted row up in the pass's own ``unmatched_rows`` and
    #:   refuses anything else BY NAME, and
    #:   :func:`~app.services.statement_match._resolve.load_lines` does the
    #:   same for lines.  A body cannot reach an account's history at all; it
    #:   reaches at most what this pass offered, which the server derived;
    #: * the RESOURCE bound exists where the body does, and it is
    #:   ``MAX_CONTENT_LENGTH`` -- **524,288 bytes, ``app/config.py``, the ONE
    #:   bound on every request body** (ruling **R-BAL218**).  Werkzeug weighs
    #:   the body against it before this schema sees anything.
    #:   :data:`MAX_BATCH_ITEMS` beside them bounds the ACTS.
    #:
    #: **And it had started to contradict the screen.**  Since plan step
    #: ``bank_import:X-gi-1`` the scriptless MATCH pane offers EVERY unexplained
    #: row on the account as a tickbox
    #: (:class:`~app.services.statement_match.MatchReach`), bounded only by the
    #: span the recorded lines cover -- so an account holding more than 100 of
    #: them rendered more controls than this would accept, which is ruling
    #: **R-HW**'s *a control that cannot succeed*.  67 on the developer's own
    #: account when the pane was measured (2026-08-30), so it had not yet
    #: bitten; the list grows with the statement span.
    #:
    #: **THE WORST CASE IS NOT MULTIPLICATIVE, and that is what makes the
    #: deletion safe.**  ``MAX_CONTENT_LENGTH`` weighs the WHOLE BODY -- one
    #: ``content_length`` against one number, with no per-field accounting --
    #: so :data:`MAX_BATCH_ITEMS` items each naming unbounded members is
    #: UNCONSTRUCTIBLE: every item's ticks come out of one 524,288-byte
    #: budget.  **The body was always the tighter bound**: 500 items x 100
    #: members is 50,000 ticks, against a body that can carry 23,830, so the
    #: deleted cap could never bind in aggregate and the door's worst case is
    #: IDENTICAL before and after this step.
    #:
    #: Measured 2026-10-06 on Werkzeug 3.1.9, in two sessions of three runs
    #: per shape on a shared host, through the form parse,
    #: ``reconcile_payload`` and the batch schema's load, at the CRAFTED tick
    #: -- ``rows-1=purchase:1:1:1&`` is **22 bytes**, where a browser's
    #: ``rows-1234=transaction%3A4567%3A-178.32%3A1&`` is 43: ONE item
    #: carrying the whole 23,830-tick budget costs **51-63 ms**, and 500
    #: items sharing it (21,593 ticks, the longer line numbers costing bytes)
    #: cost **49-139 ms** (139 the first of one session's three runs; the
    #: other five read 49-62); each shape peaks at 7.1-7.2 MB.  Every row in
    #: either is then refused by ``resolve_rows``, because no pass offers
    #: them.
    #:
    #: *Two drafts of this note were wrong and a third stopped being true;
    #: the corrections are kept because each was a different mistake.  The
    #: first quoted 21.8 ms and asked only the single-item question -- the
    #: wrong question, since whether the item ceiling MULTIPLIES is what
    #: decides safety.  The second answered that but cited
    #: ``MAX_CONTENT_LENGTH``, which on the Werkzeug 3.1.6 then pinned was not
    #: the tighter bound on these bodies, and priced a BROWSER's tick while
    #: bounding a CRAFTED one, which understated the budget by 1.86x.  Both
    #: were found by adversarial review before this step was committed.  The
    #: third named Flask's ``MAX_FORM_MEMORY_SIZE`` (500,000) as the binding
    #: bound, measured 22,727 ticks at 50-54 ms and 57-132 ms, and was true
    #: until Werkzeug 3.1.9 stopped weighing a urlencoded body against it
    #: (plan step ``balance:X-dk``).  That measurement's harness was not
    #: recorded; this note's reads 48-60 ms and 48-138 ms at 500,000 bytes
    #: over the same two sessions, so the dates compare by figure only.  The
    #: figure the deleted block quoted -- 44,600 ticks in 0.36 s -- was
    #: measured against the retired ``match-<i>-rows`` wire shape and is not
    #: any of these.*
    #: Both lists, and the note above governs the pair: neither carries a
    #: ceiling, for one reason, so they are spelled the same way.
    lines = fields.List(ReviewedLineField(), required=False, load_default=list)
    rows = fields.List(ReviewedRowField(), required=False, load_default=list)
    #: What this match's CONSENT control submitted: the DIFFERENCE it states
    #: it was REVIEWED against, and the member it lands on, as ONE value
    #: (plan steps ``bank_import:X-f6d-4`` and ``X-gp``; rulings **R-FN** and
    #: **R-BI2**).
    #:
    #: **ONE field where there were two, and that is the whole of plan step
    #: X-gp.**  This was ``residual``, the figure, beside ``difference_on``,
    #: the member, until then -- and the door compared only the figure, so
    #: the consent rendered under *record it as a row with no category* could
    #: be submitted beside a member that re-priced a budget row instead: two
    #: acts under one agreement, which ruling **R-IV** had accepted on the
    #: strength of a DOM event and ``X-gi-2a`` falsified.  The pane draws one
    #: control whose options ARE the acts and each option's value is this
    #: field's whole value, so there is no second field left to pair
    #: differently.  **The row half is graded by exactly the reader a member
    #: of ``rows`` is** -- :class:`ReviewedRowField`'s own -- so a body cannot
    #: land a difference on something the row list could not have contained,
    #: and ``resolve_rows`` compares the two as whole reviewed values.
    #:
    #: **It used to be absent on every proposal the app itself offers**, and
    #: plan step ``bank_import:X-gj-1b`` inverted that: the accept door
    #: exempts no shape now, so a proposal card renders this as a HIDDEN input
    #: carrying :func:`app.jinja_filters.stated_difference` and every ticked
    #: item submits one.  ``load_default=None`` remains, because absence is
    #: still a real thing a body can say and the DOOR is where it is answered
    #: -- refused where a difference would be written, permitted where none
    #: would be
    #: (``app.services.statement_match._variance._reject_unaccepted_difference``).
    #: A ``required=True`` here would turn a scriptless owner's balanced
    #: hand-built group into a 400 over the whole pass; and a value naming NO
    #: member is what every surface but the Reconcile card's MATCH pane
    #: sends, meaning what an absent attribution always meant -- a match
    #: naming ONE row is answered by ruling **R-GD(a)**'s determinacy, and a
    #: group naming no member mints **R-FN**'s ordinary row.
    #:
    #: **Read through the service's own strict reader, exactly as
    #: :class:`ReviewedRowField` beside it is.**  A first version declared the
    #: figure ``fields.Decimal(places=2)``, and an adversarial review measured
    #: what that cost on 2026-08-23: marshmallow quantizes with the default
    #: context rounding, which is ``ROUND_HALF_EVEN`` -- the mode
    #: :mod:`app.utils.money` says must never be reached implicitly through a
    #: bare ``.quantize`` -- so ``"0.054"`` was silently REPAIRED into
    #: agreement with a true difference of ``0.05``, on the one field the
    #: design says must be exact.  It also took ``"1_0"``, ``"+0.05"`` and
    #: ``" 0.05 "``, which the row token on the same form deliberately refuses:
    #: two strictnesses for two money strings on one door.
    #:
    #: **No ``Range`` bound, and the reason is NOT the one a first version
    #: gave.**  That version said a bound was unnecessary because the
    #: difference is "bounded by the ``Numeric(12, 2)`` columns it is summed
    #: from", which is arithmetically false -- a match may name any number of
    #: them per side.  The bound lives at the
    #: DOOR instead
    #: (``app.services.statement_match._variance._reject_unstorable``), where
    #: the sum it must bound actually exists; a bound here could only refuse a
    #: figure the door was going to refuse anyway, since nothing is written
    #: unless this equals the door's own derivation.
    consent = ReviewedDifferenceField(required=False, load_default=None)


class StatementMatchReleaseSchema(BaseSchema):
    """Validate the id of the match being released.

    Separate from :class:`StatementMatchSchema` because it is a different act
    on a different object: one names a correspondence to create, the other
    names an act to delete, and a single schema carrying both would have every
    field optional and refuse nothing.
    """

    match_id = RowId(
        required=True,
        error_messages={"required": "Which match do you want to undo?"},
    )


class StatementSkipReleaseSchema(BaseSchema):
    """Validate the id of the skip being undone (plan step X-gj-4c-2).

    Its own schema for the reason :class:`StatementMatchReleaseSchema` is
    separate from :class:`StatementMatchSchema`, applied one act over: a match
    and a skip are different objects in different tables, and one schema
    carrying both ids would have each optional and refuse a submission naming
    neither.

    **The id is a :class:`~app.schemas.validation._helpers.RowId`, not
    ``fields.Integer``** (plan step X-ae, finding **N-141**): it names a ROW,
    and ``Integer`` reads ``'١٢'``, ``' 12 '``, ``'+12'``, ``'1_0'``,
    ``'007'``, ``'-5'`` and ``'0'`` as ids -- two of which name no row at all.
    """

    skip_id = RowId(
        required=True,
        error_messages={"required": "Which skip do you want to undo?"},
    )


class AgreementDaySchema(BaseSchema):
    """The one day the books-vs-bank drill-down is asked about.

    A GET fragment's only argument, declared as a schema rather than parsed
    inline so the day it reads is a ``fields.Date`` under the project's one set
    of rules -- and so a second reader of this URL cannot invent a second
    spelling of what a day is.
    """

    day = fields.Date(required=True)


class StatementImportDeleteSchema(BaseSchema):
    """Validate the id of the import being deleted (plan step X-f6a-4).

    Its own schema for the reason :class:`StatementMatchReleaseSchema` is
    separate from :class:`StatementMatchSchema`: these are different acts on
    different objects, and one schema carrying both would have every field
    optional and refuse nothing.

    **The id is a :class:`~app.schemas.validation._helpers.RowId`, not
    ``fields.Integer``** (plan step X-ae, finding **N-141**).  It names a ROW,
    and ``Integer`` reads ``'١٢'``, ``' 12 '``, ``'+12'``, ``'1_0'``, ``'007'``,
    ``'-5'`` and ``'0'`` as ids -- two of which name no row at all.
    """

    import_id = RowId(
        required=True,
        error_messages={"required": "Which import do you want to delete?"},
    )


class StatementPurchaseSchema(BaseSchema):
    """Validate ONE bank line becoming a purchase (plan step X-f6a-3b).

    **One line and one destination, and no figure at all.**  The amount and
    both days come from the recorded LINE inside the same transaction
    (:mod:`app.services.statement_match._create`), so a stale page cannot
    commit a number the bank did not state -- the same reason
    :class:`StatementMatchSchema` beside it carries ids only.

    **The destination is ONE field naming one of two arms**
    (:class:`PurchaseDestination`), which is plan step X-f6a-3c-2's correction.
    It was a nullable ``transaction_id`` whose ABSENCE meant "make a new
    envelope", so the always-rendered name box read as a destination of its own
    and the existing-envelope arm was unreachable from a browser.  A control
    that says which arm was chosen cannot be misread; an absence can.

    The name and the category are PARAMETERS OF ONE ARM rather than a
    destination, and **whether that arm is COMPLETE is the door's question,
    not this schema's** (plan step X-f6a-3c-2).  It was a
    ``@validates_schema`` rule here, which was right while one POST was one
    act and wrong the moment a POST became a whole pass: a nested error refuses
    the ENTIRE payload, so an owner who picked "a new envelope" on one line and
    left its category untouched -- the form's own default, and the ordinary
    slip -- lost 124 proposals and 90 good creations to it.  That contradicts
    the ruled failure policy in the one case the form itself produces.
    ``_create._reject_incomplete_new_envelope`` owns it now, so it is one
    item's refusal like every other.

    **A MALFORMED payload is still a pass-level refusal, and that is the right
    asymmetry**: an id that names no row, or a body naming more acts than one
    request may carry, is a fact about the SUBMISSION rather than about an act
    the owner reviewed -- no browser of ours produces one, so there is no pass
    to salvage.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    line_id = RowId(
        required=True,
        error_messages={"required": "Which statement line are you recording?"},
    )
    destination = PurchaseDestination(
        required=True,
        error_messages={"required": "Where should this purchase go?"},
    )
    #: Defaulted from what the BANK called the merchant and editable, because
    #: the bank's own words are the only description of this spending that
    #: exists.  The 200 matches ``transactions.name``.
    envelope_name = fields.String(
        required=False, load_default=None,
        validate=validate.Length(min=1, max=200),
    )
    category_id = RowId(required=False, load_default=None)


class StatementIncomeSchema(BaseSchema):
    """Validate ONE bank line becoming an income row (ruling **bank_import:R-GW**).

    **One line id and nothing else, and the emptiness is the design.**  Its
    sibling :class:`StatementPurchaseSchema` needs a destination because a
    purchase is filed against a container the owner chooses between; an income
    row is filed against nothing, so there is no arm to name and no name or
    category to state.  The figure and the day come from the recorded LINE
    inside the same transaction (:mod:`app.services.statement_match._income`),
    so a stale page cannot commit a number the bank did not state.

    **A one-field schema still earns its place** rather than the list holding
    bare ids: it is what refuses a forged ``line_id`` -- a negative, a
    thousand-digit string, a word -- through the same :class:`RowId` every
    other id on this pass is graded by, and it is the shape a second fact
    about a deposit would be added to.

    **Which lines may be recorded this way is the DOOR's question, not this
    schema's**: that the line is on this account, that nothing already claims
    it, and that it is money ARRIVING are all facts about the database rather
    than about the submission, and a nested error here would refuse the ENTIRE
    pass -- the failure mode :class:`StatementPurchaseSchema` records having
    cost 124 proposals and 90 creations once.
    """

    line_id = RowId(
        required=True,
        error_messages={"required": "Which statement line are you recording?"},
    )


class StatementSkipSchema(BaseSchema):
    """Validate ONE bank line being recorded as explained by nothing.

    Ruling **bank_import:R-JG**, plan step ``bank_import:X-gj-4b``.

    **One line id and nothing else, and the emptiness goes further than its
    sibling's.**  :class:`StatementIncomeSchema` has one field because an
    income row is filed against no container; a SKIP is filed against nothing
    at all -- no container, no row, no figure and no reason, because the
    decision IS *explained by nothing*.  There is no second fact about a skip
    for this schema to grow.

    **A one-field schema still earns its place**, for its sibling's reason: it
    is what refuses a forged ``line_id`` -- a negative, a thousand-digit
    string, a word -- through the same :class:`RowId` every other id on this
    pass is graded by, before any of it reaches a door.

    **Which lines may be skipped is the DOOR's question, not this schema's**:
    that the line is on this account, that no accepted match already answers
    it, and that its merchant is not one a source files as paying an account
    the owner holds (ruling **bank_import:R-JI**) are all facts about the
    database rather than about the submission -- and a nested error here would
    refuse the ENTIRE pass, which is the failure mode
    :class:`StatementPurchaseSchema` records having cost 124 proposals and 90
    creations once.
    """

    line_id = RowId(
        required=True,
        error_messages={"required": "Which statement line are you skipping?"},
    )


class StatementBatchSchema(Schema):
    """Validate ONE reviewed pass: every match ticked, every line named.

    Plan step ``bank_import:X-f6a-3c-2``.  The review screen posts what the
    owner reviewed in one request rather than 215, so the payload is a LIST of
    acts and each act is validated by the schema that already owned it -- there
    is no second statement here of what a match or a purchase submission is.

    **It is a plain :class:`marshmallow.Schema`, not a
    :class:`~app.schemas.validation._helpers.BaseSchema`.**  That base exists to
    drop a FORM's ``csrf_token`` with ``unknown = EXCLUDE``; this schema never
    sees a form, because
    :func:`~app.schemas.validation.statement_reconcile.reconcile_payload` has
    already turned one into these lists.  Inheriting it would silently swallow a key this schema
    does not declare, on the one payload that carries every act in a pass.

    **The ceiling is on the SUM and is stated ONCE**
    (:data:`MAX_BATCH_ITEMS`), because the screen's own offer set is bounded
    by what an import may carry and a crafted submission is bounded by nothing.
    A per-list ``Length`` beside it was a second statement of one rule and the
    WRONG one: two lists with their own ceilings admit twice the work either
    allows, and marshmallow's generic "Longer than maximum length 500" fired
    first and named no remedy -- on a bound whose whole point is to tell the
    owner to apply the pass in two goes.
    """

    class Meta:
        """Refuse an unknown key rather than dropping it silently."""

        unknown = RAISE

    matches = fields.List(
        fields.Nested(StatementMatchSchema), required=False, load_default=list,
    )
    creations = fields.List(
        fields.Nested(StatementPurchaseSchema), required=False,
        load_default=list,
    )
    #: The lines of money COMING IN the owner ticked (ruling **bank_import:R-GW**).  **Its
    #: own list rather than a third arm of ``creations``**: that schema
    #: REQUIRES a destination, and an income row has none, so folding them
    #: would mean a required field that is meaningless for half its members --
    #: and the two reach different doors writing different shapes.
    incomes = fields.List(
        fields.Nested(StatementIncomeSchema), required=False,
        load_default=list,
    )
    #: The lines the owner decided are explained by NOTHING (ruling
    #: **bank_import:R-JG**, plan step ``bank_import:X-gj-4b``).  **Its own
    #: list beside the three above** for the reason ``incomes`` is its own:
    #: it reaches a different door, which writes a different TABLE
    #: (``budget.statement_line_skips``) and no money at all.  Folding it into
    #: any of them would mean a required field that is meaningless for its
    #: members -- a skip names no destination and no row.
    skips = fields.List(
        fields.Nested(StatementSkipSchema), required=False,
        load_default=list,
    )

    @validates_schema
    def validate_pass_is_not_too_large(self, data, **kwargs):
        """Refuse a pass asking for more acts than one request may carry.

        The ONE bound, over both kinds, because what a request's time budget
        cares about is how many acts run rather than which sort they are.

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's context, unused.

        Raises:
            ValidationError: When the pass names more than
                :data:`MAX_BATCH_ITEMS` acts in total.
        """
        total = (
            len(data.get("matches", ()))
            + len(data.get("creations", ()))
            # **Every kind of act, and the sum is why this arm is stated over
            # a list rather than per field**: four lists each carrying
            # their own ceiling would admit FOUR TIMES what the bound says.
            # *This comment said "a third list" until plan step
            # ``bank_import:X-gj-4b`` added the fourth*, which is a claim about
            # the lines below it rather than a turn of phrase.
            + len(data.get("incomes", ()))
            + len(data.get("skips", ()))
        )
        if total > MAX_BATCH_ITEMS:
            raise ValidationError(
                f"That is {total:,} things to apply at once, and this page "
                f"applies at most {MAX_BATCH_ITEMS:,} in one pass.  Untick "
                f"some and apply them in two goes -- nothing was changed.",
                field_name="matches",
            )


#: What a submitted destination carries, keyed by its BANK LINE's id.  Keyed
#: that way rather than by a rendered position because a destination names one
#: LINE, and paired arrays would depend on the browser submitting several lists
#: of equal length -- which a crafted body need not do.
#:
#: **It had TWO readers and now has ONE**, and the placement argument that put
#: it here is spent: it was stated here rather than twice because the review
#: queue's ``_creation_items`` read it beside the Reconcile page's
#: :func:`~.statement_reconcile.reconcile_payload`, and plan step
#: ``bank_import:X-gi-3`` deleted the first.  These three constants now have
#: exactly one consumer and it is the OTHER module, so rule 14's *move the
#: leaf* says they belong beside it.  **Not moved here**, because a constant
#: move is a change to two modules' surfaces and this step is a deletion --
#: adversarial review 2026-09-06 named it, and it is recorded rather than
#: silently left.
DESTINATION_PREFIX = "destination-"
ENVELOPE_NAME_PREFIX = "envelope_name-"
CATEGORY_PREFIX = "category_id-"
