# REPACK automation script for Linux using Bottles

A Python CLI tool that automates setting up a fresh [Bottles](https://usebottles.com/) Gaming prefix for FitGirl game repack folders on Linux (Flatpak or native Bottles).

## Features

- **Zero-touch Environment Discovery (`auditor.py`):** Auto-detects Flatpak vs. native `bottles-cli`, existing bottles, installed Wine runners (defaults to `soda-9.0-1`), DXVK/VKD3D/NVAPI/LatencyFleX versions, Gaming template dependencies, and cached installers in Bottles' `temp/` directory.
- **Safe One-Off Flatpak Mounts (`builder.py`):** Grants Bottles Flatpak temporary read-only access (`--filesystem=<repack_dir>:ro`) for the single installer invocation — never modifies persistent Flatpak overrides.
- **Automated `systemd-oomd` Lifecycle:** Stops `systemd-oomd.socket` and `systemd-oomd.service` immediately before launching `setup.exe` (preventing Ubuntu's userspace OOM killer from terminating `srep`/`cls-lolz` mid-extraction) and guarantees their restoration in a `try ... finally` block even if the installer crashes or is cancelled with `Ctrl+C`. Use `--keep-oomd` to opt out.
- **3-Tier Offline-Resilient Dependency Resolution:** Works reliably even when `ping.usebottles.com` is unreachable (which puts `bottles-cli` in offline mode):
  1. **Tier A (`local-redist`):** Runs the game's bundled `_Redist` / `Redist` / `_CommonRedist` installers first.
  2. **Tier B (`cached`):** Reuses matching installers already in Bottles' `temp/` cache.
  3. **Tier C (`download`):** Downloads missing installers from the Bottles dependency manifest into Bottles' `temp/` cache (with MD5 verification) **only** when `--allow-download` is passed.
- **Post-Extraction PE32/PE32+ Binary Import Scanning (`scanner.py`):** Pure-Python PE import table parser (no external `pefile` package required) inspects extracted `.exe`/`.dll` files to infer missing VC++ (`msvcr80`–`vcruntime140`), DirectX (`d3dx9_*`, `d3dx11_*`, `d3dcompiler_*`), `xinput`, `xact`, `.NET`, and `PhysX` runtimes, and applies matching Wine `DllOverrides` via `bottles-cli reg`.
- **FitGirl Expansion Disk Check:** Estimates unpacked size (`fg-*.bin` × expansion multiplier + Gaming template size + 10% headroom) against free disk space before touching anything.
- **Strict `--dry-run` Guardrail:** Prints the full execution plan (detected dependencies, runner/component choices, disk check, and every exact `bottles-cli` / `systemctl` command) without modifying files or creating a bottle.

---

## Requirements

- Linux with **Bottles** (Flatpak `com.usebottles.bottles` or native `bottles-cli`)
- Python 3.10+ and `PyYAML` (`python3-yaml`)
- `pytest` (optional, for running the unit test suite)

---

## Quick Start

```bash
# 1. Preview the full plan without making any changes
./auto_bottle.py "/path/to/Example Game - Complete Edition [FitGirl Repack]" --dry-run

# 2. Run interactively (default — lets you tick FitGirl's "Limit installer to 2 GB of RAM" box)
./auto_bottle.py "/path/to/Example Game - Complete Edition [FitGirl Repack]"

# 3. Re-scan an already extracted game (install missing _Redist/PE deps & register .exe)
./auto_bottle.py "/path/to/Example Game - Complete Edition [FitGirl Repack]" --post-install-only
```

---

## CLI Reference

| Flag | Default | Description |
|---|---|---|
| `installer_dir` | *(required)* | Path to the FitGirl installer folder containing `setup.exe` and `fg-*.bin`. |
| `--name NAME` | auto-derived | Override the bottle name and `C:\Games\<Name>` install target. |
| `--runner RUNNER` | `soda-9.0-1` | Override the Wine runner (validated against installed runners). |
| `--target WIN_PATH` | `C:\Games\<Name>` | Override the Windows destination folder inside `drive_c`. |
| `--dry-run` | off | Output the complete execution plan without modifying files or creating a bottle. |
| `--silent` | off | Pass Inno Setup silent flags (`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=...`). |
| `--keep-oomd` | off | Skip stopping/restarting `systemd-oomd.socket` & `systemd-oomd.service` during install. |
| `--reuse-bottle` | off | Install into an existing bottle of the same name instead of aborting. |
| `--allow-download` | off | Allow downloading missing dependencies into Bottles' `temp/` cache. |
| `--post-install-only` | off | Skip bottle creation & `setup.exe`; run post-extraction scan, dep install, and registration. |
| `--game-exe PATH` | auto-detected | Explicitly specify the installed game `.exe` to register in Bottles. |
| `--with-optional` | off | Include `fg-optional-*.bin` in the disk-space calculation. |
| `--expansion FLOAT` | `1.6` | FitGirl repack expansion multiplier for disk estimation. |
| `--installed-size SIZE` | auto | Explicit installed size override (e.g., `22G`). |
| `--verify-bins` | off | Verify `MD5/fitgirl-bins.md5` checksums before running `setup.exe`. |
| `--json` | off | Emit the execution plan in machine-readable JSON format. |
| `-y`, `--yes` | off | Skip the interactive `Proceed? [y/N]` prompt in live mode. |

---

## Shell Helper Functions (`ab-full` & `ab-post`)

Add these to your `~/.bashrc` or `~/.zshrc` for one-command repack setup:

```bash
# Dry-run preview followed by interactive full setup (pauses systemd-oomd around setup.exe)
ab-full() {
  local dir="$1"
  shift
  "$HOME/auto_bottle/auto_bottle.py" "$dir" --dry-run "$@" && \
  "$HOME/auto_bottle/auto_bottle.py" "$dir" "$@"
}

# Post-extraction scan, missing dependency installation, and Bottles program registration
ab-post() {
  local dir="$1"
  shift
  "$HOME/auto_bottle/auto_bottle.py" "$dir" --post-install-only "$@"
}
```

---

## Project Layout

```text
auto_bottle/
├── auto_bottle.py        # CLI entrypoint & plan renderer
├── scanner.py            # Name derivation, _Redist mapping, PE32/PE32+ import parser, disk check
├── auditor.py            # Bottles environment, component, template & temp/ cache auditor
├── builder.py            # Command generator, systemd-oomd lifecycle & live executor
├── setup_zram_perf.sh    # Optional Ubuntu 4 GiB ZRAM + sysctl desktop responsiveness helper
├── PROCEDURES.md         # Linux FitGirl repack survival guide & DXVK/fsync tuning
├── specs/
│   └── bottles_plan.md   # Architecture & CLI specification
└── tests/
    └── test_scanner.py   # Mock test suite (fake _Redist trees, PE stubs, oomd guardrails)
```

## Running Tests

```bash
pytest -v tests/test_scanner.py
```

See [PROCEDURES.md](PROCEDURES.md) for the complete Linux FitGirl repack survival guide (8 GB RAM swap/ZRAM prep, `fsync` vs. `ntsync`, and DirectX 9 / DXVK tuning).
