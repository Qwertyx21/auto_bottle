#!/usr/bin/env python3
"""auto_bottle.py — Automate creating a Bottles gaming prefix for a FitGirl repack."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from auditor import AuditReport, BottlesAuditor
from builder import ExecutionPlan, create_execution_plan, execute_plan
from scanner import (
    ScanResult,
    format_bytes,
    parse_size_string,
    scan_folder,
    verify_md5_bins,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto_bottle.py",
        description=(
            "Automate setting up a Bottles Gaming bottle for a FitGirl game installer folder. "
            "Defaults to interactive GUI mode so you can tick the 2 GB RAM limit checkbox."
        ),
    )
    parser.add_argument(
        "installer_dir",
        type=Path,
        help="Path to the FitGirl installer folder (containing setup.exe and fg-*.bin).",
    )
    parser.add_argument(
        "--name",
        dest="name",
        default=None,
        help="Override the derived bottle & game name (default: derived from folder name).",
    )
    parser.add_argument(
        "--runner",
        dest="runner",
        default=None,
        help="Override the Wine runner (default: soda-9.0-1).",
    )
    parser.add_argument(
        "--target",
        dest="target",
        default=None,
        help=r"Override the Windows install path inside the bottle (default: C:\Games\<Name>).",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Pass Inno Setup silent flags (/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=...).",
    )
    parser.add_argument(
        "--limit-ram-2gb",
        action="store_true",
        default=True,
        help="Keep interactive mode with a 2 GB RAM checkbox reminder (enabled by default).",
    )
    parser.add_argument(
        "--keep-oomd",
        action="store_true",
        help="Do not stop/restart systemd-oomd.socket & systemd-oomd.service during extraction.",
    )
    parser.add_argument(
        "--with-optional",
        action="store_true",
        help="Include fg-optional-*.bin files in the disk space estimate.",
    )
    parser.add_argument(
        "--expansion",
        type=float,
        default=1.6,
        help="FitGirl repack expansion multiplier for disk estimation (default: 1.6).",
    )
    parser.add_argument(
        "--installed-size",
        default=None,
        help="Explicit installed game size override (e.g. '22G') instead of --expansion.",
    )
    parser.add_argument(
        "--reuse-bottle",
        action="store_true",
        help="Reuse an existing bottle of the same name instead of aborting.",
    )
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help="Allow downloading missing dependencies into Bottles' temp/ cache.",
    )
    parser.add_argument(
        "--verify-bins",
        action="store_true",
        help="Verify MD5/fitgirl-bins.md5 checksums before installing.",
    )
    parser.add_argument(
        "--post-install-only",
        action="store_true",
        help="Skip bottle creation & setup.exe; scan extracted game dir, install deps, and register exe.",
    )
    parser.add_argument(
        "--game-exe",
        type=Path,
        default=None,
        help="Explicit path to the installed game .exe to register in Bottles.",
    )
    parser.add_argument(
        "--check-online",
        action="store_true",
        help="Probe ping.usebottles.com during audit to check Bottles online status.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Output the full execution plan without modifying any files or creating a bottle.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip interactive confirmation prompt in live mode.",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the execution plan as JSON.",
    )
    return parser


def plan_to_dict(scan: ScanResult, report: AuditReport, plan: ExecutionPlan) -> dict:
    return {
        "bottle_name": plan.bottle_name,
        "bottle_dir": str(plan.bottle_dir),
        "target_win_dir": plan.target_win_dir,
        "target_unix_dir": str(plan.target_unix_dir),
        "install_type": report.install_type,
        "runner": plan.runner,
        "components": {
            "dxvk": plan.dxvk,
            "vkd3d": plan.vkd3d,
            "nvapi": plan.nvapi,
            "latencyflex": plan.latencyflex,
        },
        "installer": {
            "source_dir": str(scan.source_dir),
            "setup_exe": str(scan.setup_exe) if scan.setup_exe else None,
            "is_inno_setup": scan.is_inno_setup,
            "interactive_mode": plan.interactive_mode,
            "pause_oomd": plan.pause_oomd,
        },
        "disk": {
            "compressed_bytes": scan.disk_estimate.compressed_bytes,
            "optional_bytes": scan.disk_estimate.optional_bytes,
            "expansion_factor": scan.disk_estimate.expansion_factor,
            "installed_bytes": scan.disk_estimate.installed_bytes,
            "bottle_base_bytes": scan.disk_estimate.bottle_base_bytes,
            "headroom_bytes": scan.disk_estimate.headroom_bytes,
            "required_bytes": plan.required_bytes,
            "free_bytes": plan.free_bytes,
            "ok": plan.disk_ok,
        },
        "dependencies": [
            {
                "name": d.name,
                "status": d.status,
                "reason": d.reason,
                "evidence": scan.dep_evidence.get(d.name, []),
                "dll_overrides": d.dll_overrides,
            }
            for d in plan.dependencies
        ],
        "commands": [
            {
                "stage": c.stage,
                "description": c.description,
                "argv": c.argv,
                "shell": c.shell_str(),
            }
            for c in plan.commands
        ],
        "warnings": plan.warnings,
    }


def render_plan(scan: ScanResult, report: AuditReport, plan: ExecutionPlan, dry_run: bool) -> str:
    lines: list[str] = []
    header = "AUTO-BOTTLE EXECUTION PLAN (DRY RUN — NO CHANGES WILL BE MADE)" if dry_run else "AUTO-BOTTLE EXECUTION PLAN"
    lines.append("=" * 74)
    lines.append(header)
    lines.append("=" * 74)

    lines.append("\n[Bottles Environment]")
    lines.append(f"  Install type   : {report.install_type} ({' '.join(report.cli_prefix)})")
    lines.append(f"  Data root      : {report.data_root}")
    lines.append(f"  Existing       : {', '.join(sorted(report.existing_bottles)) or '(none)'}")
    lines.append(f"  Selected runner: {plan.runner} (available: {', '.join(report.runners) or 'none'})")
    lines.append(
        f"  Components     : DXVK={plan.dxvk or 'auto'}, VKD3D={plan.vkd3d or 'auto'}, "
        f"NVAPI={plan.nvapi or 'auto'}, LatencyFleX={plan.latencyflex or 'auto'}"
    )
    lines.append(
        f"  Cached deps    : {', '.join(sorted(report.cached_deps)) or '(none)'}"
    )

    lines.append("\n[Target Game & Bottle]")
    lines.append(f"  Source folder  : {scan.source_dir}")
    lines.append(
        f"  Installer      : {scan.setup_exe.name if scan.setup_exe else 'NOT FOUND'}"
        + (" (Inno Setup)" if scan.is_inno_setup else "")
    )
    lines.append(f"  Bottle name    : {plan.bottle_name}")
    lines.append(f"  Bottle dir     : {plan.bottle_dir}")
    lines.append(f"  Install target : {plan.target_win_dir}")
    lines.append(f"  Linux target   : {plan.target_unix_dir}")
    mode_label = (
        "Interactive GUI (tick 'Limit installer to 2 GB of RAM')"
        if plan.interactive_mode
        else "Silent Inno Setup (--silent)"
    )
    lines.append(f"  Installer mode : {mode_label}")
    oomd_label = (
        "Stop systemd-oomd socket & service before setup.exe, restart after (guaranteed via try/finally)"
        if plan.pause_oomd
        else "Keep systemd-oomd running (--keep-oomd)"
    )
    lines.append(f"  OOMD handling  : {oomd_label}")

    est = scan.disk_estimate
    lines.append("\n[Disk Space Check]")
    lines.append(
        f"  Repack bins    : {format_bytes(est.compressed_bytes)} ({len(est.bin_files)} file(s))"
        + (
            f" + {format_bytes(est.optional_bytes)} optional ({len(est.optional_bin_files)} file(s))"
            if est.optional_bytes
            else ""
        )
    )
    lines.append(
        f"  Est. installed : {format_bytes(est.installed_bytes)} "
        f"(x{est.expansion_factor:.2f}) + {format_bytes(est.bottle_base_bytes)} bottle template "
        f"+ {format_bytes(est.headroom_bytes)} headroom"
    )
    status_tag = "OK" if plan.disk_ok else "INSUFFICIENT"
    lines.append(
        f"  Required / Free: {format_bytes(plan.required_bytes)} / {format_bytes(plan.free_bytes)} [{status_tag}]"
    )

    lines.append("\n[Detected Dependencies]")
    if plan.dependencies:
        for dep in plan.dependencies:
            ev = ", ".join(scan.dep_evidence.get(dep.name, []))
            ev_suffix = f"  [{ev}]" if ev else ""
            lines.append(f"  - {dep.name:<16} {dep.status:<14} {dep.reason}{ev_suffix}")
    else:
        lines.append(
            "  (none detected pre-extraction; _Redist & PE imports will be scanned after setup.exe finishes)"
        )

    lines.append("\n[Exact bottles-cli Commands]")
    for idx, cmd in enumerate(plan.commands, start=1):
        lines.append(f"  {idx}. [{cmd.stage}] {cmd.description}")
        lines.append(f"     $ {cmd.shell_str()}")

    if plan.warnings:
        lines.append("\n[Warnings / Notes]")
        for w in plan.warnings:
            lines.append(f"  ! {w}")

    lines.append("=" * 74)
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    installer_dir = args.installer_dir.expanduser().resolve()
    if not installer_dir.is_dir():
        print(f"ERROR: Installer directory does not exist: {installer_dir}", file=sys.stderr)
        return 2

    installed_override: Optional[int] = None
    if args.installed_size:
        try:
            installed_override = parse_size_string(args.installed_size)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    auditor = BottlesAuditor(
        query_cli=True,
        check_online=args.check_online,
    )
    report = auditor.audit()

    scan = scan_folder(
        folder_path=installer_dir,
        game_name=args.name,
        expansion_factor=args.expansion,
        with_optional=args.with_optional,
        bottle_base_bytes=report.gaming_template_bytes,
        installed_size_override=installed_override,
    )

    try:
        plan = create_execution_plan(
            scan=scan,
            report=report,
            bottle_name=args.name,
            requested_runner=args.runner,
            target_win_dir=args.target,
            silent=args.silent,
            limit_ram_2gb=args.limit_ram_2gb,
            pause_oomd=not args.keep_oomd,
            reuse_bottle=args.reuse_bottle,
            allow_download=args.allow_download,
            post_install_only=args.post_install_only,
            game_exe_override=args.game_exe,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(plan_to_dict(scan, report, plan), indent=2))
    else:
        print(render_plan(scan, report, plan, dry_run=args.dry_run))

    if args.dry_run:
        return 0 if plan.disk_ok else 1

    if args.verify_bins:
        print("\nVerifying FitGirl bin MD5 checksums...")
        results = verify_md5_bins(installer_dir)
        if not results:
            print("  No MD5/fitgirl-bins.md5 file found.")
        else:
            bad = [r for r in results if not r[1] and "missing" not in r[2]]
            for fname, ok, msg in results:
                tag = "OK" if ok else "FAIL"
                print(f"  [{tag}] {fname}: {msg}")
            if bad:
                print("ERROR: MD5 verification failed for one or more bin files.", file=sys.stderr)
                return 1

    if not args.yes:
        try:
            answer = input("\nProceed with execution? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Aborted by user.")
            return 0

    ok = execute_plan(
        plan=plan,
        report=report,
        post_install_only=args.post_install_only,
        game_exe_override=args.game_exe,
        dry_run=False,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
