"""Generate and execute bottles-cli plans for FitGirl game bottles."""

from __future__ import annotations

import hashlib
import shlex
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from auditor import (
    AuditReport,
    DependencyAction,
    bottle_dir_for_name,
    resolve_dependencies,
    select_runner,
)
from scanner import (
    ScanResult,
    find_game_executables,
    format_bytes,
    scan_folder,
)


INNO_SILENT_FLAGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
DLL_OVERRIDES_REG_KEY = r"HKEY_CURRENT_USER\Software\Wine\DllOverrides"
OOMD_UNITS = ["systemd-oomd.socket", "systemd-oomd.service"]


@dataclass
class PlannedCommand:
    """A single bottles-cli command in the execution plan."""

    stage: str
    description: str
    argv: list[str]

    def shell_str(self) -> str:
        return " ".join(shlex.quote(arg) for arg in self.argv)


@dataclass
class ExecutionPlan:
    """Complete, inspectable plan produced before execution or in --dry-run mode."""

    bottle_name: str
    bottle_dir: Path
    target_win_dir: str
    target_unix_dir: Path
    runner: str
    dxvk: Optional[str]
    vkd3d: Optional[str]
    nvapi: Optional[str]
    latencyflex: Optional[str]
    interactive_mode: bool
    limit_ram_2gb: bool
    pause_oomd: bool
    reuse_bottle: bool
    allow_download: bool
    bottle_already_exists: bool
    disk_ok: bool
    required_bytes: int
    free_bytes: int
    dependencies: list[DependencyAction]
    commands: list[PlannedCommand]
    warnings: list[str] = field(default_factory=list)


def windows_target_to_unix(bottle_dir: Path, target_win_dir: str) -> Path:
    """Convert a Windows install path like 'C:\\Games\\Example Game' to a drive_c path."""
    cleaned = target_win_dir.strip().strip('"').strip("'")
    if len(cleaned) >= 2 and cleaned[1] == ":" and cleaned[0].lower() == "c":
        cleaned = cleaned[2:]
    cleaned = cleaned.replace("\\", "/").lstrip("/")
    return bottle_dir / "drive_c" / cleaned


def _prefix_with_ro_filesystem(
    cli_prefix: list[str],
    install_type: str,
    ro_dir: Optional[Path],
) -> list[str]:
    """Attach one-off read-only Flatpak filesystem access when needed."""
    if install_type == "flatpak" and ro_dir is not None and len(cli_prefix) >= 2:
        if cli_prefix[0] == "flatpak" and cli_prefix[1] == "run":
            return [
                cli_prefix[0],
                cli_prefix[1],
                f"--filesystem={ro_dir}:ro",
                *cli_prefix[2:],
            ]
    return list(cli_prefix)


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def build_new_bottle_cmd(
    report: AuditReport,
    bottle_name: str,
    runner: str,
    dxvk: Optional[str],
    vkd3d: Optional[str],
    nvapi: Optional[str],
    latencyflex: Optional[str],
    arch: str = "win64",
) -> list[str]:
    """Build exact `bottles-cli new` argv reusing installed components."""
    cmd = [
        *report.cli_prefix,
        "new",
        "--bottle-name",
        bottle_name,
        "--environment",
        "gaming",
        "--arch",
        arch,
        "--runner",
        runner,
    ]
    if dxvk:
        cmd.extend(["--dxvk", dxvk])
    if vkd3d:
        cmd.extend(["--vkd3d", vkd3d])
    if nvapi:
        cmd.extend(["--nvapi", nvapi])
    if latencyflex:
        cmd.extend(["--latencyflex", latencyflex])
    return cmd


def build_run_installer_cmd(
    report: AuditReport,
    bottle_name: str,
    setup_exe: Path,
    repack_dir: Path,
    target_win_dir: str,
    silent: bool = False,
) -> list[str]:
    """Build exact `bottles-cli run` argv for setup.exe with one-off :ro mount."""
    prefix = _prefix_with_ro_filesystem(report.cli_prefix, report.install_type, repack_dir)
    cmd = [*prefix, "run", "-b", bottle_name, "-e", str(setup_exe)]
    if silent:
        cmd.extend([*INNO_SILENT_FLAGS, f"/DIR={target_win_dir}"])
    return cmd


def build_dep_commands(
    report: AuditReport,
    bottle_name: str,
    actions: list[DependencyAction],
    external_ro_root: Optional[Path] = None,
) -> list[PlannedCommand]:
    """Build exact `bottles-cli run` and `bottles-cli reg` commands for missing deps."""
    planned: list[PlannedCommand] = []
    seen_installers: set[Path] = set()
    seen_overrides: set[str] = set()

    for action in actions:
        if action.status == "satisfied" or action.status == "unresolved":
            continue

        if action.status in ("local-redist", "cached"):
            for installer_path, args in action.installers:
                resolved = installer_path.resolve()
                if resolved in seen_installers:
                    continue
                seen_installers.add(resolved)

                ro_dir: Optional[Path] = None
                if not _is_inside(installer_path, report.data_root):
                    ro_dir = external_ro_root or installer_path.parent
                prefix = _prefix_with_ro_filesystem(
                    report.cli_prefix, report.install_type, ro_dir
                )
                planned.append(
                    PlannedCommand(
                        stage="dependencies",
                        description=f"Install {action.name} via {installer_path.name} ({action.status})",
                        argv=[*prefix, "run", "-b", bottle_name, "-e", str(installer_path), *args],
                    )
                )

        elif action.status == "download":
            for entry in action.download_entries:
                dest_path = report.temp_dir / entry["name"]
                args = list(entry.get("args") or [])
                planned.append(
                    PlannedCommand(
                        stage="dependencies",
                        description=f"Install {action.name} after downloading {entry['name']} to temp/",
                        argv=[*report.cli_prefix, "run", "-b", bottle_name, "-e", str(dest_path), *args],
                    )
                )

        for dll_name, override_val in sorted(action.dll_overrides.items()):
            if dll_name in seen_overrides:
                continue
            seen_overrides.add(dll_name)
            planned.append(
                PlannedCommand(
                    stage="dll-overrides",
                    description=f"Set Wine DLL override {dll_name}={override_val} (for {action.name})",
                    argv=[
                        *report.cli_prefix,
                        "reg",
                        "-b",
                        bottle_name,
                        "-k",
                        DLL_OVERRIDES_REG_KEY,
                        "-v",
                        dll_name,
                        "-d",
                        override_val,
                        "-t",
                        "REG_SZ",
                        "add",
                    ],
                )
            )

    return planned


def build_add_program_cmd(
    report: AuditReport,
    bottle_name: str,
    program_name: str,
    exe_path: Path,
) -> list[str]:
    """Build exact `bottles-cli add` argv to register the game executable."""
    return [
        *report.cli_prefix,
        "add",
        "-b",
        bottle_name,
        "-n",
        program_name,
        "-p",
        str(exe_path),
    ]


def build_verify_programs_cmd(report: AuditReport, bottle_name: str) -> list[str]:
    """Build exact `bottles-cli -j programs` verification command."""
    return [*report.cli_prefix, "-j", "programs", "-b", bottle_name]


def build_stop_oomd_cmd() -> list[str]:
    """Build command to temporarily stop systemd-oomd socket & service during extraction."""
    return ["sudo", "systemctl", "stop", *OOMD_UNITS]


def build_start_oomd_cmd() -> list[str]:
    """Build command to re-enable/start systemd-oomd socket & service after extraction."""
    return ["sudo", "systemctl", "start", *OOMD_UNITS]


def create_execution_plan(
    scan: ScanResult,
    report: AuditReport,
    bottle_name: Optional[str] = None,
    requested_runner: Optional[str] = None,
    target_win_dir: Optional[str] = None,
    silent: bool = False,
    limit_ram_2gb: bool = True,
    pause_oomd: bool = True,
    reuse_bottle: bool = False,
    allow_download: bool = False,
    post_install_only: bool = False,
    game_exe_override: Optional[Path] = None,
) -> ExecutionPlan:
    """Assemble the complete ExecutionPlan without modifying any files."""
    name = bottle_name or scan.game_name
    win_dir = target_win_dir or f"C:\\Games\\{name}"
    bdir = bottle_dir_for_name(report, name)
    unix_install_dir = windows_target_to_unix(bdir, win_dir)
    runner = select_runner(report, requested_runner)

    dxvk = report.dxvk[0] if report.dxvk else None
    vkd3d = report.vkd3d[0] if report.vkd3d else None
    nvapi = report.nvapi[0] if report.nvapi else None
    latencyflex = report.latencyflex[0] if report.latencyflex else None

    warnings = list(report.warnings) + list(scan.warnings)
    bottle_exists = name in report.existing_bottles

    if bottle_exists and not reuse_bottle and not post_install_only:
        warnings.append(
            f"Bottle {name!r} already exists at {bdir}. "
            "Pass --reuse-bottle to install into it, or --name to choose a different name."
        )

    # Interactive mode is the default so the user can tick FitGirl's 2 GB RAM limit box.
    interactive_mode = not silent
    if silent and limit_ram_2gb:
        warnings.append(
            "FitGirl's 'Limit installer to 2 GB of RAM' checkbox has no Inno Setup CLI switch; "
            "--silent bypasses the GUI wizard and runs without the 2 GB cap."
        )

    dep_actions = resolve_dependencies(
        required_deps=scan.inferred_deps,
        redist_matches=scan.redist_matches,
        report=report,
        bottle_name=name,
        reuse_bottle=reuse_bottle or post_install_only,
        allow_download=allow_download,
    )

    # Include any Tier-C download sizes in required disk space.
    download_bytes = sum(
        int(entry.get("size") or 0)
        for act in dep_actions
        if act.status == "download"
        for entry in act.download_entries
    )
    required_bytes = scan.disk_estimate.required_bytes + download_bytes
    disk_ok = report.free_disk_bytes == 0 or report.free_disk_bytes >= required_bytes
    if not disk_ok:
        warnings.append(
            f"Insufficient free disk space: need {format_bytes(required_bytes)}, "
            f"only {format_bytes(report.free_disk_bytes)} available."
        )

    commands: list[PlannedCommand] = []

    if not post_install_only and not (bottle_exists and reuse_bottle):
        commands.append(
            PlannedCommand(
                stage="create-bottle",
                description=f"Create Gaming bottle '{name}' using {runner}",
                argv=build_new_bottle_cmd(
                    report=report,
                    bottle_name=name,
                    runner=runner,
                    dxvk=dxvk,
                    vkd3d=vkd3d,
                    nvapi=nvapi,
                    latencyflex=latencyflex,
                ),
            )
        )

    if not post_install_only:
        if scan.setup_exe is None:
            warnings.append(
                f"No setup.exe found in {scan.source_dir}; cannot build installer run command."
            )
        else:
            if pause_oomd:
                commands.append(
                    PlannedCommand(
                        stage="pause-oomd",
                        description="Temporarily stop systemd-oomd socket & service before extraction",
                        argv=build_stop_oomd_cmd(),
                    )
                )
            mode_desc = "silent Inno Setup" if silent else "interactive GUI (tick 2 GB RAM limit)"
            commands.append(
                PlannedCommand(
                    stage="run-installer",
                    description=f"Launch {scan.setup_exe.name} ({mode_desc})",
                    argv=build_run_installer_cmd(
                        report=report,
                        bottle_name=name,
                        setup_exe=scan.setup_exe,
                        repack_dir=scan.source_dir,
                        target_win_dir=win_dir,
                        silent=silent,
                    ),
                )
            )
            if pause_oomd:
                commands.append(
                    PlannedCommand(
                        stage="resume-oomd",
                        description="Restart systemd-oomd socket & service after extraction",
                        argv=build_start_oomd_cmd(),
                    )
                )

    commands.extend(
        build_dep_commands(
            report=report,
            bottle_name=name,
            actions=dep_actions,
            external_ro_root=scan.source_dir,
        )
    )

    placeholder_exe = game_exe_override or (
        unix_install_dir / f"{name.replace(' ', '')}.exe"
    )
    exe_note = (
        str(placeholder_exe)
        if game_exe_override
        else f"<auto-detected under {unix_install_dir}>"
    )
    commands.append(
        PlannedCommand(
            stage="register-program",
            description=f"Register game executable ({exe_note}) in Bottles",
            argv=build_add_program_cmd(report, name, name, placeholder_exe),
        )
    )
    commands.append(
        PlannedCommand(
            stage="verify",
            description=f"Verify '{name}' appears in Bottles program list",
            argv=build_verify_programs_cmd(report, name),
        )
    )

    return ExecutionPlan(
        bottle_name=name,
        bottle_dir=bdir,
        target_win_dir=win_dir,
        target_unix_dir=unix_install_dir,
        runner=runner,
        dxvk=dxvk,
        vkd3d=vkd3d,
        nvapi=nvapi,
        latencyflex=latencyflex,
        interactive_mode=interactive_mode,
        limit_ram_2gb=limit_ram_2gb,
        pause_oomd=pause_oomd,
        reuse_bottle=reuse_bottle,
        allow_download=allow_download,
        bottle_already_exists=bottle_exists,
        disk_ok=disk_ok,
        required_bytes=required_bytes,
        free_bytes=report.free_disk_bytes,
        dependencies=dep_actions,
        commands=commands,
        warnings=warnings,
    )


def _download_to_cache(entry: dict[str, Any], temp_dir: Path) -> Path:
    """Download a dependency installer into Bottles' temp/ cache and verify MD5."""
    temp_dir.mkdir(parents=True, exist_ok=True)
    dest = temp_dir / entry["name"]
    expected_md5 = (entry.get("md5") or "").lower()

    if dest.is_file() and expected_md5:
        if hashlib.md5(dest.read_bytes()).hexdigest().lower() == expected_md5:
            return dest

    req = urllib.request.Request(
        entry["url"], headers={"User-Agent": "curl/8.5.0"}
    )
    tmp_path = dest.with_suffix(dest.suffix + ".part")
    h = hashlib.md5()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, tmp_path.open("wb") as out:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
        if expected_md5 and h.hexdigest().lower() != expected_md5:
            raise RuntimeError(
                f"MD5 mismatch for {entry['name']}: got {h.hexdigest()}, expected {expected_md5}"
            )
        tmp_path.replace(dest)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    return dest


def _run_argv(argv: list[str], runner_fn: Optional[Callable[[list[str]], int]] = None) -> int:
    if runner_fn is not None:
        return runner_fn(argv)
    proc = subprocess.run(argv, check=False)
    return proc.returncode


def execute_plan(
    plan: ExecutionPlan,
    report: AuditReport,
    post_install_only: bool = False,
    game_exe_override: Optional[Path] = None,
    dry_run: bool = False,
    runner_fn: Optional[Callable[[list[str]], int]] = None,
    log_fn: Callable[[str], None] = print,
) -> bool:
    """Execute the planned steps live. Refuses to mutate anything if dry_run=True."""
    if dry_run:
        raise RuntimeError("execute_plan() must never be called with dry_run=True")

    if not plan.disk_ok:
        log_fn("ERROR: Aborting due to insufficient free disk space.")
        return False

    if plan.bottle_already_exists and not plan.reuse_bottle and not post_install_only:
        log_fn(
            f"ERROR: Bottle {plan.bottle_name!r} already exists. "
            "Use --reuse-bottle or --name <new-name>."
        )
        return False

    # 1. Create bottle
    for cmd in plan.commands:
        if cmd.stage == "create-bottle":
            log_fn(f"\n[1/5] {cmd.description}")
            log_fn(f"  $ {cmd.shell_str()}")
            rc = _run_argv(cmd.argv, runner_fn)
            if rc != 0:
                log_fn(f"ERROR: bottles-cli new exited with code {rc}")
                return False
            bottle_yml = plan.bottle_dir / "bottle.yml"
            if runner_fn is None and not bottle_yml.is_file():
                log_fn(f"ERROR: Expected {bottle_yml} was not created.")
                return False

    # 2. Launch setup.exe (pausing systemd-oomd around extraction if enabled)
    pause_cmd = next((c for c in plan.commands if c.stage == "pause-oomd"), None)
    resume_cmd = next((c for c in plan.commands if c.stage == "resume-oomd"), None)
    installer_cmds = [c for c in plan.commands if c.stage == "run-installer"]

    if installer_cmds:
        oomd_paused = False
        if pause_cmd is not None:
            log_fn(f"\n[2a/5] {pause_cmd.description}")
            log_fn(f"  $ {pause_cmd.shell_str()}")
            rc = _run_argv(pause_cmd.argv, runner_fn)
            if rc == 0:
                oomd_paused = True
            else:
                log_fn(f"  WARNING: Failed to stop systemd-oomd (exit {rc}); continuing anyway.")
        try:
            for cmd in installer_cmds:
                log_fn(f"\n[2/5] {cmd.description}")
                if plan.interactive_mode:
                    log_fn("  >>> REMINDER: In the FitGirl installer window:")
                    log_fn("      1. Tick 'Limit installer to 2 GB of RAM'")
                    log_fn(f"      2. Set the install destination to: {plan.target_win_dir}")
                log_fn(f"  $ {cmd.shell_str()}")
                rc = _run_argv(cmd.argv, runner_fn)
                if rc != 0:
                    log_fn(
                        f"WARNING: setup.exe exited with code {rc}; continuing to post-install scan."
                    )
        finally:
            if oomd_paused and resume_cmd is not None:
                log_fn(f"\n[2b/5] {resume_cmd.description}")
                log_fn(f"  $ {resume_cmd.shell_str()}")
                rc = _run_argv(resume_cmd.argv, runner_fn)
                if rc != 0:
                    log_fn(
                        f"  WARNING: Failed to restart systemd-oomd (exit {rc}). "
                        f"Run manually: {resume_cmd.shell_str()}"
                    )

    # 3. Post-extraction scan of drive_c/Games/<Name>
    log_fn(f"\n[3/5] Scanning extracted game directory: {plan.target_unix_dir}")
    post_actions: list[DependencyAction] = []
    if plan.target_unix_dir.is_dir():
        post_scan = scan_folder(
            plan.target_unix_dir,
            game_name=plan.bottle_name,
            bottle_base_bytes=report.gaming_template_bytes,
            scan_pe_binaries=True,
        )
        post_actions = resolve_dependencies(
            required_deps=post_scan.inferred_deps,
            redist_matches=post_scan.redist_matches,
            report=report,
            bottle_name=plan.bottle_name,
            reuse_bottle=True,
            allow_download=plan.allow_download,
        )
    else:
        log_fn(
            f"  Note: {plan.target_unix_dir} does not exist yet "
            "(if you installed to a different folder, re-run with --post-install-only --target ...)."
        )

    # Combine pre-install and post-install dependency actions (dedup by name)
    combined_actions: dict[str, DependencyAction] = {a.name: a for a in plan.dependencies}
    for a in post_actions:
        if a.name not in combined_actions or combined_actions[a.name].status in ("unresolved", "download"):
            combined_actions[a.name] = a

    # Download Tier-C deps if --allow-download was passed
    for act in combined_actions.values():
        if act.status == "download" and plan.allow_download:
            for entry in act.download_entries:
                log_fn(f"  Downloading {entry['name']} into {report.temp_dir} ...")
                _download_to_cache(entry, report.temp_dir)

    dep_cmds = build_dep_commands(
        report=report,
        bottle_name=plan.bottle_name,
        actions=list(combined_actions.values()),
        external_ro_root=plan.target_unix_dir,
    )

    # 4. Install missing dependencies & apply DLL overrides
    log_fn(f"\n[4/5] Installing missing dependencies ({len(dep_cmds)} command(s))")
    for cmd in dep_cmds:
        log_fn(f"  - {cmd.description}")
        log_fn(f"    $ {cmd.shell_str()}")
        rc = _run_argv(cmd.argv, runner_fn)
        if rc != 0:
            log_fn(f"  WARNING: command exited with code {rc}")

    # 5. Locate and register main game executable
    log_fn("\n[5/5] Registering game executable in Bottles")
    chosen_exe = game_exe_override
    if chosen_exe is None and plan.target_unix_dir.is_dir():
        candidates = find_game_executables(plan.target_unix_dir, plan.bottle_name)
        if candidates:
            chosen_exe = candidates[0]
            if len(candidates) > 1:
                log_fn(
                    "  Other candidate executables found: "
                    + ", ".join(c.name for c in candidates[1:4])
                )

    if chosen_exe is None:
        log_fn(
            f"  WARNING: Could not auto-detect game .exe under {plan.target_unix_dir}. "
            "Register manually or re-run with --post-install-only --game-exe <path>."
        )
        return True

    add_argv = build_add_program_cmd(report, plan.bottle_name, plan.bottle_name, chosen_exe)
    log_fn(f"  $ {' '.join(shlex.quote(a) for a in add_argv)}")
    rc = _run_argv(add_argv, runner_fn)
    if rc != 0:
        log_fn(f"ERROR: bottles-cli add exited with code {rc}")
        return False

    verify_argv = build_verify_programs_cmd(report, plan.bottle_name)
    log_fn(f"  Verifying: {' '.join(shlex.quote(a) for a in verify_argv)}")
    _run_argv(verify_argv, runner_fn)
    return True
