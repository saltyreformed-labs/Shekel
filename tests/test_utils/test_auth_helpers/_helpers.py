"""The capture of the ``app.utils.auth_helpers`` logger: the denial and the silence cases read it.

``tools/split`` moved ``_AuthHelpersLogCapture`` here from the module-level helpers of
``tests/test_utils/test_auth_helpers.py``; it has been edited since, so it is no longer that
module's text verbatim.
"""

import logging


class _AuthHelpersLogCapture:
    """Context manager that captures records on the auth_helpers logger.

    The helpers emit on ``app.utils.auth_helpers``; capturing on that
    specific logger keeps the assertion targeted -- a new event from
    an unrelated module cannot accidentally satisfy the test.
    """

    def __init__(self, level=logging.DEBUG):
        """Build the handler that keeps every record at *level* or above in ``records``.

        The default, DEBUG, keeps the INFO ``resource_not_found`` beside the
        WARNING denials, so a case can assert one is absent.  The logger is not
        touched until the block is entered.
        """
        self._logger = logging.getLogger("app.utils.auth_helpers")
        self._level = level
        self.records: list[logging.LogRecord] = []
        self._handler = logging.Handler()
        self._handler.emit = self.records.append
        self._prior_level = None
        self._prior_propagate = None

    def __enter__(self):
        """Attach the handler, set the logger to *level* and stop propagation.

        The logger's own level and propagation are saved first, so ``__exit__``
        puts back exactly what the block found.
        """
        self._prior_level = self._logger.level
        self._prior_propagate = self._logger.propagate
        self._logger.addHandler(self._handler)
        self._logger.setLevel(self._level)
        # Disable propagation so the captured records do not also fire
        # on parent loggers (the project's root JSON handler would
        # spam stdout during the test).
        self._logger.propagate = False
        return self

    def __exit__(self, *exc):
        """Detach the handler and restore the level and propagation ``__enter__`` saved.

        Returns ``None``, so an exception raised in the block still propagates.
        """
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._prior_level)
        self._logger.propagate = self._prior_propagate

    def find(self, event_name):
        """Return the first record whose ``event`` field matches *event_name*."""
        for record in self.records:
            if getattr(record, "event", None) == event_name:
                return record
        return None
