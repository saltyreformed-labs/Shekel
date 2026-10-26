"""What a reviewed statement pass looks like on the wire -- plan step X-f6a-3c-2.

**This is where this arc's most expensive defect lived.**  At plan step
X-f6a-3b the create form's destination arm was spelled as an ABSENT
``transaction_id``, and the always-rendered, always-prefilled name box therefore
read as a destination of its own -- so the existing-envelope arm was
unreachable from a browser and the door refused all 66 of the developer's lines
that had one.  Three independent adversarial reviews found it; the route test
that should have caught it posted a payload no browser sends.

So this module grades the FORM SHAPE itself, against the two facts that made
that defect possible:

* a browser submits every control it renders, whether or not the owner touched
  it -- so an untouched control must be recognisable as untouched;
* which arm was chosen is a fact the form has to STATE, never one a reader
  infers from an absence.

The SCHEMAS are live and this module grades them.  **The READERS beneath it
changed twice.**  It drove ``batch_payload`` and ``hand_match_payload`` -- the
review queue's and the workbench's -- until plan step ``bank_import:X-gi-2``
deleted both pages, which left those two readers with no caller in ``app/``
(finding **bank_import:BI-479**); ``bank_import:X-gi-3`` deleted them.  Every
case here now drives the LIVE readers,
:func:`~app.schemas.validation.statement_reconcile.reconcile_payload` and
:func:`~app.schemas.validation.statement_reconcile.reconcile_match_payload`
-- with ONE declared exception, ``TestAMatchMayNameAsManyRowsAsThePassOFFERS
.test_a_match_naming_more_than_a_hundred_LINES_loads_too``, which hand-builds
its payload because no reader can produce it: ``reconcile_match_payload``
emits exactly one ``line_ids`` member on every path there is.  It grades the
shape ``bank_import:X-gn`` will make submittable, and it is named here rather
than left to contradict this paragraph silently.

**The split was not the deletion BI-479 prescribed, and the difference is
measured.**  That row said the classes reaching the schemas THROUGH the two
readers go with the readers.  Taken literally that drops live coverage: the
three cases below on the NEW-ENVELOPE arm and on category optionality grade
:class:`~app.schemas.validation.statements.StatementPurchaseSchema` loading a
shape ``tests/test_schemas/test_statement_reconcile.py`` never loads, and they
are the arc's most expensive defect.  What went is every class and case whose
SUBJECT was the dead wire shape itself -- ``TestOnlyWhatWasTickedIsAnAct``,
whose subject is the ``apply`` index, and four cases that file already covers
through the live reader.

*A first draft of this paragraph also claimed ``TestThePassIsBounded``, and
both adversarial reviews of that draft measured the claim false: its subject is
:data:`~app.schemas.validation.statements.MAX_BATCH_ITEMS`, a LIVE rule, and
the reconcile file exceeds that ceiling with skips and incomes only -- so the
matches and creations terms of its sum were left unmutated on a door that MOVES
MONEY.  It is restored below, re-pointed.  The sentence was written last, about
the author's own deletion, which is the one claim a green suite cannot check.*
"""

from decimal import Decimal

import pytest
from marshmallow import ValidationError
from werkzeug.datastructures import MultiDict

from app.schemas.validation.merchant_rules import (  # pylint: disable=protected-access
    _MAX_RULE_ITEMS,
    _NAMES_NOTHING,
    _PREFIXED_ANSWERS,
    ALWAYS_ASK,
    NEVER,
    NOT_SAID,
    MerchantRuleBatchSchema,
    SubmittedAnswer,
    rule_payload,
)
from app.services.statement_match import (
    ReviewedDifference,
    ReviewedRow,
    RowKind,
    RuleAnswer,
)
from app.schemas.validation.statements import (
    StatementMatchSchema,
    NEW_ENVELOPE,
    StatementBatchSchema,
)
from app.schemas.validation.statement_reconcile import (
    reconcile_match_payload,
    reconcile_payload,
)


def _form(pairs):
    """Return a ``MultiDict`` the way a browser would submit one."""
    return MultiDict(pairs)


#: The bank line every one-card body below is about.  A card's MATCH fields are
#: keyed by it, which is what makes them one card's rather than a position's.
_LINE = "11"


def _load_one_card(form):
    """Regroup and validate ONE Reconcile card's MATCH tab.

    Plan step ``bank_import:X-gi-3``.  **The live reader**: this went through
    the workbench's ``hand_match_payload`` until that page's reader was
    deleted, and through the review queue's ``batch_payload`` before
    ``bank_import:X-gf-3b``.  The assertions are unchanged both times; what
    changes is which reader receives the body, and a class grading a payload
    nothing submits is one that has stopped testing what it names.

    Args:
        form: The request's ``MultiDict``.

    Returns:
        The loaded match.

    Raises:
        ValidationError: With marshmallow's own error structure.
    """
    return StatementMatchSchema().load(reconcile_match_payload(form, _LINE))


def _load(form):
    """Regroup and validate one submitted pass, through the live reader.

    Args:
        form: The request's ``MultiDict``.

    Returns:
        The loaded payload.  The reader's second return value -- the OK'd
        cards that named no act -- is
        ``test_statement_reconcile.TestAPressIsNeverLeftUnanswered``'s subject
        and is dropped here.

    Raises:
        ValidationError: With marshmallow's own error structure.
    """
    return StatementBatchSchema().load(reconcile_payload(form)[0])


class TestTheDifferenceTheOwnerAccepted:
    """Plan step ``bank_import:X-f6d-4``: the one field that is not per-row.

    The consent control submits nothing until an option is ticked, and the
    scriptless placeholder is rendered DISABLED with ``value=""``, so an
    unticked group, and a browser with no JavaScript, both submit nothing at
    all.  What this grades is that the three states are distinguishable on the
    wire and that a hostile spelling cannot reach the door as a figure.

    **The field carries the member beside the figure since plan step
    ``bank_import:X-gp``** (ruling **R-BI2**): ``residual-<line>`` and
    ``difference_on-<line>`` became one ``consent-<line>`` whose value is a
    :class:`~app.services.statement_match.ReviewedDifference` token.  Every
    case below that graded the figure grades the same figure inside that
    value, and the cases on the row half are new.

    **It grades ``reconcile_match_payload`` since plan step
    ``bank_import:X-gi-3``**, and ``hand_match_payload`` before that, and
    ``batch_payload``'s ``match-hand-*`` before that -- each time because the
    surface that emitted the old shape was deleted.  The assertions are
    unchanged every time; the reader beneath them is the one that now receives
    this body.  A class grading a payload nothing submits is a class that has
    stopped testing what it names, which is what an adversarial test-quality
    review found on 2026-08-28.
    """

    def test_an_unticked_group_carries_NONE(self):
        """Absence is a state the SCHEMA names, not one the reader invents.

        The reader omits the key rather than sending ``None``, so the
        default lives in exactly one place.
        """
        loaded = _load_one_card(_form([
            ("rows-11", "transaction:42:-180.00:1"),
        ]))

        assert loaded["consent"] is None

    def test_a_ticked_group_carries_the_figure_it_showed(self):
        """A signed decimal, read into a ``Decimal`` for the door to compare.

        **Naming no member**, which is what *record it as a row with no
        category* submits and what every surface but the MATCH pane submits.
        """
        loaded = _load_one_card(_form([
            ("rows-11", "transaction:42:2473.38:1"),
            ("rows-11", "transaction:43:100.00:1"),
            ("consent-11", "0.05"),
        ]))

        assert loaded["consent"] == ReviewedDifference(figure=Decimal("0.05"))

    def test_a_consent_naming_a_MEMBER_carries_both_halves(self):
        """Plan step ``bank_import:X-gp``: the figure AND the member, as one.

        The member is read through the SAME reader a ``rows`` entry is, so
        the value the door compares against the row list is the whole
        reviewed row rather than a bare id.
        """
        loaded = _load_one_card(_form([
            ("rows-11", "transaction:42:2473.38:1"),
            ("rows-11", "transaction:43:100.00:1"),
            ("consent-11", "0.05@transaction:42:2473.38:1"),
        ]))

        assert loaded["consent"] == ReviewedDifference(
            figure=Decimal("0.05"),
            on_row=ReviewedRow(
                kind=RowKind.TRANSACTION, row_id=42,
                cash_amount=Decimal("2473.38"), version_id=1,
            ),
        )

    @pytest.mark.parametrize("spelling, why", [
        ("0.05@transaction:٤٢:2473.38:1", "a non-ASCII digit run as the id"),
        ("0.05@transaction:42:2473.38:007", "a zero-padded revision"),
        ("0.05@transaction:42:NaN:1", "a NaN figure inside the member"),
        ("0.05@ledger:42:2473.38:1", "a kind of row that does not exist"),
        ("0.05@transaction:42:2473.38", "a member with three fields"),
        ("0.05@transaction:42:2473.38:1@purchase:7:-9.99:1", "two members"),
        ("@transaction:42:2473.38:1", "a member with no figure"),
        ("0.05@", "a separator with nothing after it"),
    ])
    def test_a_hostile_MEMBER_is_refused(self, spelling, why):
        """The row half is exactly as strict as a ``rows`` entry.

        Each of these would be refused inside ``rows``; a laxer reading here
        would let a body land a difference on a row the row list could not
        have carried, which is the shape ruling **R-IA** measured at
        `$2,572.36` one field over.

        Args:
            spelling: What a crafted body sends.
            why: What is wrong with it, for the failure message.
        """
        with pytest.raises(ValidationError) as caught:
            _load_one_card(_form([
                ("rows-11", "transaction:42:2473.38:1"),
                ("consent-11", spelling),
            ]))

        assert "consent" in caught.value.messages, why

    def test_a_NEGATIVE_difference_is_read_as_one(self):
        """The bank took more than the rows say, which is the expense arm."""
        loaded = _load_one_card(_form([
            ("rows-11", "transaction:42:-180.00:1"),
            ("consent-11", "-0.06"),
        ]))

        assert loaded["consent"] == ReviewedDifference(figure=Decimal("-0.06"))

    @pytest.mark.parametrize("spelling, why", [
        ("NaN", "compares unequal to every figure, so a guard becomes a no-op"),
        ("Infinity", "not a figure any row can hold"),
        ("1e1000000000", "an exponent Decimal cannot quantize"),
        ("0.05; DROP", "not a number at all"),
        ("1_0", "a spelling the row token on this same form refuses"),
        ("+0.05", "a leading plus the row token refuses"),
        (" 0.05 ", "surrounding whitespace the row token refuses"),
        ("0.05\n", "a trailing newline, which a `$` anchor matched before"),
    ])
    def test_a_hostile_spelling_is_REFUSED(self, spelling, why):
        """Each of these reaches the field from a crafted POST.

        A ``NaN`` is the one that matters most and it is why this class exists:
        the door compares the accepted figure with its own using ``!=``, and
        ``Decimal("NaN") != x`` is TRUE for every x -- so a ``NaN`` slipping
        through would not open the door, it would jam it shut on every group,
        which is a denial dressed as a safety.  The other spellings are the
        ones this project has already paid to learn about
        (``_submission._FIGURE``).

        Args:
            spelling: What a crafted body sends.
            why: What is wrong with it, for the failure message.
        """
        with pytest.raises(ValidationError) as caught:
            _load_one_card(_form([
                ("rows-11", "transaction:42:-180.00:1"),
                ("consent-11", spelling),
            ]))

        assert "consent" in caught.value.messages, why

    def test_a_SUB_CENT_figure_is_read_verbatim_and_left_to_the_door(self):
        """The reader is about the FORMAT; the VALUE is the door's question.

        This is where a quantizer went wrong.  ``fields.Decimal(places=2)``
        REPAIRED ``0.054`` into ``0.05`` and it then passed as consent for a
        true difference of ``0.05`` -- a half-cent tolerance on the one field
        the design says is exact, using the rounding mode
        :mod:`app.utils.money` forbids.  Read verbatim, it simply is not the
        door's own figure, and the door says so
        (``test_residual.test_what_the_door_refuses.TestTheFigureTheOwnerAcceptedIsReconciled``).
        """
        loaded = _load_one_card(_form([
            ("rows-11", "transaction:42:-180.00:1"),
            ("consent-11", "0.054"),
        ]))

        assert loaded["consent"] == ReviewedDifference(figure=Decimal("0.054"))

    def test_an_EMPTY_consent_box_is_UNTOUCHED_rather_than_malformed(self):
        """This module's founding principle, on the newest control.

        A browser submits every control it renders, so an untouched one has to
        be recognisable as untouched.  The panel keeps ``value=""`` and
        ``disabled`` in lockstep so no browser sends this -- and a body that
        does would otherwise 400 the WHOLE pass over a field nobody filled in.
        """
        loaded = _load_one_card(_form([
            ("rows-11", "transaction:42:-180.00:1"),
            ("consent-11", ""),
        ]))

        assert loaded["consent"] is None

    def test_a_REPEATED_consent_cannot_desynchronise_the_item(self):
        """One consent per item, so a repeated key keeps the first.

        Whichever it keeps still has to EQUAL the difference the door derives
        from the same submission's rows, so no repeated key can choose what
        gets written -- which is why this reads with ``get`` rather than
        growing a list the schema would then have to reconcile.
        """
        loaded = _load_one_card(_form([
            ("rows-11", "transaction:42:-180.00:1"),
            ("consent-11", "0.05"),
            ("consent-11", "-999.00"),
        ]))

        assert loaded["consent"] == ReviewedDifference(figure=Decimal("0.05"))

    def test_the_OLD_two_field_shape_states_NO_consent(self):
        """A page drawn before plan step ``bank_import:X-gp`` fails CLOSED.

        A stale tab still posts ``residual-<line>`` and ``difference_on-<line>``.
        A reader that honoured the first would mint a row where the owner had
        named a member in the second; ignoring both leaves the body stating no
        consent, which the door refuses with the remedies named.  **The
        control**: the same body under the new name loads a consent, so this
        is not passing because nothing loads.
        """
        stale = _load_one_card(_form([
            ("rows-11", "transaction:42:2473.38:1"),
            ("rows-11", "transaction:43:100.00:1"),
            ("residual-11", "0.05"),
            ("difference_on-11", "transaction:42:2473.38:1"),
        ]))
        current = _load_one_card(_form([
            ("rows-11", "transaction:42:2473.38:1"),
            ("rows-11", "transaction:43:100.00:1"),
            ("consent-11", "0.05@transaction:42:2473.38:1"),
        ]))

        assert stale["consent"] is None
        assert current["consent"] is not None



class TestWhatTheDestinationSelectNAMES:
    """The developer's ruling of 2026-08-19, and the X-f6a-3b defect's fix.

    One control says which of three things the owner meant, and this grades
    what :class:`~app.schemas.validation.statements.StatementPurchaseSchema`
    loads for each: an existing envelope, the NEW arm named rather than
    inferred, and a category that is the DOOR's question on one arm and absent
    on the other.  ``grep -rn StatementPurchaseSchema tests/`` reaches this
    class and nothing else.

    **It was ``TestTheDestinationSelectIsTheTick`` until plan step
    ``bank_import:X-gi-3``**, and the rename is a correction rather than
    tidying: on the retired review queue the select WAS the tick, and on the
    Reconcile page it is not -- the OK checkbox is
    (:data:`~app.schemas.validation.statement_reconcile._OK_FIELD`).  The case
    that graded the select-is-the-tick half went with that reader, because
    ``test_statement_reconcile.TestNothingIsAnActWithoutItsOwnOK`` grades the
    live spelling of it.  A class named for a rule its own subject no longer
    implements is the shape adversarial review 2026-09-06 named.
    """

    @staticmethod
    def _line_fields(line_id, destination, name="Walmart", category="3"):
        """Return exactly what one OK'd ADD card submits.

        **The OK checkbox and the VERB radio travel with it** since plan step
        ``bank_import:X-gi-3`` re-pointed this class at the live reader: on the
        Reconcile page the tick is the checkbox and the act is the radio, where
        on the retired review queue the destination select was both.  That
        difference is ``test_statement_reconcile``'s own subject; what this
        class grades is what the SCHEMA does with the destination once a card
        is OK'd, which is unchanged.
        """
        return [
            ("ok", str(line_id)),
            (f"verb-{line_id}", "add"),
            (f"destination-{line_id}", destination),
            (f"envelope_name-{line_id}", name),
            (f"category_id-{line_id}", category),
        ]

    def test_an_ENVELOPE_id_names_the_existing_arm(self):
        """The arm that was dead in a browser for one leaf."""
        loaded = _load(_form(self._line_fields(88, "2225")))

        assert loaded["creations"] == [{
            "line_id": 88,
            "destination": 2225,
            "envelope_name": "Walmart",
            "category_id": 3,
        }]

    def test_the_NEW_arm_is_NAMED_rather_than_inferred(self):
        """``"new"`` is a value; "no id" was a guess."""
        loaded = _load(_form(self._line_fields(88, NEW_ENVELOPE)))

        assert loaded["creations"][0]["destination"] == NEW_ENVELOPE

    def test_a_new_envelope_with_no_category_still_LOADS(self):
        """Completeness is the DOOR's question, and that is the ruled shape.

        It was a ``@validates_schema`` rule here, and a nested schema error
        refuses the WHOLE payload -- so an owner who picked "a new envelope"
        and left the category on the form's own default lost every other act
        they had ticked.  The rule now lives in
        ``_create._reject_incomplete_new_envelope``, where it costs one item.
        This asserts the half that makes that possible: the payload survives
        loading, carrying the absence for the door to refuse.
        """
        loaded = _load(_form(self._line_fields(88, NEW_ENVELOPE, category="")))

        assert loaded["creations"] == [{
            "line_id": 88,
            "destination": NEW_ENVELOPE,
            "envelope_name": "Walmart",
            "category_id": None,
        }]

    def test_each_line_carries_its_OWN_fields_by_id(self):
        """Two cards' fields may not cross, which is what keying by id buys.

        **No counterpart anywhere in the suite**, measured by adversarial
        review 2026-09-06: ``test_statement_reconcile`` stages three cards but
        asserts only their ORDER, and asserts every field of exactly one card.
        A reader that paired the two lists by position rather than by id would
        pass both of those and fail this.
        """
        pairs = (
            self._line_fields(88, "2225", name="Walmart", category="3")
            + self._line_fields(9, NEW_ENVELOPE, name="Lowe's", category="4")
        )

        loaded = _load(_form(pairs))

        by_line = {item["line_id"]: item for item in loaded["creations"]}
        assert by_line[88] == {
            "line_id": 88, "destination": 2225,
            "envelope_name": "Walmart", "category_id": 3,
        }
        assert by_line[9] == {
            "line_id": 9, "destination": NEW_ENVELOPE,
            "envelope_name": "Lowe's", "category_id": 4,
        }

    def test_an_EXISTING_envelope_needs_no_category_at_all(self):
        """The control for the rule above, and the defect it replaced.

        Asking for a category unconditionally told an owner who picked an
        envelope they already had that their NEW envelope was incomplete --
        about a new envelope they had not asked for.
        """
        loaded = _load(_form(self._line_fields(88, "2225", category="")))

        assert loaded["creations"][0]["category_id"] is None


class TestThePassIsBounded:
    """A crafted submission is bounded by nothing the screen is bounded by.

    An import may carry 20,000 lines (``_secu_csv.MAX_LINES``), so an account
    can in principle offer more acts than one request has time for: measured,
    an item costs about 43 ms against a 120 s gunicorn timeout.

    **It grades the MATCHES and CREATIONS terms of that ceiling**, and it is
    here rather than beside the skip and income terms in
    ``test_statement_reconcile.TestTheBOUNDCountsSkipsToo`` because those two
    cases never exceed the bound with either of these kinds: delete
    ``len(data.get("matches", ()))`` from
    :meth:`~app.schemas.validation.statements.StatementBatchSchema
    ._reject_oversized_pass`'s sum and that file stays green.  Both adversarial
    reviews of plan step ``bank_import:X-gi-3`` measured that independently,
    against a first draft of this step that deleted this class with the retired
    ``batch_payload`` it used to drive.  Its SUBJECT is a live schema rule, so
    it is re-pointed rather than deleted -- the same test the two classes above
    are kept by.
    """

    @staticmethod
    def _matches(count, first_line=1):
        """Return *count* OK'd MATCH cards, as a browser submits them.

        Args:
            count: How many cards.
            first_line: The bank line id the run starts at.

        Returns:
            The form pairs.
        """
        pairs = []
        for index in range(count):
            line = str(first_line + index)
            pairs += [
                ("ok", line),
                (f"verb-{line}", "match"),
                (f"rows-{line}", f"transaction:{first_line + index}:1.00:1"),
            ]
        return pairs

    @staticmethod
    def _creations(count, first_line):
        """Return *count* OK'd ADD cards, as a browser submits them.

        Args:
            count: How many cards.
            first_line: The bank line id the run starts at.

        Returns:
            The form pairs.
        """
        pairs = []
        for index in range(count):
            line = str(first_line + index)
            pairs += [
                ("ok", line),
                (f"verb-{line}", "add"),
                (f"destination-{line}", NEW_ENVELOPE),
                (f"envelope_name-{line}", "X"),
                (f"category_id-{line}", "3"),
            ]
        return pairs

    def test_a_pass_over_the_ceiling_is_REFUSED_and_says_so(self):
        """Never silently truncated.

        Half a pass applied without a word is worse than a refusal the owner
        can act on.
        """
        with pytest.raises(ValidationError) as raised:
            _load(_form(self._matches(501)))

        assert "at most 500" in str(raised.value)

    def test_a_pass_at_the_ceiling_still_loads(self):
        """The control: the bound is a ceiling, not an off-by-one."""
        loaded = _load(_form(self._matches(500)))

        assert len(loaded["matches"]) == 500

    def test_the_two_KINDS_are_bounded_together(self):
        """What a request's time budget cares about is the SUM.

        Two lists with their own ceilings would admit twice the work either
        one allows -- and this is the case that grades the CREATIONS term
        beside the matches one.
        """
        pairs = self._matches(300) + self._creations(300, first_line=1000)

        with pytest.raises(ValidationError) as raised:
            _load(_form(pairs))

        assert "600 things to apply" in str(raised.value)


class TestAMatchMayNameAsManyRowsAsThePassOFFERS:
    """Plan step ``bank_import:X-go``: the schema stopped capping members.

    **The FIRING CONTROL for that deletion.**  ``_MAX_MATCH_MEMBERS = 100``
    stood on both of :class:`~app.schemas.validation.statements
    .StatementMatchSchema`'s lists until the developer ruled it out on
    2026-09-06, and nothing graded it -- so nothing would notice it being put
    back, and nothing said why it should not be.

    **What it was for is held by two bounds that already existed**, each where
    its own truth lives, which is why deleting it removes no protection:

    * the DOMAIN bound -- ``_resolve.resolve_rows`` looks every submitted row
      up in the pass's own offer set and refuses anything else by name, and
      ``load_lines`` does the same for lines.  Graded by
      ``test_accept.TestEveryOtherRefusalFires`` (a cancelled row, a card
      purchase, an unknown line, a row another match already claims);
    * the RESOURCE bound -- ``MAX_CONTENT_LENGTH`` (524,288,
      ``app/config.py``), the ONE bound on a request body (ruling
      **R-BAL218**), refuses the body before this schema sees it, and
      :data:`MAX_BATCH_ITEMS` bounds the ACTS.  Graded on a form body by
      ``tests/test_config.py``'s ``TestTheUploadCeiling``.  Measured
      2026-10-06 on Werkzeug 3.1.9 at the CRAFTED 22-byte tick: that budget
      carries 23,830 ticks, costing 51-63 ms in one item and 49-139 ms spread
      across the 500-item ceiling (two sessions on a shared host), all of
      which ``resolve_rows`` then refuses.  *Through Werkzeug 3.1.8 Flask's
      ``MAX_FORM_MEMORY_SIZE`` (500,000) was the tighter bound on such a
      body, and this docstring named it.*  The schema's own docstring
      carries the drafts this corrects.

    **And the cap had begun to contradict the screen**, which is what made it
    a defect rather than merely redundant: since plan step
    ``bank_import:X-gi-1`` the scriptless MATCH pane offers EVERY unexplained
    row on the account as a tickbox, so an account past 100 of them rendered
    controls this schema would refuse -- ruling **R-HW**'s *a control that
    cannot succeed*.
    """

    #: Comfortably past the deleted ceiling, and past any real group: ruling
    #: **R-FS**'s largest measured shape is a payroll deposit against three.
    _PAST_THE_OLD_CAP = 150

    def test_a_match_naming_more_than_a_hundred_rows_LOADS(self):
        """The ceiling is gone, asserted where it used to fire."""
        form = _form([
            (f"rows-{_LINE}", f"transaction:{index}:-1.00:1")
            for index in range(1, self._PAST_THE_OLD_CAP + 1)
        ])

        loaded = _load_one_card(form)

        assert len(loaded["rows"]) == self._PAST_THE_OLD_CAP

    def test_a_match_naming_more_than_a_hundred_LINES_loads_too(self):
        """Both lists carried the cap, so both are asserted.

        ``line_ids`` is one member per card today, and
        ``bank_import:X-gn`` is the step that lets a match name a second bank
        line -- so a cap here would bound that step's own shape rather than
        anything the wire carries now.
        """
        loaded = StatementBatchSchema().load({
            "matches": [{
                "line_ids": [str(index) for index in
                             range(1, self._PAST_THE_OLD_CAP + 1)],
                "rows": ["transaction:1:-1.00:1"],
            }],
        })

        assert len(loaded["matches"][0]["line_ids"]) == self._PAST_THE_OLD_CAP


class TestTheBatchSchemaRefusesWhatItDoesNotDeclare:
    """``unknown = RAISE``, deliberately, where its siblings EXCLUDE.

    :class:`~app.schemas.validation._helpers.BaseSchema` drops unknown keys so
    a form's ``csrf_token`` does not have to be declared -- correct for a
    payload that comes straight off a form.  This one never sees a form:
    :func:`~app.schemas.validation.statement_reconcile.reconcile_payload` has
    already turned one into these lists, so a key this schema does not declare
    is a regrouper and a schema that disagree, on the payload carrying every
    act in a pass.

    *Its second case went at plan step ``bank_import:X-gi-3``: it asserted that
    the regrouper's own output loads, which
    ``test_statement_reconcile.TestTheSKIPVerbReachesTheSchema
    .test_the_batch_schema_LOADS_what_this_reader_produced`` already says of
    the LIVE regrouper.*
    """

    def test_an_undeclared_key_is_refused(self):
        """A key the schema does not declare is refused rather than dropped."""
        with pytest.raises(ValidationError):
            StatementBatchSchema().load({"matches": [], "smuggled": [1]})


class TestTheRuleSectionOnTheWire:
    """Where your merchants go, as a form submits it -- plan step X-f6a-3d.

    The same two facts this module was written for, on the section this leaf
    adds: a browser submits every control it renders, and which answer was
    chosen is a fact the form STATES rather than one a reader infers.  The
    consequence is sharper here than one card down, because the section
    submits every merchant on the account on every pass -- so *untouched* is
    the ordinary case rather than the edge, and a reader that could not
    recognise it would rewrite twenty answers to say what they already said.
    """

    @staticmethod
    def _row(index, merchant_id, answer, name="", category=""):
        """Return the fields ONE merchant row submits, as a browser sends them.

        *merchant_id* is the merchant ROW the hidden input carries (plan step
        ``bank_import:X-gd-1``).  It was the bank's own STRING, which is what
        made the schema unable to say anything about it at all.
        """
        return [
            (f"rule-{index}", answer),
            (f"rule_merchant-{index}", str(merchant_id)),
            (f"rule_name-{index}", name),
            (f"rule_category-{index}", category),
        ]

    def test_every_rendered_row_submits_including_the_untouched_ones(self):
        """The premise the door's "unchanged" arm exists for.

        There is no way to tell an untouched control from a deliberately
        repeated answer on the wire, and inventing one -- a hidden "what it
        was" field -- would be a value the submitter could forge into a write
        nobody asked for.  So every row arrives and the SERVICE compares.
        """
        payload = rule_payload(_form(
            self._row(0, 11, "t:38")
            + self._row(1, 12, NOT_SAID)
            + self._row(2, 13, NEVER),
        ))

        assert [item["merchant_id"] for item in payload["rules"]] == [
            "11", "12", "13",
        ]

    def test_NOT_SAID_is_carried_rather_than_dropped(self):
        """An arm is STATED, never inferred from an absence.

        The destination select one card down drops its do-nothing value,
        because there the default means "do not record this line" and there is
        nothing to undo.  Here it means "state nothing about this merchant",
        which the ROUTE drops -- but it has to ARRIVE for the route to drop it,
        and ``answer`` is ``required=True`` while ``BaseSchema``'s ``@pre_load``
        normalizer removes every ``""`` a form submits.  So an arm spelled as
        an absence is an arm that never reaches the door at all, which is what
        this case pins.

        **Its original reason was that this value WITHDREW a rule**, and ruling
        R-GS deleted the withdrawal in the same step that kept this case
        (plan step ``bank_import:X-gd-2``).  A stated rule is now permanent by
        design; the surviving reason is the one above.  Found by adversarial
        review 2026-08-26.
        """
        payload = rule_payload(_form(self._row(0, 11, NOT_SAID)))

        assert payload["rules"] == [{
            "merchant_id": "11", "answer": NOT_SAID,
            "envelope_name": "", "category_id": "",
        }]

    def test_a_TEMPLATE_answer_carries_its_arm_with_its_id(self):
        """The arm is STATED, never inferred from the value's shape.

        A bare number would have to be read as "a template" by convention,
        which is the inference that made the existing-envelope destination
        unreachable from a browser one leaf earlier.
        """
        loaded = MerchantRuleBatchSchema().load(
            rule_payload(_form(self._row(0, 11, "t:38"))),
        )

        assert loaded["rules"][0]["answer"] == SubmittedAnswer(
            kind=RuleAnswer.TEMPLATE, row_id=38,
        )

    def test_the_SIX_wire_values_all_load(self):
        """The closed set, so none of them is refused by the grader.

        **Four, then five at ruling R-GS, then SIX at R-HT(a)** (plan step
        ``bank_import:X-gj-2a``, which added *income under a category*).  This
        class is where a value the grader refuses is caught, so a member
        missing from it is a member with no grading at THIS tier -- delete
        ``ALWAYS_ASK`` from ``RuleAnswerField``'s accepted set and the class
        whose whole job is the wire stayed green.  Found by adversarial review
        2026-08-26.

        **The answers are compared as DISCRIMINATED values now.**  They were
        bare ids and sentinel strings, and the two id-bearing answers were
        told apart by nothing -- which is what let the route read an income
        answer as a template one.
        """
        loaded = MerchantRuleBatchSchema().load(rule_payload(_form(
            self._row(0, 11, "t:38")
            + self._row(1, 12, NOT_SAID)
            + self._row(2, 13, NEVER)
            + self._row(3, 14, NEW_ENVELOPE, name="Lowe's", category="4")
            + self._row(4, 15, ALWAYS_ASK)
            + self._row(5, 16, "i:7"),
        )))

        assert [item["answer"] for item in loaded["rules"]] == [
            SubmittedAnswer(kind=RuleAnswer.TEMPLATE, row_id=38),
            NOT_SAID,
            SubmittedAnswer(kind=RuleAnswer.NEVER),
            SubmittedAnswer(kind=RuleAnswer.NEW_ENVELOPE),
            SubmittedAnswer(kind=RuleAnswer.ALWAYS_ASK),
            SubmittedAnswer(kind=RuleAnswer.INCOME_CATEGORY, row_id=7),
        ]
        assert loaded["rules"][3]["envelope_name"] == "Lowe's"
        assert loaded["rules"][3]["category_id"] == 4

    def test_every_RuleAnswer_member_has_a_wire_value(self):
        """The totality this package's own tables CLAIM and nothing graded.

        ``_NAMES_NOTHING`` and ``_PREFIXED_ANSWERS`` assert in their docstring
        that every member appears in exactly one of them, *"which is graded as
        a round trip"* -- and nothing referenced either table (adversarial code
        review 2026-08-31).  A claimed gate that measures nothing is this
        project's own recurring defect, so here is the round trip.

        **Exactly one**, not at least one: a member in both tables would be an
        answer the field could deserialize two ways.
        """
        named = set(_NAMES_NOTHING.values())
        prefixed = {kind for _, kind in _PREFIXED_ANSWERS}

        assert named | prefixed == set(RuleAnswer)
        assert named & prefixed == set()

    def test_an_answer_with_no_merchant_beside_it_names_nothing(self):
        """Unreachable from this screen -- the two fields render together.

        Dropped rather than refused, because a crafted body naming an answer
        for nobody has asked for nothing, and refusing would give a caller a
        way to fail a whole legitimate pass by appending one key.
        """
        payload = rule_payload(_form([("rule-9", NEVER)]))

        assert payload["rules"] == []

    def test_a_merchant_key_that_str_isdigit_ACCEPTS_does_not_raise(self):
        """The same trap the tick keys carry, on this section's keys.

        ``apply=%C2%B2`` was a 500 on the money door until plan step
        X-f6a-3c-2, because ``str.isdigit`` is true for 888 characters and
        ``int()`` refuses 128 of them.  These keys are sorted through the same
        ``order_token_key``, so the fix covers them -- and this is what says so.
        """
        payload = rule_payload(_form(
            [("rule-\N{SUPERSCRIPT TWO}", NEVER),
             ("rule_merchant-\N{SUPERSCRIPT TWO}", "11")],
        ))

        assert [item["merchant_id"] for item in payload["rules"]] == ["11"]

    def test_a_respelled_template_id_is_refused(self):
        """``t:007`` names no template, exactly as ``007`` names no envelope.

        A second, laxer reading of a row id on a screen that decides where
        money is filed is what plan step X-ae removed.
        """
        with pytest.raises(ValidationError):
            MerchantRuleBatchSchema().load(
                rule_payload(_form(self._row(0, 11, "t:007"))),
            )

    def test_an_undeclared_key_is_refused(self):
        """``unknown = RAISE``: nothing is swallowed on this payload either."""
        with pytest.raises(ValidationError):
            MerchantRuleBatchSchema().load(
                {"rules": [], "sneaky": 1},
            )

    def test_a_submission_over_the_CEILING_is_refused_and_says_so(self):
        """The rule ceiling had no test at either tier.

        Its sibling :data:`~app.schemas.validation.statements.MAX_BATCH_ITEMS`
        is graded by ``TestThePassIsBounded`` above and by
        ``test_statement_reconcile.TestTheBOUNDCountsSkipsToo``, which between
        them reach all four of its terms; this one could be raised to a billion
        with the suite green.  It is a SEPARATE bound on
        purpose -- that one paces money acts, each running a settle door, and
        this one paces small writes over a set the account's own lines bound --
        so it needs its own control rather than inheriting that one's.
        """
        rows = []
        for index in range(_MAX_RULE_ITEMS + 1):
            rows.extend(self._row(index, 1000 + index, NEVER))

        with pytest.raises(ValidationError) as caught:
            MerchantRuleBatchSchema().load(rule_payload(_form(rows)))

        assert "at most" in str(caught.value)

    def test_a_submission_AT_the_ceiling_still_loads(self):
        """The bound pinned from the other side, so it is not off by one."""
        rows = []
        for index in range(_MAX_RULE_ITEMS):
            rows.extend(self._row(index, 1000 + index, NEVER))

        loaded = MerchantRuleBatchSchema().load(rule_payload(_form(rows)))

        assert len(loaded["rules"]) == _MAX_RULE_ITEMS

    def test_a_merchant_that_is_NOT_A_ROW_ID_is_refused(self):
        """The key is an id now, and it is exactly as strict as every other.

        It was free text from a BANK, so this schema could refuse only a string
        longer than the column -- everything else was the service's to check.
        A merchant is a row as of plan step ``bank_import:X-gd-1``, so
        :class:`~app.schemas.validation._fields.RowId` refuses the whole family
        it refuses everywhere else, and a well-formed id that is not this
        account's is refused by
        ``fk_merchant_rules_merchant_account`` rather than by anyone
        remembering to look.
        """
        for spelled in ("\N{ARABIC-INDIC DIGIT SEVEN}", " 7 ", "+7", "007",
                        "-7", "0", "Amazon"):
            with pytest.raises(ValidationError):
                MerchantRuleBatchSchema().load(
                    rule_payload(_form(self._row(0, spelled, NEVER))),
                )
