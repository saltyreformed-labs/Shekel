"""
Shekel Budget App -- the pay stub entry door's service (plan step salary:S11-b).

What :mod:`app.services.pay_stub_service` decides, graded without a request:

* which dates can carry a stub -- a payday the app holds or projects, up to
  the owner's next one (rulings **R-SAL48**, **R-SAL49**);
* what a stub's figures add up to, through the one waterfall a priced
  paycheck's net uses, and the printed-net check against it (**R-SAL42**);
* the one-off name clash, ignoring capitals and extra spaces (**R-SAL45**,
  **R-SAL51** (b)), asked only of a one-off a save adds or renames
  (**R-SAL57**);
* that a stub line adds up by the kind the STUB prints it under, a re-kind of
  its paycheck line moving no saved stub (**R-SAL58**, finding SAL-567);
* that a record on a held payday and an edit onto one are both refused
  (**R-SAL52**), every tax is required, and an edit writes the stub ROW once
  -- so its version guards a child-only change -- while an identical
  re-submit writes nothing;
* the comparison report: each line beside the app's figure that payday, and
  the base-pay gap (forks 2 and 5);
* the paycheck-line delete refusal's wording (**R-SAL51** (c)).

The worked example it prices is in ``tests/test_services/_pay_stub_example.py``,
shared with ``test_pay_stub_gross.py`` (the printed-gross checks, split out of
this module at plan step salary:S11-c-2c, ledger row **SAL-594**).
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm.exc import StaleDataError

from app.enums import PaycheckLineKindEnum, WithholdingKindEnum
from app.exceptions import NotFoundError, PayStubRefused
from app.extensions import db
from app.models.pay_stub import PayStub, PayStubLineAmount, PayStubOneOff
from app.services import pay_stub_service
from app.services.pay_stub_service import (
    LineFigure,
    OneOffFigure,
    StubFigures,
)
from tests._test_helpers import (
    make_flat_paycheck_line,
    make_salary_profile,
)
# The worked example and its helpers have one home (plan step salary:S11-c-2c,
# SAL-594); each is imported under the name this suite always used, and
# importing ``world_fixture`` registers the ``world`` fixture here.
from tests.test_services._pay_stub_example import (  # pylint: disable=unused-import
    GROSS as _GROSS,
    NET as _NET,
    PAYDAY as _PAYDAY,
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


class TestWhichDaysCarryAStub:
    """R-SAL49 (a payday the app holds or projects) and R-SAL48 (up to the next)."""

    def test_a_payday_up_to_the_next_one_is_accepted(self, world):
        """03-27 is the next payday on 03-20; 03-13 and the first, 01-02, are past."""
        for day in (date(2026, 3, 27), date(2026, 3, 13), date(2026, 1, 2)):
            assert pay_stub_service.payday_refusal(_ctx(world), day, _TODAY) is None

    def test_a_payday_after_the_next_one_is_refused_until_it_passes(self, world):
        """04-10 is refused on 03-20 and on 03-27 itself, and accepted on 03-28."""
        refusal = pay_stub_service.payday_refusal(_ctx(world), date(2026, 4, 10), _TODAY)
        assert refusal == (
            "2026-04-10 has not been paid yet.  A stub can be entered up to your "
            "next payday, 2026-03-27."
        )
        assert pay_stub_service.payday_refusal(
            _ctx(world), date(2026, 4, 10), date(2026, 3, 27),
        ) is not None
        assert pay_stub_service.payday_refusal(
            _ctx(world), date(2026, 4, 10), date(2026, 3, 28),
        ) is None

    def test_a_day_that_is_not_a_payday_is_refused(self, world):
        """03-20 falls inside the 03-13 paycheck, so it opens none."""
        assert pay_stub_service.payday_refusal(
            _ctx(world), date(2026, 3, 20), _TODAY,
        ) == "2026-03-20 is not one of your paydays."

    def test_a_day_below_the_record_is_refused_as_the_records(self, world):
        """A rhythm payday before 01-02 opens no paycheck the app holds (R-SAL49)."""
        assert pay_stub_service.payday_refusal(
            _ctx(world), date(2025, 12, 19), _TODAY,
        ) == "The app holds no paycheck on 2025-12-19: your pay record starts 2026-01-02."


class TestWhatAStubAddsUpTo:
    """The derived totals and the printed-net check."""

    def test_the_worked_example_adds_up_by_hand(self, world):
        """Each line by its own kind, the one-off by its kind, the waterfall's net."""
        totals = pay_stub_service.totals_of(world["profile"], _figures(world))
        assert totals.gross == Decimal("2999.62")
        assert totals.pre_tax == Decimal("315.00")
        assert totals.taxes == Decimal("472.00")
        assert totals.post_tax == Decimal("110.00")
        assert totals.after_tax == Decimal("0.00")
        assert totals.net == _NET

    def test_an_after_tax_one_off_joins_the_net_and_not_the_gross(self, world):
        """A $20.00 after-tax one-off in place of the taxable one: gross 2944.62, net 2067.62."""
        figures = _figures(world, one_offs=(
            OneOffFigure("Reimbursement", _kind(PaycheckLineKindEnum.AFTER_TAX_EARNING),
                         Decimal("20.00")),
        ))
        totals = pay_stub_service.totals_of(world["profile"], figures)
        assert totals.gross == Decimal("2944.62")
        assert totals.after_tax == Decimal("20.00")
        assert totals.net == Decimal("2067.62")

    def test_a_printed_net_a_cent_off_is_refused_and_nothing_is_written(self, world):
        """The lines make $2,102.62; a stub read as $2,102.63 is a typing slip."""
        with pytest.raises(PayStubRefused) as refused:
            _record(world, printed_net=Decimal("2102.63"))
        assert refused.value.errors == {"printed_net": (
            "The lines add up to $2,102.62, but the stub prints $2,102.63 (a "
            "difference of $0.01).  Check each figure against the stub."
        )}
        # Counted BEFORE any rollback: the query autoflushes, so a stub the
        # refused call had staged would be written and counted here.
        assert db.session.query(PayStub).count() == 0
        db.session.rollback()

    def test_a_line_adds_up_by_the_kind_the_stub_prints_it_under(self, world):
        """R-SAL58: Phone printed as a POST-TAX DEDUCTION, not the line's taxable earning.

        gross 2884.62 + 55.00 = 2939.62; post-tax 110.00 + 60.00 = 170.00;
        net 2939.62 - 315.00 - 472.00 - 170.00 = 1982.62.  The gross R-SAL99
        checks is that 2939.62, and the printed-net check reads the same kinds,
        so that net records and the line's own kind's $2,102.62 is refused.
        """
        figures = _printed_under(
            world, _figures(world), phone=PaycheckLineKindEnum.POST_TAX_DEDUCTION,
        )
        totals = pay_stub_service.totals_of(world["profile"], figures)
        assert (totals.gross, totals.post_tax, totals.net) == (
            Decimal("2939.62"), Decimal("170.00"), Decimal("1982.62"),
        )
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures, printed_gross=Decimal("2939.62"))
        assert set(refused.value.errors) == {"printed_net"}
        db.session.rollback()
        assert _record(
            world, figures, printed_net=Decimal("1982.62"), printed_gross=Decimal("2939.62"),
        ) is not None

    def test_re_kinding_a_line_moves_no_saved_stub(self, world):
        """Finding SAL-567: Phone's LINE turns post-tax; the saved stub still nets $2,102.62.

        Until R-SAL58 the stub borrowed each line's kind, and this re-kind
        moved its net to $1,982.62 with no edit to the stub.
        """
        stub_id = _record(world).id
        phone = world["lines"]["phone"]
        phone.paycheck_line_kind_id = _kind(PaycheckLineKindEnum.POST_TAX_DEDUCTION)
        db.session.commit()
        db.session.expire_all()

        stub = db.session.get(PayStub, stub_id)
        figures = pay_stub_service.figures_of(stub)
        assert figures.line_amounts[phone.id].paycheck_line_kind_id == (
            _kind(PaycheckLineKindEnum.TAXABLE_EARNING)
        )
        assert pay_stub_service.totals_of(world["profile"], figures).net == _NET

    def test_a_kind_the_app_does_not_hold_is_not_found(self, world):
        """A tampered line kind is a 404, as a tampered one-off kind is, never a write."""
        unknown = db.session.execute(
            text("SELECT MAX(id) + 1 FROM ref.paycheck_line_kinds"),
        ).scalar()
        figures = _figures(world)
        phone = world["lines"]["phone"].id
        figures.line_amounts[phone] = LineFigure(unknown, Decimal("60.00"))
        with pytest.raises(NotFoundError):
            _record(world, figures)
        db.session.rollback()
        assert db.session.query(PayStub).count() == 0

    def test_a_missing_tax_is_refused_and_the_net_is_not_blamed(self, world):
        """Medicare left out of a stub whose printed net ($2,102.62) is right.

        'The four taxes required' is the service's rule, and the net is not
        checked over an incomplete set: without Medicare the lines sum to
        $2,144.62, and a net refusal would blame figures that are correct.
        """
        figures = _figures(world)
        medicare = _tax(WithholdingKindEnum.MEDICARE)
        del figures.withholdings[medicare]
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures)
        assert refused.value.errors == {
            f"tax-{medicare}": "Enter the stub's Medicare ($0.00 if none).",
        }


class TestTheOneOffNameClash:
    """R-SAL45, compared ignoring capitals and extra spaces (R-SAL51 (b))."""

    def _refused(self, world, names):
        """Record the example with one-offs named *names*; return the refusals."""
        one_offs = tuple(
            OneOffFigure(name, _kind(PaycheckLineKindEnum.TAXABLE_EARNING), Decimal("0.00"))
            for name in names
        )
        figures = _figures(world, one_offs=one_offs)
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures, printed_net=_NET - Decimal("55.00"),
                    printed_gross=_GROSS - Decimal("55.00"))
        return refused.value.errors

    def test_a_one_off_named_like_a_paycheck_line_is_refused(self, world):
        """'  health   INSURANCE ' is the Health Insurance line."""
        assert self._refused(world, ["  health   INSURANCE "]) == {"one_off:0": (
            "'  health   INSURANCE ' is your paycheck line 'Health Insurance'; "
            "enter it on the line instead."
        )}

    def test_a_one_off_named_like_a_tax_is_refused(self, world):
        """'medicare' is the Medicare tax."""
        assert self._refused(world, ["medicare"]) == {"one_off:0": (
            "'medicare' is the tax 'Medicare'; enter it with the taxes instead."
        )}

    def test_two_one_offs_differing_only_in_capitals_are_refused(self, world):
        """'Bonus' and 'bonus' are one name; the second is the one refused."""
        assert self._refused(world, ["Bonus", "bonus"]) == {
            "one_off:1": "Two one-offs are named 'bonus'.",
        }

    def test_a_distinct_name_is_stored(self, world):
        """The control: 'Retro pay' clashes with nothing and is recorded."""
        stub = _record(world)
        assert [(o.name, o.amount) for o in stub.one_offs] == [("Retro pay", Decimal("55.00"))]


class TestTheClashIsAskedOnlyOfWhatASaveAdds:
    """R-SAL57, "Check only what a save adds": a saved one-off never blocks its own stub.

    Each case records the worked example (its one-off "Retro pay") and THEN
    gives the profile a paycheck line named "Retro pay", which the line door
    allows ("line names stay free").
    """

    @staticmethod
    def _stub_whose_one_off_a_line_now_names(world):
        """The recorded 03-27 stub, and a $5.00 taxable line named like its one-off."""
        stub = _record(world)
        make_flat_paycheck_line(
            world["profile"], "Retro pay", "5.00", PaycheckLineKindEnum.TAXABLE_EARNING,
        )
        db.session.commit()
        return stub

    @staticmethod
    def _one_offs(*rows):
        """Taxable-earning one-offs from ``(name, amount)`` pairs."""
        return tuple(
            OneOffFigure(name, _kind(PaycheckLineKindEnum.TAXABLE_EARNING), Decimal(amount))
            for name, amount in rows
        )

    def test_an_edit_that_keeps_the_one_off_is_saved(self, world):
        """Roth 110 -> 100 (net +10.00): the kept "Retro pay" does not block the edit."""
        stub = self._stub_whose_one_off_a_line_now_names(world)
        pay_stub_service.edit_stub(
            stub, _figures(world, roth="100.00"), _printed(_NET + 10), _ctx(world), _TODAY,
        )
        db.session.commit()
        assert stub.version_id == 2
        assert [o.name for o in stub.one_offs] == ["Retro pay"]

    def test_re_casing_the_kept_one_off_is_not_a_new_clash(self, world):
        """"RETRO PAY" is the saved one-off's own name in the clash's compared form."""
        stub = self._stub_whose_one_off_a_line_now_names(world)
        pay_stub_service.edit_stub(
            stub, _figures(world, one_offs=self._one_offs(("RETRO PAY", "55.00"))),
            _printed(), _ctx(world), _TODAY,
        )
        db.session.commit()
        assert [o.name for o in stub.one_offs] == ["RETRO PAY"]

    def test_a_one_off_the_edit_adds_is_still_checked(self, world):
        """A second one-off "vision" is new, and is the Vision line."""
        stub = self._stub_whose_one_off_a_line_now_names(world)
        with pytest.raises(PayStubRefused) as refused:
            pay_stub_service.edit_stub(
                stub,
                _figures(world, one_offs=self._one_offs(
                    ("Retro pay", "55.00"), ("vision", "0.00"),
                )),
                _printed(), _ctx(world), _TODAY,
            )
        assert refused.value.errors == {"one_off:1": (
            "'vision' is your paycheck line 'Vision'; enter it on the line instead."
        )}

    def test_renaming_the_one_off_onto_a_lines_name_is_checked(self, world):
        """"Retro pay" renamed "dental" is a name the stub does not hold: the Dental line."""
        stub = self._stub_whose_one_off_a_line_now_names(world)
        with pytest.raises(PayStubRefused) as refused:
            pay_stub_service.edit_stub(
                stub, _figures(world, one_offs=self._one_offs(("dental", "55.00"))),
                _printed(), _ctx(world), _TODAY,
            )
        assert refused.value.errors == {"one_off:0": (
            "'dental' is your paycheck line 'Dental'; enter it on the line instead."
        )}


class TestRecording:
    """What a record writes, and whose keys place it."""

    def test_the_stub_and_its_rows_are_written_under_the_owned_profile(self, world):
        """One stub, four line amounts, four taxes, one one-off; switch on; version 1."""
        stub = _record(world)
        db.session.expire_all()
        stub = db.session.get(PayStub, stub.id)
        assert stub.salary_profile_id == world["profile"].id
        assert stub.use_for_pricing is True
        assert stub.version_id == 1
        assert sorted(
            (row.paycheck_line_id, row.amount, row.salary_profile_id)
            for row in stub.line_amounts
        ) == sorted([
            (world["lines"]["health"].id, Decimal("280.00"), world["profile"].id),
            (world["lines"]["dental"].id, Decimal("35.00"), world["profile"].id),
            (world["lines"]["roth"].id, Decimal("110.00"), world["profile"].id),
            (world["lines"]["phone"].id, Decimal("60.00"), world["profile"].id),
        ])
        assert len(stub.withholdings) == 4
        assert pay_stub_service.totals_of(
            world["profile"], pay_stub_service.figures_of(stub),
        ).net == _NET

    def test_a_line_of_another_profile_is_not_found(self, world, seed_user):
        """A second profile's line id in the figures is a 404, never a write."""
        other = make_salary_profile(seed_user, db.session, name="Side Job")
        db.session.flush()
        foreign = make_flat_paycheck_line(
            other, "Foreign", "1.00", PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
        )
        figures = _figures(world)
        figures = StubFigures(
            payday=figures.payday, base_pay=figures.base_pay,
            line_amounts={
                **figures.line_amounts,
                foreign.id: LineFigure(foreign.paycheck_line_kind_id, Decimal("1.00")),
            },
            withholdings=figures.withholdings, one_offs=figures.one_offs, notes=None,
        )
        with pytest.raises(NotFoundError):
            _record(world, figures, printed_net=_NET - Decimal("1.00"))

    def test_recording_a_held_payday_is_refused(self, world):
        """R-SAL52: a second record of 03-27 (Roth 100) cannot overwrite the first (Roth 110)."""
        first = _record(world)
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _figures(world, roth="100.00"), printed_net=_NET + 10)
        assert refused.value.errors == {"payday": (
            "Your 2026-03-27 stub was entered while this form was open; open it "
            "to change it."
        )}
        db.session.rollback()
        assert db.session.query(PayStub).count() == 1
        roth = world["lines"]["roth"].id
        assert {row.paycheck_line_id: row.amount for row in first.line_amounts}[roth] == (
            Decimal("110.00")
        )


class TestEditing:
    """An edit rewrites the stub row by row and writes the row itself."""

    def test_a_child_only_edit_bumps_the_stubs_version(self, world):
        """Only Roth's amount changes, and the row's counter still moves 1 -> 2."""
        stub = _record(world)
        pay_stub_service.edit_stub(
            stub, _figures(world, roth="100.00"), _printed(_NET + 10), _ctx(world), _TODAY,
        )
        db.session.commit()
        assert stub.version_id == 2

    def test_a_field_and_line_edit_bumps_the_version_once(self, world):
        """Base pay AND Roth change in one edit: 1 -> 2, not 1 -> 3."""
        stub = _record(world)
        pay_stub_service.edit_stub(
            stub, _figures(world, roth="100.00", base="2884.58"),
            _printed(_NET + 10 - Decimal("0.04"), _GROSS - Decimal("0.04")),
            _ctx(world), _TODAY,
        )
        db.session.commit()
        assert stub.version_id == 2

    def test_an_edit_of_a_lines_kind_alone_rewrites_it_and_bumps_once(self, world):
        """Phone re-read as an after-tax earning: its row's kind moves, 1 -> 2.

        The net is unchanged; the gross R-SAL99 checks is $2,939.62, without it.
        """
        stub = _record(world)
        pay_stub_service.edit_stub(
            stub,
            _printed_under(world, _figures(world), phone=PaycheckLineKindEnum.AFTER_TAX_EARNING),
            _printed(gross=Decimal("2939.62")), _ctx(world), _TODAY,
        )
        db.session.commit()
        assert stub.version_id == 2
        phone = world["lines"]["phone"].id
        assert {row.paycheck_line_id: row.paycheck_line_kind_id for row in stub.line_amounts}[
            phone
        ] == _kind(PaycheckLineKindEnum.AFTER_TAX_EARNING)

    def test_an_identical_edit_writes_nothing(self, world):
        """The same figures again: no row changes and the counter stays at 1."""
        stub = _record(world)
        pay_stub_service.edit_stub(stub, _figures(world), _printed(), _ctx(world), _TODAY)
        db.session.commit()
        assert stub.version_id == 1

    def test_a_child_only_edit_races_on_the_stubs_version(self, world):
        """Another writer bumped the row; the child-only edit's flush is refused."""
        stub = _record(world)
        db.session.execute(
            text("UPDATE salary.pay_stubs SET version_id = version_id + 1 WHERE id = :id"),
            {"id": stub.id},
        )
        with pytest.raises(StaleDataError):
            pay_stub_service.edit_stub(
                stub, _figures(world, roth="100.00"), _printed(_NET + 10), _ctx(world), _TODAY,
            )
        db.session.rollback()

    def test_an_edit_onto_a_held_payday_is_refused(self, world):
        """Moving the 03-27 stub onto 03-13, which holds a stub, is refused."""
        _record(world, _figures(world, payday=date(2026, 3, 13)))
        stub = _record(world)
        with pytest.raises(PayStubRefused) as refused:
            pay_stub_service.edit_stub(
                stub, _figures(world, payday=date(2026, 3, 13)), _printed(),
                _ctx(world), _TODAY,
            )
        assert refused.value.errors == {
            "payday": "Your 2026-03-13 stub already exists; open it to change it.",
        }

    def test_a_stub_whose_payday_left_the_record_stays_editable(self, world):
        """R-SAL53: a 03-20 stub (no paycheck there) takes an edit that keeps its date."""
        stub = PayStub(salary_profile_id=world["profile"].id, payday=date(2026, 3, 20),
                       base_pay=Decimal("2884.62"))
        db.session.add(stub)
        db.session.commit()
        pay_stub_service.edit_stub(
            stub, _figures(world, payday=date(2026, 3, 20)), _printed(), _ctx(world), _TODAY,
        )
        db.session.commit()
        assert len(stub.line_amounts) == 4

    def test_an_edit_that_moves_the_date_is_checked(self, world):
        """R-SAL53's other half: moving the 03-27 stub onto 03-20 is refused."""
        stub = _record(world)
        with pytest.raises(PayStubRefused) as refused:
            pay_stub_service.edit_stub(
                stub, _figures(world, payday=date(2026, 3, 20)), _printed(), _ctx(world),
                _TODAY,
            )
        assert refused.value.errors == {"payday": "2026-03-20 is not one of your paydays."}

    def test_an_edit_removes_a_line_and_renames_a_one_off(self, world):
        """Dropping Dental deletes its row; renaming the one-off replaces its row."""
        stub = _record(world)
        figures = _figures(world, one_offs=(
            OneOffFigure("Retro pay adjustment", _kind(PaycheckLineKindEnum.TAXABLE_EARNING),
                         Decimal("55.00")),
        ))
        del figures.line_amounts[world["lines"]["dental"].id]
        pay_stub_service.edit_stub(stub, figures, _printed(_NET + 35), _ctx(world), _TODAY)
        db.session.commit()
        assert db.session.query(PayStubLineAmount).filter_by(
            paycheck_line_id=world["lines"]["dental"].id,
        ).count() == 0
        assert [o.name for o in db.session.query(PayStubOneOff).all()] == [
            "Retro pay adjustment",
        ]

    def test_the_switch_turns_and_an_identical_request_writes_nothing(self, world):
        """Off, then off again (no move), then on."""
        stub = _record(world)
        assert pay_stub_service.set_use_for_pricing(stub, False) is True
        db.session.commit()
        assert pay_stub_service.set_use_for_pricing(stub, False) is False
        assert pay_stub_service.set_use_for_pricing(stub, True) is True
        db.session.commit()
        assert stub.use_for_pricing is True
        assert stub.version_id == 3


class TestTheReport:
    """Each line beside the app's figure that payday, and the base-pay gap."""

    def test_the_report_says_whether_a_one_off_changes_the_taxes(self, world):
        """The stub page's one-off sentence follows the picker's rule (ruling R-SAL123).

        Plan step salary:S11-c-2c: the worked example with a ``$20.00``
        after-tax "Reimbursement" in place of its taxable "Retro pay" (gross 2,944.62, net
        2,944.62 - 315.00 - 472.00 - 110.00 + 20.00 = 2,067.62) does not.
        """
        untaxed = _record(
            world,
            _figures(world, one_offs=(
                OneOffFigure("Reimbursement", _kind(PaycheckLineKindEnum.AFTER_TAX_EARNING),
                             Decimal("20.00")),
            )),
            printed_net=Decimal("2067.62"), printed_gross=Decimal("2944.62"),
        )
        report = pay_stub_service.stub_report(world["profile"], untaxed, _ctx(world))
        assert report.one_off_changes_taxes is False

    def test_a_taxable_one_off_is_reported_as_changing_the_taxes(self, world):
        """The worked example's taxable "Retro pay" one-off: the sentence shows (R-SAL123)."""
        stub = _record(world)
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        assert report.one_off_changes_taxes is True

    def test_each_line_is_set_beside_the_apps_figure(self, world):
        """Health and Phone agree; Roth differs; Dental is not taken; Vision is not on the stub."""
        stub = _record(world)
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        rows = {row.name: (row.on_stub, row.in_app, row.agrees) for row in report.lines}
        assert rows == {
            "Health Insurance": (Decimal("280.00"), Decimal("280.00"), True),
            "Vision": (None, Decimal("12.00"), False),
            "Dental": (Decimal("35.00"), None, False),
            "Roth IRA": (Decimal("110.00"), Decimal("100.00"), False),
            "Phone Allowance": (Decimal("60.00"), Decimal("60.00"), True),
        }
        assert report.disagreements == 3
        assert report.app_base_pay == Decimal("2884.62")
        assert report.base_gap == Decimal("0.00")
        assert report.totals.net == _NET
        assert [(o.name, o.amount) for o in report.one_offs] == [
            ("Retro pay", Decimal("55.00")),
        ]

    def test_a_kind_the_stub_prints_differently_is_listed(self, world):
        """R-SAL58: Phone printed as an AFTER-TAX earning, the line's a taxable one.

        The net is the same either way -- an earning joins the deposit whether
        it is taxed or not, once the taxes are typed: gross 2939.62, after-tax
        60.00, net 2939.62 - 315.00 - 472.00 - 110.00 + 60.00 = 2102.62.  R-SAL99
        reads the printed gross as base pay plus the TAXABLE earnings, so the
        record is checked against 2939.62, passes both checks, and the REPORT
        is what shows it.  The figures agree; the row still counts as a
        disagreement.
        """
        figures = _printed_under(
            world, _figures(world), phone=PaycheckLineKindEnum.AFTER_TAX_EARNING,
        )
        stub = _record(world, figures, printed_gross=Decimal("2939.62"))
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        phone = {row.name: row for row in report.lines}["Phone Allowance"]
        assert (phone.on_stub, phone.in_app) == (Decimal("60.00"), Decimal("60.00"))
        assert (phone.stub_kind, phone.kind) == (
            PaycheckLineKindEnum.AFTER_TAX_EARNING, PaycheckLineKindEnum.TAXABLE_EARNING,
        )
        assert (phone.stub_kind_label, phone.kind_label) == (
            "After-tax earning", "Taxable earning",
        )
        assert phone.kind_agrees is False
        assert phone.agrees is False
        assert report.disagreements == 4
        assert report.totals.net == _NET

    def test_health_printed_under_either_pre_tax_kind_records_and_the_other_is_listed(
        self, world,
    ):
        """Plan step salary:S11-e: both pre-tax kinds net alike; the report tells them apart.

        Health Insurance's LINE is "Pre-tax, income tax only".  A stub that
        prints its $280.00 under "Pre-tax, all payroll taxes" (what an employer
        exempting it prints) nets the same: both kinds leave the deposit, so
        the pre-tax total stays 315.00 (280.00 + 35.00 Dental) and the net
        2,999.62 - 315.00 - 472.00 - 110.00 = 2,102.62, and the printed-net
        check passes.  The stub's all-payroll-taxes total is the 280.00.  The
        report lists the kind that differs from the line's; re-kinding the
        line to the stub's kind makes the row agree.
        """
        figures = _printed_under(
            world, _figures(world), health=PaycheckLineKindEnum.PRE_FICA_DEDUCTION,
        )
        totals = pay_stub_service.totals_of(world["profile"], figures)
        assert (totals.pre_tax, totals.pre_fica, totals.net) == (
            Decimal("315.00"), Decimal("280.00"), _NET,
        )
        stub = _record(world, figures)
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        health = {row.name: row for row in report.lines}["Health Insurance"]
        assert (health.on_stub, health.in_app) == (Decimal("280.00"), Decimal("280.00"))
        assert (health.stub_kind, health.kind) == (
            PaycheckLineKindEnum.PRE_FICA_DEDUCTION, PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
        )
        assert (health.stub_kind_label, health.kind_label) == (
            "Pre-tax, all payroll taxes", "Pre-tax, income tax only",
        )
        assert health.kind_agrees is False

        world["lines"]["health"].paycheck_line_kind_id = _kind(
            PaycheckLineKindEnum.PRE_FICA_DEDUCTION,
        )
        db.session.commit()
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        health = {row.name: row for row in report.lines}["Health Insurance"]
        assert health.kind_agrees is True
        assert health.agrees is True

    def test_a_line_the_stub_does_not_print_has_no_kind_to_disagree(self, world):
        """Vision is not on the stub: its row has no stub kind and fails on its figure alone."""
        stub = _record(world)
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        vision = {row.name: row for row in report.lines}["Vision"]
        assert vision.stub_kind is None
        assert vision.stub_kind_label is None
        assert vision.kind_agrees is True
        assert vision.agrees is False

    def test_the_base_gap_is_the_stub_less_the_salary(self, world):
        """A stub printing $2,884.58 base is $0.04 under the salary's $2,884.62."""
        stub = _record(
            world, _figures(world, base="2884.58"),
            printed_net=_NET - Decimal("0.04"), printed_gross=_GROSS - Decimal("0.04"),
        )
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        assert report.base_gap == Decimal("-0.04")

    def test_a_payday_the_app_no_longer_holds_compares_with_nothing(self, world):
        """A stub row on a non-payday (written past the door) reports no paycheck."""
        stub = PayStub(salary_profile_id=world["profile"].id, payday=date(2026, 3, 20),
                       base_pay=Decimal("2884.62"))
        db.session.add(stub)
        db.session.flush()
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        assert report.app_base_pay is None
        assert report.base_gap is None
        assert not report.lines

    def test_the_form_splits_the_lines_by_the_apps_payday(self, world):
        """Dental is taken on 03-13 (a month's first paycheck) and not on 03-27."""
        taken_27 = pay_stub_service.form_lines(world["profile"], _ctx(world), _PAYDAY)
        taken_13 = pay_stub_service.form_lines(
            world["profile"], _ctx(world), date(2026, 3, 13),
        )
        assert [line.name for line in taken_27.not_taken] == ["Dental"]
        assert "Dental" in [line.name for line in taken_13.taken]
        assert not taken_13.not_taken

    def test_a_priced_line_carries_its_rows_identity(self, world):
        """The engine's priced lines name the rows they priced, the report's join key."""
        ctx = _ctx(world)
        period = ctx.calendar().period_containing(_PAYDAY)
        breakdown = ctx.paychecks().for_profile(world["profile"]).at(period)
        priced = [*breakdown.earnings.taxable, *breakdown.deductions.pre_tax,
                  *breakdown.deductions.post_tax]
        lines = world["lines"]
        assert {line.paycheck_line_id: line.name for line in priced} == {
            lines["health"].id: "Health Insurance",
            lines["vision"].id: "Vision",
            lines["roth"].id: "Roth IRA",
            lines["phone"].id: "Phone Allowance",
        }


class TestTheLineDeleteRefusal:
    """Fork 10, worded by R-SAL51 (c): every stub that names the line, newest first."""

    def test_a_line_no_stub_names_may_be_deleted(self, world):
        """Vision is on no stub."""
        _record(world)
        assert pay_stub_service.line_delete_refusal(world["lines"]["vision"]) is None

    def test_one_stub_is_named(self, world):
        """Health Insurance on the 03-27 stub only."""
        _record(world)
        assert pay_stub_service.line_delete_refusal(world["lines"]["health"]) == (
            "Health Insurance is on your 2026-03-27 stub; end it instead."
        )

    def test_every_stub_is_named_newest_first(self, world):
        """Health Insurance on 01-02, 03-13 and 03-27."""
        _record(world, _figures(world, payday=date(2026, 3, 13)))
        _record(world, _figures(world, payday=date(2026, 1, 2)))
        _record(world)
        assert pay_stub_service.line_delete_refusal(world["lines"]["health"]) == (
            "Health Insurance is on your 2026-03-27, 2026-03-13 and 2026-01-02 "
            "stubs; end it instead."
        )
