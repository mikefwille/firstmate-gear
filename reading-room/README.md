# fm-read - the firstmate reading room

A terminal reading room for everything the crew has written: briefs, scout reports, and the fleet's own docs (backlog, captain, morning digest, decisions).
The [fleet board](../fleet-dashboard) shows what's happening *now*; this is where you sit down and read what the crew *produced*.

A two-pane interactive TUI ([Textual](https://textual.textualize.io/)): a document map on the left, live-rendered markdown on the right.
Single self-bootstrapping [`uv`](https://docs.astral.sh/uv/) script - deps resolve on first run, no venv. Read-only over a firstmate home's `data/`.

## Run

Install once (see the [repo README](../README.md)), then run it from anywhere inside a firstmate home:

```sh
fm-read                    # open the reading room
fm-read surveillance       # open with the first matching doc selected
fm-read reader mode report # jump straight to a report (all words must match)
FM_HOME=/path fm-read      # read a firstmate home from anywhere
```

It finds the home like git finds a repo: `--home` wins, then `$FM_HOME`, then it walks up from your current directory. From a clone, `./fm-read` runs the script directly (requires [`uv`](https://docs.astral.sh/uv/)).

## Keys

| key | action |
|---|---|
| `↑` `↓` / `j` `k` | move through the document map |
| `enter` | open the selected doc |
| `t` | toggle the table of contents (great for the long reports) |
| `r` | rescan `data/` and reload |
| `q` | quit |

## The map

The sidebar mirrors the fleet's own structure:

- **FLEET** - the orientation docs: backlog, morning digest, captain, projects, context, decisions.
- **IN FLIGHT** - tasks currently running (● green), each expanded to its brief + any direction notes.
- **REPORTS & DONE** - finished tasks (✓ dimmed green), each holding its brief and (for scouts) the full report.

In-flight vs done is read live from `data/backlog.md`, so the map re-sorts itself as the fleet moves. A bright ● marks what's running; a dimmed ✓ marks what's finished - the only state color, same language as the fleet board.

## Design

Calm captain's bridge: dark, restrained, `tokyo-night` base. The sidebar is the map; the page is the focus. Nothing decorative competes with the words.

## Files

- `fm_read.py` - the app (single file, uv inline deps).
- `fm-read` - thin launcher.
