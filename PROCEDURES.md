# Linux FitGirl Repack Survival Guide & Procedures

Practical procedures for reliably unpacking and running FitGirl repacks inside **Bottles** on Ubuntu Linux — especially on **8 GiB RAM laptops**, **mechanical HDDs**, and **Intel integrated GPUs**.

---

## 1. Surviving `srep` + `cls-lolz` Decompression on 8 GiB RAM

FitGirl repacks use multi-threaded `srep` + `cls-lolz` decompressors that allocate large virtual memory blocks (often 2 GiB+ contiguous `mmap` chunks). On 8 GiB RAM Linux machines, two things commonly kill the installer mid-way:
1. **`systemd-oomd`** terminating the Flatpak scope under memory pressure.
2. **Exhausted RAM + swap** causing Wine's `err:virtual:allocate_virtual_memory out of memory` followed by a kernel `SIGKILL`.

### Built-in Protection (`auto_bottle.py`)
- By default, `auto_bottle.py` stops `systemd-oomd.socket` and `systemd-oomd.service` immediately before `setup.exe` and restores both in a `try ... finally` block after installation finishes or is cancelled (`Ctrl+C`).
- By default, `auto_bottle.py` launches `setup.exe` in **interactive GUI mode** so you can tick **"Limit installer to 2 GB of RAM"** (FitGirl's custom Inno Setup wizard has no CLI switch for this checkbox).

### Recommended System Prep for Large Repacks (>10 GiB)
1. **Enable compressed ZRAM swap + desktop sysctl tuning** (prevents mechanical HDD swap thrashing):
   ```bash
   sudo ./setup_zram_perf.sh
   ```
   This installs `systemd-zram-generator` (up to 4 GiB `zstd` compressed RAM swap at priority `100`) and writes `/etc/sysctl.d/99-desktop-perf.conf` (`vm.swappiness=15`, `vm.vfs_cache_pressure=50`, `vm.page-cluster=0`).

2. **Add a temporary swapfile during extraction** if you still hit `allocate_virtual_memory` errors:
   ```bash
   sudo fallocate -l 12G /swapfile_fg
   sudo chmod 600 /swapfile_fg
   sudo mkswap /swapfile_fg
   sudo swapon /swapfile_fg
   ```
   Remove it after installation completes:
   ```bash
   sudo swapoff /swapfile_fg && sudo rm /swapfile_fg
   ```

3. **Close heavy browsers/IDEs** before starting `setup.exe`, and ensure `WINE_LARGE_ADDRESS_AWARE=1` is active (enabled by default in modern Soda/Wine runners; can be set explicitly via `bottles-cli edit -b "<Bottle>" --env-var WINE_LARGE_ADDRESS_AWARE=1`).

---

## 2. Bottles Sync (`fsync` vs. `ntsync`) & GameMode

Bottles Gaming templates may default to `sync: ntsync`. On kernels where `/dev/ntsync` is not present (or with Wine/Soda runners predating mainline `ntsync`), Wine silently falls back to slow unaccelerated wineserver synchronization, causing severe frame-time stutter.

Verify whether `/dev/ntsync` exists:
```bash
ls -la /dev/ntsync
```
If it does **not** exist, switch the bottle to `fsync` and enable `gamemode`:
```bash
flatpak run --command=bottles-cli com.usebottles.bottles edit \
  -b "<Bottle Name>" \
  --params sync:fsync,gamemode:true
```

---

## 3. Offline Dependency Fallback (`ping.usebottles.com`)

Bottles checks `https://ping.usebottles.com` on startup. When that endpoint is unreachable or times out, `bottles-cli` enters offline mode and skips its internal dependency catalog.

`auto_bottle.py` avoids this failure mode with a 3-tier strategy:
1. **Post-extraction `_Redist` scan:** FitGirl packs `_Redist` inside `fg-*.bin`, so `auto_bottle.py` re-scans `drive_c/Games/<Name>` after `setup.exe` finishes and runs bundled redistributables directly inside the bottle.
2. **Bottles `temp/` cache reuse:** Matches required runtimes against already-downloaded files in `~/.var/app/com.usebottles.bottles/data/bottles/temp/` (e.g., `vcredist2015_x86.exe`, `dotnet452.exe`) without re-downloading.
3. **Optional `--allow-download`:** Fetches missing installers directly from Microsoft/Bottles manifest URLs into Bottles' `temp/` cache with MD5 verification, then applies the same Wine `DllOverrides` registry keys Bottles would set.

---

## 4. DirectX 9 / DXVK & Intel iGPU Tuning

### Fixing Intel UHD 620 (Gen9 / Kaby Lake) Diagonal RGB Artifacts
On Intel UHD Graphics 620 with Mesa ANV + DXVK 2.x/3.x, certain DirectX 9 titles exhibit a blinking diagonal RGB streak across the screen caused by Intel Color Compression (CCS). Fix it per-bottle without disabling DXVK:
```bash
flatpak run --command=bottles-cli com.usebottles.bottles edit \
  -b "<Bottle Name>" \
  --env-var INTEL_DEBUG=noccs
```

### Recommended `dxvk.conf` for DirectX 9 Titles on Integrated GPUs
Place in the game's installation folder alongside the main `.exe`:
```ini
dxgi.maxFrameLatency = 1
d3d9.maxFrameLatency = 1
d3d9.maxFrameRate = 40
```
- Avoid `d3d9.floatEmulation = Strict` unless needed for a specific shader bug, as it adds ALU overhead on low-power iGPUs.
- Capping `d3d9.maxFrameRate` to `30`–`40` stabilizes frame pacing and prevents 15W mobile CPUs from power/thermal throttling.

### Recommended `commandline.txt` (DirectX 9 / VRAM-Limited Games)
```text
-norestrictions
-nomemrestrict
-availablevidmem 1536
-percentvidmem 100
-fullscreen
```
- **Do not use `-noprecache` on a 5400 RPM HDD** — it forces on-demand asset streaming off the mechanical disk and causes driving/camera hitches.
- Cap `-availablevidmem` to `1536`–`2048` MB on 8 GiB shared-memory laptops so the game does not starve the OS.

---

## 5. Standard End-to-End Workflow for a New Repack

```bash
# 1. Inspect plan & disk space first
~/auto_bottle/auto_bottle.py "~/Downloads/My Game [FitGirl Repack]" --dry-run

# 2. Create bottle, pause systemd-oomd, launch setup.exe (tick 2 GB RAM limit),
#    resume systemd-oomd, scan post-extraction deps, and register game .exe
~/auto_bottle/auto_bottle.py "~/Downloads/My Game [FitGirl Repack]"

# 3. Switch to fsync + gamemode if /dev/ntsync is absent
flatpak run --command=bottles-cli com.usebottles.bottles edit \
  -b "My Game" \
  --params sync:fsync,gamemode:true
```
