"""Audit local Bottles setup, runners, templates, dependency caches, and disk space."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from scanner import RedistMatch, sanitize_bottle_dirname


FLATPAK_APP_ID = "com.usebottles.bottles"
FLATPAK_DATA_ROOT = Path.home() / ".var/app/com.usebottles.bottles/data/bottles"
NATIVE_DATA_ROOT = Path.home() / ".local/share/bottles"
DEFAULT_PREFERRED_RUNNER = "soda-9.0-1"

# Visual C++ 2015-2022 share the vcruntime140 ABI; newer versions satisfy older ones.
VCREDIST_MODERN_ORDER = ["vcredist2015", "vcredist2019", "vcredist2022"]

# Built-in Bottles dependency metadata (extracted from proxy.usebottles.com/repo/dependencies).
# Used for offline cache matching, DLL overrides, and optional --allow-download.
DEPENDENCY_CATALOG: dict[str, dict[str, Any]] = {
    "vcredist2005": {
        "category": "Essentials",
        "files": [
            {
                "name": "vcredist2005_x86.exe",
                "args": ["/q"],
                "arch": "any",
                "md5": "4f1611f2d0ae799507f60c10ff8654c5",
                "size": 2710520,
                "url": "https://download.microsoft.com/download/8/B/4/8B42259F-5D70-43F4-AC2E-4B208FD8D66A/vcredist_x86.EXE",
            },
            {
                "name": "vcredist2005_x64.exe",
                "args": ["/q"],
                "arch": "win64",
                "md5": "e231fbcce2c2cb16dcc299d36c734df3",
                "size": 3179000,
                "url": "https://download.microsoft.com/download/8/B/4/8B42259F-5D70-43F4-AC2E-4B208FD8D66A/vcredist_x64.EXE",
            },
        ],
        "overrides": {
            "atl80": "native,builtin",
            "msvcm80": "native,builtin",
            "msvcp80": "native,builtin",
            "msvcr80": "native,builtin",
            "vcomp": "native,builtin",
        },
    },
    "vcredist2008": {
        "category": "Essentials",
        "files": [
            {
                "name": "vcredist2008_x86.exe",
                "args": ["/q"],
                "arch": "any",
                "md5": "a92a4d8e784d8f859217f828fe879047",
                "size": 4483040,
                "url": "https://download.microsoft.com/download/5/D/8/5D8C65CB-C849-4025-8E95-C3966CAFD8AE/vcredist_x86.exe",
            },
            {
                "name": "vcredist2008_x64.exe",
                "args": ["/q"],
                "arch": "win64",
                "md5": "472c10efa75a30deb2a15ec8b777227b",
                "size": 5211080,
                "url": "https://download.microsoft.com/download/5/D/8/5D8C65CB-C849-4025-8E95-C3966CAFD8AE/vcredist_x64.exe",
            },
        ],
        "overrides": {
            "atl90": "native,builtin",
            "msvcm90": "native,builtin",
            "msvcp90": "native,builtin",
            "msvcr90": "native,builtin",
            "vcomp90": "native,builtin",
        },
    },
    "vcredist2010": {
        "category": "Essentials",
        "files": [
            {
                "name": "vcredist2010_x86.exe",
                "args": ["/q"],
                "arch": "any",
                "md5": "5faedf5ae484adcb842bec6aa14ae8d9",
                "size": 5076456,
                "url": "https://download.microsoft.com/download/5/B/C/5BC5DBB3-652D-4DCE-B14A-475AB85EEF6E/vcredist_x86.exe",
            },
            {
                "name": "vcredist2010_x64.exe",
                "args": ["/q"],
                "arch": "win64",
                "md5": "be79543624f806ced4c7dff25751a3e4",
                "size": 5677032,
                "url": "https://download.microsoft.com/download/A/8/0/A80747C3-41BD-45DF-B505-E9710D2744E0/vcredist_x64.exe",
            },
        ],
        "overrides": {
            "atl100": "native,builtin",
            "msvcp100": "native,builtin",
            "msvcr100": "native,builtin",
            "vcomp100": "native,builtin",
        },
    },
    "vcredist2012": {
        "category": "Essentials",
        "files": [
            {
                "name": "vcredist2012_x86.exe",
                "args": ["/q"],
                "arch": "any",
                "md5": "5faedf5ae484adcb842bec6aa14ae8d9",
                "size": 5076456,
                "url": "https://download.microsoft.com/download/5/B/C/5BC5DBB3-652D-4DCE-B14A-475AB85EEF6E/vcredist_x86.exe",
            },
            {
                "name": "vcredist2012_x64.exe",
                "args": ["/q"],
                "arch": "win64",
                "md5": "3c03562b5af9ed347614053d459d7778",
                "size": 7186992,
                "url": "https://download.microsoft.com/download/1/6/B/16B06F60-3B20-4FF2-B699-5E9B7962F9AE/VSU_4/vcredist_x64.exe",
            },
        ],
        "overrides": {
            "atl110": "native,builtin",
            "msvcp110": "native,builtin",
            "msvcr110": "native,builtin",
            "vcomp110": "native,builtin",
        },
    },
    "vcredist2013": {
        "category": "Essentials",
        "files": [
            {
                "name": "vcredist2013_x86.exe",
                "args": ["/quiet", "/norestart"],
                "arch": "any",
                "md5": "38a1b890ce847167d16567cf7b7a5642",
                "size": 6510136,
                "url": "https://download.visualstudio.microsoft.com/download/pr/10912113/5da66ddebb0ad32ebd4b922fd82e8e25/vcredist_x86.exe",
            },
            {
                "name": "vcredist2013_x64.exe",
                "args": ["/quiet", "/norestart"],
                "arch": "win64",
                "md5": "49b1164f8e95ec6409ea83cdb352d8da",
                "size": 7200744,
                "url": "https://download.visualstudio.microsoft.com/download/pr/10912041/cee5d6bca2ddbcd039da727bf4acb48a/vcredist_x64.exe",
            },
        ],
        "overrides": {
            "atl120": "native,builtin",
            "msvcp120": "native,builtin",
            "msvcr120": "native,builtin",
            "vcomp120": "native,builtin",
        },
    },
    "vcredist2015": {
        "category": "Essentials",
        "files": [
            {
                "name": "vcredist2015_x86.exe",
                "args": ["/quiet", "/norestart"],
                "arch": "any",
                "md5": "1a15e6606bac9647e7ad3caa543377cf",
                "size": 13767776,
                "url": "https://download.microsoft.com/download/9/3/F/93FCF1E7-E6A4-478B-96E7-D4B285925B00/vc_redist.x86.exe",
            },
            {
                "name": "vcredist2015_x64.exe",
                "args": ["/quiet", "/norestart"],
                "arch": "win64",
                "md5": "27b141aacc2777a82bb3fa9f6e5e5c1c",
                "size": 14572000,
                "url": "https://download.microsoft.com/download/9/3/F/93FCF1E7-E6A4-478B-96E7-D4B285925B00/vc_redist.x64.exe",
            },
        ],
        "overrides": {
            "atl140": "native,builtin",
            "concrt140": "native,builtin",
            "msvcp140": "native,builtin",
            "msvcr140": "native,builtin",
            "ucrtbase": "native,builtin",
            "vcomp140": "native,builtin",
            "vcruntime140": "native,builtin",
        },
    },
    "vcredist2019": {
        "category": "Essentials",
        "files": [
            {
                "name": "vcredist2019_x86.exe",
                "args": ["/quiet", "/norestart"],
                "arch": "any",
                "md5": "540dd9aed5ad7e3a6fbf3f2d7753a093",
                "size": 13888640,
                "url": "https://download.visualstudio.microsoft.com/download/pr/5efdbcb7-d3bd-4432-a2fb-b267c386e2f3/49545CB0F6499C4A65E1E8D5033441EEEB4EDFAE465A68489A70832C6A4F6399/VC_redist.x86.exe",
            },
            {
                "name": "vcredist2019_x64.exe",
                "args": ["/quiet", "/norestart"],
                "arch": "win64",
                "md5": "",
                "size": 14500000,
                "url": "https://aka.ms/vs/16/release/vc_redist.x64.exe",
            },
        ],
        "overrides": {
            "atl140": "native,builtin",
            "concrt140": "native,builtin",
            "msvcp140": "native,builtin",
            "msvcr140": "native,builtin",
            "ucrtbase": "native,builtin",
            "vcomp140": "native,builtin",
            "vcruntime140": "native,builtin",
        },
    },
    "vcredist2022": {
        "category": "Essentials",
        "files": [
            {
                "name": "vcredist2022_x86.exe",
                "args": ["/quiet", "/norestart"],
                "arch": "any",
                "md5": "",
                "size": 13900000,
                "url": "https://aka.ms/vs/17/release/vc_redist.x86.exe",
            },
            {
                "name": "vcredist2022_x64.exe",
                "args": ["/quiet", "/norestart"],
                "arch": "win64",
                "md5": "",
                "size": 25000000,
                "url": "https://aka.ms/vs/17/release/vc_redist.x64.exe",
            },
        ],
        "overrides": {
            "atl140": "native,builtin",
            "concrt140": "native,builtin",
            "msvcp140": "native,builtin",
            "msvcr140": "native,builtin",
            "ucrtbase": "native,builtin",
            "vcomp140": "native,builtin",
            "vcruntime140": "native,builtin",
        },
    },
    "dotnet40": {
        "category": "Essentials",
        "files": [
            {
                "name": "dotnet40.exe",
                "args": ["/q", "/norestart"],
                "arch": "any",
                "md5": "",
                "size": 50449456,
                "url": "https://download.microsoft.com/download/9/5/A/95A9616B-7A37-4AF6-BC36-D6EA96C8DAAE/dotNetFx40_Full_x86_x64.exe",
            }
        ],
        "overrides": {"mscoree": "native"},
    },
    "dotnet45": {
        "category": "Essentials",
        "files": [
            {
                "name": "dotnet45.exe",
                "args": ["/q", "/norestart"],
                "arch": "any",
                "md5": "",
                "size": 50352408,
                "url": "https://download.microsoft.com/download/b/a/4/ba4a7e71-2906-4b2d-a0e1-80cf16844f5f/dotnetfx45_full_x86_x64.exe",
            }
        ],
        "overrides": {"mscoree": "native"},
    },
    "dotnet452": {
        "category": "Essentials",
        "files": [
            {
                "name": "dotnet452.exe",
                "args": ["/q", "/norestart"],
                "arch": "any",
                "md5": "",
                "size": 69999448,
                "url": "https://download.microsoft.com/download/E/2/1/E21644B5-2DF2-47C2-91BD-63C560427900/NDP452-KB2901907-x86-x64-AllOS-ENU.exe",
            }
        ],
        "overrides": {"mscoree": "native"},
    },
    "dotnet48": {
        "category": "Essentials",
        "files": [
            {
                "name": "dotnet48.exe",
                "args": ["/q", "/norestart"],
                "arch": "any",
                "md5": "4e7a8586d06ce1f8cebac0d4bc379e16",
                "size": 72721568,
                "url": "https://download.visualstudio.microsoft.com/download/pr/7afca223-55d2-470a-8edc-6a1739ae3252/abd170b4b0ec15ad0222a809b761a036/ndp48-x86-x64-allos-enu.exe",
            }
        ],
        "overrides": {"mscoree": "native"},
    },
    "dotnetdesktop6": {
        "category": "Essentials",
        "files": [
            {
                "name": "windowsdesktop-runtime-6.0.36-win-x86.exe",
                "args": ["/quiet"],
                "arch": "any",
                "md5": "9c62d5c212725b00563ca6b201326b8f",
                "size": 57380656,
                "url": "https://builds.dotnet.microsoft.com/dotnet/WindowsDesktop/6.0.36/windowsdesktop-runtime-6.0.36-win-x86.exe",
            },
            {
                "name": "windowsdesktop-runtime-6.0.36-win-x64.exe",
                "args": ["/quiet"],
                "arch": "win64",
                "md5": "2f34b7c01b2c0ec8c95e2050d0a48b56",
                "size": 52173224,
                "url": "https://builds.dotnet.microsoft.com/dotnet/WindowsDesktop/6.0.36/windowsdesktop-runtime-6.0.36-win-x64.exe",
            },
        ],
        "overrides": {},
    },
    "dotnetdesktop8": {
        "category": "Essentials",
        "files": [
            {
                "name": "windowsdesktop-runtime-8.0.25-win-x86.exe",
                "args": ["/quiet"],
                "arch": "any",
                "md5": "9fe649ca4ce6e3b4c2ff8bd305ba25ed",
                "size": 53804640,
                "url": "https://builds.dotnet.microsoft.com/dotnet/WindowsDesktop/8.0.25/windowsdesktop-runtime-8.0.25-win-x86.exe",
            },
            {
                "name": "windowsdesktop-runtime-8.0.25-win-x64.exe",
                "args": ["/quiet"],
                "arch": "win64",
                "md5": "2f2b3b6edd03f1f38913cf017cc48563",
                "size": 58461256,
                "url": "https://builds.dotnet.microsoft.com/dotnet/WindowsDesktop/8.0.25/windowsdesktop-runtime-8.0.25-win-x64.exe",
            },
        ],
        "overrides": {},
    },
    "xna31": {
        "category": "Essentials",
        "files": [
            {
                "name": "xnafx31_redist.msi",
                "args": ["/quiet"],
                "arch": "any",
                "md5": "52873853e4981d5b9147c93472bb4ee7",
                "size": 7500000,
                "url": "https://proxy.usebottles.com/redistributable/dependencies/xnafx31_redist.msi",
            }
        ],
        "overrides": {},
    },
    "xna40": {
        "category": "Essentials",
        "files": [
            {
                "name": "xnafx40_redist.msi",
                "args": ["/quiet"],
                "arch": "any",
                "md5": "97c2eebb30c5a88c68c8f24f37183f1d",
                "size": 7000000,
                "url": "https://proxy.usebottles.com/redistributable/dependencies/xnafx40_redist.msi",
            }
        ],
        "overrides": {},
    },
    "physx": {
        "category": "Essentials",
        "files": [
            {
                "name": "physx.exe",
                "args": ["/s"],
                "arch": "any",
                "md5": "5114bf28a575264366c1ad0f5fe76fd0",
                "size": 28000000,
                "url": "https://uk.download.nvidia.com/Windows/9.19.0218/PhysX-9.19.0218-SystemSoftware.exe",
            }
        ],
        "overrides": {},
    },
    "d3dx9": {
        "category": "Essentials",
        "files": [{"name": "d3dx9.tar.xz", "args": [], "arch": "any", "md5": "", "size": 19651992, "url": ""}],
        "overrides": {},
    },
    "d3dx11": {
        "category": "Essentials",
        "files": [{"name": "d3dx11.tar.xz", "args": [], "arch": "any", "md5": "", "size": 267924, "url": ""}],
        "overrides": {"d3dx11_42": "native,builtin", "d3dx11_43": "native,builtin"},
    },
    "d3dcompiler_43": {
        "category": "Essentials",
        "files": [{"name": "d3dx9.tar.xz", "args": [], "arch": "any", "md5": "", "size": 0, "url": ""}],
        "overrides": {"d3dcompiler_43": "native,builtin"},
    },
    "d3dcompiler_47": {
        "category": "Essentials",
        "files": [{"name": "d3dx9.tar.xz", "args": [], "arch": "any", "md5": "", "size": 0, "url": ""}],
        "overrides": {"d3dcompiler_47": "native,builtin"},
    },
    "xact": {
        "category": "Essentials",
        "files": [],
        "overrides": {},
    },
    "xinput": {
        "category": "Essentials",
        "files": [],
        "overrides": {},
    },
}

DEFAULT_GAMING_TEMPLATE_DEPS = [
    "d3dx9",
    "msls31",
    "arial32",
    "times32",
    "courie32",
    "d3dcompiler_43",
    "d3dcompiler_47",
    "mono",
    "gecko",
]


@dataclass
class BottleSummary:
    """Summary of an existing local Bottle."""

    name: str
    path_name: str
    full_path: Path
    environment: str
    runner: str
    installed_dependencies: list[str] = field(default_factory=list)


@dataclass
class DependencyAction:
    """Resolution of a single required dependency."""

    name: str
    status: str  # "satisfied", "local-redist", "cached", "download", "unresolved"
    reason: str
    installers: list[tuple[Path, list[str]]] = field(default_factory=list)
    download_entries: list[dict[str, Any]] = field(default_factory=list)
    dll_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class AuditReport:
    """Snapshot of the local Bottles installation and caches."""

    install_type: str  # "flatpak" or "native"
    cli_prefix: list[str]
    data_root: Path
    bottles_dir: Path
    temp_dir: Path
    runners: list[str]
    dxvk: list[str]
    vkd3d: list[str]
    nvapi: list[str]
    latencyflex: list[str]
    existing_bottles: dict[str, BottleSummary]
    gaming_template_runner: Optional[str]
    gaming_template_deps: list[str]
    gaming_template_bytes: int
    cached_files: set[str]
    cached_deps: dict[str, list[Path]]
    free_disk_bytes: int
    bottles_ping_online: Optional[bool] = None
    warnings: list[str] = field(default_factory=list)


def detect_bottles_installation() -> tuple[str, list[str], Path]:
    """Detect whether Bottles is installed natively or via Flatpak."""
    native_cli = shutil.which("bottles-cli")
    if native_cli:
        return "native", [native_cli], NATIVE_DATA_ROOT

    flatpak_bin = shutil.which("flatpak")
    if flatpak_bin:
        try:
            proc = subprocess.run(
                [flatpak_bin, "info", FLATPAK_APP_ID],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if proc.returncode == 0:
                return (
                    "flatpak",
                    ["flatpak", "run", "--command=bottles-cli", FLATPAK_APP_ID],
                    FLATPAK_DATA_ROOT,
                )
        except OSError:
            pass

    # Fallback: check if Flatpak data root exists on disk
    if FLATPAK_DATA_ROOT.exists():
        return (
            "flatpak",
            ["flatpak", "run", "--command=bottles-cli", FLATPAK_APP_ID],
            FLATPAK_DATA_ROOT,
        )

    return "native", ["bottles-cli"], NATIVE_DATA_ROOT


def _dir_size_bytes(path: Path) -> int:
    """Compute total size of a directory tree in bytes."""
    total = 0
    if not path.is_dir():
        return 0
    for root, _, files in os.walk(path):
        for fname in files:
            try:
                total += (Path(root) / fname).stat().st_size
            except OSError:
                pass
    return total


def _list_subdirs(path: Path) -> list[str]:
    if not path.is_dir():
        return []
    return sorted(p.name for p in path.iterdir() if p.is_dir())


def _extract_json_from_cli_output(stdout: str) -> Optional[dict[str, Any]]:
    """Parse JSON from bottles-cli -j output, skipping leading warning lines."""
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                continue
    return None


def check_bottles_ping_online(timeout: float = 4.0) -> bool:
    """Check whether ping.usebottles.com returns HTTP 200 (Bottles' online check)."""
    req = urllib.request.Request(
        "https://ping.usebottles.com",
        method="HEAD",
        headers={"User-Agent": "curl/8.5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


class BottlesAuditor:
    """Inspects local Bottles configuration, runners, templates, and dependency caches."""

    def __init__(
        self,
        data_root: Optional[Path] = None,
        install_type: Optional[str] = None,
        cli_prefix: Optional[list[str]] = None,
        query_cli: bool = True,
        check_online: bool = False,
    ) -> None:
        if data_root is None or install_type is None or cli_prefix is None:
            det_type, det_prefix, det_root = detect_bottles_installation()
            self.install_type = install_type or det_type
            self.cli_prefix = cli_prefix or det_prefix
            self.data_root = Path(data_root) if data_root is not None else det_root
        else:
            self.install_type = install_type
            self.cli_prefix = list(cli_prefix)
            self.data_root = Path(data_root)
        self.query_cli = query_cli
        self.check_online = check_online

    def _query_cli_components(self) -> Optional[dict[str, list[str]]]:
        if not self.query_cli:
            return None
        try:
            proc = subprocess.run(
                [*self.cli_prefix, "-j", "list", "components"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if proc.returncode == 0:
                parsed = _extract_json_from_cli_output(proc.stdout)
                if isinstance(parsed, dict):
                    return parsed
        except (OSError, subprocess.SubprocessError):
            pass
        return None

    def _read_existing_bottles(self, bottles_dir: Path) -> dict[str, BottleSummary]:
        bottles: dict[str, BottleSummary] = {}
        if not bottles_dir.is_dir():
            return bottles

        for child in sorted(bottles_dir.iterdir()):
            yml_path = child / "bottle.yml"
            if not yml_path.is_file():
                continue
            try:
                cfg = yaml.safe_load(yml_path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            name = cfg.get("Name") or child.name
            bottles[name] = BottleSummary(
                name=name,
                path_name=cfg.get("Path") or child.name,
                full_path=child,
                environment=str(cfg.get("Environment") or ""),
                runner=str(cfg.get("Runner") or ""),
                installed_dependencies=list(cfg.get("Installed_Dependencies") or []),
            )
        return bottles

    def _read_gaming_template(
        self, templates_dir: Path
    ) -> tuple[Optional[str], list[str], int]:
        if not templates_dir.is_dir():
            return None, list(DEFAULT_GAMING_TEMPLATE_DEPS), 1288490188

        for child in sorted(templates_dir.iterdir()):
            yml_path = child / "template.yml"
            if not yml_path.is_file():
                continue
            try:
                data = yaml.safe_load(yml_path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            if str(data.get("env", "")).lower() == "gaming":
                cfg = data.get("config") or {}
                runner = cfg.get("Runner") or None
                deps = list(cfg.get("Installed_Dependencies") or DEFAULT_GAMING_TEMPLATE_DEPS)
                size = _dir_size_bytes(child) or 1288490188
                return runner, deps, size

        return None, list(DEFAULT_GAMING_TEMPLATE_DEPS), 1288490188

    def _scan_temp_cache(self, temp_dir: Path) -> tuple[set[str], dict[str, list[Path]]]:
        cached_files: set[str] = set()
        if temp_dir.is_dir():
            for p in temp_dir.iterdir():
                if p.is_file():
                    cached_files.add(p.name)

        cached_deps: dict[str, list[Path]] = {}
        lower_map = {name.lower(): temp_dir / name for name in cached_files}
        for dep_name, meta in DEPENDENCY_CATALOG.items():
            matched_paths: list[Path] = []
            for fentry in meta.get("files", []):
                fname = fentry["name"]
                if fname.lower() in lower_map:
                    matched_paths.append(lower_map[fname.lower()])
            if matched_paths:
                cached_deps[dep_name] = matched_paths

        return cached_files, cached_deps

    def _free_disk_bytes(self, target_dir: Path) -> int:
        probe = target_dir
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            st = os.statvfs(probe)
            return st.f_bavail * st.f_frsize
        except OSError:
            return 0

    def audit(self) -> AuditReport:
        """Run full read-only audit of the local Bottles installation."""
        bottles_dir = self.data_root / "bottles"
        temp_dir = self.data_root / "temp"
        templates_dir = self.data_root / "templates"
        warnings: list[str] = []

        runners = _list_subdirs(self.data_root / "runners")
        dxvk = _list_subdirs(self.data_root / "dxvk")
        vkd3d = _list_subdirs(self.data_root / "vkd3d")
        nvapi = _list_subdirs(self.data_root / "nvapi")
        latencyflex = _list_subdirs(self.data_root / "latencyflex")

        cli_components = self._query_cli_components()
        if cli_components:
            for key, target in (
                ("runners", runners),
                ("dxvk", dxvk),
                ("vkd3d", vkd3d),
                ("nvapi", nvapi),
                ("latencyflex", latencyflex),
            ):
                for item in cli_components.get(key, []):
                    if item not in target:
                        target.append(item)

        existing_bottles = self._read_existing_bottles(bottles_dir)
        tmpl_runner, tmpl_deps, tmpl_bytes = self._read_gaming_template(templates_dir)
        cached_files, cached_deps = self._scan_temp_cache(temp_dir)
        free_disk = self._free_disk_bytes(bottles_dir)

        ping_online: Optional[bool] = None
        if self.check_online:
            ping_online = check_bottles_ping_online()
            if not ping_online:
                warnings.append(
                    "ping.usebottles.com is unreachable (Bottles CLI will operate in offline mode; "
                    "using local _Redist / cached installers instead of Bottles' built-in dep downloader)."
                )

        if not runners:
            warnings.append(
                f"No Wine runners detected under {self.data_root / 'runners'}."
            )

        return AuditReport(
            install_type=self.install_type,
            cli_prefix=list(self.cli_prefix),
            data_root=self.data_root,
            bottles_dir=bottles_dir,
            temp_dir=temp_dir,
            runners=runners,
            dxvk=dxvk,
            vkd3d=vkd3d,
            nvapi=nvapi,
            latencyflex=latencyflex,
            existing_bottles=existing_bottles,
            gaming_template_runner=tmpl_runner,
            gaming_template_deps=tmpl_deps,
            gaming_template_bytes=tmpl_bytes,
            cached_files=cached_files,
            cached_deps=cached_deps,
            free_disk_bytes=free_disk,
            bottles_ping_online=ping_online,
            warnings=warnings,
        )


def select_runner(report: AuditReport, requested_runner: Optional[str] = None) -> str:
    """Choose the Wine runner, validating explicit overrides against installed runners."""
    if requested_runner:
        if report.runners and requested_runner not in report.runners:
            raise ValueError(
                f"Requested runner {requested_runner!r} is not installed. "
                f"Available runners: {', '.join(report.runners)}"
            )
        return requested_runner

    if DEFAULT_PREFERRED_RUNNER in report.runners:
        return DEFAULT_PREFERRED_RUNNER
    if report.gaming_template_runner and report.gaming_template_runner in report.runners:
        return report.gaming_template_runner
    if report.runners:
        return report.runners[0]
    return DEFAULT_PREFERRED_RUNNER


def _is_satisfied_by_installed(dep: str, installed: set[str]) -> bool:
    if dep in installed:
        return True
    if dep in VCREDIST_MODERN_ORDER:
        idx = VCREDIST_MODERN_ORDER.index(dep)
        return any(newer in installed for newer in VCREDIST_MODERN_ORDER[idx:])
    return False


def resolve_dependencies(
    required_deps: list[str],
    redist_matches: list[RedistMatch],
    report: AuditReport,
    bottle_name: str,
    reuse_bottle: bool = False,
    allow_download: bool = False,
) -> list[DependencyAction]:
    """Resolve required dependencies into satisfied, local-redist, cached, download, or unresolved."""
    installed: set[str] = set(report.gaming_template_deps)
    if reuse_bottle and bottle_name in report.existing_bottles:
        installed.update(report.existing_bottles[bottle_name].installed_dependencies)

    # Collapse redundant vcredist2015/2019/2022 requests to the newest one requested.
    deduped = list(dict.fromkeys(required_deps))
    present_modern = [d for d in VCREDIST_MODERN_ORDER if d in deduped]
    if len(present_modern) > 1:
        newest = present_modern[-1]
        deduped = [d for d in deduped if d not in present_modern or d == newest]

    actions: list[DependencyAction] = []
    for dep in deduped:
        meta = DEPENDENCY_CATALOG.get(dep, {})
        overrides = dict(meta.get("overrides") or {})

        # 1. Already satisfied by Gaming template or existing bottle?
        if _is_satisfied_by_installed(dep, installed):
            source_label = (
                f"existing bottle '{bottle_name}'"
                if reuse_bottle and bottle_name in report.existing_bottles
                and dep in report.existing_bottles[bottle_name].installed_dependencies
                else "Gaming template"
            )
            actions.append(
                DependencyAction(
                    name=dep,
                    status="satisfied",
                    reason=f"Already provided by {source_label}",
                )
            )
            continue

        # 2. Tier A: Bundled installer in the game's _Redist folder
        local_installers: list[tuple[Path, list[str]]] = []
        for rm in redist_matches:
            if dep in rm.dependencies:
                local_installers.append((rm.path, list(rm.silent_args)))
        if local_installers:
            actions.append(
                DependencyAction(
                    name=dep,
                    status="local-redist",
                    reason=f"Bundled in _Redist ({len(local_installers)} installer(s))",
                    installers=local_installers,
                    dll_overrides=overrides,
                )
            )
            continue

        # 3. Tier B: Cached in Bottles' temp/ directory
        cached_paths = report.cached_deps.get(dep, [])
        exe_cached: list[tuple[Path, list[str]]] = []
        if cached_paths:
            for cpath in cached_paths:
                if cpath.suffix.lower() in {".exe", ".msi"}:
                    args = []
                    for fentry in meta.get("files", []):
                        if fentry["name"].lower() == cpath.name.lower():
                            args = list(fentry.get("args") or [])
                            break
                    exe_cached.append((cpath, args))
        if exe_cached:
            actions.append(
                DependencyAction(
                    name=dep,
                    status="cached",
                    reason=f"Reusing Bottles temp/ cache ({', '.join(p.name for p, _ in exe_cached)})",
                    installers=exe_cached,
                    dll_overrides=overrides,
                )
            )
            continue

        # 4. Tier C: Downloadable from Bottles dependency manifest (only if --allow-download)
        downloadable = [
            fentry
            for fentry in meta.get("files", [])
            if fentry.get("url") and Path(fentry["name"]).suffix.lower() in {".exe", ".msi"}
        ]
        if downloadable and allow_download:
            actions.append(
                DependencyAction(
                    name=dep,
                    status="download",
                    reason="Will download into Bottles temp/ cache (--allow-download)",
                    download_entries=downloadable,
                    dll_overrides=overrides,
                )
            )
            continue

        reason = (
            "Not in _Redist or Bottles temp/ cache (pass --allow-download to fetch)"
            if downloadable
            else "No standalone installer in _Redist or cache"
        )
        actions.append(
            DependencyAction(
                name=dep,
                status="unresolved",
                reason=reason,
                dll_overrides=overrides,
            )
        )

    return actions


def bottle_dir_for_name(report: AuditReport, bottle_name: str) -> Path:
    """Resolve the filesystem directory for a new or existing bottle."""
    if bottle_name in report.existing_bottles:
        return report.existing_bottles[bottle_name].full_path
    return report.bottles_dir / sanitize_bottle_dirname(bottle_name)
