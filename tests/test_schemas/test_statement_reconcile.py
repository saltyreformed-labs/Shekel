"""What the RECONCILE page's form submits, and what the schemas make of it.

Plan step **bank_import:X-gj-1b**.  The readers under test move a browser's
body into the payloads :class:`~app.schemas.validation.statements
.StatementBatchSchema` and :class:`~app.schemas.validation.statements
.StatementMatchSchema` already grade -- so what is asserted here is the FORM's
own contract, and never a second set of money rules.

**The load-bearing case is the one that posts a whole rendered page with
nothing pressed.**  Ruling **R-HS** pre-fills a justified suggestion and then
says *an untouched card is not submitted*, and those two can only both hold
because the OK checkbox is what the reader keys on: a browser submits a
checkbox only when it is ticked.
"""

import pytest
from werkzeug.datastructures import MultiDict

from app.schemas.validation.statement_reconcile import (
    reconcile_match_payload,
    reconcile_payload,
)
from app.schemas.validation.statements import StatementBatchSchema


def _form(pairs):
    """Return *pairs* as the ``MultiDict`` a request carries."""
    return MultiDict(pairs)


class TestNothingIsAnActWithoutItsOwnOK:
    """Rulings **R-FP** and **R-HS**, at the grain of the reader."""

    def test_a_pre_filled_destination_alone_is_not_an_act(self):
        """The whole reason this reader exists beside the retired
        ``batch_payload``.

        On the review queue the destination select WAS the tick, so a rule's
        remembered destination may not arrive selected.  Here it does, and
        the OK checkbox is the tick -- so a card nobody pressed contributes
        nothing.
        """
        payload, silent = reconcile_payload(_form([
            ("verb-7", "add"),
            ("destination-7", "42"),
            ("envelope_name-7", "Lowe's"),
            ("category_id-7", "3"),
        ]))

        assert payload == {
            "matches": [], "creations": [], "incomes": [], "skips": [],
        }
        assert silent == ()

    def test_an_OK_D_add_card_becomes_a_creation(self):
        """The same body with the box ticked."""
        payload, silent = reconcile_payload(_form([
            ("ok", "7"),
            ("verb-7", "add"),
            ("destination-7", "42"),
            ("envelope_name-7", "Lowe's"),
            ("category_id-7", "3"),
        ]))

        assert payload["creations"] == [{
            "line_id": "7", "destination": "42",
            "envelope_name": "Lowe's", "category_id": "3",
        }]
        assert silent == ()

    def test_an_OK_D_match_card_becomes_a_match_with_its_rows(self):
        """One card, its line AS THE SCREEN SHOWED IT, and the rows ticked.

        The line travels as the card's own ``line-<line>`` token (plan step
        ``bank_import:X-f6b-2``, ruling **bank_import:R-BI14**) and never
        as the key: the key locates the card's controls, the token is the
        line's identity and reviewed day as one value.
        """
        payload, _ = reconcile_payload(_form([
            ("ok", "7"),
            ("verb-7", "match"),
            ("line-7", "7:2026-06-10"),
            ("rows-7", "transaction:1:100.00:2"),
            ("rows-7", "transaction:2:2473.38:2"),
            ("consent-7", "0.04@transaction:2:2473.38:2"),
        ]))

        assert payload["matches"] == [{
            "lines": ["7:2026-06-10"],
            "rows": ["transaction:1:100.00:2", "transaction:2:2473.38:2"],
            "consent": "0.04@transaction:2:2473.38:2",
        }]

    def test_a_match_card_carrying_NO_line_token_names_no_line(self):
        """Fail closed: a page drawn before the token posts none.

        The reader does not fall back to the key -- that would submit a
        line nobody reviewed under a day nobody saw -- so the item names no
        line and the door refuses it as an empty side.
        """
        payload, _ = reconcile_payload(_form([
            ("ok", "7"),
            ("verb-7", "match"),
            ("rows-7", "transaction:1:100.00:2"),
        ]))

        assert payload["matches"] == [{
            "lines": [],
            "rows": ["transaction:1:100.00:2"],
        }]

    def test_an_OK_D_income_card_becomes_an_income(self):
        """Ruling **bank_import:R-GW**: one id and nothing to unpack."""
        payload, _ = reconcile_payload(_form([
            ("ok", "7"), ("verb-7", "add"), ("destination-7", "income"),
        ]))

        assert payload["incomes"] == [{"line_id": "7"}]
        assert payload["creations"] == []


class TestAPressIsNeverLeftUnanswered:
    """A card OK'd on nothing is REPORTED rather than dropped.

    It is reachable from a browser, so it may not be a pass-level refusal
    (**R-FZ(a)**, which would cost every other OK on the page) and it may not
    be silent.
    """

    def test_an_OK_with_no_destination_chosen_is_named(self):
        """The ADD tab's own button, pressed before the select was moved."""
        payload, silent = reconcile_payload(_form([
            ("ok", "7"), ("verb-7", "add"), ("destination-7", ""),
        ]))

        assert payload == {
            "matches": [], "creations": [], "incomes": [], "skips": [],
        }
        assert silent == ("7",)

    def test_an_OK_on_a_verb_with_no_door_is_named(self):
        """TRANSFER has no door in this build (**R-HW**).

        Its tab renders an explanation and no submitting control, so a body
        naming it is crafted or stale -- and it still gets an answer rather
        than silence.

        *This named SKIP too until plan step ``bank_import:X-gj-4b``*, which
        lit that verb: a card OK'd on SKIP is an ACT now, so it is graded by
        ``TestTheSKIPVerbReachesTheSchema`` rather than here.  TRANSFER is the
        one verb left that this case is about.
        """
        payload, silent = reconcile_payload(_form([
            ("ok", "7"), ("verb-7", "transfer"),
        ]))

        assert payload == {
            "matches": [], "creations": [], "incomes": [], "skips": [],
        }
        assert silent == ("7",)

    def test_an_OK_naming_no_verb_at_all_is_named(self):
        """A radio group always submits one, so this is a crafted body."""
        _, silent = reconcile_payload(_form([("ok", "7")]))

        assert silent == ("7",)


class TestTheOrderIsTheBankLinesAndNotTheFieldNames:
    """The receipt this order becomes is meant to read down the page."""

    def test_line_100_does_not_sort_between_10_and_2(self):
        """Lexical order put line 100 between 10 and 2 once already."""
        payload, _ = reconcile_payload(_form([
            ("ok", "100"), ("verb-100", "add"), ("destination-100", "1"),
            ("ok", "2"), ("verb-2", "add"), ("destination-2", "1"),
            ("ok", "10"), ("verb-10", "add"), ("destination-10", "1"),
        ]))

        assert [item["line_id"] for item in payload["creations"]] == [
            "2", "10", "100",
        ]

    def test_a_repeated_OK_names_its_line_once(self):
        """A crafted body cannot make one card into two acts."""
        payload, _ = reconcile_payload(_form([
            ("ok", "7"), ("ok", "7"),
            ("verb-7", "add"), ("destination-7", "1"),
        ]))

        assert len(payload["creations"]) == 1

    @pytest.mark.parametrize("token, why", [
        ("\N{SUPERSCRIPT TWO}", "isdigit() is True and int() raises"),
        ("\N{ARABIC-INDIC DIGIT ONE}\N{ARABIC-INDIC DIGIT TWO}",
         "a non-ASCII digit script int() DOES convert, to another id"),
        ("9" * 4301, "past CPython's 4,300-digit conversion limit"),
    ])
    def test_an_OK_that_str_isdigit_ACCEPTS_does_not_raise(self, token, why):
        """The same tolerance ``order_token_key`` gives every other reader.

        **All three arms, because only one of them reaches the length
        clause.**  ``str.isdigit()`` is true for 888 characters, 128 of which
        make ``int()`` raise, and true for a digit run longer than CPython will
        convert -- so
        :func:`~app.schemas.validation._helpers.order_token_key` guards on
        ``is_ascii_digits(raw) and len(raw) <= MAX_ORDER_DIGITS``, and
        ``app/error_handlers.py`` registers no ``ValueError`` arm.  A body
        carrying ``ok=999...9`` past that limit would be an unhandled 500 on
        the door that applies a whole pass.

        *These three rode the retired ``batch_payload``'s ``apply`` key until
        plan step ``bank_import:X-gi-3``, whose first draft deleted them with
        it and left only the superscript arm covered -- so the LENGTH clause,
        and the non-ASCII script ``int()`` silently converts to a DIFFERENT
        id, were graded by nothing.  Adversarial review 2026-09-06.  This is
        the live reader's own key, which is where the guard now has to hold.*

        Args:
            token: A spelling ``str.isdigit`` accepts.
            why: What is wrong with it, for the failure message.
        """
        payload, silent = reconcile_payload(_form([("ok", token)]))

        assert payload == {
            "matches": [], "creations": [], "incomes": [], "skips": [],
        }, why
        assert silent == (token,), why


class TestWhatTheReadersHandTheSchemasIsWhatTheSchemasGrade:
    """The readers validate nothing; the schemas refuse everything."""

    def test_a_forged_destination_is_the_schema_s_refusal(self):
        """A destination that names no row is graded where ids are."""
        payload, _ = reconcile_payload(_form([
            ("ok", "7"), ("verb-7", "add"), ("destination-7", "007"),
        ]))

        assert StatementBatchSchema().validate(payload)

    def test_a_well_formed_pass_loads(self):
        """The ordinary body, through the door's own grader."""
        payload, _ = reconcile_payload(_form([
            ("ok", "7"), ("verb-7", "add"), ("destination-7", "42"),
            ("envelope_name-7", "Lowe's"), ("category_id-7", "3"),
        ]))

        loaded = StatementBatchSchema().load(payload)

        assert loaded["creations"][0]["line_id"] == 7
        assert loaded["creations"][0]["destination"] == 42


class TestTheMatchReaderIsSharedByThePassAndThePanel:
    """Two callers, one reading of one body.

    The panel prices what is ticked and the pass applies it, so the figure on
    screen and the figure the door compares against are one derivation.
    """

    def test_an_untouched_consent_is_omitted_rather_than_sent_as_empty(self):
        """The schema's own ``load_default`` is the one statement of absence.

        The panel renders the scriptless box ``value=""`` and ``disabled`` in
        lockstep, so a browser cannot send one -- but a body that does must
        not 400 the whole pass over a field nobody filled in.
        """
        item = reconcile_match_payload(
            _form([("rows-7", "transaction:1:100.00:2"), ("consent-7", "")]),
            "7",
        )

        assert "consent" not in item

    def test_it_reads_the_SAME_fields_the_pass_does(self):
        """One reader, so the two cannot disagree about a card's rows."""
        body = _form([
            ("ok", "7"), ("verb-7", "match"),
            ("rows-7", "transaction:1:100.00:2"),
            ("consent-7", "0.04"),
        ])

        assert reconcile_payload(body)[0]["matches"] == [
            reconcile_match_payload(body, "7"),
        ]


class TestTheSKIPVerbReachesTheSchema:
    """Plan step ``bank_import:X-gj-4b``, ruling **bank_import:R-JG**.

    The fourth act class on the wire.  A card left on the SKIP tab and OK'd
    submits its verb and nothing else, because a skip is filed against no
    container and no row -- so what these cases grade is that the reader turns
    that into an ITEM rather than into a silent drop.
    """

    def test_an_OK_D_card_on_SKIP_becomes_a_skip_item(self):
        """The tab a card is left on IS the verb it is OK'd with."""
        payload, silent = reconcile_payload(_form([
            ("ok", "7"),
            ("verb-7", "skip"),
        ]))

        assert payload["skips"] == [{"line_id": "7"}]
        assert payload["matches"] == []
        assert payload["creations"] == []
        assert payload["incomes"] == []
        assert silent == ()

    def test_an_UNTICKED_card_on_SKIP_submits_nothing(self):
        """Ruling **R-HS**: OK is the consent, and the verb radio is not.

        Every card renders all four tabs, so a body carrying ``verb-7=skip``
        without ``ok=7`` is what an owner who opened a card, looked at the
        SKIP tab and left it alone actually sends.
        """
        payload, silent = reconcile_payload(_form([("verb-7", "skip")]))

        assert payload["skips"] == []
        assert silent == ()

    def test_a_skip_is_NOT_reported_as_a_press_with_no_act(self):
        """FIRING CONTROL against the arm this verb used to fall through to.

        Before the verb was lit, a card OK'd on SKIP produced no item and was
        reported through ``ok_with_no_act``, whose sentence reads *without
        choosing what to do with them* -- false of an owner who chose.
        """
        _, silent = reconcile_payload(_form([
            ("ok", "7"),
            ("verb-7", "skip"),
        ]))

        assert silent == (), (
            "a card OK'd on SKIP was reported as naming no act, which is the "
            "sentence that is false of an owner who chose the verb"
        )

    def test_the_batch_schema_LOADS_what_this_reader_produced(self):
        """The loop neither half closes alone: a Jinja field name, this
        reader's key and a Marshmallow field name have no compile-time
        relationship."""
        payload, _ = reconcile_payload(_form([
            ("ok", "7"),
            ("verb-7", "skip"),
        ]))

        loaded = StatementBatchSchema().load(payload)

        assert loaded["skips"] == [{"line_id": 7}]

    def test_a_forged_line_id_is_REFUSED_by_the_schema(self):
        """The one thing :class:`StatementSkipSchema` exists for: the door
        never sees an id ``RowId`` would not take."""
        payload, _ = reconcile_payload(_form([
            ("ok", "-3"),
            ("verb--3", "skip"),
        ]))

        errors = StatementBatchSchema().validate(payload)

        assert "skips" in errors


class TestTheBOUNDCountsSkipsToo:
    """The ceiling is on the SUM over every act class, stated ONCE.

    Plan step ``bank_import:X-gj-4b``.  Four lists each carrying their own
    ceiling would admit FOUR TIMES what the bound says, which is the defect
    ``StatementBatchSchema`` records a per-list ``Length`` having been.  *An
    earlier draft of this paragraph said "twice again" and contradicted the
    comment it mirrors.*
    Built through :func:`reconcile_payload` rather than by hand, so what is
    graded is what the page can actually submit.
    """

    def test_a_skip_only_pass_over_the_ceiling_is_refused(self):
        """FIRING CONTROL: drop ``skips`` from the sum and this passes."""
        pairs = []
        for line_id in range(1, 502):
            pairs += [("ok", str(line_id)), (f"verb-{line_id}", "skip")]
        payload, _ = reconcile_payload(_form(pairs))

        errors = StatementBatchSchema().validate(payload)

        assert "501 things to apply" in str(errors)

    def test_skips_and_deposits_are_bounded_TOGETHER(self):
        """The sum, across two act classes that reach different doors."""
        pairs = []
        for line_id in range(1, 301):
            pairs += [("ok", str(line_id)), (f"verb-{line_id}", "skip")]
        for line_id in range(1000, 1300):
            pairs += [
                ("ok", str(line_id)),
                (f"verb-{line_id}", "add"),
                (f"destination-{line_id}", "income"),
            ]
        payload, _ = reconcile_payload(_form(pairs))

        errors = StatementBatchSchema().validate(payload)

        assert len(payload["skips"]) == 300
        assert len(payload["incomes"]) == 300
        assert "600 things to apply" in str(errors)

    def test_a_pass_AT_the_ceiling_still_loads(self):
        """The bound is a ceiling, not an off-by-one."""
        pairs = []
        for line_id in range(1, 501):
            pairs += [("ok", str(line_id)), (f"verb-{line_id}", "skip")]
        payload, _ = reconcile_payload(_form(pairs))

        loaded = StatementBatchSchema().load(payload)

        assert len(loaded["skips"]) == 500
