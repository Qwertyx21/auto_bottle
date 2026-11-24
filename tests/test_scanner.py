"""Mock test suite for scanner.py, auditor.py, builder.py, and auto_bottle.py."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest
import yaml

# Allow importing project modules directly from the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auto_bottle
from auditor import AuditReport, BottlesAuditor, resolve_dependencies, select_runner
from builder import create_execution_plan, execute_plan, windows_target_to_unix
from scanner import (
    calculate_disk_space,
    derive_game_name,
    find_game_executables,
    map_redist_file,
    parse_pe_imports,
    scan_folder,
)


def _build_minimal_pe32(dll_names: list[str]) -> bytes:
    """Build a tiny, valid PE32 binary with an import table listing dll_names."""
    # Layout:
    # 0x000..0x07F : DOS header (e_lfanew = 0x80)
    # 0x080..0x083 : PE\0\0
    # 0x084..0x097 : COFF header (20 bytes, 1 section, opt header size = 224)
    # 0x098..0x177 : Optional header PE32 (224 bytes; DataDirectory[1] at +104)
    # 0x178..0x19F : 1 Section header (.idata, VAddr=0x2000, RawPtr=0x200, Size=0x400)
    # 0x200..0x5FF : .idata raw bytes (Import Descriptors + null-terminated DLL strings)
    buf = bytearray(0x600)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, 0x80)

    pe_off = 0x80
    buf[pe_off : pe_off + 4] = b"PE\x00\x00"
    # Machine=0x14c (i386), NumberOfSections=1, SizeOfOptionalHeader=224
    struct.pack_into("<HHIIIHH", buf, pe_off + 4, 0x014C, 1, 0, 0, 0, 224, 0x0102)

    opt_off = pe_off + 24
    struct.pack_into("<H", buf, opt_off, 0x10B)  # PE32 magic
    # DataDirectory[1] (Import Table): RVA=0x2000, Size=0x100
    struct.pack_into("<II", buf, opt_off + 96 + 8, 0x2000, 0x100)

    sec_off = opt_off + 224
    buf[sec_off : sec_off + 8] = b".idata\x00\x00"
    # VirtualSize, VirtualAddress, SizeOfRawData, PointerToRawData
    struct.pack_into("<IIII", buf, sec_off + 8, 0x400, 0x2000, 0x400, 0x200)

    # Write import descriptors at file offset 0x200 (RVA 0x2000)
    # Strings start at file offset 0x300 (RVA 0x2100)
    desc_off = 0x200
    str_off = 0x300
    for dll in dll_names:
        name_rva = 0x2000 + (str_off - 0x200)
        # OriginalFirstThunk=0, TimeDateStamp=0, ForwarderChain=0, Name=name_rva, FirstThunk=0x2000
        struct.pack_into("<IIIII", buf, desc_off, 0, 0, 0, name_rva, 0x2000)
        encoded = dll.encode("ascii") + b"\x00"
        buf[str_off : str_off + len(encoded)] = encoded
        desc_off += 20
        str_off += len(encoded)

    # Null terminator descriptor is already zero-filled.
    return bytes(buf)


def _create_fake_bottles_root(tmp_path: Path) -> Path:
    """Create a realistic mock Bottles Flatpak data directory."""
    root = tmp_path / "bottles_data"
    (root / "runners" / "soda-9.0-1").mkdir(parents=True)
    (root / "runners" / "caffe-9.7").mkdir(parents=True)
    (root / "dxvk" / "dxvk-3.0.2").mkdir(parents=True)
    (root / "vkd3d" / "vkd3d-proton-3.0.1").mkdir(parents=True)
    (root / "nvapi" / "dxvk-nvapi-v0.9.2").mkdir(parents=True)
    (root / "latencyflex" / "latencyflex-v0.1.1").mkdir(parents=True)

    # Gaming template
    tmpl_dir = root / "templates" / "01a94690-test-template"
    tmpl_dir.mkdir(parents=True)
    (tmpl_dir / "template.yml").write_text(
        yaml.safe_dump(
            {
                "env": "gaming",
                "uuid": "01a94690-test-template",
                "config": {
                    "Runner": "soda-9.0-1",
                    "Installed_Dependencies": [
                        "d3dx9",
                        "msls31",
                        "arial32",
                        "times32",
                        "courie32",
                        "d3dcompiler_43",
                        "d3dcompiler_47",
                        "mono",
                        "gecko",
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    # Cached dependency installers in temp/
    temp_dir = root / "temp"
    temp_dir.mkdir(parents=True)
    (temp_dir / "vcredist2015_x86.exe").write_bytes(b"fake-vc2015-x86")
    (temp_dir / "vcredist2015_x64.exe").write_bytes(b"fake-vc2015-x64")
    (temp_dir / "dotnet452.exe").write_bytes(b"fake-dotnet452")

    # Existing bottle (Existing Game)
    existing_dir = root / "bottles" / "Existing-Game"
    existing_dir.mkdir(parents=True)
    (existing_dir / "bottle.yml").write_text(
        yaml.safe_dump(
            {
                "Name": "Existing Game",
                "Path": "Existing-Game",
                "Environment": "Gaming",
                "Runner": "soda-9.0-1",
                "Installed_Dependencies": ["d3dx9", "vcredist2015", "dotnet452"],
            }
        ),
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# 1. Name derivation & Redist mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "folder_name,expected",
    [
        ("Example Game - Complete Edition [FitGirl Repack]", "Example Game"),
        ("Cyberpunk 2077 (v2.12 + All DLCs) [FitGirl Repack]", "Cyberpunk 2077"),
        ("Elden Ring - Deluxe Edition [FitGirl Repack]", "Elden Ring"),
        ("The Witcher 3 - Game of the Year Edition (v4.04)", "The Witcher 3"),
        ("Red Dead Redemption 2 [DODI Repack]", "Red Dead Redemption 2"),
        ("Hollow Knight", "Hollow Knight"),
    ],
)
def test_derive_game_name(folder_name: str, expected: str) -> None:
    assert derive_game_name(Path("/home/user/Downloads") / folder_name) == expected


@pytest.mark.parametrize(
    "rel_path,expected_deps",
    [
        ("_Redist/vcredist_x86_2005.exe", ["vcredist2005"]),
        ("_Redist/vcredist_x86_2008.exe", ["vcredist2008"]),
        ("_Redist/vcredist_x64_2010.exe", ["vcredist2010"]),
        ("_Redist/vcredist_2012_x64.exe", ["vcredist2012"]),
        ("_Redist/vcredist_x64_2013.exe", ["vcredist2013"]),
        ("_Redist/VC_redist.x64.exe", ["vcredist2015"]),
        ("_CommonRedist/vcredist/2019/VC_redist.x64.exe", ["vcredist2019"]),
        ("Redist/DirectX/DXSETUP.exe", ["d3dx9", "d3dx11", "d3dcompiler_43", "xact", "xinput"]),
        ("_Redist/DotNet/NDP452-KB2901907-x86-x64-AllOS-ENU.exe", ["dotnet452"]),
        ("_Redist/DotNet/ndp48-x86-x64-allos-enu.exe", ["dotnet48"]),
        ("_Redist/windowsdesktop-runtime-8.0.5-win-x64.exe", ["dotnetdesktop8"]),
        ("_Redist/XNA/xnafx40_redist.msi", ["xna40"]),
        ("_Redist/PhysX_9.19_SystemSoftware.exe", ["physx"]),
    ],
)
def test_map_redist_file(rel_path: str, expected_deps: list[str]) -> None:
    deps, silent_args = map_redist_file(rel_path)
    assert deps == expected_deps
    assert len(silent_args) >= 1


def test_scan_fake_redist_tree_and_pe_imports(tmp_path: Path) -> None:
    repack = tmp_path / "Example Game - Complete Edition [FitGirl Repack]"
    repack.mkdir()
    (repack / "setup.exe").write_bytes(b"MZ" + b"\x00" * 64 + b"Inno Setup Setup Data (5.5.0)")
    (repack / "fg-01.bin").write_bytes(b"A" * 1024 * 1024)  # 1 MiB
    (repack / "fg-optional-modpack.bin").write_bytes(b"B" * 512 * 1024)  # 0.5 MiB

    # Fake _Redist tree
    redist = repack / "_Redist"
    (redist / "DirectX").mkdir(parents=True)
    (redist / "DirectX" / "DXSETUP.exe").write_bytes(b"fake-dx")
    (redist / "vcredist_x86_2008.exe").write_bytes(b"fake-vc2008")
    (redist / "xnafx40_redist.msi").write_bytes(b"fake-xna40")

    # Fake PE binary importing msvcr90.dll, d3dx9_43.dll, xinput1_3.dll, and xlive.dll
    pe_bytes = _build_minimal_pe32(
        ["KERNEL32.dll", "MSVCR90.dll", "d3dx9_43.dll", "xinput1_3.dll", "xlive.dll"]
    )
    (repack / "ExampleGame.exe").write_bytes(pe_bytes)

    assert parse_pe_imports(repack / "ExampleGame.exe") == [
        "d3dx9_43.dll",
        "kernel32.dll",
        "msvcr90.dll",
        "xinput1_3.dll",
        "xlive.dll",
    ]

    result = scan_folder(repack, bottle_base_bytes=100_000)
    assert result.game_name == "Example Game"
    assert result.setup_exe == repack / "setup.exe"
    assert result.is_inno_setup is True
    for expected_dep in ("vcredist2008", "d3dx9", "d3dx11", "d3dcompiler_43", "xact", "xinput", "xna40"):
        assert expected_dep in result.inferred_deps
    assert any("xlive.dll" in w for w in result.warnings)


def test_disk_space_expansion_with_and_without_optional(tmp_path: Path) -> None:
    repack = tmp_path / "Game [FitGirl Repack]"
    repack.mkdir()
    (repack / "fg-01.bin").write_bytes(b"\x00" * 10_000)
    (repack / "fg-optional-bonus.bin").write_bytes(b"\x00" * 5_000)

    est_default = calculate_disk_space(
        repack, expansion_factor=2.0, with_optional=False, bottle_base_bytes=1_000
    )
    assert est_default.compressed_bytes == 10_000
    assert est_default.optional_bytes == 5_000
    assert est_default.installed_bytes == 20_000
    assert est_default.headroom_bytes == 2_000
    assert est_default.required_bytes == 23_000

    est_opt = calculate_disk_space(
        repack, expansion_factor=2.0, with_optional=True, bottle_base_bytes=1_000
    )
    assert est_opt.installed_bytes == 30_000
    assert est_opt.required_bytes == 34_000


def test_find_game_executables_ranking(tmp_path: Path) -> None:
    game_dir = tmp_path / "Example Game"
    game_dir.mkdir()
    (game_dir / "unins000.exe").write_bytes(b"\x00" * 5000)
    (game_dir / "ExampleGame.exe").write_bytes(b"\x00" * 50000)
    (game_dir / "PlayExampleGame.exe").write_bytes(b"\x00" * 10000)
    (game_dir / "_Redist").mkdir()
    (game_dir / "_Redist" / "vcredist_x86_2008.exe").write_bytes(b"\x00" * 50000)

    exes = find_game_executables(game_dir, "Example Game")
    assert exes[0].name == "ExampleGame.exe"
    assert all(e.name != "unins000.exe" for e in exes)
    assert all("_Redist" not in str(e) for e in exes)


# ---------------------------------------------------------------------------
# 2. Auditor & dependency resolution
# ---------------------------------------------------------------------------


def test_auditor_reads_template_bottles_and_cache(tmp_path: Path) -> None:
    root = _create_fake_bottles_root(tmp_path)
    auditor = BottlesAuditor(
        data_root=root,
        install_type="flatpak",
        cli_prefix=["flatpak", "run", "--command=bottles-cli", "com.usebottles.bottles"],
        query_cli=False,
        check_online=False,
    )
    report = auditor.audit()

    assert "soda-9.0-1" in report.runners
    assert "dxvk-3.0.2" in report.dxvk
    assert "Existing Game" in report.existing_bottles
    assert "d3dx9" in report.gaming_template_deps
    assert "vcredist2015" in report.cached_deps
    assert "dotnet452" in report.cached_deps
    assert select_runner(report) == "soda-9.0-1"
    assert select_runner(report, "caffe-9.7") == "caffe-9.7"

    with pytest.raises(ValueError, match="not installed"):
        select_runner(report, "nonexistent-runner-1.0")


def test_dependency_resolution_tiers(tmp_path: Path) -> None:
    root = _create_fake_bottles_root(tmp_path)
    auditor = BottlesAuditor(
        data_root=root,
        install_type="flatpak",
        cli_prefix=["flatpak", "run", "--command=bottles-cli", "com.usebottles.bottles"],
        query_cli=False,
    )
    report = auditor.audit()

    # Fake repack with vcredist2008 in _Redist, plus PE-inferred vcredist2015 (in cache) & vcredist2010 (missing)
    repack = tmp_path / "Sample Game [FitGirl Repack]"
    (repack / "_Redist").mkdir(parents=True)
    (repack / "_Redist" / "vcredist_x86_2008.exe").write_bytes(b"vc2008")
    scan = scan_folder(repack)

    actions = resolve_dependencies(
        required_deps=["d3dx9", "vcredist2008", "vcredist2015", "vcredist2010"],
        redist_matches=scan.redist_matches,
        report=report,
        bottle_name="Sample Game",
        allow_download=False,
    )
    by_name = {a.name: a for a in actions}
    assert by_name["d3dx9"].status == "satisfied"
    assert by_name["vcredist2008"].status == "local-redist"
    assert by_name["vcredist2015"].status == "cached"
    assert by_name["vcredist2010"].status == "unresolved"

    # With --allow-download, vcredist2010 promotes to download
    actions_dl = resolve_dependencies(
        required_deps=["vcredist2010"],
        redist_matches=scan.redist_matches,
        report=report,
        bottle_name="Sample Game",
        allow_download=True,
    )
    assert actions_dl[0].status == "download"


# ---------------------------------------------------------------------------
# 3. Builder CLI command generation & dry-run guardrails
# ---------------------------------------------------------------------------


def test_builder_generates_exact_bottles_cli_commands(tmp_path: Path) -> None:
    root = _create_fake_bottles_root(tmp_path)
    auditor = BottlesAuditor(
        data_root=root,
        install_type="flatpak",
        cli_prefix=["flatpak", "run", "--command=bottles-cli", "com.usebottles.bottles"],
        query_cli=False,
    )
    report = auditor.audit()

    repack = tmp_path / "Example Game - Complete Edition [FitGirl Repack]"
    (repack / "_Redist").mkdir(parents=True)
    (repack / "setup.exe").write_bytes(b"Inno Setup")
    (repack / "fg-01.bin").write_bytes(b"\x00" * 4096)
    (repack / "_Redist" / "vcredist_x86_2008.exe").write_bytes(b"vc2008")
    scan = scan_folder(repack, bottle_base_bytes=1024)

    # Default interactive mode (2 GB RAM checkbox)
    plan_gui = create_execution_plan(scan, report, silent=False)
    assert plan_gui.bottle_name == "Example Game"
    assert plan_gui.target_win_dir == r"C:\Games\Example Game"
    assert (
        plan_gui.target_unix_dir
        == root / "bottles" / "Example-Game" / "drive_c" / "Games" / "Example Game"
    )
    assert plan_gui.interactive_mode is True

    by_stage = {}
    for c in plan_gui.commands:
        by_stage.setdefault(c.stage, []).append(c)

    new_cmd = by_stage["create-bottle"][0].argv
    assert new_cmd[:7] == [
        "flatpak",
        "run",
        "--command=bottles-cli",
        "com.usebottles.bottles",
        "new",
        "--bottle-name",
        "Example Game",
    ]
    assert "--runner" in new_cmd and new_cmd[new_cmd.index("--runner") + 1] == "soda-9.0-1"
    assert "--dxvk" in new_cmd and "dxvk-3.0.2" in new_cmd

    run_cmd = by_stage["run-installer"][0].argv
    assert f"--filesystem={repack}:ro" in run_cmd
    assert "/VERYSILENT" not in run_cmd

    # DLL override reg commands generated for vcredist2008 (msvcr90, etc.)
    reg_cmds = by_stage["dll-overrides"]
    assert any("msvcr90" in c.argv and "native,builtin" in c.argv for c in reg_cmds)

    # Program registration
    add_cmd = by_stage["register-program"][0].argv
    assert "add" in add_cmd and "-b" in add_cmd and "Example Game" in add_cmd

    # Silent mode appends Inno Setup flags + /DIR=
    plan_silent = create_execution_plan(scan, report, silent=True)
    silent_run = [c for c in plan_silent.commands if c.stage == "run-installer"][0].argv
    assert "/VERYSILENT" in silent_run
    assert "/DIR=C:\\Games\\Example Game" in silent_run

    # Guardrail: execute_plan refuses to run when dry_run=True
    with pytest.raises(RuntimeError, match="dry_run=True"):
        execute_plan(plan_gui, report, dry_run=True)


def test_windows_target_to_unix(tmp_path: Path) -> None:
    bdir = tmp_path / "Example-Game"
    assert (
        windows_target_to_unix(bdir, r"C:\Games\Example Game")
        == bdir / "drive_c" / "Games" / "Example Game"
    )


def test_cli_dry_run_json_creates_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _create_fake_bottles_root(tmp_path)
    repack = tmp_path / "Hollow Knight - Voidheart Edition [FitGirl Repack]"
    repack.mkdir()
    (repack / "setup.exe").write_bytes(b"Inno Setup")
    (repack / "fg-01.bin").write_bytes(b"\x00" * 2048)

    # Point BottlesAuditor at our mock root so the test is 100% isolated.
    real_init = BottlesAuditor.__init__

    def fake_init(self, *args, **kwargs):
        real_init(
            self,
            data_root=root,
            install_type="flatpak",
            cli_prefix=["flatpak", "run", "--command=bottles-cli", "com.usebottles.bottles"],
            query_cli=False,
            check_online=False,
        )

    monkeypatch.setattr(BottlesAuditor, "__init__", fake_init)

    rc = auto_bottle.main([str(repack), "--dry-run", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["bottle_name"] == "Hollow Knight - Voidheart Edition" or "Hollow Knight" in payload["bottle_name"]
    assert payload["runner"] == "soda-9.0-1"
    assert payload["disk"]["ok"] is True
    # Confirm no bottle directory was created
    assert not (root / "bottles" / "Hollow-Knight").exists()


def test_oomd_pause_and_resume_in_plan_and_execution(tmp_path: Path) -> None:
    root = _create_fake_bottles_root(tmp_path)
    auditor = BottlesAuditor(
        data_root=root,
        install_type="flatpak",
        cli_prefix=["flatpak", "run", "--command=bottles-cli", "com.usebottles.bottles"],
        query_cli=False,
    )
    report = auditor.audit()

    repack = tmp_path / "Example Game - Complete Edition [FitGirl Repack]"
    repack.mkdir()
    (repack / "setup.exe").write_bytes(b"Inno Setup")
    (repack / "fg-01.bin").write_bytes(b"\x00" * 4096)
    scan = scan_folder(repack, bottle_base_bytes=1024)

    # Default plan pauses oomd before setup.exe and resumes after
    plan = create_execution_plan(scan, report, pause_oomd=True)
    stages = [c.stage for c in plan.commands]
    p_idx = stages.index("pause-oomd")
    r_idx = stages.index("run-installer")
    res_idx = stages.index("resume-oomd")
    assert p_idx < r_idx < res_idx
    assert plan.commands[p_idx].argv == [
        "sudo",
        "systemctl",
        "stop",
        "systemd-oomd.socket",
        "systemd-oomd.service",
    ]
    assert plan.commands[res_idx].argv == [
        "sudo",
        "systemctl",
        "start",
        "systemd-oomd.socket",
        "systemd-oomd.service",
    ]

    # Opt-out (--keep-oomd) omits both
    plan_keep = create_execution_plan(scan, report, pause_oomd=False)
    assert all(c.stage not in ("pause-oomd", "resume-oomd") for c in plan_keep.commands)

    # Guardrail: even if setup.exe is interrupted, resume-oomd MUST still run via finally
    executed: list[list[str]] = []

    def interrupting_runner(argv: list[str]) -> int:
        executed.append(argv)
        if "setup.exe" in " ".join(argv):
            raise KeyboardInterrupt("user cancelled")
        return 0

    with pytest.raises(KeyboardInterrupt):
        execute_plan(plan, report, dry_run=False, runner_fn=interrupting_runner, log_fn=lambda _: None)

    assert [
        "sudo",
        "systemctl",
        "stop",
        "systemd-oomd.socket",
        "systemd-oomd.service",
    ] in executed
    assert executed[-1] == [
        "sudo",
        "systemctl",
        "start",
        "systemd-oomd.socket",
        "systemd-oomd.service",
    ]

