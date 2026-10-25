"""What CI runs to grade a change, and the code repository's facts the other tools share.

The bottom layer of ``tools/``: it imports nothing else of this repository's,
so the tracker tool, the release tool, the test-split tool and the rehearsal kit
import from it and nothing here can import back.  :mod:`tools.ci.arcs` is the one
home of the arcs and the repository root; :mod:`tools.ci.archive` holds every
archived document to its banner; :mod:`tools.ci.ci_verdict` decides whether the
required ``lint-and-test`` check is green.  Standard library only, so CI runs it
with no install.
"""
