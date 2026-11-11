"""One Taxes report slices the primary profile's law once and resolves its year once.

Plan step X-at-5 made the primary profile's pricer the one home of its law
in a read pass (ruling R-IZ: move the leaf): the report reads the liability's
law, the W-2's Social Security cap and the marginal-rate chip off
``ProfilePaychecks.law``, the same set the primary's paychecks are priced on.
Before that the report resolved the year itself beside the pricer, and the
adversarial review of that step measured one single-profile 2026 report at
two slices of the law and two resolutions of 2026 (plan#3364).  Two calls of
one producer for the same inputs in one request is the duplication this
counts.

The instrument is the suite's one call counter (``counting_calls``), which
patches the owning module and every ``app`` module holding its own reference
to the name, so a caller that imported the function directly is counted too.
"""

from datetime import date
from decimal import Decimal

from app.services.tax_config_service import TaxLawBasis
from app.services.tax_report_service import compute_tax_report
from tests._test_helpers import counting_calls

from ._helpers import _committed_profile, _make_full_year_periods

#: The resolver's two steps: slicing the law to a profile, and resolving one
#: year off the slice.  ``_configs_from_series`` is the one body every
#: resolution runs, whichever public door reached it.
_SLICE = ("app.services.tax_config_service", "profile_tax_series")
_RESOLVE = ("app.services.tax_config_service", "_configs_from_series")


class TestThePrimarysLawIsResolvedOnce:
    """The report and the primary's paychecks share one resolution of its law."""

    def test_one_profile_one_slice_one_resolution(self, db, seed_user):
        """One NC single filer, 26 paydays in 2026, no pay stub and no checkpoint.

        The report asks the primary's pricer for 2026's law (one slice, made
        when the pricer is built, and one resolution).  The withholding
        prices all 26 paychecks through that pricer, whose ``over`` reads
        2026 off ``law``'s memo, and the modelled pre-tax asks the same 26,
        already priced.  No pay stub, so no other year is asked for: one
        resolution in all is one resolution of 2026.  Before X-at-5 the
        same report counted 2 and 2.
        """
        _committed_profile(seed_user)
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        with counting_calls(_SLICE, _RESOLVE) as counts:
            report = compute_tax_report(seed_user["user"].id, 2026, date(2026, 3, 1))

        assert report.liability.basis == TaxLawBasis(2026, 2026, 2026, 2026)
        assert report.withholding.total.gross == Decimal("130000.00")  # 26 x 5,000.00
        assert counts["profile_tax_series"] == 1
        assert counts["_configs_from_series"] == 1
