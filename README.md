<h1 align="center">firstmate-gear</h1>

<p align="center">
  <a href="https://github.com/mikefwille/firstmate-gear/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/mikefwille/firstmate-gear/actions/workflows/ci.yml/badge.svg" /></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" /></a>
</p>

<h3 align="center">Extra gear for your firstmate fleet.</h3>

Companion tools for [firstmate](https://github.com/kunchenguid/firstmate), the agent-orchestration template where you talk to one agent and it runs a crew for you.
firstmate gives you the crew; this repo outfits the captain.
Everything here is read-only over a firstmate home's on-disk state, so it is always safe to point at a live fleet.

## The gear

| Tool | What it does |
| --- | --- |
| [`fm-status`](fleet-dashboard/) | **The fleet board.** One calm, live terminal view of every in-flight crew job. A 3-color semaphore per job answers the only question that matters: does anything need you? Leave it open in a spare pane. |
| [`fm-read`](reading-room/) | **The reading room.** A two-pane TUI for everything the crew has written: briefs, scout reports, and the fleet's own docs. The board shows what's happening; this is where you read what got produced. |

Both are single-file Python scripts with inline dependency metadata - no venv, no manual dependency install.

## Install

Requires [`uv`](https://docs.astral.sh/uv/) (`brew install uv`).

```sh
uv tool install git+https://github.com/mikefwille/firstmate-gear
```

That puts `fm-status` and `fm-read` on your PATH.
Update later with `uv tool upgrade firstmate-gear`; remove with `uv tool uninstall firstmate-gear`.

Or try it without installing anything:

```sh
uvx --from git+https://github.com/mikefwille/firstmate-gear fm-status
```

Or clone and run the scripts directly - each is self-bootstrapping:

```sh
git clone https://github.com/mikefwille/firstmate-gear
firstmate-gear/fleet-dashboard/fm-status
```

## Quick start

`cd` into your firstmate folder and go:

```sh
cd your/firstmate
fm-status            # the live board; q to quit (--snapshot for a one-shot render)
fm-read              # the reading room
```

The tools find the firstmate home the same way git finds a repo: they walk up from your current directory.
Anywhere inside a firstmate home - the primary or a secondmate home - just works.

To target a home from somewhere else, set `$FM_HOME` or pass `--home`:

```sh
FM_HOME=~/code/firstmate fm-status
fm-read --home ~/code/firstmate
```

Running more than one fleet? One install serves them all - each terminal pane points wherever its cwd or `$FM_HOME` says.

## Documentation

- [fleet-dashboard/README.md](fleet-dashboard/README.md) - the board: the semaphore, scrolling, the roadmap view, what it reads.
- [reading-room/README.md](reading-room/README.md) - the reading room: the document map, keys, search.

## License

MIT - see [LICENSE](LICENSE).
