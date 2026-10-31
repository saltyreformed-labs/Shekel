# Budget App -- Development Environment Setup

**Platform:** Arch Linux **Editor:** NeoVim + LazyVim **Stack:** Flask · Jinja2 · HTMX · Bootstrap 5
· PostgreSQL

---

## 1. System Dependencies

### PostgreSQL

```bash
# Install PostgreSQL.
sudo pacman -S postgresql

# Initialize the database cluster. Arch doesn't do this automatically.
sudo -u postgres initdb -D /var/lib/postgres/data

# Start and enable the service.
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Create your app role and databases.
# Using your Linux username as the role makes psql auth seamless.
sudo -u postgres createuser --createdb $(whoami)

# Create the development and test databases.
createdb budget_app
createdb budget_app_test

# Verify connectivity.
psql budget_app -c "SELECT version();"
```

If `createuser` fails with a peer authentication error, check `/var/lib/postgres/data/pg_hba.conf`
and ensure the `local` line uses `peer` (the Arch default). Peer auth maps your Linux username to a
PostgreSQL role with the same name -- no password needed for local development.

### Python

uv installs the Python version `.python-version` names (its own build of it when the system's Python
is another minor) and every package `uv.lock` pins:

```bash
sudo pacman -S uv
uv --version
```

### Other System Packages

```bash
# Required for psycopg's C module (psycopg-c, the PostgreSQL adapter) to compile.
sudo pacman -S base-devel python

# Required for bcrypt to compile.
# (Usually already present with base-devel.)

# Git (you probably have this already).
sudo pacman -S git

# Optional but useful: pgcli (a better psql with autocomplete).
sudo pacman -S pgcli
```

---

## 2. Project Setup

### Create the Project Directory

```bash
mkdir -p ~/projects/budget-app
cd ~/projects/budget-app
git init
```

### Python Virtual Environment

```bash
# Create the venv inside the project directory and install the dependencies
# into it (below).
uv sync --locked

# Activate it. You will do this every time you open a terminal
# to work on this project.
source .venv/bin/activate

# Verify the venv is active.
which python    # Should show ~/projects/budget-app/.venv/bin/python
```

### Install Python Dependencies

The repository's `uv.lock` is the one statement of every pinned version. `uv sync --locked` (above)
installs exactly what it pins, the runtime packages plus the test, lint and build tools, and refuses
a lock that no longer matches `pyproject.toml`; run it again after a pull that moves the lock. This
page used to carry a copy of the pins, and the copy went stale.

### Environment File

Create `.env` for local configuration. This file is **not committed** to git.

```bash
cat > .env << 'EOF'
FLASK_APP=run.py
FLASK_ENV=development
SECRET_KEY=dev-secret-key-change-in-production
DATABASE_URL=postgresql:///budget_app
TEST_DATABASE_URL=postgresql:///budget_app_test
EOF
```

### Git Ignore

```bash
cat > .gitignore << 'EOF'
# Python
.venv/
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/

# Environment
.env

# IDE / Editor
.vscode/
*.swp
*.swo
*~
.netrw*

# OS
.DS_Store
Thumbs.db

# Project
*.db
*.sqlite3
migrations/versions/__pycache__/
EOF
```

### Pylint Configuration

Create `.pylintrc` in the project root. This configures pylint for Flask/SQLAlchemy patterns that
otherwise produce false positives:

```bash
cat > .pylintrc << 'EOF'
[MAIN]
# Load Flask/SQLAlchemy-aware plugins if installed.
load-plugins=pylint.extensions.docparams

[FORMAT]
# Max line length. 99 is a reasonable compromise.
max-line-length=99

[MESSAGES CONTROL]
# Disable warnings that conflict with Flask/SQLAlchemy patterns.
disable=
    missing-module-docstring,
    too-few-public-methods,
    import-error

[DESIGN]
# SQLAlchemy models often have many attributes.
max-attributes=15

[BASIC]
# Allow short variable names common in Flask (db, bp, app, etc.)
good-names=id,db,bp,app,fs,e,f,i,j,k,n,v
EOF
```

---

## 3. LazyVim Configuration for Python + Flask

LazyVim is a Neovim configuration framework built on `lazy.nvim`. It comes with sensible defaults
but needs a few additions for Python development. All config files live in `~/.config/nvim/`.

### Verify Your LazyVim Installation

```bash
# Open nvim. LazyVim should load and you should see the dashboard.
nvim

# Inside nvim, check that lazy.nvim is working:
:Lazy
# You should see the plugin manager UI. Press 'q' to close.
```

### Install LazyVim Extras for Python

LazyVim has a built-in "extras" system for language support. Enable the Python extra which
configures LSP (pyright), linting, formatting, and debugging in one step.

Create or edit `~/.config/nvim/lua/plugins/python.lua`:

```lua
return {
  -- Enable LazyVim's Python extras.
  -- This configures pyright (LSP), ruff (linter/formatter),
  -- and debugpy (debugger) automatically.
  { import = "lazyvim.plugins.extras.lang.python" },
}
```

After saving, restart nvim. LazyVim will install pyright, ruff, and related Mason packages
automatically. You'll see progress in the bottom status bar.

### Add Pylint as an Additional Linter

LazyVim's Python extra uses ruff by default (which is fast and covers most linting). Since you want
pylint specifically, add it alongside ruff:

Create or edit `~/.config/nvim/lua/plugins/linting.lua`:

```lua
return {
  {
    "mfussenegger/nvim-lint",
    opts = {
      linters_by_ft = {
        python = { "pylint" },
      },
    },
  },
}
```

Mason should install pylint automatically. If not:

```text
:MasonInstall pylint
```

**Note:** You will now get diagnostics from both pyright (type checking) and pylint
(style/convention). If the overlap feels noisy, you can disable specific pylint rules in `.pylintrc`
as you encounter them.

### SQL Support (Optional)

If you want SQL syntax highlighting and SQLFluff linting in `.sql` files:

Create or edit `~/.config/nvim/lua/plugins/sql.lua`:

```lua
return {
  -- Treesitter grammar for SQL highlighting.
  {
    "nvim-treesitter/nvim-treesitter",
    opts = {
      ensure_installed = { "sql" },
    },
  },
}
```

SQLFluff is an editor tool, not one of the project's packages, and `uv sync` removes from `.venv`
every package `uv.lock` does not pin, so install it as a uv tool of its own:

```bash
uv tool install sqlfluff
```

### Jinja / HTML + HTMX Support

For Jinja2 template editing with HTML support:

Create or edit `~/.config/nvim/lua/plugins/web.lua`:

```lua
return {
  -- Treesitter grammars for HTML and Jinja/htmldjango.
  {
    "nvim-treesitter/nvim-treesitter",
    opts = {
      ensure_installed = {
        "html",
        "css",
        "javascript",
        "htmldjango",  -- Covers Jinja2 syntax.
      },
    },
  },

  -- Tell nvim to treat .html files in templates/ as htmldjango
  -- so Jinja2 blocks get proper highlighting.
  {
    "neovim/nvim-lspconfig",
    opts = {
      servers = {
        html = {
          filetypes = { "html" },
        },
      },
    },
  },
}
```

Then add a filetype detection autocmd. Create or edit `~/.config/nvim/lua/config/autocmds.lua`:

```lua
-- Treat .html files in a templates directory as Jinja2 (htmldjango).
vim.api.nvim_create_autocmd({ "BufRead", "BufNewFile" }, {
  pattern = "*/templates/*.html",
  callback = function()
    vim.bo.filetype = "htmldjango"
  end,
})
```

### Virtual Environment Detection

LazyVim's Python extra uses pyright, which needs to know about your venv to resolve imports. The
simplest approach: always activate your venv before opening nvim from the project directory.

```bash
cd ~/projects/budget-app
source .venv/bin/activate
nvim
```

Pyright automatically detects an active venv. If it doesn't, create a `pyrightconfig.json` in the
project root:

```bash
cat > pyrightconfig.json << 'EOF'
{
  "venvPath": ".",
  "venv": ".venv"
}
EOF
```

---

## 4. Key LazyVim Keybindings to Learn

You don't need to memorize everything -- learn these first and add more as you go. LazyVim uses
`<Space>` as the leader key.

### Navigation

| Keys        | Action                                         |
| ----------- | ---------------------------------------------- |
| `<Space>ff` | Find file (fuzzy search)                       |
| `<Space>fg` | Live grep across project                       |
| `<Space>fb` | Switch between open buffers                    |
| `<Space>e`  | Toggle file explorer (neo-tree)                |
| `gd`        | Go to definition                               |
| `gr`        | Go to references                               |
| `K`         | Hover docs (show function signature/docstring) |
| `<Ctrl-o>`  | Jump back (after gd or gr)                     |
| `<Ctrl-i>`  | Jump forward                                   |

### Editing

| Keys          | Action                                       |
| ------------- | -------------------------------------------- |
| `<Space>ca`   | Code action (quick fixes, imports)           |
| `<Space>cr`   | Rename symbol (refactor)                     |
| `<Space>cf`   | Format file                                  |
| `<Space>cd`   | Line diagnostics (show error/warning detail) |
| `[d` / `]d`   | Jump to previous/next diagnostic             |
| `gcc`         | Toggle line comment                          |
| `gc` (visual) | Toggle comment on selection                  |

### Windows and Buffers

| Keys             | Action                   |
| ---------------- | ------------------------ |
| `<Space>-`       | Split window horizontal  |
| `<Space>\|`      | Split window vertical    |
| `<Ctrl-h/j/k/l>` | Move between splits      |
| `<Space>bd`      | Close current buffer     |
| `H` / `L`        | Previous/next buffer tab |

### Terminal

| Keys        | Action                                 |
| ----------- | -------------------------------------- |
| `<Ctrl-/>`  | Toggle floating terminal               |
| `<Space>ft` | Toggle floating terminal (alternative) |

This is where you'll run Flask, pytest, and database commands without leaving nvim.

### Searching

| Keys        | Action                             |
| ----------- | ---------------------------------- |
| `/`         | Search in current file             |
| `n` / `N`   | Next/previous match                |
| `<Space>sg` | Grep with preview across project   |
| `<Space>sw` | Search current word across project |

### The Most Important Keybinding

| Keys             | Action                               |
| ---------------- | ------------------------------------ |
| `<Space>` (wait) | Show all available keybinding groups |

LazyVim has a which-key popup. Press `<Space>` and wait -- it shows every keybinding group. Press a
letter to drill in. This is how you discover keybindings you've forgotten.

---

## 5. Workflow: Terminal + NeoVim

On your Windows/VS Code setup, you probably have an integrated terminal panel and click-to-run
buttons. The NeoVim workflow is different -- you'll use tmux or nvim's built-in terminal.

### Recommended: tmux

tmux lets you have persistent terminal sessions with splits. Your layout:

```text
┌─────────────────────────────┬──────────────────────┐
│                             │                      │
│        NeoVim               │   Flask server       │
│    (editing code)           │   (flask run)        │
│                             │                      │
│                             ├──────────────────────┤
│                             │                      │
│                             │   psql / pgcli       │
│                             │   (database queries) │
│                             │                      │
└─────────────────────────────┴──────────────────────┘
```

```bash
# Install tmux if you don't have it.
sudo pacman -S tmux

# Start a named session for the project.
tmux new-session -s budget

# Split right for the server: Ctrl-b %
# Split that pane down for psql: Ctrl-b "
# Move between panes: Ctrl-b arrow keys

# To detach (leave running): Ctrl-b d
# To reattach later: tmux attach -t budget
```

### Alternative: NeoVim's Built-in Terminal

If you prefer staying inside nvim:

- `<Ctrl-/>` opens a floating terminal. Run Flask there.
- `<Space>|` splits a window. Open a terminal in the split with `:terminal`.
- Exit terminal mode with `<Ctrl-\><Ctrl-n>`, then navigate normally.

### Daily Startup Routine

```bash
cd ~/projects/budget-app
source .venv/bin/activate
tmux new-session -s budget

# Pane 1: editor
nvim

# Pane 2 (Ctrl-b %): Flask dev server
source .venv/bin/activate
flask run --debug

# Pane 3 (Ctrl-b "): database / tests
source .venv/bin/activate
pgcli budget_app
# or: pytest
```

---

## 6. Verify Everything Works

After completing the setup, run through this checklist:

### PostgreSQL

```bash
psql budget_app -c "SELECT 1 AS connected;"
# Should return: connected | 1
```

### Python + Venv

```bash
cd ~/projects/budget-app
source .venv/bin/activate
python -c "import flask; print(flask.__version__)"
# Should print: 3.1.0
```

### NeoVim + LSP

```bash
source .venv/bin/activate
nvim
```

Inside nvim:

1. Create a test file: `:e test_setup.py`
2. Type:

   ```python
   import flask
   app = flask.Flask(__name__)
   app.
   ```

3. After typing `app.`, you should see autocomplete suggestions from pyright (route, config, etc.).
4. Delete the file: `:!rm test_setup.py`
5. Run `:LspInfo` -- should show pyright attached.
6. Run `:Mason` -- should show pyright, ruff, and pylint installed.

### Pylint

```bash
source .venv/bin/activate
echo 'x=1' > /tmp/test_lint.py
pylint /tmp/test_lint.py
# Should show pylint output with warnings about missing docstring, etc.
rm /tmp/test_lint.py
```

---

## 7. Project Scaffolding -- First Files

Once everything above checks out, create the initial project structure. This is just the skeleton --
enough to prove the stack works end-to-end.

```bash
cd ~/projects/budget-app

# Create the directory structure.
mkdir -p app/{models,routes,services,templates/auth,templates/grid,static/css,static/js,schemas,utils}
mkdir -p migrations
mkdir -p scripts
mkdir -p tests/{test_services,test_routes}

# Create empty __init__.py files where needed.
touch app/__init__.py
touch app/models/__init__.py
touch app/routes/__init__.py
touch app/services/__init__.py
touch app/schemas/__init__.py
touch app/utils/__init__.py
touch tests/__init__.py
touch tests/test_services/__init__.py
touch tests/test_routes/__init__.py
```

Create the entry point `run.py`:

```python
"""Entry point for the Flask application."""

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run()
```

Create a minimal `app/__init__.py` to verify the factory works:

```python
"""Application factory for the Budget App."""

from flask import Flask


def create_app(config_name="development"):
    """Create and configure the Flask application.

    Args:
        config_name: Configuration to use (development, testing, production).

    Returns:
        Configured Flask application instance.
    """
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "dev-secret-key"

    @app.route("/")
    def index():
        """Temporary index route to verify the app runs."""
        return "<h1>Budget App</h1><p>It works.</p>"

    return app
```

### Run It

```bash
source .venv/bin/activate
flask run --debug
```

Open `http://localhost:5000` in a browser. You should see "Budget App -- It works." When you do,
your development environment is confirmed working and you're ready to start building the real app.

---

## Appendix: VS Code vs NeoVim -- Mental Model Shift

Coming from VS Code, the biggest adjustment is that NeoVim is **keyboard-first**. Here are the
equivalents for things you're used to:

| VS Code                       | NeoVim (LazyVim)              |
| ----------------------------- | ----------------------------- |
| Ctrl+P (open file)            | `<Space>ff`                   |
| Ctrl+Shift+F (search project) | `<Space>sg`                   |
| Ctrl+Shift+E (explorer)       | `<Space>e`                    |
| F2 (rename)                   | `<Space>cr`                   |
| F12 (go to definition)        | `gd`                          |
| Ctrl+. (quick fix)            | `<Space>ca`                   |
| Ctrl+` (terminal)             | `<Ctrl-/>` |  |
| Ctrl+Shift+` (new terminal)   | `<Space>\|` then `:terminal` |  |
| Ctrl+B (toggle sidebar)       | `<Space>e`                    |
| Ctrl+/ (toggle comment)       | `gcc` (line) or `gc` (visual) |
| Ctrl+S (save)                 | `:w` or just `:w` habit       |
| Ctrl+Z (undo)                 | `u`                           |
| Ctrl+Shift+Z (redo)           | `<Ctrl-r>`                    |

**The learning curve is real.** For the first few days you'll be slower. That's expected. Resist the
urge to go back to VS Code -- the speed comes after you internalize the keybindings. Two
suggestions:

1. **Keep `vimtutor` accessible.** Run `:Tutor` inside nvim for LazyVim's built-in tutorial. The
   original `vimtutor` (run from the terminal) covers core vim motions.
2. **Learn one new keybinding per session.** Don't try to memorize the whole table. Pick one thing
   you keep reaching for the mouse to do, find the keybinding, and use it until it's muscle memory.
