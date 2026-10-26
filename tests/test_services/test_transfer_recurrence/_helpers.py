"""The module-level helpers of ``tests/test_services/test_transfer_recurrence.py``, moved
verbatim by ``tools/split``, and which part imports each: ``make_template_with_rule``
(``test_generation``, ``test_regeneration``, ``test_resolve_conflicts``) and ``_LogCapture``
(``test_maintain``, ``test_regeneration``, ``test_resolve_conflicts``); ``test_negative_paths``
imports neither.  ``_assert_shadows_valid`` read a transfer's two twin rows and went at plan step
``balance:X-bi-6-4d-2`` (ruling **R-BAL167** class 4): a transfer stores no leg row since
migration 82f7.
"""

import logging
from decimal import Decimal

from app.extensions import db
from app.models.transfer_template import TransferTemplate
from tests._test_helpers import create_account_of_type, make_cadence_rule
from tests._test_helpers import state_template_price


def make_template_with_rule(seed_user, cadence, **rule_kwargs):
    """Create a savings account, a transfer template and its cadence rule.

    **ONE copy of what was three byte-identical methods.**  Four other classes
    in this file define a `_make_template_with_rule` of their own and keep it:
    each differs genuinely -- a second endpoint, an explicit bound, a loan
    destination -- so collapsing those would be a shared helper with a flag per
    caller, which is the shape being removed rather than a second instance of
    it.

    Args:
        seed_user: The `seed_user` fixture's mapping.
        cadence: The `RecurrenceUnitEnum` the rule fires on.
        **rule_kwargs: `interval_n`, `day_of_month`, `month_of_year`.

    Returns:
        The refreshed `TransferTemplate`.
    """
    savings = create_account_of_type(
        seed_user, db.session, "Savings", "Savings",
        anchor_balance=Decimal("500.00"),
    )

    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        name="Test Transfer",
        default_amount=Decimal("100.00"),
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template)
    # The definition first, then the cadence onto it (plan step R-F6).
    make_cadence_rule(
        template, cadence,
        interval_n=rule_kwargs.get("interval_n", 1),
        fires_on_day=rule_kwargs.get("day_of_month"),
        fires_in_month=rule_kwargs.get("month_of_year"),
    )

    db.session.refresh(template)
    return template


class _LogCapture:
    """Capture log records emitted by ``logger_name`` with propagation off.

    Mirrors the helper in ``tests/test_services/test_service_log_events/_helpers.py``;
    duplicated here rather than imported so the Commit 34 test class has
    no cross-file dependency on another test module's private helper.
    """

    def __init__(self, logger_name: str, level: int = logging.DEBUG) -> None:
        self._logger = logging.getLogger(logger_name)
        self._level = level
        self.records: list[logging.LogRecord] = []
        self._handler = logging.Handler()
        self._handler.emit = lambda record: self.records.append(record)
        self._prior_level: int | None = None
        self._prior_propagate: bool | None = None

    def __enter__(self) -> "_LogCapture":
        self._prior_level = self._logger.level
        self._prior_propagate = self._logger.propagate
        self._logger.addHandler(self._handler)
        self._logger.setLevel(self._level)
        self._logger.propagate = False
        return self

    def __exit__(self, *exc) -> None:
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._prior_level)
        self._logger.propagate = self._prior_propagate

    def find_all(self, event_name: str) -> list[logging.LogRecord]:
        """Return every captured record whose ``event`` field matches."""
        return [
            r for r in self.records
            if getattr(r, "event", None) == event_name
        ]
