"""The archive banner (:mod:`tools.ci.archive`): every archived document says so on its
first line.

Moved here with the rule from the plan gate's ``test_order_and_archive.py`` when step X-cx's
S3b deleted the gate, every case and its assertions unchanged.
"""
from __future__ import annotations

from tools.ci import arcs, archive


class TestAnArchivedDocumentSaysSoOnItsFirstLine:
    """CLAUDE.md: anything under an ``archive/`` or ``historical/`` directory says so on its first
    line."""

    def test_every_archived_document_carries_the_banner(self):
        """Every archived document carries the banner."""
        assert not archive.archive_banner_violations()

    def test_the_archive_actually_holds_documents_to_grade(self):
        """A floor far under the live count, so a broken walk is visible.

        **The floor came DOWN from 100 to 40 on 2026-08-11, and the reason is
        the point**: 100 sat just under a 124-document archive, so it was
        really tracking the corpus size rather than proving the walk works.
        Deleting an archived document is now an EXPECTED operation -- 25 went
        that day, referenced by nothing -- and a floor set just under the
        current count turns every such deletion into a red suite. The floor's
        only job is catching a walk that has stopped finding files at all.
        """
        assert len(list(archive.archived_docs())) >= 40

    def test_the_walk_covers_every_archived_tree(self):
        """Three separate trees hold archived documents, and all three count.

        ``docs/plans/historical`` and ``docs/historical`` are the ones a
        directory walk rooted at the balance arc's archive would miss, and the
        document that started this rule was reachable from none of them.
        """
        parents = {str(p.relative_to(arcs.REPO).parent)
                   for p in archive.archived_docs()}
        for tree in ("docs/audits/balance_architecture/archive",
                     "docs/historical",
                     "docs/plans/historical"):
            assert tree in parents, f"{tree} is not being walked"

    def test_the_control_fires_on_a_document_that_does_not_say_it_is_archived(
        self, tmp_path, monkeypatch,
    ):
        """The defect: an archived plan a session reads as the plan of record."""
        fake = tmp_path / "docs" / "plans" / "historical"
        fake.mkdir(parents=True)
        (fake / "implementation_plan_something.md").write_text(
            "# Implementation Plan: Something\n\nThis is the plan of record.\n",
        )
        monkeypatch.setattr(arcs, "REPO", tmp_path)
        problems = archive.archive_banner_violations()
        assert len(problems) == 1, problems
        assert "implementation_plan_something.md" in problems[0]

    def test_a_live_document_outside_an_archive_is_not_graded(
        self, tmp_path, monkeypatch,
    ):
        """The negative control: the arm keys on the DIRECTORY, not the name."""
        live = tmp_path / "docs" / "plans"
        live.mkdir(parents=True)
        (live / "implementation_plan_something.md").write_text("# Live\n")
        monkeypatch.setattr(arcs, "REPO", tmp_path)
        assert not archive.archive_banner_violations()
