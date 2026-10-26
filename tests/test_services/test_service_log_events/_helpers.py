"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_service_log_events.py``, moved verbatim by ``tools/split``.
"""

import logging


class _LogCapture:
    """Capture log records on a target logger with propagation off.

    The Shekel logging config attaches a JSON handler at the root.
    Without ``propagate = False`` the captured records would also fire
    through the root handler and clutter the test stdout.
    """

    def __init__(self, logger_name: str, level: int = logging.DEBUG):
        self._logger = logging.getLogger(logger_name)
        self._level = level
        self.records: list[logging.LogRecord] = []
        self._handler = logging.Handler()
        self._handler.emit = lambda record: self.records.append(record)
        self._prior_level = None
        self._prior_propagate = None

    def __enter__(self):
        self._prior_level = self._logger.level
        self._prior_propagate = self._logger.propagate
        self._logger.addHandler(self._handler)
        self._logger.setLevel(self._level)
        self._logger.propagate = False
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._prior_level)
        self._logger.propagate = self._prior_propagate

    def find(self, event_name):
        """Return the first record whose ``event`` field matches *event_name*.

        Returns ``None`` if no match is found, so tests can compose a
        helpful assertion message that prints every observed event.
        """
        for record in self.records:
            if getattr(record, "event", None) == event_name:
                return record
        return None

    def event_summary(self):
        """Return a list of (level, event) tuples for assertion messages."""
        return [
            (r.levelname, getattr(r, "event", None)) for r in self.records
        ]
