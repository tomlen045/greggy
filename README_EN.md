# greggy

[中文](README.md) | [English](README_EN.md)

**Machine Rebirth Card** — Rebuilding a machine after a crash, an upgrade, or a fresh install always dies the same way: halfway through you discover a missing brew package, a forgotten launchd job, an environment variable that never got set. greggy ends the "rebuild from memory" era.

Where bakcheck verifies that backup *artifacts* actually restore ([bakcheck](https://github.com/tomlen045/bakcheck)), **greggy governs the rebirth of the machine environment itself**: take a snapshot of this machine, and when it dies, turn that snapshot into an executable rebuild checklist in one command — tick items off until the machine is "reborn".

Four commands: **snapshot** → **plan** (diff against the current machine) → **apply** (dry-run by default) → **doctor** (rebirth completeness score).

[![tests](https://img.shields.io/badge/self--tests-44%2F44-green)]() [![deps](https://img.shields.io/badge/deps-zero-yellow)]() [![license](https://img.shields.io/badge/license-MIT-blue)]()

## What problem it solves

* Can't remember what was installed → **one JSON snapshot**: brew/cask/pip packages (with versions), launchd user jobs, crontab, export/alias lines from shell rc files, interpreter versions, and your frequently-used directories — the machine's entire inventory in one portable file
* The snapshot is the blueprint; a tampered blueprint is deadly → **SHA256 fingerprint binds content + item count**; plan/apply/doctor verify it first and refuse tampered files (exit 2)
* Afraid to let commands loose on a new machine → **apply is dry-run by default** — zero commands executed; `--exec` only runs brew/pip install-class commands; launchd/cron/shell items are always printed for manual confirmation
* One package fails midway → **failures don't interrupt**: each failure is recorded, the rest continue, summary report at the end
* Is the rebuild actually done? → **doctor** checks every item (✅/❌/⏭ manual) and prints a **rebirth completeness NN%** — 100% or it's not done
* Silent version drift → items whose version differs from the snapshot are listed as `[drifted]`, separate from `[missing]`

## Install

```bash
# Option A: one-line script (Gitee → GitHub → jsDelivr mirror fallback)
curl -fsSL https://gitee.com/tomlen/greggy/raw/main/install.sh | sh

# Option B: single file directly (stdlib only, Python 3.8+)
curl -fsSLO https://gitee.com/tomlen/greggy/raw/main/greggy.py
```

## 30-second start

```bash
# 1) On the old machine: take a snapshot (read-only collection)
python3 greggy.py snapshot -o greggy-snapshot.json

# 2) Copy the snapshot to the new machine (self-contained + fingerprint verified), diff it
python3 greggy.py plan greggy-snapshot.json

# 3) Preview what would run (dry-run, zero execution)
python3 greggy.py apply greggy-snapshot.json

# 4) Actually run install-class items; manual items are printed for you
python3 greggy.py apply greggy-snapshot.json --exec

# 5) Post-rebuild health check with completeness score
python3 greggy.py doctor greggy-snapshot.json
```

## What's inside a snapshot

| Section | Content | Rebuild |
|---|---|---|
| brew_formulae / brew_casks | CLI packages / GUI apps (with versions) | `brew install`, --exec capable |
| pip_packages | Pinned Python packages | `pip3 install x==v`, --exec capable |
| launchd_plists | User LaunchAgents jobs | printed for manual `launchctl load` |
| cron_lines | All crontab lines | printed for manual restore |
| shell_exports / shell_aliases | export/alias lines from rc files | printed for manual write-back |
| interpreters | python3/node/go etc. versions | manual verification on drift |
| dirs | Frequently-used directories | `mkdir -p`, --exec capable |

The snapshot carries a fingerprint on top: capture time + host hash + total item count + content SHA256. Human-readable JSON — you can eyeball it when in doubt.

## Four invariants

1. **Read-only collection** — snapshot/doctor never write any system file (except greggy's own snapshot output)
2. **apply is dry-run by default** — without explicit `--exec`, zero commands are executed
3. **Portable snapshot** — self-contained JSON + fingerprint verification, works across machines; tampered snapshots are rejected
4. **Failures don't interrupt** — apply records each failure and continues; summary at the end

## Design boundaries

* greggy governs **declarative checklists and tick-box rebuilding**: which packages, which services, which variables. It does **not** back up your data — that's [bakcheck](https://github.com/tomlen045/bakcheck)'s job; use both for real disaster readiness
* `brew upgrade` appears in plans only for version drift; packages whose version brew can't report are recorded as `?` and checked for existence only
* `--exec` is explicit trust: brew/pip install-class commands only; system-level changes (launchd/cron/shell) are always manual
* The pip inventory reflects the default pip environment; snapshot each venv separately if needed

## Self-test

```bash
python3 greggy.py selftest   # 44/44 green (20 cases, all four invariants verified)
```

## License

MIT
