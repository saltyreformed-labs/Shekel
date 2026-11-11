"""
Shekel Budget App -- Configuration Tests

Tests for config class defaults, fallbacks, and production validation.
ProdConfig's SECRET_KEY tests live in ``test_production_config.py``
(rejection of empty / placeholder / short keys, acceptance of valid
keys), and Dev/Test loading without a fallback default in
``test_settings_every_config_shares.py``.  ``test_routes/test_errors.py``
also has a small overlap (DEBUG=False, dev-only placeholder rejection)
exercising the same code path through a different lens.

Extension-level posture (LoginManager session_protection, etc.) is
asserted alongside the config classes in this package so a single
regression gate covers every static security knob the app ships with.

The suite's configuration class is imported as ``_TestConfig``: bound by
its own name, ``python_classes = Test*`` would collect it as a test class
in each module that imports it.
"""
