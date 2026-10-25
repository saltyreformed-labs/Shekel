"""The run half of the proof: the id map, the collection and outcome comparisons, pylint's
comparison, the census, and the observer plugin.  Each comparison is shown FAILING on a
hand-built observation; the observer is run under a real pytest on the sample, before and
after the tool splits it."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci import scratch
from tools.ci.arcs import REPO
from tools.split import _dynamic as dynamic
from tools.split._model import parse_plan
from tools.split._observe import OBSERVE_ENV, verdict
from tools.split._prove import line_origins
from tools.split._render import Origin
from tools.split._run import census, mentions
from tools.split._source import Refusal
from tools.split.conftest import SAMPLE, SOURCE, build, layout_of, plan_dict
from tools.split.test_apply import applied

PACKAGE = "tests/test_area/test_sample"

#: The sample's collection, as pytest names it.
BEFORE_IDS = [
    f"{SOURCE}::TestFirst::test_one",
    f"{SOURCE}::TestFirst::test_cases[a-1]",
    f"{SOURCE}::TestFirst::test_cases[b-2]",
    f"{SOURCE}::test_top_level",
    f"{SOURCE}::TestBig::test_a",
    f"{SOURCE}::TestBig::test_b",
    f"{SOURCE}::TestBig::test_c",
    f"{SOURCE}::TestBig::test_d",
    f"{SOURCE}::TestBig::test_e",
]


def mapping(repo: Path) -> dict[str, str]:
    """The sample's id map."""
    return dynamic.id_map(layout_of(repo), BEFORE_IDS)


class TestTheIdMap:
    """Only the file changes, and the map is total and one-to-one."""

    def test_each_id_keeps_its_class_test_and_parameters(self, repo):
        """A whole class, a module-level test and each slice of a split class."""
        found = mapping(repo)
        assert found[f"{SOURCE}::TestFirst::test_cases[b-2]"] == (
            f"{PACKAGE}/test_first.py::TestFirst::test_cases[b-2]")
        assert found[f"{SOURCE}::test_top_level"] == f"{PACKAGE}/test_first.py::test_top_level"
        assert found[f"{SOURCE}::TestBig::test_b"] == (
            f"{PACKAGE}/test_big_early.py::TestBig::test_b")
        assert found[f"{SOURCE}::TestBig::test_c"] == (
            f"{PACKAGE}/test_big_late.py::TestBig::test_c")
        assert len(found) == len(set(found.values())) == len(BEFORE_IDS)

    def test_an_id_from_another_file_is_refused(self, repo):
        """The map covers this source's tests and nothing else."""
        with pytest.raises(Refusal, match="is not a test of"):
            dynamic.id_map(layout_of(repo), ["tests/other.py::test_x"])

    def test_a_duplicated_id_is_not_one_to_one(self, repo):
        """Two old ids mapping to one new id is refused."""
        with pytest.raises(Refusal, match="one-to-one"):
            dynamic.id_map(layout_of(repo), BEFORE_IDS + BEFORE_IDS[:1])


class TestTheCollectionComparison:
    """The split must collect exactly the mapped ids."""

    def test_identical_passes(self, repo):
        """The mapped ids, collected once each, fail nothing."""
        found = mapping(repo)
        assert not dynamic.compare_collection(found, list(found.values()))

    @pytest.mark.parametrize("change, why", [
        (lambda ids: ids[1:], "not collected after the split"),
        (lambda ids: ids + [ids[0]], "collected twice"),
        (lambda ids: ids + [f"{PACKAGE}/test_first.py::test_new"], "mapped from nothing"),
        (lambda ids: [i.replace("[b-2]", "[b-3]") for i in ids], "mapped from nothing"),
    ])
    def test_each_difference_fails(self, repo, change, why):
        """A dropped id, a doubled one, a new one, and a changed parametrize id."""
        found = mapping(repo)
        assert any(why in f for f in dynamic.compare_collection(found, change(
            list(found.values()))))


class TestTheOutcomeComparison:
    """Every test must end the same way after the split, skip / xfail / xpass included."""

    @staticmethod
    def outcomes(found: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
        """A before / after pair that agrees."""
        before = {old: "passed" for old in found}
        before[f"{SOURCE}::TestBig::test_c"] = "skipped"
        before[f"{SOURCE}::TestBig::test_d"] = "xfailed"
        before[f"{SOURCE}::TestBig::test_e"] = "xpassed"
        return before, {found[old]: status for old, status in before.items()}

    def test_identical_passes(self, repo):
        """The same outcome for every id fails nothing."""
        found = mapping(repo)
        before, after = self.outcomes(found)
        assert not dynamic.compare_outcomes(found, before, after)

    @pytest.mark.parametrize("test, status", [
        ("TestBig::test_d", "xpassed"), ("TestBig::test_e", "passed"),
        ("TestBig::test_c", "passed"), ("TestFirst::test_one", "failed"),
        ("TestFirst::test_one", "error"), ("TestFirst::test_one", None),
    ])
    def test_each_changed_outcome_fails(self, repo, test, status):
        """xfail to xpass, xpass to pass, skip to pass, pass to fail or error, or not run."""
        found = mapping(repo)
        before, after = self.outcomes(found)
        new = found[f"{SOURCE}::{test}"]
        if status is None:
            del after[new]
        else:
            after[new] = status
        assert any(test in f for f in dynamic.compare_outcomes(found, before, after))

    def test_a_scope_compares_only_its_part(self, repo):
        """A part run alone is held to its own ids, and an extra id still fails."""
        found = mapping(repo)
        before, after = self.outcomes(found)
        scope = {o for o, n in found.items() if "test_big_late.py" in n}
        alone = {found[o]: after[found[o]] for o in scope}
        assert not dynamic.compare_outcomes(found, before, alone, scope)
        alone[found[f"{SOURCE}::test_top_level"]] = "passed"
        assert dynamic.compare_outcomes(found, before, alone, scope)


class TestThePylintComparison:
    """Each message in the parts judged by its origin: no message the source did not hold at
    the line (or the import statement) it came from, and none on a line the writer composed;
    duplicate-code is left to the pair-by-pair arm."""

    #: test_salary's measured message: ``import pytest`` (source :38) splits its app imports.
    APP = "Imports from package app are not grouped"

    @staticmethod
    def message(symbol: str, line: int, path: str = "a.py", text: str = "") -> dict:
        """One pylint JSON message."""
        return {"symbol": symbol, "line": line, "path": path, "message": text}

    @staticmethod
    def judge(before: list[dict], after: list[dict], origins: dict,
              block: tuple[int, int] = (1, 0)) -> list[str]:
        """The arm's failures; the source's import block is empty unless a case gives one."""
        return dynamic.compare_pylint(before, after, origins, block)

    @staticmethod
    def copied(*source_lines: int) -> list[Origin | None]:
        """A written file whose line N is a verbatim copy of ``source_lines[N - 1]``, or a
        line the writer composed where that entry is 0."""
        return [Origin(n, n, copied=True) if n else None for n in source_lines]

    def test_fewer_or_equal_passes(self):
        """The parts may hold fewer messages, never one the source did not."""
        before = [self.message("unused-import", 3), self.message("too-many-lines", 1),
                  self.message("unused-argument", 10)]
        after = [self.message("unused-import", 2)]
        assert not self.judge(before, after, {"a.py": self.copied(0, 3)})

    def test_a_kind_the_source_did_not_hold_there_fails(self):
        """A message whose symbol its source line never carried is new."""
        failures = self.judge([self.message("unused-import", 3)],
                                          [self.message("line-too-long", 1)],
                                          {"a.py": self.copied(3)})
        assert failures == ["a.py:1: line-too-long, which source line(s) 3-3 do not hold"]

    def test_a_source_message_answers_once_per_file(self):
        """Two messages of one symbol in one file, against the one the source held: the
        second is new."""
        failures = self.judge(
            [self.message("unused-argument", 7)],
            [self.message("unused-argument", 1), self.message("unused-argument", 1)],
            {"a.py": self.copied(7)})
        assert failures == ["a.py:1: unused-argument, which source line(s) 7-7 do not hold"]

    def test_a_message_on_a_composed_line_fails(self):
        """A docstring, a ``class`` line, a mixin's line, a blank or the ``_helpers`` import
        has no origin, whatever the source held."""
        failures = self.judge([self.message("too-few-public-methods", 9)],
                                          [self.message("too-few-public-methods", 2)],
                                          {"a.py": self.copied(1, 0)})
        assert failures == ["a.py:2: too-few-public-methods on a line the writer composed"]

    def test_an_import_two_parts_read_carries_its_message_into_both(self):
        """The shape test_savings_dashboard_service measured: one source statement (line 41)
        filtered into two parts, its one W9910 now on each."""
        statement = [None, Origin(41, 41, copied=False)]
        before = [self.message("shekel-private-module-import", 41)]
        after = [self.message("shekel-private-module-import", 2, path)
                 for path in ("p.py", "q.py")]
        assert not self.judge(before, after, {"p.py": statement, "q.py": statement})

    def test_an_import_matches_anywhere_on_its_source_statement(self):
        """A statement written over lines 40-44 in the source and on one line in a part, its
        source message on a later line of the statement: the origin is the statement, not
        its first line.  (Synthetic: every import message in the three files split so far
        sits on its statement's first line; an alias carries a line of its own.)"""
        before = [self.message("shekel-private-module-import", 42)]
        assert not self.judge(
            before, [self.message("shekel-private-module-import", 1)],
            {"a.py": [Origin(40, 44, copied=False)]})

    def test_a_new_message_hidden_by_a_vanished_one_fails(self):
        """The count per kind's hole: the source held two redefined-outer-name, the parts
        hold one, on a moved line whose source line held none.  A count reads 1 <= 2."""
        before = [self.message("redefined-outer-name", 10),
                  self.message("redefined-outer-name", 20)]
        after = [self.message("redefined-outer-name", 2)]
        failures = self.judge(before, after, {"a.py": self.copied(29, 30)})
        assert failures == ["a.py:2: redefined-outer-name, which source line(s) 30-30 do not "
                            "hold"]

    def test_an_import_sequence_flag_filtering_moved_is_the_sources_defect(self):
        """test_salary's shape: the source flags :39 (``from app.services import
        account_service``); a part that does not read it holds :40 next after ``import
        pytest``, and pylint flags :40 for the same package.  Answered from the import block."""
        before = [self.message("ungrouped-imports", 39, text=self.APP)]
        after = [self.message("ungrouped-imports", 19, text=self.APP)]
        origins = {"a.py": [None] * 18 + [Origin(40, 40, copied=False)]}
        assert not self.judge(before, after, origins, block=(6, 47))
        assert self.judge(before, after, origins), "the statement alone does not answer it"

    def test_an_import_sequence_flag_the_source_block_never_held_fails(self):
        """The source's import block is clean of the check: the part's flag is new."""
        failures = self.judge([self.message("wrong-import-order", 38, text='third party '
                                            'import "pytest" should be placed before x')],
                              [self.message("ungrouped-imports", 1, text=self.APP)],
                              {"a.py": [Origin(40, 40, copied=False)]}, block=(6, 47))
        assert failures == ["a.py:1: ungrouped-imports naming 'app', which neither source "
                            "line(s) 40-40 nor the source's import block (6-47) holds"]

    def test_an_import_sequence_flag_naming_another_package_fails(self):
        """The source block flags package app; a flag for package tests is another defect."""
        failures = self.judge([self.message("ungrouped-imports", 39, text=self.APP)],
                              [self.message("ungrouped-imports", 1, text="Imports from "
                                            "package tests are not grouped")],
                              {"a.py": [Origin(40, 40, copied=False)]}, block=(6, 47))
        assert failures == ["a.py:1: ungrouped-imports naming 'tests', which neither source "
                            "line(s) 40-40 nor the source's import block (6-47) holds"]

    def test_an_import_sequence_flag_on_a_composed_import_fails(self):
        """Only a source-derived statement may be answered from the block: the ``_helpers``
        import is the writer's own."""
        failures = self.judge([self.message("ungrouped-imports", 39, text=self.APP)],
                              [self.message("ungrouped-imports", 1, text=self.APP)],
                              {"a.py": [None]}, block=(6, 47))
        assert failures == ["a.py:1: ungrouped-imports on a line the writer composed"]

    def test_a_message_pylints_own_template_does_not_read_is_refused(self):
        """The defect is read back through pylint's template; a text it does not fit stops
        the arm rather than being matched on its symbol alone."""
        with pytest.raises(Refusal, match="does not read through its own template"):
            self.judge([], [self.message("ungrouped-imports", 1, text="something else")],
                       {"a.py": [Origin(40, 40, copied=False)]}, block=(6, 47))

    def test_duplicate_code_is_left_to_the_pair_by_pair_arm(self):
        """The source's duplicate-code count is structurally zero, so it is not judged here;
        :mod:`tools.split._similar` judges each pair (test_similar.py shows it refusing)."""
        assert not self.judge([self.message("unused-import", 3)],
                                          [self.message("unused-import", 1),
                                           self.message("duplicate-code", 1)],
                                          {"a.py": self.copied(3)})

    def test_new_kinds_are_named_per_importer(self):
        """An edited importer's new kind is reported against its own file."""
        before = [self.message("unused-argument", 4, path="t.py")]
        after = before + [self.message("shekel-private-module-import", 2, path="t.py")]
        assert dynamic.new_kinds(before, after) == {"t.py": ["shekel-private-module-import"]}


class TestTheCensus:
    """The old module's name is found in every spelling, and a path into the package is
    not."""

    def test_the_pattern(self):
        """The file name, its directories, its dotted path; not a sibling or the package."""
        mention = mentions(parse_plan(plan_dict()))
        assert sum(bool(mention.search(line)) for line in [
            "see tests/test_area/test_sample.py::TestBig",
            "``test_sample.py``",
            "from tests.test_area.test_sample import x",
            "from tests.test_area.test_sample._helpers import x",
            "tests/test_area/test_sample/test_first.py",
            "test_sample_other.py",
            "my_test_sample.py",
        ]) == 3

    @pytest.mark.parametrize("line, listed", [
        ("``test_sample.TestBig``'s ``test_a``", True),
        ("the far-leg cases in `test_sample`", True),
        ("``test_sample.test_top_level``", True),
        ("``test_sample.test_first_thing``", True),
        ("graded in test_sample.", True),
        ("moved from test_sample.py to test_sample/test_first.py", True),
        ("tests/test_area/test_sample/test_first.py", False),
        ("the test_sample/ package", False),
        ("from tests.test_area.test_sample.test_first import x", False),
        ("from tests.test_area.test_sample.conftest import x", False),
    ])
    def test_every_spelling_of_the_name(self, line, listed):
        """The unit is the module's NAME (the coordinator's ruling, 2026-10-15): a member
        (``m.Member``), a bare ``m``, a module-level test spelled in lowercase, a part's name
        as a mere PREFIX and a sentence's full stop are all mentions; only a continuation
        into the package (``m/``, ``m.<part>``, ``m.conftest``) is not, and a line holding
        both is listed."""
        assert bool(mentions(parse_plan(plan_dict())).search(line)) is listed

    #: The one line of :meth:`tracked_notes` the census lists.
    NOTES_HIT = ["docs/notes.md:1:see ``test_sample.TestBig``"]

    @staticmethod
    def tracked_notes(parent: Path) -> Path:
        """A throwaway repository, built by :mod:`tools.ci.scratch` (which starts git in it and
        in no other), tracking one document: a mention of the sample's module beside a line
        holding the name as a mere substring and a path into the package; its root."""
        root = scratch.repository(parent)
        notes = root / "docs" / "notes.md"
        notes.parent.mkdir()
        notes.write_text("see ``test_sample.TestBig``\ntest_sample_other.py\n"
                         "tests/test_area/test_sample/test_first.py\n", encoding="utf-8")
        scratch.run(root, "add", "-A")
        return root

    def test_the_census_reads_the_tracked_tree(self, tmp_path):
        """The real reader, ``git grep``, on a repository: the one mention among lines that
        only hold the name as a substring or continue into the package."""
        assert census(self.tracked_notes(tmp_path), parse_plan(plan_dict())) == self.NOTES_HIT

    @pytest.mark.parametrize("binding", scratch.BINDINGS)
    def test_a_bound_environment_reaches_no_repository_but_the_one_named(
            self, binding, tmp_path, monkeypatch):
        """The 2026-10-04 incident's mechanism, which this class's first census test repeated
        on 2026-10-15: it started git as a plain subprocess, and under the pre-commit hook of
        a linked worktree its ``git init`` wrote ``core.bare=true`` into the shared
        repository's config.  With this process bound to a sentinel (proven bound, its
        indexes not empty: ``scratch._prove_bound``), the census answers the directory named
        and the sentinel -- its refs, config, HEADs and indexes -- is byte-identical
        afterwards.  The tests' builder and the tracker tool are held the same way
        (``tools/ci/test_gitcmd.py``, ``tools/quill/test__git.py``)."""
        sentinel, before = scratch.bound_sentinel(tmp_path, binding, monkeypatch.setenv)
        assert census(self.tracked_notes(tmp_path), parse_plan(plan_dict())) == self.NOTES_HIT
        assert scratch.sentinel_state(sentinel) == before

    def test_archived_documents_are_classified(self, tmp_path):
        """archive/, historical/ and an ARCHIVED first line are records; the rest is live."""
        for path, first in {"docs/archive/a.md": "x", "docs/plans/historical/b.md": "x",
                            "docs/c.md": "> **ARCHIVED.** old", "docs/d.md": "# Live"}.items():
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_text(first + "\n", encoding="utf-8")
        hits = dynamic.classify(tmp_path, ["docs/archive/a.md:3:x", "docs/plans/historical/b.md:"
                                           "4:x", "docs/c.md:5:x", "docs/d.md:6:x"], {})
        assert [h.archived for h in hits] == [True, True, True, False]

    @staticmethod
    def package_hits(tmp_path: Path) -> list[dynamic.Hit]:
        """The sample, one of whose tests cites the old path in its docstring, split; every
        line of the package the census judge finds, classified with the verified origins
        (``git grep`` is the census's own reader; the sample is not a repository)."""
        repo = build(tmp_path, SAMPLE.replace(
            '"""One."""', '"""One; its twin lives in tests/test_area/test_sample.py."""'))
        layout = applied(repo)
        origins, failures = line_origins(layout, repo)
        assert not failures
        pattern = mentions(layout.plan)
        raw = [f"{path}:{number}:{line}" for path in sorted(origins)
               for number, line in enumerate((repo / path).read_text(
                   encoding="utf-8").splitlines(), start=1) if pattern.search(line)]
        return dynamic.classify(repo, raw, origins)

    def test_the_tools_own_provenance_is_not_counted(self, tmp_path):
        """conftest.py's and _helpers.py's docstrings name the source by design: composed."""
        composed = [h for h in self.package_hits(tmp_path) if h.composed]
        assert sorted(Path(h.path).name for h in composed) == ["_helpers.py", "conftest.py"]
        assert all("test_sample.py" in h.text for h in composed)

    def test_a_stale_pointer_inside_the_package_is_counted(self, tmp_path):
        """The moved docstring citing the old path is the source's line, not the tool's: it
        stays live although it sits inside the new package."""
        live = [h for h in self.package_hits(tmp_path) if not h.composed and not h.archived]
        assert [(Path(h.path).name, h.text) for h in live] == [
            ("test_first.py", '"""One; its twin lives in tests/test_area/test_sample.py."""')]


class TestTheObserver:
    """The plugin's verdicts, and the plugin run under pytest on the sample."""

    @pytest.mark.parametrize("phases, status", [
        ({"setup": ("passed", False), "call": ("passed", False)}, "passed"),
        ({"setup": ("passed", False), "call": ("failed", False)}, "failed"),
        ({"setup": ("failed", False)}, "error"),
        ({"setup": ("skipped", False)}, "skipped"),
        ({"setup": ("skipped", True)}, "xfailed"),
        ({"setup": ("passed", False), "call": ("skipped", True)}, "xfailed"),
        ({"setup": ("passed", False), "call": ("passed", True)}, "xpassed"),
        ({"setup": ("passed", False), "call": ("passed", False),
          "teardown": ("failed", False)}, "passed+teardown-error"),
    ])
    def test_verdict(self, phases, status):
        """Each phase combination names its outcome."""
        assert verdict(phases) == status

    @staticmethod
    def observed(root: Path, target: str, collect_only: bool) -> dict:
        """Run pytest on ``target`` in ``root`` with the observer, as the tool's runs do."""
        out = root / "observed.json"
        env = dict(os.environ)
        env[OBSERVE_ENV] = str(out)
        env["PYTHONPATH"] = str(REPO)
        args = [sys.executable, "-m", "pytest", target, "-c", "pytest.ini", "-q",
                "-p", "tools.split._observe", "-p", "no:cacheprovider"]
        subprocess.run(args + (["--collect-only"] if collect_only else []), cwd=root,
                       env=env, capture_output=True, text=True, check=False)
        return json.loads(out.read_text(encoding="utf-8"))

    def test_the_split_sample_is_the_same_suite(self, repo):
        """Collected and run before and after: the same ids under the map, the same
        outcomes (pass, skip, xfail and xpass among them), and each part alone too."""
        before = self.observed(repo, SOURCE, collect_only=False)
        collected = self.observed(repo, SOURCE, collect_only=True)["collected"]
        assert sorted(collected) == sorted(BEFORE_IDS)
        assert dynamic.outcome_counts(before["outcomes"]) == {
            "passed": 6, "skipped": 1, "xfailed": 1, "xpassed": 1}
        layout = applied(repo)
        found = dynamic.id_map(layout, collected)
        after = self.observed(repo, PACKAGE, collect_only=False)
        assert not dynamic.compare_collection(
            found, self.observed(repo, PACKAGE, collect_only=True)["collected"])
        assert not dynamic.compare_outcomes(found, before["outcomes"], after["outcomes"])
        for module in ("test_first", "test_big_early", "test_big_late"):
            target = f"{PACKAGE}/{module}.py"
            alone = self.observed(repo, target, collect_only=False)["outcomes"]
            scope = {o for o, n in found.items() if n.startswith(target + "::")}
            assert scope and not dynamic.compare_outcomes(
                found, before["outcomes"], alone, scope)
