"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_pay_calendar_derivation.py``, moved verbatim by ``tools/split``.
"""

from datetime import date


#: The phase the refusal cases give their one era: every payday they state
#: opens on it, so the era is on the record's grid and only the value under
#: test is wrong.
_OPENING = date(2026, 1, 2)
