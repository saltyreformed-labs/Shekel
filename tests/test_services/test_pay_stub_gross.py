"""
Shekel Budget App -- the pay stub door's printed-gross check (plan step salary:S11-b, S11-c-2b).

What :mod:`app.services.pay_stub_gross` decides through
:mod:`app.services.pay_stub_service`'s record door, graded without a request:

* the printed-GROSS check (**R-SAL99**, finding **SAL-590**, which reads the
  printed gross as base pay plus the taxable earnings): a gross typed as base
  pay is refused, and an earning moved between taxable and after-tax -- which
  moves the gross but not the net -- is refused on the gross, in both
  directions;
* the job's answer to "my stub's gross includes after-tax earnings"
  (**R-SAL102**, finding SAL-592): "yes" adds them to the checked gross, and
  cannot see an earning under the other of the two headings (the ruling's
  accepted cost);
* how a gross miss is worded, read off the net's miss beside it (**R-SAL106**,
  amending R-SAL104; **R-SAL108**, **R-SAL110**, **R-SAL111**, **R-SAL112**):
  net exact names the typed gross, and beside it R-SAL104's question and the
  setting on a "no" job short by exactly the after-tax total, the setting on
  a "yes" job over by it, else the headings on a "no" job; net off by the
  same names an amount, or -- when base pay is the printed gross -- has both
  typed totals checked and asks the base pay the stub shows, which tells the
  Base pay box from an earning the stub does not print (**R-SAL119**,
  **R-SAL120**); twice names a line on the wrong side; anything else,
  the difference.  Every message is in the plain words of **R-SAL114** to
  **R-SAL117** (the approved list, **R-SAL115**).

Split out of ``test_pay_stub_service.py`` at plan step salary:S11-c-2c
(ledger row **SAL-594**), a pure move: every class and helper below is as it
stood there.  The worked example it prices is in
``tests/test_services/_pay_stub_example.py``.
"""

from decimal import Decimal

import pytest

from app.enums import PaycheckLineKindEnum, WithholdingKindEnum
from app.exceptions import PayStubRefused
from app.extensions import db
from app.models.pay_stub import PayStub
from app.services import pay_stub_gross, pay_stub_service
from app.services.pay_stub_service import LineFigure, OneOffFigure
from app.services.paycheck_calculator import StubTotals

# The worked example and its helpers have one home (plan step salary:S11-c-2c,
# SAL-594); each is imported under the name this suite always used, and
# importing ``world_fixture`` registers the ``world`` fixture here.
# Pylint: ``unused-import`` -- importing ``world_fixture`` IS its use: pytest
# finds the ``world`` fixture by the module-level name.
from tests.test_services._pay_stub_example import (  # pylint: disable=unused-import
    TODAY as _TODAY,
    example_figures as _figures,
    fresh_ctx as _ctx,
    kind_id as _kind,
    printed_totals as _printed,
    printed_under as _printed_under,
    record_example as _record,
    tax_id as _tax,
    world_fixture,
)


class TestThePrintedGross:
    """R-SAL99, "Check the gross too" (finding SAL-590): base pay plus the taxable earnings."""

    def test_a_gross_typed_as_base_pay_is_asked_about(self, world):
        """The stub's gross, $2,999.62, typed into Base pay, its earnings entered again.

        Base pay 2999.62 + Phone 60.00 + Retro pay 55.00 = 3114.62 against the
        printed 2999.62: the $115.00 of taxable earnings is counted twice, and
        the net misses by the same $115.00 (2217.62 against 2102.62), refused
        on its own field.  Base pay equal to the printed gross with that pair
        is asked as the Base pay box OR an earning the stub does not print,
        which makes the same pair (ruling R-SAL111 reworded R-SAL99's "is
        counted twice", which stated the first as fact), told apart by the
        base pay the stub shows: 2999.62 - 115.00 = 2884.62 for the first
        (ruling R-SAL112, both typed totals checked first by R-SAL120; worded
        by R-SAL114 and R-SAL117).
        """
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _figures(world, base="2999.62"))
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable earnings come to $3,114.62, but the stub's "
                "Gross Pay is $2,999.62.  Check the Gross Pay and Net Pay you typed.  If "
                "they're right, what base pay does the stub show?  $2,884.62: you typed "
                "the Gross Pay into Base pay.  Type $2,884.62 there instead.  $2,999.62: "
                "you entered extra pay this stub doesn't list.  Remove it."
            ),
            "printed_net": (
                "The lines add up to $2,217.62, but the stub prints $2,102.62 (a "
                "difference of $115.00).  Check each figure against the stub."
            ),
        }
        # Counted BEFORE any rollback, as the net's refusal test counts.
        assert db.session.query(PayStub).count() == 0
        db.session.rollback()

    def test_an_earning_under_another_kind_is_caught_by_the_gross_alone(self, world):
        """Phone ($60.00, printed as taxable) entered as an AFTER-TAX earning.

        The net balances -- an earning joins the deposit either way, once the
        taxes are typed: 2939.62 - 315.00 - 472.00 - 110.00 + 60.00 = 2102.62
        -- so only the gross sees it: 2884.62 + 55.00 = 2939.62 against the
        printed 2999.62.  The gross is short by exactly the $60.00 entered as
        after-tax, which a "no" job's stub whose gross held a real after-tax
        earning makes too, and so does a gross typed $60.00 high, so the
        refusal names the typed gross, asks after the entry AND names the job's
        setting (rulings R-SAL104 and R-SAL110: the developer's rulings of
        2026-10-04 changed this expectation from R-SAL99's single kind
        question; worded by R-SAL114 and R-SAL116).
        """
        figures = _printed_under(
            world, _figures(world), phone=PaycheckLineKindEnum.AFTER_TAX_EARNING,
        )
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures)
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable earnings come to $2,939.62, but the stub's "
                "Gross Pay is $2,999.62: $60.00 less, the same as your untaxed earnings.  "
                "Check the Gross Pay you typed.  If it's right, is one of those earnings "
                "taxed on your stub?  Then choose Taxable earning for it."
            ),
            "stub_gross_includes_after_tax": (
                "Or, if your stub counts untaxed pay in its Gross Pay,"
            ),
        }

    def test_an_after_tax_earning_entered_as_taxable_is_not_blamed_on_base_pay(self, world):
        """A stub with no taxable earning prints Phone ($60.00) AFTER-TAX; it is entered taxable.

        Its gross is its base, 2884.62, so the typed base pay equals the
        printed gross -- the SAL-590 slip's first sign -- but the net balances
        (2884.62 - 315.00 - 472.00 - 110.00 + 60.00 = 2047.62, and the same
        with Phone taxed: 2944.62 - 897.00), so nothing was counted twice and
        the refusal names the difference, never the Base pay box.  The net is
        exact, so the amounts are right and the refusal names what the pair
        allows: the typed gross, and the headings (rulings R-SAL106 and
        R-SAL108 reworded this from R-SAL99's "check the base pay ..."; worded
        by R-SAL114 and R-SAL116).
        """
        figures = _figures(world, one_offs=())
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures, printed_net=Decimal("2047.62"),
                    printed_gross=Decimal("2884.62"))
        assert refused.value.errors == {"printed_gross": (
            "Base pay plus your taxable earnings come to $2,944.62, but the stub's Gross "
            "Pay is $2,884.62 ($60.00 apart).  Your amounts match the stub's Net Pay, so "
            "check the Gross Pay you typed, and which earnings you marked Taxable earning "
            "and which After-tax earning."
        )}

    def test_a_gross_typed_as_base_pay_without_every_tax_is_not_diagnosed(self, world):
        """The SAL-590 slip with Medicare ($42.00) left out: the net cannot confirm it.

        The lines' net leaves Medicare out, so it misses by $157.00 against
        the gross's $115.00 (2259.62 against 2102.62); without the net's
        matching miss the gross alone cannot tell the slip from a kind slip,
        so the refusal names the $115.00 difference and nothing else (ruling
        R-SAL106: a pair no single mistake makes; worded by R-SAL114).
        """
        figures = _figures(world, base="2999.62")
        medicare = _tax(WithholdingKindEnum.MEDICARE)
        del figures.withholdings[medicare]
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures)
        assert refused.value.errors["printed_gross"] == (
            "Base pay plus your taxable earnings come to $3,114.62, but the stub's Gross "
            "Pay is $2,999.62 ($115.00 apart).  Check each amount against the stub."
        )
        assert set(refused.value.errors) == {f"tax-{medicare}", "printed_gross"}

    def test_a_base_pay_typo_is_not_called_a_gross_in_the_base_box(self, world):
        """Base pay typed $2,884.72 for $2,884.62: both totals miss by $0.10.

        Gross 2999.72 against 2999.62 and net 2102.72 against 2102.62 -- the
        net's matching miss alone would read as the SAL-590 slip, so the
        base pay differing from the printed gross is what keeps it from naming
        the Base pay box as the gross's; the net off by the same names an
        amount (ruling R-SAL106; worded by R-SAL114).
        """
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _figures(world, base="2884.72"))
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable earnings come to $2,999.72, but the stub's "
                "Gross Pay is $2,999.62 ($0.10 apart), and your amounts are $0.10 apart "
                "from its Net Pay too.  One amount is wrong: check Base pay and each "
                "earning."
            ),
            "printed_net": (
                "The lines add up to $2,102.72, but the stub prints $2,102.62 (a "
                "difference of $0.10).  Check each figure against the stub."
            ),
        }

    def test_a_base_pay_typed_above_the_printed_gross_is_an_amount(self, world):
        """No earning on the stub, base pay typed $2,884.72 for $2,884.62: still an amount.

        Base pay ABOVE the printed gross, both totals $0.10 over (gross
        2884.72 against 2884.62, net 2884.72 - 897.00 = 1987.72 against
        1987.62).  Only base pay EQUAL to the printed gross asks what the stub
        shows (R-SAL112), so this names an amount, as it did before the
        plain-words rewrite (R-SAL114: when a message appears does not
        change; worded by R-SAL114; the tail's review, LOW-4).
        """
        figures = _figures(world, one_offs=(), base="2884.72")
        del figures.line_amounts[world["lines"]["phone"].id]
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures, printed_net=Decimal("1987.62"),
                    printed_gross=Decimal("2884.62"))
        assert refused.value.errors["printed_gross"] == (
            "Base pay plus your taxable earnings come to $2,884.72, but the stub's Gross "
            "Pay is $2,884.62 ($0.10 apart), and your amounts are $0.10 apart from its "
            "Net Pay too.  One amount is wrong: check Base pay and each earning."
        )
        assert set(refused.value.errors) == {"printed_gross", "printed_net"}

    def test_the_slip_with_a_zero_tax_left_blank_is_still_asked(self, world):
        """The SAL-590 slip on a stub printing Federal $0.00, the Federal box left blank.

        The stub nets 2102.62 + 150.00 = 2252.62.  Typed: Base pay 2999.62
        (the gross) and no Federal, so the lines make gross 3114.62 and net
        3114.62 - 315.00 - 322.00 - 110.00 = 2367.62: both miss by $115.00,
        exactly as with $0.00 typed, so the slip is asked about (ruling
        R-SAL112's question, which amends R-SAL111's wording, after R-SAL120's
        check of both typed totals; worded by R-SAL114 and R-SAL117) beside the
        missing tax's own refusal.
        """
        federal = _tax(WithholdingKindEnum.FEDERAL_INCOME)
        figures = _figures(world, base="2999.62")
        del figures.withholdings[federal]
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures, printed_net=Decimal("2252.62"))
        assert refused.value.errors == {
            f"tax-{federal}": "Enter the stub's Federal income tax ($0.00 if none).",
            "printed_gross": (
                "Base pay plus your taxable earnings come to $3,114.62, but the stub's "
                "Gross Pay is $2,999.62.  Check the Gross Pay and Net Pay you typed.  If "
                "they're right, what base pay does the stub show?  $2,884.62: you typed "
                "the Gross Pay into Base pay.  Type $2,884.62 there instead.  $2,999.62: "
                "you entered extra pay this stub doesn't list.  Remove it."
            ),
        }

    def test_a_printed_gross_a_cent_off_is_refused_on_its_own(self, world):
        """$2,999.63 printed against the lines' $2,999.62; the net is right.

        Ruling R-SAL108's own case: a typo in the gross box leaves the net
        exact, so the gross typed is named beside the headings (worded by
        R-SAL114 and R-SAL116).
        """
        with pytest.raises(PayStubRefused) as refused:
            _record(world, printed_gross=Decimal("2999.63"))
        assert refused.value.errors == {"printed_gross": (
            "Base pay plus your taxable earnings come to $2,999.62, but the stub's Gross "
            "Pay is $2,999.63 ($0.01 apart).  Your amounts match the stub's Net Pay, so "
            "check the Gross Pay you typed, and which earnings you marked Taxable earning "
            "and which After-tax earning."
        )}

    def test_the_gross_is_checked_without_a_complete_set_of_taxes(self, world):
        """No tax enters the gross, so a missing Medicare does not hold its check back.

        The net is still not checked over the incomplete set (its own rule).
        """
        figures = _figures(world)
        medicare = _tax(WithholdingKindEnum.MEDICARE)
        del figures.withholdings[medicare]
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures, printed_gross=Decimal("2999.63"))
        assert set(refused.value.errors) == {f"tax-{medicare}", "printed_gross"}


def _say_yes(world):
    """Set the world's job to "my stub's gross includes after-tax earnings" (R-SAL102)."""
    world["profile"].stub_gross_includes_after_tax = True
    db.session.commit()


def _reimbursed(world, **departures):
    """The worked example with a $20.00 AFTER-TAX one-off "Reimbursement" for Retro pay.

    gross (base + Phone) 2884.62 + 60.00 = 2944.62; after-tax 20.00;
    net 2944.62 - 315.00 - 472.00 - 110.00 + 20.00 = 2067.62.  A stub whose
    gross holds the reimbursement prints 2964.62.
    """
    return _figures(world, one_offs=(
        OneOffFigure("Reimbursement", _kind(PaycheckLineKindEnum.AFTER_TAX_EARNING),
                     Decimal("20.00")),
    ), **departures)


def _reimbursed_without_phone(world, **departures):
    """:func:`_reimbursed` with no taxable earning at all: Phone is not on the stub.

    gross = base 2884.62; after-tax 20.00;
    net 2884.62 - 315.00 - 472.00 - 110.00 + 20.00 = 2007.62.
    """
    figures = _reimbursed(world, **departures)
    del figures.line_amounts[world["lines"]["phone"].id]
    return figures


class TestTheJobSaysWhatItsGrossHolds:
    """R-SAL102 ("A yes/no on each job", finding SAL-592), worded by R-SAL104 and R-SAL106.

    A "yes" job's printed gross is checked as base pay plus the taxable AND
    the after-tax earnings.  How a miss is worded is read off the net
    (R-SAL106): with the net exact, a miss of exactly the after-tax total in
    the direction that fits the job names the typed gross and the setting
    (R-SAL104's question beside them on a "no" job; R-SAL108, R-SAL110);
    with the net off, the pair decides and no setting is named.
    """

    def test_the_door_reads_the_jobs_answer(self, world):
        """The same stub, gross 2964.62 holding the reimbursement: "no" refuses, "yes" saves.

        "no": base + taxable = 2944.62, $20.00 short -- exactly the after-tax
        earnings, with the net exact -- so the refusal names the typed gross,
        asks after the entry and names the setting (R-SAL104, R-SAL110; worded
        by R-SAL114 and R-SAL116).
        """
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _reimbursed(world), printed_net=Decimal("2067.62"),
                    printed_gross=Decimal("2964.62"))
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable earnings come to $2,944.62, but the stub's "
                "Gross Pay is $2,964.62: $20.00 less, the same as your untaxed earnings.  "
                "Check the Gross Pay you typed.  If it's right, is one of those earnings "
                "taxed on your stub?  Then choose Taxable earning for it."
            ),
            "stub_gross_includes_after_tax": (
                "Or, if your stub counts untaxed pay in its Gross Pay,"
            ),
        }
        db.session.rollback()
        assert db.session.query(PayStub).count() == 0

        _say_yes(world)
        stub = _record(world, _reimbursed(world), printed_net=Decimal("2067.62"),
                       printed_gross=Decimal("2964.62"))
        assert [(o.name, o.amount) for o in stub.one_offs] == [
            ("Reimbursement", Decimal("20.00")),
        ]

    def test_an_edit_reads_the_stubs_own_job(self, world):
        """A "yes" job's stub takes an edit (Roth 110 -> 100, net +10.00) checked the same way."""
        _say_yes(world)
        stub = _record(world, _reimbursed(world), printed_net=Decimal("2067.62"),
                       printed_gross=Decimal("2964.62"))
        pay_stub_service.edit_stub(
            stub, _reimbursed(world, roth="100.00"),
            _printed(Decimal("2077.62"), Decimal("2964.62")), _ctx(world), _TODAY,
        )
        db.session.commit()
        assert stub.version_id == 2

    def test_a_yes_jobs_gross_that_leaves_them_out_names_the_setting(self, world):
        """"yes", but the stub prints 2944.62: the check's 2964.62 is $20.00 over, net exact.

        On a "yes" job no heading moves the gross, so with every amount right
        the pair allows the setting (ruling R-SAL106) or a typo in the gross
        box (ruling R-SAL110), and the refusal names both (worded by R-SAL114).
        """
        _say_yes(world)
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _reimbursed(world), printed_net=Decimal("2067.62"),
                    printed_gross=Decimal("2944.62"))
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your earnings, taxable and untaxed, come to $2,964.62, "
                "but the stub's Gross Pay is $2,944.62: $20.00 more, the same as your "
                "untaxed earnings.  Check the Gross Pay you typed."
            ),
            "stub_gross_includes_after_tax": (
                "If it's right, your stub leaves untaxed pay out of its Gross Pay:"
            ),
        }

    def test_a_deduction_entered_as_an_earning_is_named_by_the_net(self, world):
        """"yes", Roth ($110.00, a post-tax deduction) entered as an AFTER-TAX earning.

        The stub prints the worked example (gross 2999.62, net 2102.62).  The
        check makes 2999.62 + 110.00 = 3109.62, $110.00 over -- the after-tax
        total -- but the net misses by TWICE that, 2999.62 - 315.00 - 472.00
        + 110.00 = 2322.62 against 2102.62, which rules the setting out: the
        refusal asks after a deduction entered as an earning and names no
        setting (ruling R-SAL106; worded by R-SAL114).
        """
        _say_yes(world)
        figures = _printed_under(
            world, _figures(world), roth=PaycheckLineKindEnum.AFTER_TAX_EARNING,
        )
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures)
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your earnings, taxable and untaxed, come to $3,109.62, "
                "but the stub's Gross Pay is $2,999.62: $110.00 more, and your amounts "
                "come to $220.00 more than its Net Pay.  Is a deduction marked as an "
                "earning?  Check the kind chosen beside each amount."
            ),
            "printed_net": (
                "The lines add up to $2,322.62, but the stub prints $2,102.62 (a "
                "difference of $220.00).  Check each figure against the stub."
            ),
        }

    def test_a_gross_typed_as_base_pay_on_a_yes_job_is_asked_about(self, world):
        """"yes", the stub's gross 2964.62 typed into Base pay beside its earnings.

        The check makes 2964.62 + 60.00 + 20.00 = 3044.62: $80.00 counted
        twice, and the net misses by the same (2147.62 against 2067.62): asked
        as the Base pay box or an earning the stub does not print (R-SAL111),
        by the base pay the stub shows, 2964.62 - 80.00 = 2884.62 for the
        first (R-SAL112, after R-SAL120's check of both typed totals; worded by
        R-SAL114 and R-SAL117).
        """
        _say_yes(world)
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _reimbursed(world, base="2964.62"),
                    printed_net=Decimal("2067.62"), printed_gross=Decimal("2964.62"))
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your earnings, taxable and untaxed, come to $3,044.62, "
                "but the stub's Gross Pay is $2,964.62.  Check the Gross Pay and Net Pay "
                "you typed.  If they're right, what base pay does the stub show?  "
                "$2,884.62: you typed the Gross Pay into Base pay.  Type $2,884.62 there "
                "instead.  $2,964.62: you entered extra pay this stub doesn't list.  "
                "Remove it."
            ),
            "printed_net": (
                "The lines add up to $2,147.62, but the stub prints $2,067.62 (a "
                "difference of $80.00).  Check each figure against the stub."
            ),
        }

    def test_the_net_tells_the_doubled_gross_from_the_setting(self, world):
        """With no taxable earning both slips put base pay at the printed gross, $20.00 over.

        A "yes" stub printing base 2884.62 + Reimbursement 20.00 = 2904.62,
        that gross typed as base pay: the check's 2924.62 is $20.00 over AND
        the net misses by $20.00 (2027.62 against 2007.62) -- the Base pay box
        or an earning the stub does not list, asked by the base pay the stub
        shows (ruling R-SAL112, which closes review MED-1: the stub shows
        2884.62, so removing the reimbursement it DOES list, the other exit,
        no longer fits it).  A stub that leaves the reimbursement out prints
        2884.62, typed right: the same $20.00 over, but the net balances at
        2007.62 -- the setting (or the typed gross).  Read without the net,
        the first would name the setting.  Both typed totals are checked first
        (R-SAL120); worded by R-SAL114 and R-SAL117.
        """
        _say_yes(world)
        with pytest.raises(PayStubRefused) as doubled:
            _record(world, _reimbursed_without_phone(world, base="2904.62"),
                    printed_net=Decimal("2007.62"), printed_gross=Decimal("2904.62"))
        assert doubled.value.errors == {
            "printed_gross": (
                "Base pay plus your earnings, taxable and untaxed, come to $2,924.62, "
                "but the stub's Gross Pay is $2,904.62.  Check the Gross Pay and Net Pay "
                "you typed.  If they're right, what base pay does the stub show?  "
                "$2,884.62: you typed the Gross Pay into Base pay.  Type $2,884.62 there "
                "instead.  $2,904.62: you entered extra pay this stub doesn't list.  "
                "Remove it."
            ),
            "printed_net": (
                "The lines add up to $2,027.62, but the stub prints $2,007.62 (a "
                "difference of $20.00).  Check each figure against the stub."
            ),
        }
        db.session.rollback()
        with pytest.raises(PayStubRefused) as left_out:
            _record(world, _reimbursed_without_phone(world),
                    printed_net=Decimal("2007.62"), printed_gross=Decimal("2884.62"))
        assert set(left_out.value.errors) == {
            "printed_gross", "stub_gross_includes_after_tax",
        }
        assert left_out.value.errors["stub_gross_includes_after_tax"] == (
            "If it's right, your stub leaves untaxed pay out of its Gross Pay:"
        )

    @pytest.mark.parametrize(("yes", "printed_gross", "message"), [
        (False, "2924.62", (
            "Base pay plus your taxable earnings come to $2,944.62, but the stub's Gross "
            "Pay is $2,924.62 ($20.00 apart).  Your amounts match the stub's Net Pay, so "
            "check the Gross Pay you typed, and which earnings you marked Taxable earning "
            "and which After-tax earning."
        )),
        (True, "2984.62", (
            "Base pay plus your earnings, taxable and untaxed, come to $2,964.62, but "
            "the stub's Gross Pay is $2,984.62 ($20.00 apart).  Your amounts match the "
            "stub's Net Pay, so check the Gross Pay you typed."
        )),
    ], ids=["no-job-over", "yes-job-short"])
    def test_the_after_tax_total_the_wrong_way_names_no_setting(
        self, world, yes, printed_gross, message,
    ):
        """A miss of $20.00 the OTHER way, net exact, names no setting.

        "no": 2924.62 printed is $20.00 UNDER the check (2944.62) -- no
        after-tax earning a gross holds makes that.  "yes": 2984.62 is $20.00
        ABOVE the check (2964.62) -- the reverse of a gross that leaves them
        out.  The net is exact, so the typed gross is named, and on "no" the
        headings too (rulings R-SAL106, R-SAL108); on "yes" no heading moves
        the gross.  Worded by R-SAL114 and R-SAL116.
        """
        if yes:
            _say_yes(world)
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _reimbursed(world), printed_net=Decimal("2067.62"),
                    printed_gross=Decimal(printed_gross))
        assert refused.value.errors == {"printed_gross": message}

    def test_an_earning_the_stub_does_not_print_is_asked_beside_the_base_box(self, world):
        """A "no" job whose stub prints no taxable earning; Phone ($60.00) entered anyway.

        Base pay is typed right and equals the printed gross, 2884.62; the
        stub nets 2884.62 - 315.00 - 472.00 - 110.00 = 1987.62.  The lines
        make 2884.62 + 60.00 = 2944.62 and net 2047.62: both $60.00 over --
        the pair the gross typed into Base pay makes too, so both are asked
        and neither is stated (ruling R-SAL111; review round 3, MEDIUM-1,
        where "is counted twice" sent the owner to lower base pay and save).
        The stub shows 2884.62, the second answer, and not 2884.62 - 60.00 =
        2824.62, the first (ruling R-SAL112, after R-SAL120's check of both
        typed totals; worded by R-SAL114 and R-SAL117).
        """
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _figures(world, one_offs=()),
                    printed_net=Decimal("1987.62"), printed_gross=Decimal("2884.62"))
        assert refused.value.errors["printed_gross"] == (
            "Base pay plus your taxable earnings come to $2,944.62, but the stub's Gross "
            "Pay is $2,884.62.  Check the Gross Pay and Net Pay you typed.  If they're "
            "right, what base pay does the stub show?  $2,824.62: you typed the Gross Pay "
            "into Base pay.  Type $2,824.62 there instead.  $2,884.62: you entered extra "
            "pay this stub doesn't list.  Remove it."
        )
        assert set(refused.value.errors) == {"printed_gross", "printed_net"}

    @pytest.mark.parametrize(("phone", "message"), [
        ("6000.00", (
            "Base pay plus your taxable earnings come to $8,884.62, but the stub's Gross "
            "Pay is $2,884.62.  Check the Gross Pay and Net Pay you typed.  If they're "
            "right, you entered extra pay this stub doesn't list.  Remove it."
        )),
        ("2884.62", (
            "Base pay plus your taxable earnings come to $5,769.24, but the stub's Gross "
            "Pay is $2,884.62.  Check the Gross Pay and Net Pay you typed.  If they're "
            "right, you entered extra pay this stub doesn't list.  Remove it."
        )),
        ("2884.61", (
            "Base pay plus your taxable earnings come to $5,769.23, but the stub's Gross "
            "Pay is $2,884.62.  Check the Gross Pay and Net Pay you typed.  If they're "
            "right, what base pay does the stub show?  $0.01: you typed the Gross Pay "
            "into Base pay.  Type $0.01 there instead.  $2,884.62: you entered extra pay "
            "this stub doesn't list.  Remove it."
        )),
    ], ids=["above-the-base-pay", "the-whole-base-pay", "a-cent-under"])
    def test_an_earning_of_the_whole_base_pay_names_only_the_extra_pay(
        self, world, phone, message,
    ):
        """Ruling R-SAL119: a stub's base pay is above zero, so an answer below it goes.

        A "no" job whose stub prints no taxable earning (gross = base 2884.62,
        net 2884.62 - 897.00 = 1987.62), Phone entered at *phone*: both totals
        miss by Phone's amount, the pair of the gross typed into Base pay and
        of an earning the stub does not list (on a stub that lists none, any
        earning entered is extra, whatever its size).  R-SAL112's first answer
        would be 2884.62 less that amount: below zero for an earning larger
        than the whole base pay and 0.00 for one of exactly the base pay,
        neither a base pay, so only the extra pay is named; a cent under
        leaves 0.01, a base pay, and the question stands.  Either way both
        typed totals are checked first (R-SAL120).
        """
        figures = _figures(world, one_offs=())
        phone_line = world["lines"]["phone"]
        figures.line_amounts[phone_line.id] = LineFigure(
            phone_line.paycheck_line_kind_id, Decimal(phone),
        )
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures, printed_net=Decimal("1987.62"),
                    printed_gross=Decimal("2884.62"))
        assert refused.value.errors["printed_gross"] == message
        assert set(refused.value.errors) == {"printed_gross", "printed_net"}

    def test_a_base_pay_typo_of_the_after_tax_total_is_an_amount(self, world):
        """A "no" job, base typed 2864.62 for 2884.62 -- $20.00, the after-tax total.

        The stub's gross leaves the reimbursement out (2944.62).  The check
        makes 2864.62 + 60.00 = 2924.62, $20.00 short like R-SAL104's figure,
        but the net is off by the same $20.00 (2047.62 against 2067.62), which
        neither of R-SAL104's causes makes: an amount is wrong, and no setting
        is named (ruling R-SAL106; adversarial review round 1, M-1 (a); worded
        by R-SAL114).
        """
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _reimbursed(world, base="2864.62"),
                    printed_net=Decimal("2067.62"), printed_gross=Decimal("2944.62"))
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable earnings come to $2,924.62, but the stub's "
                "Gross Pay is $2,944.62 ($20.00 apart), and your amounts are $20.00 "
                "apart from its Net Pay too.  One amount is wrong: check Base pay and "
                "each earning."
            ),
            "printed_net": (
                "The lines add up to $2,047.62, but the stub prints $2,067.62 (a "
                "difference of $20.00).  Check each figure against the stub."
            ),
        }

    def test_the_doubled_gross_beside_a_second_mistake_names_no_setting(self, world):
        """"yes", no taxable earning: the gross 2904.62 typed as base pay AND Roth 100 for 110.

        The check makes 2904.62 + 20.00 = 2924.62, $20.00 over -- the after-tax
        total -- and the net 2904.62 - 315.00 - 472.00 - 100.00 + 20.00 =
        2037.62 is $30.00 off 2007.62: a pair no single mistake makes, so the
        difference alone (ruling R-SAL106; review round 1, M-1 (b), where the
        setting's exit ended in the reimbursement saved as base pay; worded by
        R-SAL114).
        """
        _say_yes(world)
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _reimbursed_without_phone(world, base="2904.62", roth="100.00"),
                    printed_net=Decimal("2007.62"), printed_gross=Decimal("2904.62"))
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your earnings, taxable and untaxed, come to $2,924.62, "
                "but the stub's Gross Pay is $2,904.62 ($20.00 apart).  Check each "
                "amount against the stub."
            ),
            "printed_net": (
                "The lines add up to $2,037.62, but the stub prints $2,007.62 (a "
                "difference of $30.00).  Check each figure against the stub."
            ),
        }

    def test_an_earning_entered_as_a_deduction_is_the_mirror(self, world):
        """A "no" job, Phone ($60.00 taxable) entered as a POST-TAX deduction.

        The check makes 2884.62 + 55.00 = 2939.62, $60.00 short of 2999.62,
        and the net 2939.62 - 315.00 - 472.00 - 170.00 = 1982.62 is $120.00
        short of 2102.62 -- twice: an earning entered as a deduction (worded
        by R-SAL114).
        """
        figures = _printed_under(
            world, _figures(world), phone=PaycheckLineKindEnum.POST_TAX_DEDUCTION,
        )
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures)
        assert refused.value.errors["printed_gross"] == (
            "Base pay plus your taxable earnings come to $2,939.62, but the stub's Gross "
            "Pay is $2,999.62: $60.00 less, and your amounts come to $120.00 less than "
            "its Net Pay.  Is an earning marked as a deduction?  Check the kind chosen "
            "beside each amount."
        )
        assert set(refused.value.errors) == {"printed_gross", "printed_net"}

    def test_yes_cannot_see_an_earning_under_the_other_heading(self, world):
        """R-SAL102's accepted cost: Phone (taxed on the stub) entered as AFTER-TAX saves.

        "yes": the check adds the after-tax earnings too, so 2884.62 + 60.00 +
        20.00 = 2964.62 makes the printed gross, and the net balances at
        2884.62 - 897.00 + 80.00 = 2067.62.  Only the stub's page shows the
        slip: Phone's kind beside its paycheck line's.
        """
        _say_yes(world)
        figures = _printed_under(
            world, _reimbursed(world), phone=PaycheckLineKindEnum.AFTER_TAX_EARNING,
        )
        stub = _record(world, figures, printed_net=Decimal("2067.62"),
                       printed_gross=Decimal("2964.62"))
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        phone = {row.name: row for row in report.lines}["Phone Allowance"]
        assert phone.kind_agrees is False


class TestTheCheckedGross:
    """The ONE spelling of what Gross Pay a stub's figures make (ruling R-SAL127).

    :func:`app.services.pay_stub_gross.checked_gross` is the rule the door's
    check reads and plan step salary:S11-d's rehearsal measure adds a saved
    stub up with: base pay plus the taxable earnings (R-SAL99), and on a "yes"
    job the after-tax earnings too (R-SAL102).  Graded on totals built by
    hand, so a figure here moves only with the rule.
    """

    #: Base pay 2884.62 + taxable 115.00 = gross 2999.62; after-tax 20.00.
    _TOTALS = StubTotals(
        gross=Decimal("2999.62"), pre_tax=Decimal("315.00"), taxes=Decimal("472.00"),
        post_tax=Decimal("110.00"), after_tax=Decimal("20.00"), net=Decimal("2122.62"),
    )

    def test_a_no_job_counts_the_taxable_earnings_alone(self):
        """A "no" job: the totals' gross, 2999.62, the after-tax 20.00 left out."""
        assert pay_stub_gross.checked_gross(
            self._TOTALS, includes_after_tax=False,
        ) == Decimal("2999.62")

    def test_a_yes_job_adds_the_after_tax_earnings(self):
        """A "yes" job: 2999.62 + 20.00 = 3019.62."""
        assert pay_stub_gross.checked_gross(
            self._TOTALS, includes_after_tax=True,
        ) == Decimal("3019.62")

    def test_the_door_refuses_by_the_same_figure(self, world):
        """The door's refusal quotes the figure the rule makes, on a "no" job.

        Base pay 2884.62 + Phone 60.00 + Retro pay 55.00 = 2999.62 (the worked
        example), printed as 3000.62: the refusal names $2,999.62, which is
        :func:`checked_gross` over the same figures' totals.
        """
        figures = _figures(world)
        totals = pay_stub_service.totals_of(world["profile"], figures)
        checked = pay_stub_gross.checked_gross(totals, includes_after_tax=False)
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures, printed_gross=Decimal("3000.62"))
        assert checked == Decimal("2999.62")
        assert refused.value.errors["printed_gross"].startswith(
            f"Base pay plus your taxable earnings come to ${checked:,.2f}, "
        )
