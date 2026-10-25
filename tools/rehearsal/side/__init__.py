"""The side probe: scripts each run BY PATH under a side's own venv, never imported
(``tools/rehearsal/__init__.py`` says why).  This file exists so that CI's
``pylint tools/rehearsal/`` reaches them: pylint walks only into directories that are
packages, and measured 2026-10-20 it skipped ``side/`` without one."""
