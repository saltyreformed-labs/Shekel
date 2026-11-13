# Visual Loop (Fable 5 design iteration)

How a design session screenshots a UI and iterates on it. Uses the repo's existing Python Playwright
plus Chromium through `tests/manual/shoot.py`. No Node, no new dependencies. Verified working
2026-06-10.

## Principle: do not anchor the fresh session

A fresh session must generate its own directions from the written design intent, not from a prior
mockup someone already made. So:

- Source of truth for a screen: `docs/design/fable5-design-language.md` plus that screen's audit
  (for the grid, `docs/design/grid_audit.md`). The `shekel-design` skill loads these. They state the
  constraints and the option space, not a fixed look.
- Keep exploration mockups OUT of the repo and out of `docs/design/`. Put scratch mockup HTML and
  its screenshots under `/tmp` (or another scratch dir) and delete them when done. A mockup
  committed to `docs/design/` would anchor the next session toward that look. (This is why the
  earlier grid directions mockup was removed.)

## The two loops

### Loop A: design exploration (fast, no app / DB / auth)

For trying visual directions on a screen before touching real templates.

1. Create a self-contained mockup HTML in a scratch dir, e.g. `/tmp/grid_explore.html`. Inline
   `<style>`/`<script>` are fine here because the file is opened directly, not served through the
   app, so the app CSP does not apply.
2. Shoot it:

   ```text
   .venv/bin/python tests/manual/shoot.py /tmp/grid_explore.html \
       --directions a,b,c --themes dark,light --viewports desktop,mobile \
       --out /tmp/grid_shots
   ```

3. `Read` the PNGs in `/tmp/grid_shots` (the Read tool renders images). Compare against the design
   language and the audit. Edit the mockup, re-shoot, repeat.
4. Once a direction is chosen, move to Loop B to build it for real, then delete the scratch files.

### Loop B: real-app implementation (truer, needs the app plus a login session)

For building the chosen direction into the real grid.

1. Dev database: `docker compose -f docker-compose.dev.yml up -d db`.
2. Start the dev app the way the manual harness expects it. The harness base URL is
   `http://172.32.0.1:5000` (see `tests/manual/save_dev_session.py`); the app must be reachable
   there. Confirm your normal dev-run command serves on that address.
3. One-time login capture (re-run when the cookie expires or after logout):

   ```text
   .venv/bin/python tests/manual/save_dev_session.py
   ```

   It prompts for the password (typed silently), types the dev key's two-factor code, and writes
   `tests/manual/.dev_session_state.json` (gitignored). Every account must use two-factor, so the
   dev copy's account needs the dev key first: set `SHEKEL_DEV_TOTP_SECRET` in `.env` and run
   `.venv/bin/python -m tools.dev_two_factor <email>` against the copy (`tools/dev_two_factor.py`
   says how).
4. Edit the real templates (`app/templates/grid/`) and the matching stylesheet under
   `app/static/css/` (`grid.css` for the grid; layout in `css_architecture_audit.md`). Honor the
   hard constraints: no inline `<style>`/`<script>` (CSP), tokens not raw hex, both themes via
   `data-bs-theme`, money computed in services not templates.
5. Shoot the running grid:

   ```text
   .venv/bin/python tests/manual/shoot.py http://172.32.0.1:5000/grid \
       --themes dark,light --viewports desktop,mobile \
       --storage-state tests/manual/.dev_session_state.json
   ```

   (Default `--out` is `tests/manual/screenshots/`, which is gitignored.)
6. `Read` the PNGs, compare, iterate.

## shoot.py reference

- `target`: a repo-relative or absolute file path, or an `http(s)` URL.
- `--themes` (default `dark,light`): sets `data-theme` and `data-bs-theme` on `<html>`.
- `--directions`: sets `data-direction` for mockup variants; omit for the real app.
- `--viewports` (default `desktop,mobile`): desktop is 1440x900, mobile is 390x844, captured at 2x.
- `--storage-state`: Playwright `storage_state` JSON for an authenticated app target.
- `--out`: output directory. Use a scratch dir for exploration; the default is the gitignored
  `tests/manual/screenshots/`.
- `--name`: filename label (defaults to the target basename).
- Output filenames: `<name>__<viewport>[__<direction>]__<theme>.png`.

## Model discipline (same split as the shekel-design skill)

- Fable 5 (`/model fable`) for visual, template, and CSS work.
- Opus 4.8 for `app/services/`, `app/routes/`, and test-assertion edits (financial logic).

## Acceptance checks before a screen is "done"

For the grid specifically (from `grid_audit.md`):

- Desktop mark-paid in one click, no popover detour.
- A wide six-month grid is easy to track across rows and columns, without added clutter.
- Renders correctly in both themes and both viewports.
- No inline `<style>`/`<script>` introduced in app templates; grep the changed templates.
- Targeted route and service tests pass, then the full suite via `./scripts/test.sh`.

## Fable availability

Claude Code 2.1.170 is the Fable 5 minimum (this machine is on 2.1.170). If `/model fable` does not
stick, run `claude update` and retry. If Fable is still unavailable, the loop works identically on
Opus 4.8, just with a lower design ceiling.
