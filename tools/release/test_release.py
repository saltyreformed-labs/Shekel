"""The ``release`` command over throwaway repositories under tmp_path: ``plan``'s report and
exit status, and ``census``'s listing.  The dev database listing is injected, so no test
calls docker."""
from __future__ import annotations

import pytest

from tools.ci.scratch import run
from tools.quill.door import FakeTracker
from tools.release import release
from tools.release._census import COMPOSE, MANIFEST
from tools.release.conftest import MONEY, lock, migration, pull_request, ship

COMPOSED = ("services:\n  db:\n    container_name: dev-db\n    environment:\n"
            "      POSTGRES_USER: someone\n      POSTGRES_DB: budget\n")


def _two_movers(repo):
    """dev took #40 (moves money) then #41 (moves money): the base, the first merge, the
    second, and the tracker holding both cards."""
    base = repo.commit("base")
    first = pull_request(repo, base, ship(40))
    second = pull_request(repo, first, ship(41))
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    tracker.add(41, labels=MONEY)
    return base, first, second, tracker


def test_plan_prints_what_refuses_the_release_and_the_cut_to_offer_and_exits_1(repo, capsys):
    """The report names each carried step, the rule, the cut offered, and the verdict."""
    base, first, second, tracker = _two_movers(repo)
    assert release.main(["plan", "--base", base, "--cut", second], root=repo.root,
                        connect=lambda: tracker) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"release plan: base {base}"
    assert out[1] == f"cut {second} 'Merge pull request'"
    assert out[2] == "steps carried (2):"
    assert out[3].startswith("  plan#40  moves money: yes  (claimed at ")
    assert out[4].startswith("  plan#41  moves money: yes  (claimed at ")
    assert out[5:] == [
        "migrations (0):",
        "REFUSED -- it carries 2 steps that move money (plan#40, plan#41); a release "
        "carries one at most",
        f"newest earlier cut that passes: {first} 'Merge pull request' (carries plan#40 "
        "[moves money]; exit 0)",
        "verdict: refused (exit 1)",
    ]


def test_plan_exits_0_on_a_clean_release(repo, capsys):
    """One mover alone: clean, no offer printed."""
    base, first, _, tracker = _two_movers(repo)
    assert release.main(["plan", "--base", base, "--cut", first], root=repo.root,
                        connect=lambda: tracker) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "verdict: clean (exit 0)"


def test_plan_prints_what_the_approval_question_must_name_and_exits_3(repo, capsys):
    """Two added migrations in one pull request: the approval question must name them, no
    earlier cut adds at most one, and the verdict says so."""
    base = repo.commit("base")
    cut = pull_request(repo, base, files={
        "migrations/versions/a1_first.py": migration("a1", None),
        "migrations/versions/b2_second.py": migration("b2", "a1")})
    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root,
                        connect=FakeTracker) == 3
    assert capsys.readouterr().out.splitlines()[-3:] == [
        "APPROVAL MUST NAME -- it adds 2 migrations: a1 (migrations/versions/a1_first.py), "
        "b2 (migrations/versions/b2_second.py)",
        "newest cut adding at most one migration: none (no earlier cut on dev's first-parent "
        "line qualifies)",
        "verdict: approval must name the above (exit 3)"]


def test_plan_exits_2_and_judges_nothing_on_a_name_that_names_nothing(repo, capsys):
    """A mistyped unrehearsed name is a stop, never a release read as rehearsed."""
    base, first, _, tracker = _two_movers(repo)
    assert release.main(["plan", "--base", base, "--cut", first, "--unrehearsed", "typo"],
                        root=repo.root, connect=lambda: tracker) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("release plan: nothing was judged: unrehearsed typo")


def test_plan_exits_2_on_a_cut_git_does_not_have(repo, capsys):
    """An unknown commit is an error, never an empty release."""
    base, _, _, tracker = _two_movers(repo)
    assert release.main(["plan", "--base", base, "--cut", "0" * 40], root=repo.root,
                        connect=lambda: tracker) == 2
    assert "nothing was judged" in capsys.readouterr().err


# -- production ------------------------------------------------------------------------------

def test_production_lists_each_pull_request_changing_what_production_runs_and_exits_0(
        repo, capsys):
    """Newest first, each pull request whose own change changes what production runs, with
    each path; one changing none is counted, not listed, and no block of changes no pull
    request made is printed when there are none."""
    base = repo.commit("base", {".dockerignore": "docs\n", "app.py": "0", "docs/a.md": "0",
                                "docker-compose.yml": "0"})
    first = pull_request(repo, base, files={"app.py": "1", "docker-compose.yml": "1"})
    second = pull_request(repo, first, files={"docs/a.md": "1"})
    assert release.main(["production", "--base", base, "--cut", second],
                        root=repo.root) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"release production: base {base}, cut {second}",
        f"{first} 'Merge pull request' changes 2 of production's files:",
        "  app.py",
        "  docker-compose.yml",
        "verdict: 2 of production's files change; 1 of 2 pull requests change them, 0 by "
        "none (exit 0)"]


def test_production_names_apart_a_change_no_pull_request_made(repo, capsys):
    """A resync of a hotfix to ``app.py`` whose resolution kept ``dev``'s side: the release
    takes the hotfix out of production, and the report names it under its own heading."""
    start = repo.commit("base", {"app.py": "0", "gunicorn.conf.py": "0"})
    main = pull_request(repo, start, files={"app.py": "1"})
    work = pull_request(repo, start, files={"gunicorn.conf.py": "1"})
    resync = repo.commit("Merge main into dev", parents=(work, main))
    assert release.main(["production", "--base", main, "--cut", resync],
                        root=repo.root) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"release production: base {main}, cut {resync}",
        f"{work} 'Merge pull request' changes 1 of production's files:",
        "  gunicorn.conf.py",
        "changed by no pull request's own change (1): a merge's resolution, or an ignore-file "
        "edit meeting a file the other branch brought:",
        "  app.py",
        "verdict: 2 of production's files change; 1 of 2 pull requests change them, 1 by "
        "none (exit 0)"]


def test_production_lists_each_version_the_release_moves(repo, capsys):
    """A lock update moving one package and adding another: the report names each version
    moved under its own heading, one added against none, before its verdict."""
    base = repo.commit("base", {"app.py": "0", "uv.lock": lock(("flask", "3.1.0"))})
    cut = pull_request(repo, base, files={"uv.lock": lock(("flask", "3.1.1"),
                                                          ("olefile", "0.47"))})
    assert release.main(["production", "--base", base, "--cut", cut], root=repo.root) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"release production: base {base}, cut {cut}",
        f"{cut} 'Merge pull request' changes 1 of production's files:",
        "  uv.lock",
        "versions moved (2), from the base's to the cut's:",
        "  uv.lock: flask 3.1.0 -> 3.1.1",
        "  uv.lock: olefile none -> 0.47",
        "verdict: 1 of production's files change; 1 of 1 pull requests change them, 0 by "
        "none (exit 0)"]


def test_production_exits_2_and_lists_nothing_on_a_lock_no_pin_of_which_can_be_named(
        repo, capsys):
    """A cut whose lock is no TOML is an error, never a release moving no version."""
    base = repo.commit("base", {"app.py": "0", "uv.lock": lock()})
    cut = pull_request(repo, base, files={"uv.lock": "[[package\n"})
    assert release.main(["production", "--base", base, "--cut", cut], root=repo.root) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("release production: nothing was judged: uv.lock is no "
                                   "TOML: ")


def test_production_exits_2_and_lists_nothing_on_an_ignore_file_docker_refuses(repo, capsys):
    """An ignore file Docker refuses is an error, never a release changing nothing."""
    base = repo.commit("base", {"app.py": "0"})
    cut = pull_request(repo, base, files={".dockerignore": "!\n"})
    assert release.main(["production", "--base", base, "--cut", cut], root=repo.root) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("release production: nothing was judged: a line \"!\" "
                                   "names no pattern")


# -- census ---------------------------------------------------------------------------------

REFUSED_ONE = "verdict: cleanup NOT proven, 1 of 1 kits refused (exit 4)"


@pytest.fixture(name="released")
def _released(repo, tmp_path):
    """A repository just released into ``origin/main``, its compose file, and the places a
    test names: its release branch (local and on origin), a peer's merged branch checked
    out in a worktree of its own, an unmerged branch, ``main``; the kit's two worktrees
    (one detached, one on the kit's unmerged branch ``kit/a``), a detached worktree no
    kit names beside them whose path starts with the detached one's, a symlink to their
    directory and one to the main worktree, and the kit's directory."""
    tip = repo.commit("released", {COMPOSE: COMPOSED})
    later = repo.commit("not released", parents=(tip,))
    for ref in ("refs/remotes/origin/main", "refs/remotes/origin/dev", "refs/heads/main",
                "refs/heads/release/2026-10-14", "refs/remotes/origin/release/2026-10-14",
                "refs/heads/feat/peer"):
        run(repo.root, "update-ref", ref, tip)
    run(repo.root, "update-ref", "refs/heads/feat/unmerged", later)
    run(repo.root, "checkout", "--quiet", "--detach", tip)
    trees = tmp_path / "trees"
    places = {"ROOT": repo.root, "PEER": tmp_path / "trees-peer" / "wt_peer",
              "A": trees / "wt_a", "BASE": trees / "wt_base", "OTHER": trees / "wt_base2",
              "TREES": trees, "LINK": tmp_path / "link", "ROOTLINK": tmp_path / "rootlink",
              "KIT": tmp_path / "kit"}
    run(repo.root, "worktree", "add", "--quiet", str(places["PEER"]), "feat/peer")
    run(repo.root, "worktree", "add", "--quiet", "-b", "kit/a", str(places["A"]), later)
    for tree in ("BASE", "OTHER"):
        run(repo.root, "worktree", "add", "--quiet", "--detach", str(places[tree]), tip)
    places["LINK"].symlink_to(trees, target_is_directory=True)
    places["ROOTLINK"].symlink_to(repo.root, target_is_directory=True)
    places["KIT"].mkdir()
    return repo, tip, places


def _manifest(places, text):
    """The kit's manifest written as ``text``, each ``{NAME}`` in it a place of
    :func:`_released`'s."""
    (places["KIT"] / MANIFEST).write_text(text.format(**places), encoding="utf-8")


def _census(repo, *flags, databases=("budget",), root=None):
    """``census`` run with ``flags`` over a listing of ``databases``: its exit status."""
    return release.main(["census", *flags], root=root or repo.root,
                        listing=lambda argv: list(databases))


def test_census_marks_exactly_what_this_release_and_its_kit_made(released, capsys):
    """MADE: the release branch, local and on origin; each worktree, branch and clone the
    kit's manifest names exactly and that is present, a worktree named through a symlink
    included, and the kit's branch though ``main`` does not hold it.  Not MADE: a clone
    or a worktree whose name merely starts with a named one's, a worktree inside a named
    directory, and everything else, each listed for its owner (a branch with the worktree
    it is checked out in).  Each entry naming nothing present is GONE; a repeated entry
    counts once; main, dev and an unmerged branch no kit names are not listed."""
    repo, tip, places = released
    asked = []
    _manifest(places, "worktree {LINK}/wt_base\nworktree {A}\nbranch kit/a\n"
                      "database shekel_rel_1014_a\ndatabase shekel_rel_1014_a\n"
                      "worktree {TREES}\nbranch kit/gone\ndatabase shekel_rel_1014_gone\n")

    def listing(argv):
        asked.append(argv)
        return ["budget", "shekel_rel_1014_a", "shekel_rel_1014_ab", "shekel_rel_1014_pristine"]

    assert release.main(["census", "--release-branch", "release/2026-10-14",
                         "--kit", str(places["KIT"])], root=repo.root, listing=listing) == 0
    assert asked == [["docker", "exec", "dev-db", "psql", "-U", "someone", "-d", "budget",
                      "-Atc", "SELECT datname FROM pg_database WHERE NOT datistemplate "
                      "ORDER BY datname"]]
    assert capsys.readouterr().out.splitlines() == [
        f"census (read-only) after a release into {tip}",
        "worktrees (5):",
        f"  other {repo.root}  (detached at {tip[:12]})",
        f"  other {places['PEER']}  (refs/heads/feat/peer)",
        f"  MADE  {places['A']}  (refs/heads/kit/a)",
        f"  MADE  {places['BASE']}  (detached at {tip[:12]})",
        f"  other {places['OTHER']}  (detached at {tip[:12]})",
        f"branches whose tip {tip[:12]} holds, or a kit names (4):",
        f"  other refs/heads/feat/peer  ({places['PEER']})",
        "  MADE  refs/heads/release/2026-10-14",
        "  MADE  refs/remotes/origin/release/2026-10-14",
        f"  MADE  refs/heads/kit/a  ({places['A']})",
        "dev databases (4):",
        "  other budget",
        "  MADE  shekel_rel_1014_a",
        "  other shekel_rel_1014_ab",
        "  other shekel_rel_1014_pristine",
        f"kit {places['KIT']}: accepted, 7 named",
        f"  GONE  worktree {places['TREES']}",
        "  GONE  branch kit/gone",
        "  GONE  database shekel_rel_1014_gone",
        "verdict: proven (exit 0)",
    ]


def test_census_marks_nothing_when_told_nothing(released, capsys):
    """No release branch and no kit: everything is the owners', and nothing is unproven."""
    repo, _, _ = released
    assert _census(repo) == 0
    out = capsys.readouterr().out.splitlines()
    assert not [line for line in out if "MADE" in line]
    assert out[-1] == "verdict: proven (exit 0)"


def test_census_accepts_an_empty_manifest(released, capsys):
    """A kit that made nothing yet names nothing, and is no refusal."""
    repo, _, places = released
    _manifest(places, "")
    assert _census(repo, "--kit", str(places["KIT"])) == 0
    assert capsys.readouterr().out.splitlines()[-2:] == [
        f"kit {places['KIT']}: accepted, 0 named", "verdict: proven (exit 0)"]


def test_census_lists_no_release_branch_main_does_not_hold(released, capsys):
    """Only a kit's branch is listed beyond what ``main`` holds; a release branch ``main``
    does not hold was never released, so it is not this cleanup's."""
    repo, _, _ = released
    run(repo.root, "update-ref", "refs/heads/release/2026-10-15", "refs/heads/feat/unmerged")
    assert _census(repo, "--release-branch", "release/2026-10-15") == 0
    assert "release/2026-10-15" not in capsys.readouterr().out


GUARDED = [
    ("worktree {ROOT}", "it names {ROOT}, the repository's main worktree"),
    ("worktree {ROOTLINK}", "it names {ROOT}, the repository's main worktree"),
    ("worktree {PEER}", "it names {PEER}, which has refs/heads/feat/peer checked out, and not "
                        "that branch"),
    ("worktree {A}", "it names {A}, which has refs/heads/kit/a checked out, and not that "
                     "branch"),
    ("worktree {LINK}/wt_a", "it names {A}, which has refs/heads/kit/a checked out, and not "
                             "that branch"),
    ("branch feat/peer", "it names refs/heads/feat/peer, which is checked out in {PEER}, and "
                         "not that worktree"),
    ("branch main", "it names refs/heads/main"),
    ("branch dev", "it names refs/heads/dev"),
    ("database budget", "it names the database 'budget', which no kit makes"),
    ("database postgres", "it names the database 'postgres', which no kit makes"),
]
MALFORMED = ["worktree trees/wt_base", "branch", "branch  kit/a", "database x ", "volume x",
             "database shekel_rel_1014_a\r", "", " database x", "database",
             "worktree /tmp/a\0b"]


@pytest.mark.parametrize("text, says", [
    (f"database shekel_rel_1014_a\n{line}\n", says) for line, says in GUARDED] + [
    ("database shekel_rel_1014_a", "its manifest {KIT}/release-manifest.txt does not end in a "
                                   "newline, so its last line may be torn")] + [
    (f"database shekel_rel_1014_a\n{line}\n",
     f"its manifest's line 2 {line!r} is not `worktree <absolute path>`, `branch <name>` "
     "or `database <name>`") for line in MALFORMED])
def test_census_refuses_a_kit_whose_manifest_names_what_no_kit_makes(released, capsys, text,
                                                                     says):
    """The skill removes what is MADE, so a manifest naming the repository, a peer's
    checked-out worktree or branch, a long-lived branch, the dev database or the server's
    own, or one that is torn or malformed, marks NOTHING of its kit (its clone stays
    other) and exits 4: the listing is printed, the cleanup is not proven."""
    repo, _, places = released
    _manifest(places, text)
    assert _census(repo, "--kit", str(places["KIT"]),
                   databases=("budget", "shekel_rel_1014_a")) == 4
    out = capsys.readouterr().out.splitlines()
    assert f"kit {places['KIT']}: REFUSED -- {says.format(**places)}" in out
    assert "  other shekel_rel_1014_a" in out
    assert not [line for line in out if "MADE" in line or "GONE" in line]
    assert out[-1] == REFUSED_ONE


@pytest.mark.parametrize("make, says", [
    (lambda kit: None, "it has no manifest {KIT}/release-manifest.txt"),
    (lambda kit: (kit / MANIFEST).mkdir(), "its manifest {KIT}/release-manifest.txt cannot "
                                            "be read ([Errno 21] Is a directory"),
    (lambda kit: (kit / MANIFEST).write_bytes(b"database \xff\n"),
     "its manifest {KIT}/release-manifest.txt cannot be read ('utf-8' codec can't decode"),
])
def test_census_refuses_a_kit_whose_manifest_cannot_be_read(released, capsys, make, says):
    """An absent, unreadable or undecodable manifest proves nothing the kit made."""
    repo, _, places = released
    make(places["KIT"])
    assert _census(repo, "--kit", str(places["KIT"])) == 4
    out = capsys.readouterr().out.splitlines()
    assert out[-2].startswith(f"kit {places['KIT']}: REFUSED -- {says.format(**places)}")
    assert out[-1] == REFUSED_ONE


def test_a_refused_kit_lists_what_it_names_and_marks_none_of_it(released, capsys):
    """Under its refusal a kit lists each entry, present or absent, for the person who
    finishes its cleanup by hand; none is MADE or GONE, and its branch ``main`` does not
    hold is not in the branch listing."""
    repo, tip, places = released
    _manifest(places, "worktree {A}\nbranch kit/a\ndatabase shekel_rel_1014_a\n"
                      "database shekel_rel_1014_gone\nbranch main\n")
    assert _census(repo, "--kit", str(places["KIT"]),
                   databases=("budget", "shekel_rel_1014_a")) == 4
    out = capsys.readouterr().out.splitlines()
    assert f"branches whose tip {tip[:12]} holds, or a kit names (3):" in out
    assert not [line for line in out if line.startswith(("  other refs/heads/kit/a",
                                                         "  MADE  refs/heads/kit/a"))]
    assert f"  other {places['A']}  (refs/heads/kit/a)" in out
    assert out[-7:] == [
        f"kit {places['KIT']}: REFUSED -- it names refs/heads/main",
        f"  named worktree {places['A']} (present)",
        "  named branch kit/a (present)",
        "  named database shekel_rel_1014_a (present)",
        "  named database shekel_rel_1014_gone (absent)",
        "  named branch main (present)",
        REFUSED_ONE]


def test_a_kit_may_name_its_worktree_and_branch_through_a_symlink(released, capsys):
    """Names are compared resolved in the guards as in the marks: a kit naming its worktree
    through a symlink, and the branch checked out there, is accepted and both are MADE."""
    repo, _, places = released
    _manifest(places, "worktree {LINK}/wt_a\nbranch kit/a\n")
    assert _census(repo, "--kit", str(places["KIT"])) == 0
    out = capsys.readouterr().out.splitlines()
    assert f"  MADE  {places['A']}  (refs/heads/kit/a)" in out
    assert f"  MADE  refs/heads/kit/a  ({places['A']})" in out


def test_a_thing_a_refused_kit_names_is_never_made(released, capsys, tmp_path):
    """Two kits naming one clone, one of them refused: the clone is left to a person, not
    marked on the accepted kit's word."""
    repo, _, places = released
    _manifest(places, "database shekel_rel_1014_a\n")
    refused = tmp_path / "kit-refused"
    refused.mkdir()
    (refused / MANIFEST).write_text("database shekel_rel_1014_a\nbranch main\n",
                                    encoding="utf-8")
    assert _census(repo, "--kit", str(places["KIT"]), "--kit", str(refused),
                   databases=("budget", "shekel_rel_1014_a")) == 4
    out = capsys.readouterr().out.splitlines()
    assert "  other shekel_rel_1014_a" in out
    assert not [line for line in out if "MADE" in line]


def test_a_release_branch_a_refused_kit_names_is_not_made(released, capsys):
    """The release branch is marked by ``--release-branch``, but a refused kit naming it
    (``branch`` names the local branch) leaves that copy to a person too."""
    repo, _, places = released
    _manifest(places, "branch release/2026-10-14\nbranch main\n")
    assert _census(repo, "--release-branch", "release/2026-10-14",
                   "--kit", str(places["KIT"])) == 4
    out = capsys.readouterr().out.splitlines()
    assert "  other refs/heads/release/2026-10-14" in out
    assert "  MADE  refs/remotes/origin/release/2026-10-14" in out


def test_a_refused_kit_leaves_an_accepted_kit_marked(released, capsys, tmp_path):
    """Kits are judged one by one: the accepted kit's clone is MADE, the refused one's
    reason is printed, and the census exits 4."""
    repo, _, places = released
    _manifest(places, "database shekel_rel_1014_a\n")
    absent = tmp_path / "kit-without-manifest"
    assert _census(repo, "--kit", str(places["KIT"]), "--kit", str(absent),
                   databases=("budget", "shekel_rel_1014_a")) == 4
    out = capsys.readouterr().out.splitlines()
    assert "  MADE  shekel_rel_1014_a" in out
    assert out[-3:] == [
        f"kit {places['KIT']}: accepted, 1 named",
        f"kit {absent}: REFUSED -- it has no manifest {absent}/release-manifest.txt",
        "verdict: cleanup NOT proven, 1 of 2 kits refused (exit 4)"]


@pytest.mark.parametrize("where, named", [("itself", "{BASE}"), ("a subdirectory", "{BASE}"),
                                          ("a symlink", "{BASE}"), ("itself", "{LINK}/wt_base")])
def test_census_refuses_a_kit_naming_the_worktree_it_runs_in(released, capsys, where, named):
    """Run from a linked worktree -- by its own path, a subdirectory of it, or a symlink to
    it -- a kit naming that worktree, by its path or through a symlink, is refused, so no
    cleanup removes the checkout the census ran from; a detached one would otherwise be
    marked MADE."""
    repo, _, places = released
    _manifest(places, f"worktree {named}\n")
    root = {"itself": places["BASE"], "a subdirectory": places["BASE"] / "sub",
            "a symlink": places["LINK"] / "wt_base"}[where]
    (places["BASE"] / "sub").mkdir()
    assert _census(repo, "--kit", str(places["KIT"]), root=root) == 4
    assert capsys.readouterr().out.splitlines()[-3:] == [
        f"kit {places['KIT']}: REFUSED -- it names {places['BASE']}, the worktree this "
        "census runs in",
        f"  named worktree {named.format(**places)} (present)",
        REFUSED_ONE]


@pytest.mark.parametrize("name", ["dev", "main", "release/", "feat/release/x"])
def test_census_refuses_a_release_branch_that_is_no_release_branch(released, name):
    """No branch outside ``release/<...>`` can be marked for the skill to delete."""
    repo, _, _ = released
    with pytest.raises(SystemExit) as exited:
        _census(repo, "--release-branch", name)
    assert exited.value.code == 2


def test_census_help_names_the_exit_a_refused_kit_gets(capsys):
    """The skill reads exit 4 as "cleanup not proven"; the help says so."""
    with pytest.raises(SystemExit):
        release.main(["census", "--help"])
    assert "a kit refused exits 4: the cleanup is not proven" in " ".join(
        capsys.readouterr().out.split())


def test_census_exits_2_when_the_compose_file_names_no_database_server(released, capsys):
    """The server's name is the compose file's; a file without it lists nothing."""
    repo, _, _ = released
    (repo.root / COMPOSE).write_text("services:\n  app: {}\n", encoding="utf-8")
    assert _census(repo) == 2
    assert "gives service 'db' no container_name" in capsys.readouterr().err


def test_census_exits_2_when_the_listing_fails(released, capsys):
    """A docker failure is an error, never an empty listing."""
    repo, _, _ = released

    def failing(argv):
        raise OSError("docker: not found")

    assert release.main(["census"], root=repo.root, listing=failing) == 2
    assert "docker: not found" in capsys.readouterr().err
