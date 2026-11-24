# auto_bottle — Phase 1: Environment Discovery & Spec

_Reference environment spec (Ubuntu Linux, 8 threads, 8 GiB RAM class host, Flatpak Bottles 64.1)._

---

## 1. Discovered environment

### 1.1 Bottles installation

| Item | Value |
|---|---|
| Install type | **Flatpak** (system install, Flathub) — no native `bottles`/`bottles-cli` on `$PATH` |
| App ID / version | `com.usebottles.bottles` **64.1** (runtime `org.gnome.Platform//49`) |
| CLI invocation | `flatpak run --command=bottles-cli com.usebottles.bottles <subcommand>` |
| Bottles data root | `~/.var/app/com.usebottles.bottles/data/bottles/` |
| Bottles dir (`info bottles-path`) | `~/.var/app/com.usebottles.bottles/data/bottles/bottles` |
| Dependency/download cache | `~/.var/app/com.usebottles.bottles/data/bottles/temp/` (2.1 GiB) |
| Templates | `~/.var/app/com.usebottles.bottles/data/bottles/templates/01a94690-…` (env `gaming`, 1.2 GiB) |
| Backend source (read-only) | `$(flatpak info --show-location com.usebottles.bottles)/files/share/bottles/bottles/` |
| Sandbox file access | **none beyond its own data dir** (`shared=network;ipc`, no `filesystems=`) → it **cannot see `~/Downloads`** |

### 1.2 Installed components (`bottles-cli -j list components`)

| Kind | Installed |
|---|---|
| Runners | `soda-9.0-1` (downloaded), `sys-wine-11.0` (Wine bundled in the Flatpak) |
| DXVK | `dxvk-3.0.2` |
| VKD3D | `vkd3d-proton-3.0.1` |
| NVAPI | `dxvk-nvapi-v0.9.2` |
| LatencyFleX | `latencyflex-v0.1.1` |

### 1.3 Existing bottles

| Name | Path | Env | Runner | Installed_Dependencies |
|---|---|---|---|---|
| `Existing Game` | `Existing-Game` | Gaming | soda-9.0-1 | d3dx9, msls31, arial32, times32, courie32, d3dcompiler_43, d3dcompiler_47, mono, gecko, vcredist2015, d3dx11, dotnet40, dotnet45, dotnet452, gdiplus |

**Gaming template** (`templates/01a94690…/template.yml`): runner `soda-9.0-1`, `sync: ntsync`, already contains
`d3dx9, msls31, arial32, times32, courie32, d3dcompiler_43, d3dcompiler_47, mono, gecko`.
It matches Bottles 64.1's `Samples.environments["gaming"]`, so it is **not outdated** and `new --environment gaming` will clone it rather than rebuilding (fast, and nothing downloaded again).

### 1.4 Cached dependency installers already in `temp/` (reused, not downloaded again)

`vcredist2015_x86.exe`, `vcredist2015_x64.exe`, `dotnet40.exe`, `dotnet45.exe`, `dotnet452.exe`, `d3dx9.tar.xz` (+extracted), `d3dx11.tar.xz` (+extracted), `arial32/courie32/times32(.exe)`, `InstMsiW.exe`, `wine_gecko-2.47.4-{x86,x86_64}.msi`, `wine-mono-11.2.0-x86.msi`, `windows6.1-kb976932-{x86,x64}.exe`, and the component tarballs (soda, dxvk, vkd3d, nvapi, latencyflex).

### 1.5 ⚠️ Connectivity finding (this matters)

* `https://ping.usebottles.com` currently returns **HTTP 522** (Cloudflare origin timeout).
  Bottles uses that endpoint for its online check, so **every `bottles-cli` call logs `Connection status: offline …`**.
* When Bottles thinks it is offline, `DependencyManager.fetch_catalog()` returns `{}`, so **Bottles silently skips every dependency install** (in `create_bottle`, the code runs `if dep in self.supported_dependencies:`, and that dict is empty).
* The dependency repository itself (`https://proxy.usebottles.com/repo/dependencies/index.yml`) **does respond** from the host, and its manifests (download URLs + MD5) can be read.

### 1.6 Example target repack layout

| Item | Value |
|---|---|
| Folder | `~/Downloads/Example Game - Complete Edition [FitGirl Repack]` |
| Installer | `setup.exe` — **Inno Setup 5.5.0 (Unicode)**, FitGirl custom build |
| Data bins | `fg-01..07.bin` (~13.2 GiB) |
| Optional bins | `fg-optional-modpack.bin` (~0.4 GiB) |
| Integrity | `MD5/fitgirl-bins.md5` |
| `_Redist` on disk | **Not present pre-install.** FitGirl packs `_Redist` inside the bins; it only shows up as `<install dir>\_Redist` **after** installation |

---

## 2. Verified `bottles-cli` syntax (Bottles 64.1)

Global: `bottles-cli [-h] [-v] [-j/--json] {info,list,programs,add,tools,reg,reg-rules,edit,new,run,standalone,shell}`

| Subcommand | Exact syntax (from `--help`) |
|---|---|
| `info` | `info {bottles-path,health-check}` |
| `list` | `list [-f FILTER] {bottles,components}` (e.g. `-f environment:gaming`) |
| `programs` | `programs -b BOTTLE` |
| `add` | `add -b BOTTLE -n NAME -p PATH [-l LAUNCH_OPTIONS] [--no-dxvk] [--no-vkd3d] [--no-dxvk-nvapi]` |
| `tools` | `tools -b BOTTLE {cmd,winecfg,uninstaller,regedit,taskmgr,control,explorer}` |
| `reg` | `reg -b BOTTLE -k KEY -v VALUE [-d DATA] [-t {REG_DWORD,REG_SZ,REG_BINARY,REG_MULTI_SZ}] {add,edit,del}` |
| `edit` | `edit -b BOTTLE [--params k:v,…] [--env-var K=V] [--win WIN] [--runner R] [--dxvk D] [--vkd3d V] [--nvapi N] [--latencyflex L]` |
| `new` | `new --bottle-name NAME --environment {gaming,application,custom} [--custom-environment YML] [--arch {win32,win64}] [--runner R] [--dxvk D] [--vkd3d V] [--nvapi N] [--latencyflex L]` |
| `run` | `run -b BOTTLE [-e EXECUTABLE \| -p PROGRAM] [--args-replace] [args ...]` |
| `shell` | `shell -b BOTTLE -i INPUT` |
| `standalone` | `standalone -b BOTTLE` |

**Things I checked in the 64.1 source that `--help` doesn't tell you:**

1. **There is no "install dependency" subcommand.** The only CLI path into Bottles' dependency manager is `new --environment custom --custom-environment recipe.yml` (it installs `Installed_Dependencies` from the recipe). **But** a custom environment never uses the Gaming template, skips the Windows-version/CMD/winemenubuilder setup, and still needs Bottles to think it is online (§1.5).
2. `-b BOTTLE` matches the bottle's **display `Name`** (e.g. `"Example Game"`), not its directory (`Example-Game`).
3. `run -e` extra args are joined with spaces and passed straight to Wine, so Inno flags like `/DIR=…` go through as positional args.
4. `add -p` takes a Unix path (it must exist and be visible inside the sandbox) or a Windows path.
5. `new` doesn't reliably report failure through its exit code → the builder **checks that `<bottles>/<path>/bottle.yml` exists afterwards**.

---

## 3. Execution plan (the commands the tool will produce)

Shorthand: `BCLI = flatpak run --command=bottles-cli com.usebottles.bottles`
Variables for an example game: `NAME="Example Game"`, `BOTTLE_DIR=~/.var/app/com.usebottles.bottles/data/bottles/bottles/Example-Game`, `SRC="$HOME/Downloads/Example Game - Complete Edition [FitGirl Repack]"`, `TARGET='C:\Games\Example Game'`.

| # | Step | Command |
|---|---|---|
| 0 | Pre-flight (read-only) | `BCLI -j list bottles`, `BCLI -j list components`, `BCLI info bottles-path`, read `templates/*/template.yml`, list `temp/`, `statvfs()` on the bottles dir |
| 1 | Create bottle (Gaming template, reuse installed components) | `BCLI new --bottle-name "Example Game" --environment gaming --arch win64 --runner soda-9.0-1 --dxvk dxvk-3.0.2 --vkd3d vkd3d-proton-3.0.1 --nvapi dxvk-nvapi-v0.9.2 --latencyflex latencyflex-v0.1.1` |
| 2a | Pause `systemd-oomd` | `sudo systemctl stop systemd-oomd.socket systemd-oomd.service` |
| 2 | Run FitGirl installer (grant **read-only, one-off** access to the repack folder only) | `flatpak run --filesystem="$SRC":ro --command=bottles-cli com.usebottles.bottles run -b "Example Game" -e "$SRC/setup.exe" [silent flags]` |
| 2b | …with `--silent` | adds `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR="C:\Games\Example Game"` (`/LANG=english` optional) |
| 2c | Resume `systemd-oomd` (`finally`) | `sudo systemctl start systemd-oomd.socket systemd-oomd.service` |
| 3 | Post-install re-scan | scanner runs again on `$BOTTLE_DIR/drive_c/Games/Example Game` (reads `_Redist/` + PE imports of the game exe) |
| 4 | Install **missing** deps only (see §4) | e.g. `BCLI run -b "Example Game" -e "$BOTTLE_DIR/drive_c/Games/Example Game/_Redist/vcredist_x86_2008.exe" /q` |
| 4b | DLL overrides Bottles would have set | e.g. `BCLI reg -b "Example Game" -k 'HKEY_CURRENT_USER\Software\Wine\DllOverrides' -v msvcr90 -d native,builtin -t REG_SZ add` |
| 5 | Register the game | `BCLI add -b "Example Game" -n "Example Game" -p "$BOTTLE_DIR/drive_c/Games/Example Game/ExampleGame.exe"` |
| 6 | Verify | `BCLI -j programs -b "Example Game"` → the list should contain `Example Game` |

`--dry-run` prints all of the above (with real resolved values), plus detected deps, runner choice, and the disk check, then exits **without** running anything that writes. Read-only discovery commands from step 0 still run, because the plan depends on their output.

---

## 4. Dependency mapping (`_Redist` / PE imports → Bottles dependency names)

All target names exist in the live Bottles dependency index (verified: `vcredist2005…2022`, `dotnet20…dotnet481`, `dotnetdesktop6…10`, `d3dx9`, `d3dx11`, `d3dcompiler_43/47`, `xact`, `xact_x64`, `xinput`, `xna31`, `xna40`, `physx`, …).

### 4.1 Filename rules (case-insensitive, applied to every file under `_Redist`, `Redist`, `_CommonRedist`, `__Installer`)

| Pattern (regex on relative path) | Bottles dep(s) | Silent switch when run directly |
|---|---|---|
| `vcredist.*2005\|vcredist_x(86\|64)_2005\|\b2005\b.*vc` | `vcredist2005` | `/q` |
| `…2008…` | `vcredist2008` | `/q` |
| `…2010…` | `vcredist2010` | `/q /norestart` |
| `…2012…` | `vcredist2012` | `/install /quiet /norestart` |
| `…2013…` | `vcredist2013` | `/install /quiet /norestart` |
| `…2015…`, `…2017…`, `…2019…`, `…2022…`, `vc_redist.x(86\|64)` | `vcredist2015`* / `vcredist2019` / `vcredist2022` | `/install /quiet /norestart` |
| `dxsetup\.exe`, `directx.*redist`, `dxwebsetup` | `d3dx9`, `d3dx11`, `d3dcompiler_43`, `xact`, `xinput` | `DXSETUP.exe /silent` |
| `dotnetfx35\|dotnet.*3\.5` | `dotnet35sp1` | `/q /norestart` |
| `dotnetfx40\|dotnet.*4\.0` | `dotnet40` | `/q /norestart` |
| `ndp45[0-9]?\|dotnet.*4\.5(\.2)?` | `dotnet45` / `dotnet452` | `/q /norestart` |
| `ndp46[0-9]?\|ndp47[0-9]?\|ndp48[0-9]?` | `dotnet46x` / `dotnet472` / `dotnet48` / `dotnet481` | `/q /norestart` |
| `windowsdesktop-runtime-(6\|7\|8\|9\|10)` | `dotnetdesktopN` | `/install /quiet /norestart` |
| `xnafx31` / `xnafx40` | `xna31` / `xna40` | `msiexec /i … /quiet` |
| `physx` | `physx` | `/quiet` or `msiexec /i … /quiet` |
| `oalinst` | _(none in repo — run directly)_ | `/s` |

\* Every 2015–2022 runtime installs the same `vcruntime140` family. The **newest** one found wins, and it counts as satisfied if a newer one is already installed.

### 4.2 Binary-import rules (minimal pure-Python PE import-table parser, no `pefile` needed)

| Imported DLL | Bottles dep |
|---|---|
| `msvcr80/msvcp80` | `vcredist2005` |
| `msvcr90/msvcp90` | `vcredist2008` |
| `msvcr100/msvcp100` | `vcredist2010` |
| `msvcr110/msvcp110` | `vcredist2012` |
| `msvcr120/msvcp120` | `vcredist2013` |
| `vcruntime140*/msvcp140*` | `vcredist2015` (or newer) |
| `d3dx9_*` | `d3dx9` |
| `d3dx10_*`, `d3dx11_*` | `d3dx11` |
| `d3dcompiler_43` / `_47` | `d3dcompiler_43` / `d3dcompiler_47` |
| `xinput1_1..1_3` | `xinput` |
| `xactengine*`, `x3daudio*`, `xapofx*`, `xaudio2_0..7` | `xact` (+ `xact_x64` for 64-bit exes) |
| `mscoree.dll` | .NET (version from the CLR header → `dotnet40`/`dotnet48`) |
| `physxloader`, `physx3*` | `physx` |
| `xlive.dll` | ⚠️ warning only (Games for Windows Live; verify if a GFWL-free patch is included) |

### 4.3 Resolution order: what counts as "missing"

```
required  = filename_rules(_Redist) ∪ import_rules(game exe + DLLs next to it)
satisfied = template.Installed_Dependencies      (Gaming template → d3dx9, d3dcompiler_43/47, fonts, mono, gecko)
          ∪ bottle.yml Installed_Dependencies    (if --reuse-bottle)
          ∪ "newer vcredist2015-family already present"
missing   = required − satisfied
```

### 4.4 How a missing dep gets installed (strategy), best option first

| Tier | Source | Mechanism | Download? |
|---|---|---|---|
| **A** | The game's own `_Redist` installer | `BCLI run -b … -e <file> <silent switch>` + `BCLI reg … DllOverrides` where Bottles' manifest sets overrides (vcredist2005/2008: `atl80/90, msvcm*, msvcp*, msvcr*, vcomp*` → `native,builtin`) | No |
| **B** | Bottles cache `temp/` (filenames from the manifest `rename`, e.g. `vcredist2015_x86.exe`) | same as A | No |
| **C** | URL + MD5 from the Bottles manifest (`proxy.usebottles.com/repo/dependencies/<Category>/<dep>.yml`) | downloaded **into Bottles' own `temp/`** and MD5-checked, so Bottles reuses it later too; then same as A | Yes |
| **D** | Bottles' own dependency manager | only if Bottles reports online: create with `--environment custom` + generated recipe | Bottles decides |

Tier D is off by default. It loses the Gaming template, and right now it silently does nothing because of the ping-522 issue.

---

## 5. Checking existing bottles & cached dependencies (auditor)

| Check | Source | Effect |
|---|---|---|
| Bottle name collision | `BCLI -j list bottles` + `bottles/*/bottle.yml` | abort, unless `--reuse-bottle` (then only missing deps + install) or `--name` |
| Runner choice | `-j list components` + template `Runner` | default = template runner (`soda-9.0-1`), so the template can be cloned. `--runner` overrides; an unknown runner is an error (we never download runners) |
| DLL components | `-j list components` | pass the installed dxvk/vkd3d/nvapi/latencyflex explicitly → no downloads |
| Template validity | `templates/*/template.yml` vs `Samples.environments` (parsed with `ast` from the Flatpak's `backend/models/samples.py`, hard-coded fallback) | if outdated, warn: Bottles will **delete and rebuild** it (slow, downloads) |
| Dependency cache | file listing of `temp/` matched to the manifest's `file_name`/`rename` | each dep labelled `local-redist` / `cached` / `download (N MB)` |
| Other bottles' deps | each `bottle.yml` | info only (deps can't be copied between prefixes, but this shows the cache is warm) |
| Online status | HEAD `ping.usebottles.com` + GET dependency `index.yml` | sets whether Tier C/D are available; prints the 522 warning |
| Disk | `os.statvfs(bottles dir)` | need ≥ estimate (§6), otherwise abort |

---

## 6. Disk-space estimate (FitGirl expansion)

```
compressed   = Σ fg-*.bin (+ fg-optional-*.bin if --with-optional / selected)
installed    = compressed × expansion_factor          (default 1.6, override --expansion or --installed-size)
bottle_base  = size of Gaming template (measured: 1.2 GiB)
redist_dl    = Σ sizes of tier-C downloads
headroom     = 10 % of installed
required     = installed + bottle_base + redist_dl + headroom
```

Example 13.2 GiB repack (+0.4 GiB optional) → installed ≈ 21.1–21.8 GiB → **required ≈ 24.5–25.3 GiB**.

---

## 7. Guardrails

* `--dry-run`: no `new`/`run`/`reg`/`add`, no downloads, no file writes. It prints a plan with: detected deps (with evidence), satisfied vs missing, runner/components, every command fully expanded, the disk check, and warnings.
* Real runs print the same plan and ask for `y/N` confirmation (`--yes` skips the prompt).
* We never edit `bottle.yml` or Bottles' YAML by hand. All changes go through `bottles-cli`.
* Filesystem access for the Flatpak is **per-invocation and read-only** (`flatpak run --filesystem=…:ro`). No persistent `flatpak override`.
* Optional `--verify-bins`: check MD5s against `MD5/fitgirl-bins.md5` before installing (~14 GB read).

## 8. CLI surface (proposed)

```
auto_bottle.py INSTALLER_DIR [--name NAME] [--runner R] [--target 'C:\Games\X']
               [--silent] [--limit-ram-2gb] [--with-optional] [--expansion 1.6 | --installed-size 22G]
               [--reuse-bottle] [--allow-download] [--use-bottles-deps]
               [--verify-bins] [--dry-run] [--yes] [--json]
```

Modules: `scanner.py` (setup.exe, redists, PE imports, size), `auditor.py` (bottles/components/template/cache/online/disk), `builder.py` (command generation + execution), `auto_bottle.py` (CLI and plan printing), `tests/test_scanner.py` (fake `_Redist` trees, PE stubs, command-generation snapshots).

---

## 9. Decisions I need from you before coding

1. **Flatpak file access**: OK to use the one-off `flatpak run --filesystem="<repack dir>":ro` per invocation (recommended)? Or would you rather I print a persistent `flatpak override --user --filesystem=~/Downloads:ro com.usebottles.bottles` for you to run yourself?
2. **2 GB RAM limit toggle**: FitGirl's "Limit installer to 2 GB of RAM" is a GUI checkbox, and **no documented Inno switch** sets it. Options:
   a. *(recommended)* `--limit-ram-2gb` forces interactive mode and reminds you to tick the box. With only 7.6 GiB RAM and ~3 GiB free, I'd turn it on by default for you.
   b. Also allow `--silent --limit-ram-2gb`, enforced with `systemd-run --user --scope -p MemoryMax=2G …`. That's a hard cgroup cap, so the unpacker can get OOM-killed partway through.
3. **Dependency strategy**: Tier A→B→C (run redist installers directly, reuse the cache, download into Bottles' cache only with `--allow-download`) as the default, with Tier D opt-in? This works around the ping-522 issue without touching Bottles internals.
4. **Bottle name / install dir**: derived automatically (e.g. `Example Game` and `C:\Games\Example Game`), with `--name` and `--target` overrides.
5. **Runner**: defaults to `soda-9.0-1` (matching the Gaming template), with `--runner` override.
