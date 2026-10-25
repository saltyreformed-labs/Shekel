"""After a release: every worktree, every branch already in ``main`` or named by a rehearsal
kit, and every database on the dev server, each marked MADE when this release made it
(read-only).

The skill removes only what is marked MADE: the release's own ``release/<date>`` branch,
local and on ``origin``, and what each of its rehearsal kits names in its MANIFEST,
``<kit directory>/release-manifest.txt`` (:data:`MANIFEST`).  A kit writes one line per
thing it makes -- ``worktree <absolute path>``, ``branch <name>`` (a local branch) or
``database <name>`` (a clone on the dev server) -- and appends each line BEFORE it makes
the thing, so a kit that dies mid-run leaves a line naming something absent, never a
thing no line names.  An entry marks a thing MADE when it names it EXACTLY (a worktree's
path compared resolved) and the thing is present; an entry naming nothing present is
listed as GONE.  A branch a kit names is listed, and MADE, even when ``main`` does not
hold it.  Everything else is reported with the worktree it is checked out in, for its
owner: a peer lane's worktree is not this session's to remove.  ``main`` and ``dev`` are
never listed, so no release can be told to delete them.

A kit is REFUSED, and nothing it names is marked MADE or GONE on its word -- nor MADE on
any other kit's, or on ``--release-branch``'s -- when its manifest is absent, unreadable
or malformed (:func:`_entries`), or names what no kit makes
(:func:`_guards`): the repository's main worktree or the worktree this census runs in;
a worktree with a branch checked out that the manifest does not also name, or a branch
checked out in a worktree it does not also name; ``main`` or ``dev``; the dev database
or the server's own.  Each entry of a refused kit is still listed, present or absent,
for the person who finishes its cleanup by hand.  A census with a refused kit is NOT a
proven cleanup (:attr:`Census.proven`).  What it cannot tell: an entry naming EXACTLY a
thing the kit did not make -- a peer's clone or detached worktree of the same name, or a
peer's branch checked out nowhere, whose unpushed commits ``git branch -D`` would lose --
is marked MADE, so a kit's names must be its own (the skill tells kit authors how).

The dev database server's container, user and database are read from the dev compose
file, their one home; the database listing runs behind an injected runner, so a test
never calls docker.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from tools.ci.gitcmd import git
from tools.release._model import ReleaseError

#: The dev compose file and the service whose server holds the dev databases.
COMPOSE, DB_SERVICE = "docker-compose.dev.yml", "db"
#: The long-lived branches, which no census lists and no kit may name.
LONG_LIVED = frozenset({"refs/heads/main", "refs/heads/dev", "refs/remotes/origin/main",
                        "refs/remotes/origin/dev", "refs/remotes/origin/HEAD"})
#: The prefix a release branch's name must carry, so no other branch is marked MADE.
RELEASE_PREFIX = "release/"
#: The file in a rehearsal kit's directory naming each thing the kit makes.
MANIFEST = "release-manifest.txt"
#: What a manifest line names: a worktree, a local branch, a dev database.
WORKTREE, BRANCH, DATABASE = "worktree", "branch", "database"
#: The database every postgres server holds for itself, which no kit makes.
SERVER_DATABASE = "postgres"
#: ``census``'s exit status when a kit was refused: the listing is printed, but the
#: cleanup it guides is not proven (0 is listed and proven).
UNPROVEN = 4


@dataclass(frozen=True)
class Worktree:
    """One worktree: its path, the branch it has checked out (None when detached) and
    its HEAD."""

    path: str
    branch: str | None
    head: str


@dataclass(frozen=True)
class Marked:
    """One thing the census lists, whether this release made it, and what else locates it:
    a worktree's branch (or its detached HEAD), a branch's worktree (None when it is
    checked out nowhere), nothing for a database."""

    name: str
    made: bool
    where: str | None = None


@dataclass(frozen=True)
class Entry:
    """One manifest line: the kind of thing it names, the name, and whether that thing is
    present."""

    kind: str
    name: str
    present: bool = False


@dataclass(frozen=True)
class Kit:
    """One rehearsal kit as its manifest was judged: its directory, why it is refused
    (empty when it is not), and the entries its manifest's well-formed lines name."""

    directory: str
    refusals: tuple[str, ...]
    entries: tuple[Entry, ...]


@dataclass(frozen=True)
class Census:
    """The worktrees, the branches whose tip ``main`` holds or a kit names, the dev
    databases, and each kit's manifest as judged."""

    main: str
    worktrees: tuple[Marked, ...]
    branches: tuple[Marked, ...]
    databases: tuple[Marked, ...]
    kits: tuple[Kit, ...] = ()

    @property
    def proven(self) -> bool:
        """Whether no kit was refused, so every MADE mark a kit gave rests on a manifest
        the census accepted."""
        return not any(kit.refusals for kit in self.kits)


@dataclass(frozen=True)
class Made:
    """What this release made: its branch (``release/<date>``; None for none) and the
    directories of its rehearsal kits, each holding its :data:`MANIFEST`."""

    release_branch: str | None = None
    kits: tuple[str, ...] = ()


def worktrees(root: Path) -> list[Worktree]:
    """Every worktree of the repository at ``root``, as ``git worktree list`` gives it."""
    found, fields = [], {}
    for line in [*git(root, "worktree", "list", "--porcelain").splitlines(), ""]:
        if line:
            key, _, value = line.partition(" ")
            fields[key] = value
        elif fields:
            branch = fields.get("branch")
            found.append(Worktree(fields["worktree"], branch, fields.get("HEAD", "")))
            fields = {}
    return found


def merged_branches(root: Path, main: str) -> list[str]:
    """Every local and remote-tracking branch whose tip ``main`` holds, but the long-lived."""
    out = git(root, "for-each-ref", f"--merged={main}", "--format=%(refname)",
              "refs/heads", "refs/remotes")
    return [ref for ref in out.splitlines() if ref and ref not in LONG_LIVED]


@dataclass(frozen=True)
class Server:
    """The dev database server, as the compose file names it: its container, user and
    database."""

    container: str
    user: str
    database: str

    @property
    def listing(self) -> list[str]:
        """``docker exec`` + ``psql`` argv listing its databases, read-only."""
        return ["docker", "exec", self.container, "psql", "-U", self.user, "-d",
                self.database, "-Atc",
                "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname"]


def dev_server(root: Path) -> Server:
    """The dev database server, from the compose file, its one home.

    Raises:
        ReleaseError: when the compose file names no such service, container or variable.
        OSError: when the compose file cannot be read.
        yaml.YAMLError: when it is not YAML.
    """
    composed = yaml.safe_load((root / COMPOSE).read_text(encoding="utf-8"))
    try:
        service = composed["services"][DB_SERVICE]
        env = service["environment"]
        return Server(service["container_name"], env["POSTGRES_USER"], env["POSTGRES_DB"])
    except (KeyError, TypeError) as missing:
        raise ReleaseError(f"{COMPOSE} gives service {DB_SERVICE!r} no container_name, "
                           f"POSTGRES_USER or POSTGRES_DB ({missing!r})") from missing


def docker_listing(argv: list[str]) -> list[str]:
    """Run ``argv`` and return its output's lines.

    Raises:
        subprocess.CalledProcessError: when it exits non-zero.
    """
    done = subprocess.run(argv, capture_output=True, text=True, check=True)
    return [line for line in done.stdout.splitlines() if line]


def _well_formed(kind: str, name: str) -> bool:
    """Whether a manifest line's kind and name make an entry: a kind it knows, and a name
    with no whitespace around it and no NUL in it, a worktree's absolute."""
    if kind not in (WORKTREE, BRANCH, DATABASE) or not name or name != name.strip():
        return False
    return "\0" not in name and (kind != WORKTREE or Path(name).is_absolute())


def _entries(directory: str) -> tuple[list[Entry], list[str]]:
    """The entries the well-formed lines of ``directory``'s manifest name, and why the
    manifest refuses its kit: absent; unreadable or not UTF-8; not ending in a newline (a
    torn last append can name a PREFIX of the name meant, exactly); or a line that is not
    ``<kind> <name>`` (:func:`_well_formed`).  An empty manifest names nothing, and is no
    refusal."""
    path = Path(directory) / MANIFEST
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return [], [f"it has no manifest {path}"]
    except (OSError, UnicodeDecodeError) as error:
        return [], [f"its manifest {path} cannot be read ({error})"]
    problems = [] if not text or text.endswith("\n") else [
        f"its manifest {path} does not end in a newline, so its last line may be torn"]
    entries = []
    for number, line in enumerate(text.removesuffix("\n").split("\n") if text else [], 1):
        kind, _, name = line.partition(" ")
        if _well_formed(kind, name):
            entries.append(Entry(kind, name))
        else:
            problems.append(f"its manifest's line {number} {line!r} is not `worktree "
                            "<absolute path>`, `branch <name>` or `database <name>`")
    return entries, problems


def _guards(entries: list[Entry], trees: list[Worktree], here: Path,
            server: Server) -> list[str]:
    """Why a manifest naming ``entries`` names what no kit makes, which the skill would
    then remove: the repository's main worktree (the first git lists) or ``here``, the
    worktree this census runs in; a worktree with a branch checked out that the manifest
    does not also name, or a branch checked out in a worktree it does not also name;
    ``main`` or ``dev``; the dev database or the server's own."""
    paths = {Path(entry.name).resolve() for entry in entries if entry.kind == WORKTREE}
    refs = {f"refs/heads/{entry.name}" for entry in entries if entry.kind == BRANCH}
    databases = {entry.name for entry in entries if entry.kind == DATABASE}
    reasons = []
    for index, tree in enumerate(trees):
        if Path(tree.path).resolve() in paths:
            if index == 0:
                reasons.append(f"it names {tree.path}, the repository's main worktree")
            elif Path(tree.path).resolve() == here:
                reasons.append(f"it names {tree.path}, the worktree this census runs in")
            if tree.branch and tree.branch not in refs:
                reasons.append(f"it names {tree.path}, which has {tree.branch} checked "
                               "out, and not that branch")
        elif tree.branch in refs:
            reasons.append(f"it names {tree.branch}, which is checked out in {tree.path}, "
                           "and not that worktree")
    reasons += [f"it names {ref}" for ref in sorted(refs & LONG_LIVED)]
    reasons += [f"it names the database {name!r}, which no kit makes"
                for name in (server.database, SERVER_DATABASE) if name in databases]
    return reasons


def _kit(directory: str, trees: list[Worktree], here: Path, server: Server,
         present: dict[str, set[str]]) -> Kit:
    """The kit in ``directory``: its manifest's entries, each marked present when
    ``present`` holds its name under its kind, and why it is refused."""
    entries, refusals = _entries(directory)
    refusals = refusals or _guards(entries, trees, here, server)
    found = [Entry(entry.kind, entry.name, _key(entry) in present[entry.kind])
             for entry in dict.fromkeys(entries)]
    return Kit(directory, tuple(refusals), tuple(found))


def _key(entry: Entry) -> str:
    """What an entry's name is matched on: a worktree's resolved path, a branch's ref, a
    database's name."""
    if entry.kind == WORKTREE:
        return str(Path(entry.name).resolve())
    return f"refs/heads/{entry.name}" if entry.kind == BRANCH else entry.name


def census(root: Path, main: str, made: Made,
           listing: Callable[[list[str]], list[str]] = docker_listing) -> Census:
    """The census of the repository at ``root`` after a release into ``main``, run from
    the worktree holding ``root`` (found by git, resolved, whatever path names it).  A
    thing any refused kit names is never MADE.

    Raises:
        ReleaseError: when the compose file names no dev database server.
        OSError: when the compose file cannot be read, or ``listing`` cannot run.
        UnicodeDecodeError: when the compose file is not UTF-8.
        yaml.YAMLError: when the compose file is not YAML.
        subprocess.CalledProcessError: when :func:`docker_listing` exits non-zero.
        tools.ci.gitcmd.GitError: when git cannot read ``main`` or the worktrees.
    """
    here = Path(git(root, "rev-parse", "--show-toplevel").strip()).resolve()
    trees = worktrees(here)
    server = dev_server(here)
    names = listing(server.listing)
    present = {WORKTREE: {str(Path(tree.path).resolve()) for tree in trees},
               BRANCH: set(git(here, "for-each-ref", "--format=%(refname)",
                               "refs/heads").split()),
               DATABASE: set(names)}
    kits = tuple(_kit(directory, trees, here, server, present) for directory in made.kits)
    doubted = {(entry.kind, _key(entry)) for kit in kits if kit.refusals
               for entry in kit.entries}
    marked = {(entry.kind, _key(entry)) for kit in kits for entry in kit.entries
              if entry.present} - doubted
    held = merged_branches(here, main)
    named = sorted(ref for kind, ref in marked if kind == BRANCH and ref not in held)
    if made.release_branch is not None:
        marked |= {(BRANCH, f"refs/heads/{made.release_branch}"),
                   (BRANCH, f"refs/remotes/origin/{made.release_branch}")} - doubted
    checked_out = {tree.branch: tree.path for tree in trees if tree.branch}
    return Census(
        git(here, "rev-parse", "--verify", f"{main}^{{commit}}").strip(),
        tuple(Marked(tree.path, (WORKTREE, str(Path(tree.path).resolve())) in marked,
                     tree.branch or f"detached at {tree.head[:12]}") for tree in trees),
        tuple(Marked(ref, (BRANCH, ref) in marked, checked_out.get(ref))
              for ref in [*held, *named]),
        tuple(Marked(name, (DATABASE, name) in marked) for name in names),
        kits)
