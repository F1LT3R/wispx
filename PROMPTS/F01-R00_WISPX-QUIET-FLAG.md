# F01 — wispx shell script: `--quiet` flag (foreground by default)

**Status:** PLANNED — approved by operator 2026-08-26 (spec settled by explicit
agreement; do not change the CLI shape below).

## Implementation order

1. Rewrite argument handling + `start()` in `/Users/user/repos/wispx/wispx`
   (the bash script, not `wispx.py`)
2. Update the daemon section + files table in `/Users/user/repos/wispx/README.md`
3. Run the acceptance tests below (all are single-shell, agent-runnable)
4. Commit (see "Commit")

One logical change; fits easily in one fresh-agent session.

## Context for the fresh agent

- Repo: `/Users/user/repos/wispx` (git, main). Files: `wispx` (executable bash
  script), `wispx.py` (the dictation program), `mic_scan.py`, `README.md`.
  `wispx.py` is NOT modified by this feature.
- `wispx` currently wraps `~/repos/wispx/whisper_env/bin/python wispx.py`
  (absolute paths, `$HOME`-anchored). Subcommands today: bare `wispx` and
  `wispx start` both launch a **background** daemon (nohup, log
  `~/.wispx.log`, pidfile `~/.wispx.pid`, prints `wispx started (pid N)`);
  `stop` and `status` use pgrep with the bracket-trick pattern
  `[w]ispx/wispx.py`. (Note: F01 *replaces* that pattern — see
  Implementation notes; the description here is as-is behavior only.)
- Single-instance guard (keep it, apply to every start path): pidfile alive?
  else `pgrep -f "$PATTERN"`? → print `wispx already running: <pids>` and exit 0.
- `wispx.py` startup output, in order:
  `Loading faster-whisper model 'base' (int8, cpu)...`, `Model ready.`,
  `Mic: 'Clarett+ 8Pre' input(s) [3, 4] -> mono`,
  `Listening for Ctrl+Option... (press and hold to record, release to stop)`.
  (Per-dictation lines are also printed but are out of scope for tests.)

## The spec (as agreed with the operator — do not deviate)

CLI model: **`wispx` and `wispx start` are the same invocation (start,
foreground). `--quiet` may be appended to either — `wispx --quiet` ===
`wispx start --quiet` — and switches to the background start. `stop` and
`status` take no flags.**

| Invocation | Behavior |
|---|---|
| `wispx` | Start the service in the **foreground**, full program output visible in this terminal. Prints `wispx started (pid N)` as its first line. Ctrl-C stops it and frees the terminal. |
| `wispx start` | Identical to `wispx`. |
| `wispx --quiet` | Start the service in the **background** (nohup). Prints exactly one line: `wispx started (pid N)`. All program output goes to `~/.wispx.log`. Shell returns immediately. |
| `wispx start --quiet` | Identical to `wispx --quiet`. |
| `wispx stop` | Unchanged from current behavior. |
| `wispx status` | Unchanged from current behavior. |
| Anything else (e.g. `wispx badarg`, `wispx stop --quiet`) | Print `usage: wispx [--quiet] \| {stop|status}` to stderr, exit 1. |

## Implementation notes (remove ambiguity)

- **Foreground start:** print `wispx started (pid $$)` then
  `exec "$PYTHON" "$SCRIPT"` — so the script process *becomes* the python
  process: the printed pid is the real one, Ctrl-C kills it directly, and the
  terminal is fully freed afterwards. Do NOT write `~/.wispx.pid` in the
  foreground path; the pgrep side of the guard already detects foreground
  instances (that is why `stop`/`status` work on them today).
- **Quiet start:** exactly the current `start()` body (nohup,
  `echo $! > "$PIDFILE"`, log to `$LOGFILE`, print the one pid line). No
  behavior change there.
- Argument parsing: accept an optional first token from
  `{start, --quiet}` in any combination (including none), then run the
  matching start path. `stop` and `status` accept **no** flags. Preserve
  `set -u` and the existing style (functions `start`/`stop`/`status`,
  `case` dispatch, `shellcheck` comment where present).
- `start()` is currently called in two shapes; restructure so the foreground
  and quiet paths are distinct (e.g. `start_foreground` / `start_quiet`, or a
  flag parameter — your choice, but keep the single-instance guard shared).
- **Tighten `$PATTERN`** (replaces the `[w]ispx/wispx.py` bracket trick from
  the current script): set
  `PATTERN="$WISPX_DIR/whisper_env/bin/python .*wispx/wispx\.py"` so that
  `stop`/`status`/the guard match only processes the wrapper itself launches
  (the venv interpreter running the script) — never an editor, shell, or
  other process whose command line merely mentions the file path. The
  bracket trick is no longer needed: the anchored pattern cannot match the
  wrapper's own `./wispx stop` command line.
- Do NOT change: `wispx.py`, `mic_scan.py`, paths (`$WISPX_DIR`, `$PYTHON`,
  `$SCRIPT`, `$PIDFILE`, `$LOGFILE`), or log/pidfile names. (The one
  sanctioned exception is `$PATTERN`, tightened as specified above.)

## README changes (exact)

Replace the current daemon block (the one starting
"Quit with `Ctrl-C` (foreground), or manage it as a background daemon:") with
documentation matching the spec table: `./wispx` = foreground with output;
`./wispx --quiet` = background, logs to `~/.wispx.log`, pidfile
`~/.wispx.pid`; `./wispx status` / `./wispx stop` from any shell. Keep it short
(a fenced code block of the four invocations + one line about the log/pidfile).

## Acceptance criteria + how to test

All commands from `/Users/user/repos/wispx`, in one shell, macOS + bash.
No GNU coreutils (no `timeout`); the model is already cached on this
machine, so the wait budgets below are safe.

1. **Foreground output.**
   `./wispx >/tmp/wispx_t1.log 2>&1 & P=$!; sleep 15; head -5
   /tmp/wispx_t1.log; ./wispx stop; kill -0 $P 2>/dev/null && echo
   STILL-ALIVE || echo DEAD`
   → line 1 matches `wispx started (pid [0-9]+)`; lines 2–5 are the four
   `wispx.py` startup lines in the documented order; final line `DEAD`.
2. **Foreground is stoppable + visible to other commands.**
   `( ./wispx >/tmp/wispx_fg.log 2>&1 & FPID=$!; sleep 12; ./wispx status;
   ./wispx stop; kill -0 $FPID 2>/dev/null && echo STILL-ALIVE || echo
   DEAD )`
   → `status` prints `running: <one pid>`; `stop` prints
   `wispx stopped: <same pid>`; final line is `DEAD`.
3. **Quiet start output.** `./wispx --quiet` → stdout is exactly one line:
   `wispx started (pid [0-9]+)`. Prompt returns immediately.
4. **Quiet logs.** After step 3, wait up to 60s for startup to finish:
   `for i in {1..60}; do grep -q 'Listening for Ctrl+Option' ~/.wispx.log
   && break; sleep 1; done; tail -2 ~/.wispx.log`
   → shows the `Mic:` and `Listening for Ctrl+Option...` lines (i.e.
   program output went to the log, not the terminal).
5. **Single instance, both directions.** While the quiet daemon from step 3/4
   is alive: `./wispx` → `wispx already running: <pid>` and no new process
   (`./wispx status` lists exactly one pid). `./wispx stop` afterwards works.
   Then reverse: start foreground via step 2's subshell and while it is alive
   run `./wispx --quiet` → `already running` line, still one pid.
6. **Stop/status idle.** With nothing running: `./wispx status` →
   `not running`; `./wispx stop` → `wispx not running`; both exit 0.
7. **Bad args.** `./wispx badarg` and `./wispx stop --quiet` → usage line on
   stderr, exit 1.
8. **Stale pidfile recovery.** With the quiet daemon running, `kill -9 <pid>`
   but do NOT delete the pidfile (it now points at a dead pid). Then
   `./wispx --quiet` → must start a fresh instance (the guard's pidfile
   check fails on the dead pid and falls through to pgrep). Afterwards
   `./wispx stop` cleans up (it also removes the pidfile).
9. **No second process leaks.** At the very end: `./wispx status` →
   `not running`, and `pgrep -f "$PATTERN"` prints nothing.
10. **Unrelated-process immunity.** With the quiet daemon running, launch
    `python3 -c 'import time; time.sleep(120)' "$WISPX_DIR/wispx.py" &
    FAKE=$!` — a live process whose command line contains the script path
    but is not the service (stand-in for `vim .../wispx.py`; note the venv
    interpreter is deliberately NOT used, so it cannot match the pattern).
    `./wispx stop` → the service dies and `kill -0 $FAKE` still succeeds.
    Afterwards `kill $FAKE`.

## Commit

`git add -A`, commit as `sungunAgentic <agent@sungun.ai>`, subject
`wispx script: foreground default, --quiet for background start`, body noting
the CLI contract from the spec table. (Per repo convention; also run
`cp-pi-conv` first if available.)

## Out of scope

- Any change to `wispx.py` (transcription, hotkey, mic handling)
- `stop --quiet`, other flags, `--model` passthrough, autostat/launchd
- Changing log/pidfile locations
