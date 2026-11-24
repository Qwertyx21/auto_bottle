"""Scan FitGirl game installer folders, _Redist trees, PE imports, and disk needs."""

from __future__ import annotations

import hashlib
import os
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


# Patterns that map relative file paths inside _Redist / Redist folders to
# (bottles_dependency_names, default_silent_args).
REDIST_FILE_RULES: list[tuple[re.Pattern[str], list[str], list[str]]] = [
    # Visual C++ Redistributables (newest / specific years first)
    (
        re.compile(r"(vc_?redist|vcredist).*2022", re.IGNORECASE),
        ["vcredist2022"],
        ["/install", "/quiet", "/norestart"],
    ),
    (
        re.compile(r"(vc_?redist|vcredist).*2019", re.IGNORECASE),
        ["vcredist2019"],
        ["/install", "/quiet", "/norestart"],
    ),
    (
        re.compile(r"(vc_?redist|vcredist).*(2015|2017)", re.IGNORECASE),
        ["vcredist2015"],
        ["/install", "/quiet", "/norestart"],
    ),
    (
        re.compile(r"(vc_?redist|vcredist).*2013", re.IGNORECASE),
        ["vcredist2013"],
        ["/install", "/quiet", "/norestart"],
    ),
    (
        re.compile(r"(vc_?redist|vcredist).*2012", re.IGNORECASE),
        ["vcredist2012"],
        ["/install", "/quiet", "/norestart"],
    ),
    (
        re.compile(r"(vc_?redist|vcredist).*2010", re.IGNORECASE),
        ["vcredist2010"],
        ["/q", "/norestart"],
    ),
    (
        re.compile(r"(vc_?redist|vcredist).*2008", re.IGNORECASE),
        ["vcredist2008"],
        ["/q"],
    ),
    (
        re.compile(r"(vc_?redist|vcredist).*2005", re.IGNORECASE),
        ["vcredist2005"],
        ["/q"],
    ),
    # Year in parent directory name, e.g. vcredist/2008/vcredist_x86.exe
    (
        re.compile(r"\b2022\b.*vc_?redist", re.IGNORECASE),
        ["vcredist2022"],
        ["/install", "/quiet", "/norestart"],
    ),
    (
        re.compile(r"\b2019\b.*vc_?redist", re.IGNORECASE),
        ["vcredist2019"],
        ["/install", "/quiet", "/norestart"],
    ),
    (
        re.compile(r"\b(2015|2017)\b.*vc_?redist", re.IGNORECASE),
        ["vcredist2015"],
        ["/install", "/quiet", "/norestart"],
    ),
    (
        re.compile(r"\b2013\b.*vc_?redist", re.IGNORECASE),
        ["vcredist2013"],
        ["/install", "/quiet", "/norestart"],
    ),
    (
        re.compile(r"\b2012\b.*vc_?redist", re.IGNORECASE),
        ["vcredist2012"],
        ["/install", "/quiet", "/norestart"],
    ),
    (
        re.compile(r"\b2010\b.*vc_?redist", re.IGNORECASE),
        ["vcredist2010"],
        ["/q", "/norestart"],
    ),
    (
        re.compile(r"\b2008\b.*vc_?redist", re.IGNORECASE),
        ["vcredist2008"],
        ["/q"],
    ),
    (
        re.compile(r"\b2005\b.*vc_?redist", re.IGNORECASE),
        ["vcredist2005"],
        ["/q"],
    ),
    # Unversioned modern VC_redist.x86.exe / VC_redist.x64.exe (2015-2022 family)
    (
        re.compile(r"vc_redist\.(x86|x64)\.exe$", re.IGNORECASE),
        ["vcredist2015"],
        ["/install", "/quiet", "/norestart"],
    ),
    # DirectX End-User Runtime
    (
        re.compile(r"(dxsetup\.exe|directx.*redist.*\.exe|dxwebsetup\.exe)$", re.IGNORECASE),
        ["d3dx9", "d3dx11", "d3dcompiler_43", "xact", "xinput"],
        ["/silent"],
    ),
    # .NET Desktop Runtimes (6-10)
    (
        re.compile(r"windowsdesktop-runtime-10\.", re.IGNORECASE),
        ["dotnetdesktop10"],
        ["/install", "/quiet", "/norestart"],
    ),
    (
        re.compile(r"windowsdesktop-runtime-9\.", re.IGNORECASE),
        ["dotnetdesktop9"],
        ["/install", "/quiet", "/norestart"],
    ),
    (
        re.compile(r"windowsdesktop-runtime-8\.", re.IGNORECASE),
        ["dotnetdesktop8"],
        ["/install", "/quiet", "/norestart"],
    ),
    (
        re.compile(r"windowsdesktop-runtime-7\.", re.IGNORECASE),
        ["dotnetdesktop7"],
        ["/install", "/quiet", "/norestart"],
    ),
    (
        re.compile(r"windowsdesktop-runtime-6\.", re.IGNORECASE),
        ["dotnetdesktop6"],
        ["/install", "/quiet", "/norestart"],
    ),
    # .NET Framework
    (
        re.compile(r"(ndp481|dotnet.*4\.?8\.?1)", re.IGNORECASE),
        ["dotnet481"],
        ["/q", "/norestart"],
    ),
    (
        re.compile(r"(ndp48|dotnet.*4\.?8)", re.IGNORECASE),
        ["dotnet48"],
        ["/q", "/norestart"],
    ),
    (
        re.compile(r"(ndp472|dotnet.*4\.?7\.?2)", re.IGNORECASE),
        ["dotnet472"],
        ["/q", "/norestart"],
    ),
    (
        re.compile(r"(ndp462|dotnet.*4\.?6\.?2)", re.IGNORECASE),
        ["dotnet462"],
        ["/q", "/norestart"],
    ),
    (
        re.compile(r"(ndp461|dotnet.*4\.?6\.?1)", re.IGNORECASE),
        ["dotnet461"],
        ["/q", "/norestart"],
    ),
    (
        re.compile(r"(ndp46[^12]|ndp46$|dotnet.*4\.?6)", re.IGNORECASE),
        ["dotnet46"],
        ["/q", "/norestart"],
    ),
    (
        re.compile(r"(ndp452|dotnet.*4\.?5\.?2)", re.IGNORECASE),
        ["dotnet452"],
        ["/q", "/norestart"],
    ),
    (
        re.compile(r"(ndp45[^2]|ndp45$|dotnet.*4\.?5)", re.IGNORECASE),
        ["dotnet45"],
        ["/q", "/norestart"],
    ),
    (
        re.compile(r"(dotnetfx40|ndp40|dotnet.*4\.?0)", re.IGNORECASE),
        ["dotnet40"],
        ["/q", "/norestart"],
    ),
    (
        re.compile(r"(dotnetfx35|dotnet.*3\.?5)", re.IGNORECASE),
        ["dotnet35sp1"],
        ["/q", "/norestart"],
    ),
    # XNA Framework
    (
        re.compile(r"xnafx40", re.IGNORECASE),
        ["xna40"],
        ["/quiet"],
    ),
    (
        re.compile(r"xnafx31", re.IGNORECASE),
        ["xna31"],
        ["/quiet"],
    ),
    # PhysX
    (
        re.compile(r"physx.*\.(exe|msi)$", re.IGNORECASE),
        ["physx"],
        ["/quiet"],
    ),
    # OpenAL (local installer only, not in Bottles dependency repo)
    (
        re.compile(r"oalinst\.exe$", re.IGNORECASE),
        [],
        ["/s"],
    ),
]

# DLL import rules (lowercase DLL name -> Bottles dependency name)
DLL_IMPORT_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^msvc[rp]80\.dll$"), "vcredist2005"),
    (re.compile(r"^msvc[rp]90\.dll$"), "vcredist2008"),
    (re.compile(r"^msvc[rp]100\.dll$"), "vcredist2010"),
    (re.compile(r"^msvc[rp]110\.dll$"), "vcredist2012"),
    (re.compile(r"^msvc[rp]120\.dll$"), "vcredist2013"),
    (re.compile(r"^(vcruntime140|msvcp140).*\.dll$"), "vcredist2015"),
    (re.compile(r"^d3dx9_\d+\.dll$"), "d3dx9"),
    (re.compile(r"^d3dx1[01]_\d+\.dll$"), "d3dx11"),
    (re.compile(r"^d3dcompiler_43\.dll$"), "d3dcompiler_43"),
    (re.compile(r"^d3dcompiler_47\.dll$"), "d3dcompiler_47"),
    (re.compile(r"^xinput1_[1-3]\.dll$"), "xinput"),
    (re.compile(r"^(xactengine|x3daudio|xapofx|xaudio2_[0-7]).*\.dll$"), "xact"),
    (re.compile(r"^mscoree\.dll$"), "dotnet40"),
    (re.compile(r"^physx(loader|3).*\.dll$"), "physx"),
]

REDIST_DIR_NAMES = {
    "_redist",
    "redist",
    "_commonredist",
    "commonredist",
    "__installer",
    "redistributables",
}

IGNORED_EXE_PATTERNS = re.compile(
    r"(unins\d*|setup|install|vcredist|vc_redist|dxsetup|dxwebsetup|"
    r"dotnet|ndp\d+|xnafx|physx|oalinst|quicksfv|crash|report|updater|redist)",
    re.IGNORECASE,
)

EDITION_SUFFIX_PATTERN = re.compile(
    r"\s*(?:[-:–—]\s*)?\b("
    r"complete edition|game of the year edition|goty edition|goty|"
    r"deluxe edition|ultimate edition|definitive edition|director'?s cut|"
    r"remastered|gold edition|special edition|enhanced edition|"
    r"anniversary edition|collector'?s edition|standard edition|"
    r"fitgirl repack|dodi repack|repack"
    r")\b\s*$",
    re.IGNORECASE,
)


@dataclass
class RedistMatch:
    """A detected redistributable installer file on disk."""

    path: Path
    relative_path: str
    dependencies: list[str]
    silent_args: list[str]


@dataclass
class DiskEstimate:
    """Disk space calculation factoring in FitGirl repack expansion."""

    compressed_bytes: int
    optional_bytes: int
    expansion_factor: float
    installed_bytes: int
    bottle_base_bytes: int
    headroom_bytes: int
    required_bytes: int
    bin_files: list[str] = field(default_factory=list)
    optional_bin_files: list[str] = field(default_factory=list)


@dataclass
class ScanResult:
    """Complete scan output for a FitGirl installer or extracted game folder."""

    source_dir: Path
    game_name: str
    setup_exe: Optional[Path]
    is_inno_setup: bool
    redist_dirs: list[Path]
    redist_matches: list[RedistMatch]
    pe_imports: dict[str, list[str]]
    inferred_deps: list[str]
    dep_evidence: dict[str, list[str]]
    warnings: list[str]
    disk_estimate: DiskEstimate
    md5_file: Optional[Path] = None


def derive_game_name(folder_path: Path | str) -> str:
    """Derive a clean bottle / game name from a FitGirl repack folder name.

    Examples:
        'Example Game - Complete Edition [FitGirl Repack]' -> 'Example Game'
        'Cyberpunk 2077 (v2.12 + All DLCs) [FitGirl Repack]' -> 'Cyberpunk 2077'
        'Elden Ring - Deluxe Edition [FitGirl Repack]' -> 'Elden Ring'
    """
    raw = Path(folder_path).name.strip()
    if not raw:
        return "Game"

    # Strip bracketed and parenthesized release tags: [FitGirl Repack], (v1.2 + DLCs), etc.
    cleaned = re.sub(r"\s*\[[^\]]*\]", "", raw)
    cleaned = re.sub(r"\s*\([^)]*\)", "", cleaned)

    # Repeatedly strip trailing edition / repack suffixes.
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = EDITION_SUFFIX_PATTERN.sub("", cleaned).strip(" -–—:_")

    # Collapse multiple spaces and remove filesystem-hostile characters.
    cleaned = re.sub(r'[<>:"/\\|?*]+', "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or raw


def sanitize_bottle_dirname(bottle_name: str) -> str:
    """Match Bottles' directory naming convention (spaces -> hyphens)."""
    s = bottle_name.strip().replace(" ", "-")
    s = re.sub(r'[<>:"/\\|?*]+', "", s)
    return s or "Bottle"


def parse_size_string(size_str: str) -> int:
    """Parse human sizes like '22G', '15.5GB', '500M' into bytes."""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]?i?B?)?\s*$", size_str, re.IGNORECASE)
    if not m:
        raise ValueError(f"Invalid size specification: {size_str!r}")
    val = float(m.group(1))
    unit = (m.group(2) or "").upper().rstrip("B").rstrip("I")
    multipliers = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    return int(val * multipliers[unit])


def format_bytes(num_bytes: int) -> str:
    """Format a byte count into a human-readable GiB/MiB string."""
    if num_bytes >= 1024**3:
        return f"{num_bytes / (1024**3):.2f} GiB"
    if num_bytes >= 1024**2:
        return f"{num_bytes / (1024**2):.1f} MiB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KiB"
    return f"{num_bytes} B"


def detect_inno_setup(exe_path: Path) -> bool:
    """Check whether a Windows installer binary contains Inno Setup markers."""
    try:
        data = exe_path.read_bytes()
    except OSError:
        return False
    return b"Inno Setup" in data or b"JR.Inno.Setup" in data


def find_setup_exe(folder_path: Path) -> Optional[Path]:
    """Find the primary setup.exe in a repack folder."""
    if not folder_path.is_dir():
        return None

    # First pass: exact 'setup.exe' in root (case-insensitive)
    for entry in sorted(folder_path.iterdir()):
        if entry.is_file() and entry.name.lower() == "setup.exe":
            return entry

    # Second pass: any *setup*.exe or *install*.exe in root
    candidates = [
        entry
        for entry in sorted(folder_path.iterdir())
        if entry.is_file()
        and entry.suffix.lower() == ".exe"
        and re.search(r"(setup|install)", entry.name, re.IGNORECASE)
        and not re.search(r"(unins|quicksfv|redist)", entry.name, re.IGNORECASE)
    ]
    if candidates:
        return candidates[0]

    return None


def find_redist_dirs(root: Path, max_depth: int = 3) -> list[Path]:
    """Locate _Redist / Redist / _CommonRedist / __Installer directories."""
    found: list[Path] = []
    if not root.is_dir():
        return found

    root_depth = len(root.parts)
    for dirpath, dirnames, _ in os.walk(root):
        current = Path(dirpath)
        depth = len(current.parts) - root_depth
        if depth > max_depth:
            dirnames[:] = []
            continue
        for d in sorted(dirnames):
            if d.lower() in REDIST_DIR_NAMES:
                found.append(current / d)
    return found


def map_redist_file(rel_path: str) -> tuple[list[str], list[str]]:
    """Map a redistributable file's relative path to (bottles_deps, silent_args)."""
    normalized = rel_path.replace("\\", "/")
    for pattern, deps, silent_args in REDIST_FILE_RULES:
        if pattern.search(normalized):
            return list(deps), list(silent_args)
    return [], []


def scan_redist_dirs(redist_dirs: Iterable[Path], base_dir: Path) -> list[RedistMatch]:
    """Scan all redist directories for known runtime installers."""
    matches: list[RedistMatch] = []
    for rdir in redist_dirs:
        if not rdir.is_dir():
            continue
        for file_path in sorted(rdir.rglob("*")):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in {".exe", ".msi"}:
                continue
            try:
                rel = str(file_path.relative_to(base_dir))
            except ValueError:
                rel = file_path.name
            deps, silent_args = map_redist_file(rel)
            if deps or silent_args:
                matches.append(
                    RedistMatch(
                        path=file_path,
                        relative_path=rel,
                        dependencies=deps,
                        silent_args=silent_args,
                    )
                )
    return matches


def _rva_to_offset(rva: int, sections: list[tuple[int, int, int, int]]) -> Optional[int]:
    """Translate a PE Relative Virtual Address to a raw file offset."""
    for vaddr, vsize, raw_ptr, raw_size in sections:
        span = max(vsize, raw_size)
        if vaddr <= rva < vaddr + span:
            return raw_ptr + (rva - vaddr)
    return None


def parse_pe_imports(pe_path: Path) -> list[str]:
    """Minimal pure-Python PE32 / PE32+ import table parser (no pefile required).

    Returns a sorted list of lowercase imported DLL names. Returns [] for
    non-PE files or binaries without an import directory.
    """
    try:
        data = pe_path.read_bytes()
    except OSError:
        return []

    if len(data) < 0x40 or data[:2] != b"MZ":
        return []

    (pe_offset,) = struct.unpack_from("<I", data, 0x3C)
    if pe_offset + 24 > len(data) or data[pe_offset : pe_offset + 4] != b"PE\x00\x00":
        return []

    # COFF File Header (20 bytes after PE signature)
    _, num_sections, _, _, _, opt_header_size, _ = struct.unpack_from(
        "<HHIIIHH", data, pe_offset + 4
    )
    opt_offset = pe_offset + 24
    if opt_offset + opt_header_size > len(data) or opt_header_size < 2:
        return []

    (magic,) = struct.unpack_from("<H", data, opt_offset)
    if magic == 0x10B:  # PE32
        data_dir_offset = opt_offset + 96
    elif magic == 0x20B:  # PE32+ (64-bit)
        data_dir_offset = opt_offset + 112
    else:
        return []

    # Import Directory is DataDirectory[1] (offset +8 from start of DataDirectory)
    if data_dir_offset + 16 > opt_offset + opt_header_size:
        return []
    import_rva, import_size = struct.unpack_from("<II", data, data_dir_offset + 8)
    if import_rva == 0 or import_size == 0:
        return []

    # Section Table immediately follows Optional Header
    sec_offset = opt_offset + opt_header_size
    sections: list[tuple[int, int, int, int]] = []
    for i in range(num_sections):
        entry_off = sec_offset + i * 40
        if entry_off + 40 > len(data):
            break
        vsize, vaddr, raw_size, raw_ptr = struct.unpack_from(
            "<IIII", data, entry_off + 8
        )
        sections.append((vaddr, vsize, raw_ptr, raw_size))

    imp_file_off = _rva_to_offset(import_rva, sections)
    if imp_file_off is None:
        return []

    dlls: set[str] = set()
    pos = imp_file_off
    while pos + 20 <= len(data):
        orig_first_thunk, timestamp, fwd_chain, name_rva, first_thunk = (
            struct.unpack_from("<IIIII", data, pos)
        )
        if orig_first_thunk == 0 and name_rva == 0 and first_thunk == 0:
            break
        name_off = _rva_to_offset(name_rva, sections)
        if name_off is not None and name_off < len(data):
            end = data.find(b"\x00", name_off, min(len(data), name_off + 260))
            if end != -1:
                raw_name = data[name_off:end]
                try:
                    dll_name = raw_name.decode("ascii").strip().lower()
                except UnicodeDecodeError:
                    dll_name = ""
                if dll_name:
                    dlls.add(dll_name)
        pos += 20

    return sorted(dlls)


def map_dll_imports_to_deps(
    dll_names: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Map imported DLL names to (bottles_deps, warnings)."""
    deps: list[str] = []
    warnings: list[str] = []
    for dll in dll_names:
        low = dll.lower()
        if low == "xlive.dll":
            warnings.append(
                "Binary imports xlive.dll (Games for Windows Live); verify whether a GFWL-free patch is included."
            )
            continue
        for pattern, dep in DLL_IMPORT_RULES:
            if pattern.match(low):
                if dep not in deps:
                    deps.append(dep)
                break
    return deps, warnings


def calculate_disk_space(
    folder_path: Path,
    expansion_factor: float = 1.6,
    with_optional: bool = False,
    bottle_base_bytes: int = 1288490188,  # ~1.2 GiB measured Gaming template
    installed_size_override: Optional[int] = None,
) -> DiskEstimate:
    """Calculate required disk space factoring in FitGirl repack expansion."""
    bin_files: list[str] = []
    optional_bin_files: list[str] = []
    compressed = 0
    optional = 0

    if folder_path.is_dir():
        all_files = [p for p in sorted(folder_path.iterdir()) if p.is_file()]
        fg_bins = [
            p for p in all_files if p.name.lower().startswith("fg-") and p.suffix.lower() == ".bin"
        ]
        if fg_bins:
            for p in fg_bins:
                size = p.stat().st_size
                if "optional" in p.name.lower():
                    optional += size
                    optional_bin_files.append(p.name)
                else:
                    compressed += size
                    bin_files.append(p.name)
        else:
            # Non-standard repack layout: sum all files in root
            for p in all_files:
                size = p.stat().st_size
                compressed += size
                bin_files.append(p.name)

    effective_compressed = compressed + (optional if with_optional else 0)
    installed = (
        installed_size_override
        if installed_size_override is not None
        else int(effective_compressed * expansion_factor)
    )
    headroom = int(installed * 0.10)
    required = installed + bottle_base_bytes + headroom

    return DiskEstimate(
        compressed_bytes=compressed,
        optional_bytes=optional,
        expansion_factor=expansion_factor,
        installed_bytes=installed,
        bottle_base_bytes=bottle_base_bytes,
        headroom_bytes=headroom,
        required_bytes=required,
        bin_files=bin_files,
        optional_bin_files=optional_bin_files,
    )


def find_game_executables(install_dir: Path, game_name: str) -> list[Path]:
    """Find likely main game .exe files in an extracted game directory, ranked best-first."""
    if not install_dir.is_dir():
        return []

    tokens = [t.lower() for t in re.findall(r"[a-zA-Z0-9]+", game_name) if len(t) >= 2]
    scored: list[tuple[int, Path]] = []

    for exe in install_dir.rglob("*"):
        if not exe.is_file() or exe.suffix.lower() != ".exe":
            continue
        rel = exe.relative_to(install_dir)
        rel_str = str(rel).replace("\\", "/")
        if any(part.lower() in REDIST_DIR_NAMES for part in rel.parts[:-1]):
            continue
        if IGNORED_EXE_PATTERNS.search(exe.stem):
            continue

        score = 0
        # Prefer executables closer to the game root
        depth = len(rel.parts) - 1
        score += max(0, 30 - depth * 10)

        stem_low = exe.stem.lower()
        # Exact or near-exact match with game name
        compact_name = re.sub(r"[^a-z0-9]", "", game_name.lower())
        compact_stem = re.sub(r"[^a-z0-9]", "", stem_low)
        if compact_name and compact_stem == compact_name:
            score += 100
        elif compact_name and (compact_name in compact_stem or compact_stem in compact_name):
            score += 60
        for tok in tokens:
            if tok in stem_low:
                score += 15
        if "launcher" in stem_low:
            score -= 5
        # Slightly prefer larger binaries (real game exes vs tiny shims)
        try:
            size_mb = exe.stat().st_size / (1024 * 1024)
            score += min(20, int(size_mb))
        except OSError:
            pass
        scored.append((score, exe))

    scored.sort(key=lambda item: (-item[0], str(item[1])))
    return [p for _, p in scored]


def verify_md5_bins(folder_path: Path) -> list[tuple[str, bool, str]]:
    """Verify FitGirl MD5/fitgirl-bins.md5 checksums if present."""
    md5_path = folder_path / "MD5" / "fitgirl-bins.md5"
    if not md5_path.is_file():
        return []

    results: list[tuple[str, bool, str]] = []
    for raw_line in md5_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue
        m = re.match(r"^([0-9a-fA-F]{32})\s+\*?(.+)$", line)
        if not m:
            continue
        expected_hash = m.group(1).lower()
        rel_win = m.group(2).strip().replace("\\", "/")
        target = (md5_path.parent / rel_win).resolve()
        if not target.is_file():
            results.append((target.name, False, "missing (may be optional/selective)"))
            continue
        h = hashlib.md5()
        with target.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        actual = h.hexdigest().lower()
        ok = actual == expected_hash
        results.append((target.name, ok, "OK" if ok else f"mismatch ({actual} != {expected_hash})"))
    return results


def scan_folder(
    folder_path: Path | str,
    game_name: Optional[str] = None,
    expansion_factor: float = 1.6,
    with_optional: bool = False,
    bottle_base_bytes: int = 1288490188,
    installed_size_override: Optional[int] = None,
    scan_pe_binaries: bool = True,
) -> ScanResult:
    """Scan a FitGirl installer directory or an extracted game directory."""
    source = Path(folder_path).expanduser().resolve()
    derived_name = game_name or derive_game_name(source)
    setup_exe = find_setup_exe(source)
    is_inno = detect_inno_setup(setup_exe) if setup_exe else False

    redist_dirs = find_redist_dirs(source)
    redist_matches = scan_redist_dirs(redist_dirs, source)

    dep_evidence: dict[str, list[str]] = {}
    warnings: list[str] = []

    for match in redist_matches:
        for dep in match.dependencies:
            dep_evidence.setdefault(dep, []).append(f"redist:{match.relative_path}")

    pe_imports: dict[str, list[str]] = {}
    if scan_pe_binaries and source.is_dir():
        # Scan top-level and 1-level-deep game executables/DLLs (skip setup.exe & redist dirs)
        for p in sorted(source.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in {".exe", ".dll"}:
                continue
            rel = p.relative_to(source)
            if len(rel.parts) > 3:
                continue
            if any(part.lower() in REDIST_DIR_NAMES for part in rel.parts[:-1]):
                continue
            if setup_exe and p.resolve() == setup_exe.resolve():
                continue
            if IGNORED_EXE_PATTERNS.search(p.stem):
                continue
            dlls = parse_pe_imports(p)
            if dlls:
                pe_imports[str(rel)] = dlls
                deps, warns = map_dll_imports_to_deps(dlls)
                for dep in deps:
                    dep_evidence.setdefault(dep, []).append(f"pe-import:{rel}")
                for w in warns:
                    if w not in warnings:
                        warnings.append(w)

    if not redist_dirs and setup_exe is not None:
        warnings.append(
            "No _Redist folder found alongside setup.exe (normal for FitGirl repacks — "
            "redistributables are packed inside fg-*.bin and will be scanned post-extraction)."
        )

    md5_candidate = source / "MD5" / "fitgirl-bins.md5"
    disk_estimate = calculate_disk_space(
        source,
        expansion_factor=expansion_factor,
        with_optional=with_optional,
        bottle_base_bytes=bottle_base_bytes,
        installed_size_override=installed_size_override,
    )

    inferred_deps = sorted(dep_evidence.keys())
    return ScanResult(
        source_dir=source,
        game_name=derived_name,
        setup_exe=setup_exe,
        is_inno_setup=is_inno,
        redist_dirs=redist_dirs,
        redist_matches=redist_matches,
        pe_imports=pe_imports,
        inferred_deps=inferred_deps,
        dep_evidence=dep_evidence,
        warnings=warnings,
        disk_estimate=disk_estimate,
        md5_file=md5_candidate if md5_candidate.is_file() else None,
    )
