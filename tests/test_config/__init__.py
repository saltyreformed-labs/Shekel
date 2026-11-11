"""
Shekel Budget App -- Configuration Tests

Tests for config class defaults, fallbacks, and production validation.
ProdConfig DEBUG and SECRET_KEY tests live in this file (rejection of
empty / placeholder / short keys, acceptance of valid keys, Dev/Test
loading without a fallback default).  ``test_routes/test_errors.py``
also has a small overlap (DEBUG=False, dev-only placeholder rejection)
exercising the same code path through a different lens.

Extension-level posture (LoginManager session_protection, etc.) is
asserted alongside the config classes here so a single regression
gate covers every static security knob the app ships with.

The suite's configuration class is imported as ``_TestConfig``: bound by
its own name, ``python_classes = Test*`` would collect it as a test class
in each module that imports it.
"""
