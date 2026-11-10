"""Detecting this Linux PC's hardware and Steam library drives."""

from __future__ import annotations

import glob
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GPUInfo:
    name: str                      # name used for matching / display
    vendor: str                    # nvidia | amd | intel | other
    integrated: bool | None = None
    vram_gb: float | None = None
    vulkan: bool | None = None     # None = couldn't check
    vulkan_version: str | None = None
    pci_ids: tuple[str, str] | None = None   # (vendor_id, device_id), lowercase hex
    pci_address: str | None = None
    name_ambiguous: bool = False   # e.g. lspci said "RX 6600/6600 XT/6600M"
    source: str = ""


@dataclass
class LibraryInfo:
    path: str
    free_gb: float
    total_gb: float
    ssd: bool | None
    device: str | None
    app_ids: set[int] = field(default_factory=set)


@dataclass
class SystemSpecs:
    cpu_name: str = "Unknown CPU"
    cpu_cores: int | None = None
    cpu_threads: int | None = None
    cpu_max_ghz: float | None = None
    ram_usable_gb: float | None = None
    ram_installed_gb: float | None = None
    gpus: list[GPUInfo] = field(default_factory=list)
    arch: str = ""
    is_64bit: bool = True
    is_laptop: bool | None = None
    device_label: str | None = None    # e.g. "Steam Deck"
    vulkaninfo_available: bool = False
    steam_root: str | None = None
    libraries: list[LibraryInfo] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return None


def _run(cmd: list[str], timeout: int = 20) -> str | None:
    if not shutil.which(cmd[0]):
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.stdout else None


# --------------------------------------------------------------------------- #
# CPU / RAM / platform
# --------------------------------------------------------------------------- #

def _detect_cpu(specs: SystemSpecs) -> None:
    info = _read("/proc/cpuinfo") or ""
    m = re.search(r"^model name\s*:\s*(.+)$", info, re.M)
    name = m.group(1).strip() if m else None
    if not name:
        out = _run(["lscpu"]) or ""
        m = re.search(r"^Model name:\s*(.+)$", out, re.M)
        name = m.group(1).strip() if m else None
    if name:
        specs.cpu_name = re.sub(r"\s+", " ", name)
    cores = set()
    phys = core = None
    for line in info.splitlines():
        if line.startswith("physical id"):
            phys = line.split(":")[1].strip()
        elif line.startswith("core id"):
            core = line.split(":")[1].strip()
        elif not line.strip():
            if core is not None:
                cores.add((phys, core))
            phys = core = None
    if core is not None:
        cores.add((phys, core))
    specs.cpu_threads = os.cpu_count()
    specs.cpu_cores = len(cores) or specs.cpu_threads
    freqs = []
    for f in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq"):
        try:
            freqs.append(int(_read(f) or "0"))
        except ValueError:
            pass
    if freqs and max(freqs) > 0:
        specs.cpu_max_ghz = max(freqs) / 1e6


_RAM_SIZES = [1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48, 64, 96, 128, 192, 256, 384, 512]


def installed_ram_estimate(usable_gb: float) -> float:
    """The kernel reports a bit less than what's installed (firmware and
    integrated graphics reserve some).  Round up to a common module size,
    but don't jump more than ~20%."""
    for size in _RAM_SIZES:
        if size >= usable_gb - 0.05:
            return float(size) if size <= usable_gb * 1.2 + 0.5 else round(usable_gb, 1)
    return round(usable_gb, 1)


def _detect_ram(specs: SystemSpecs) -> None:
    info = _read("/proc/meminfo") or ""
    m = re.search(r"^MemTotal:\s*(\d+)\s*kB", info, re.M)
    if m:
        specs.ram_usable_gb = int(m.group(1)) / 1024 / 1024
        specs.ram_installed_gb = installed_ram_estimate(specs.ram_usable_gb)


def _detect_platform(specs: SystemSpecs) -> None:
    specs.arch = platform.machine()
    specs.is_64bit = specs.arch.lower() in ("x86_64", "amd64") and sys.maxsize > 2**32
    chassis = (_read("/sys/class/dmi/id/chassis_type") or "").strip()
    product = (_read("/sys/class/dmi/id/product_name") or "").strip()
    if product in ("Jupiter", "Galileo"):
        specs.device_label = "Steam Deck"
        specs.is_laptop = True
    elif chassis.isdigit():
        specs.is_laptop = int(chassis) in (8, 9, 10, 11, 14, 30, 31, 32)


# --------------------------------------------------------------------------- #
# GPU
# --------------------------------------------------------------------------- #

_VENDORS = {"10de": "nvidia", "1002": "amd", "1022": "amd", "8086": "intel"}

# Mesa reports some integrated GPUs by a generic name plus a codename, e.g.
# "AMD Radeon Graphics (RADV RENOIR)".  Map the codename to a table entry.
_AMD_CODENAMES = {
    "RAVEN": "AMD Radeon Vega 8", "RAVEN2": "AMD Radeon Vega 3", "PICASSO": "AMD Radeon Vega 8",
    "RENOIR": "AMD Radeon Vega 7", "LUCIENNE": "AMD Radeon Vega 7", "CEZANNE": "AMD Radeon Vega 8",
    "BARCELO": "AMD Radeon Vega 8", "REMBRANDT": "AMD Radeon 680M", "YELLOW_CARP": "AMD Radeon 680M",
    "PHOENIX": "AMD Radeon 780M", "HAWK_POINT": "AMD Radeon 780M", "GFX1103_R1": "AMD Radeon 780M",
    "STRIX_POINT": "AMD Radeon 890M", "GFX1150": "AMD Radeon 890M",
    "RAPHAEL_MENDOCINO": "AMD Radeon 610M", "MENDOCINO": "AMD Radeon 610M", "RAPHAEL": "AMD Radeon 610M",
    "VANGOGH": "Steam Deck GPU",
}
_INTEL_CODENAMES = {
    "TGL": "Intel Iris Xe Graphics", "ADL": "Intel Iris Xe Graphics", "RPL": "Intel Iris Xe Graphics",
    "MTL": "Intel Arc Graphics", "ARL": "Intel Arc Graphics", "LNL": "Intel Arc 140V",
    "CML": "Intel UHD Graphics 620", "KBL": "Intel UHD Graphics 620", "CFL": "Intel UHD Graphics 630",
    "SKL": "Intel HD Graphics 520", "ICL": "Intel Iris Plus Graphics G7", "JSL": "Intel UHD Graphics 600",
}


def friendly_gpu_name(raw: str) -> str:
    """'AMD Radeon RX 6600 (RADV NAVI23)' -> 'AMD Radeon RX 6600'; generic
    integrated names are replaced using the driver codename."""
    raw = re.sub(r"\s+", " ", raw).strip()
    m = re.search(r"\((?:RADV|NVK|ANV|LLVM)?\s*([A-Z0-9_\-]+)[^)]*\)\s*$", raw)
    code = m.group(1).upper().replace("-", "_") if m else ""
    base = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()
    generic_amd = re.fullmatch(r"(AMD )?(Radeon )?(Graphics|Radeon Graphics|Custom GPU.*)", base, re.I)
    generic_intel = re.fullmatch(r"Intel(\(R\))? (Graphics|UHD Graphics|Iris\(R\) Graphics)", base, re.I)
    if code:
        for key, name in _AMD_CODENAMES.items():
            if (generic_amd or "Custom GPU" in base) and code.startswith(key):
                return name
        if generic_intel:
            for key, name in _INTEL_CODENAMES.items():
                if code.startswith(key):
                    return name
    return base


def _parse_vulkaninfo_summary(out: str) -> list[dict]:
    devices = []
    for block in re.split(r"\n\s*GPU\d+:\s*\n", out)[1:]:
        d = {}
        for line in block.splitlines():
            m = re.match(r"\s*(\w+)\s*=\s*(.+)$", line)
            if m:
                d[m.group(1)] = m.group(2).strip()
        if d:
            devices.append(d)
    return devices


def _parse_lspci(out: str) -> list[dict]:
    devices = []
    for line in out.splitlines():
        try:
            parts = shlex.split(line)
        except ValueError:
            continue
        if len(parts) < 4:
            continue
        addr, cls, vendor, device = parts[0], parts[1], parts[2], parts[3]
        if not re.search(r"\[03(00|02|80)\]", cls):
            continue
        vm = re.search(r"\[([0-9a-f]{4})\]\s*$", vendor)
        dm = re.search(r"\[([0-9a-f]{4})\]\s*$", device)
        devices.append({
            "address": addr if addr.count(":") == 2 else f"0000:{addr}",
            "vendor_id": vm.group(1) if vm else "",
            "device_id": dm.group(1) if dm else "",
            "vendor_name": re.sub(r"\s*\[[0-9a-f]{4}\]\s*$", "", vendor),
            "device_name": re.sub(r"\s*\[[0-9a-f]{4}\]\s*$", "", device),
        })
    return devices


_LSPCI_INTEL = {
    "COMETLAKE": "Intel UHD Graphics 620", "COFFEELAKE": "Intel UHD Graphics 630",
    "KABYLAKE": "Intel UHD Graphics 620", "WHISKEYLAKE": "Intel UHD Graphics 620",
    "SKYLAKE": "Intel HD Graphics 520", "TIGERLAKE": "Intel Iris Xe Graphics",
    "ALDERLAKE": "Intel Iris Xe Graphics", "RAPTORLAKE": "Intel Iris Xe Graphics",
    "METEORLAKE": "Intel Arc Graphics", "ARROWLAKE": "Intel Arc Graphics",
    "LUNARLAKE": "Intel Arc 140V", "ICELAKE": "Intel Iris Plus Graphics G7",
    "JASPERLAKE": "Intel UHD Graphics 600",
}


def _lspci_friendly(dev: dict) -> tuple[str, bool, bool | None]:
    """'GA106 [GeForce RTX 3060 Lite Hash Rate]' -> 'NVIDIA GeForce RTX 3060'.

    Returns (name, name_is_ambiguous, integrated)."""
    name = dev["device_name"]
    vendor = _VENDORS.get(dev["vendor_id"], "other")
    code = re.sub(r"[^A-Z0-9]", "", name.split("[")[0].upper())
    if vendor == "amd":
        for key, mapped in _AMD_CODENAMES.items():
            if code.startswith(key.replace("_", "")):
                return mapped, False, True
    m = re.search(r"\[([^\]]+)\]\s*$", name)
    model = m.group(1) if m else name
    ambiguous = "/" in model
    if ambiguous:  # "Radeon RX 6600/6600 XT/6600M" -> first variant
        model = model.split("/")[0].strip()
    integrated = False if vendor in ("nvidia", "amd") else None
    if vendor == "intel":
        integrated = "arc a" not in model.lower() and "arc b" not in model.lower()
        if integrated and not re.search(r"\d", model):
            for key, mapped in _LSPCI_INTEL.items():
                if code.startswith(key):
                    return mapped, True, True
    prefix = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel"}.get(vendor, dev["vendor_name"])
    if not model.lower().startswith(prefix.lower()):
        model = f"{prefix} {model}"
    return model.replace(" Lite Hash Rate", ""), ambiguous, integrated


def _vram_from_sysfs(address: str | None) -> float | None:
    if not address:
        return None
    val = _read(f"/sys/bus/pci/devices/{address}/mem_info_vram_total")
    try:
        return int(val) / 1024**3 if val else None
    except ValueError:
        return None


def _nvidia_smi() -> dict[str, tuple[str, float]]:
    out = _run(["nvidia-smi", "--query-gpu=pci.bus_id,name,memory.total",
                "--format=csv,noheader,nounits"]) or ""
    result = {}
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3:
            bus = parts[0].lower()
            bus = bus[-12:] if len(bus) > 12 else bus   # 00000000:01:00.0 -> 0000:01:00.0
            try:
                result[bus] = (parts[1], float(parts[2]) / 1024)
            except ValueError:
                pass
    return result


def _vram_from_vulkaninfo_full() -> dict[str, float]:
    """Largest device-local memory heap per GPU name, from full `vulkaninfo`."""
    out = _run(["vulkaninfo"], timeout=30) or ""
    result: dict[str, float] = {}
    device = None
    heap_size = None
    for line in out.splitlines():
        m = re.match(r"\s*deviceName\s*=\s*(.+)$", line)
        if m:
            device = m.group(1).strip()
            continue
        if re.match(r"\s*memoryHeaps\[\d+\]", line) or re.match(r"\s*memoryTypes", line):
            heap_size = None
        m = re.match(r"\s*size\s*=\s*(\d+)", line)
        if m:
            heap_size = int(m.group(1))
            continue
        if "MEMORY_HEAP_DEVICE_LOCAL_BIT" in line and device and heap_size:
            result[device] = max(result.get(device, 0.0), heap_size / 1024**3)
            heap_size = None
    return result


def _detect_gpus(specs: SystemSpecs) -> None:
    vk_out = _run(["vulkaninfo", "--summary"])
    specs.vulkaninfo_available = shutil.which("vulkaninfo") is not None
    vk_devices = [d for d in _parse_vulkaninfo_summary(vk_out or "")
                  if "CPU" not in d.get("deviceType", "")]
    pci_devices = _parse_lspci(_run(["lspci", "-mm", "-nn"]) or "")
    smi = _nvidia_smi()
    gpus: list[GPUInfo] = []
    used_pci = set()

    for d in vk_devices:
        vid = d.get("vendorID", "").lower().replace("0x", "").zfill(4)
        did = d.get("deviceID", "").lower().replace("0x", "").zfill(4)
        pci = next((p for p in pci_devices if p["vendor_id"] == vid and p["device_id"] == did
                    and p["address"] not in used_pci), None)
        if pci:
            used_pci.add(pci["address"])
        ver = re.search(r"(\d+\.\d+\.\d+)", d.get("apiVersion", ""))
        gpus.append(GPUInfo(
            name=friendly_gpu_name(d.get("deviceName", "Unknown GPU")),
            vendor=_VENDORS.get(vid, "other"),
            integrated="INTEGRATED" in d.get("deviceType", ""),
            vulkan=True,
            vulkan_version=ver.group(1) if ver else None,
            pci_ids=(vid, did),
            pci_address=pci["address"] if pci else None,
            source="vulkaninfo",
        ))

    for p in pci_devices:
        if p["address"] in used_pci:
            continue
        name, ambiguous, integrated = _lspci_friendly(p)
        vendor = _VENDORS.get(p["vendor_id"], "other")
        gpus.append(GPUInfo(
            name=name,
            vendor=vendor,
            integrated=integrated,
            vulkan=False if (vk_out and vk_devices) else None,
            pci_ids=(p["vendor_id"], p["device_id"]),
            pci_address=p["address"],
            name_ambiguous=ambiguous,
            source="lspci",
        ))

    if not gpus and smi:  # no lspci/vulkaninfo, but the NVIDIA driver is there
        for bus, (name, vram) in smi.items():
            gpus.append(GPUInfo(name=name, vendor="nvidia", integrated=False, vram_gb=vram,
                                pci_address=bus, source="nvidia-smi"))

    # VRAM
    need_full = False
    for g in gpus:
        if g.vram_gb is None and g.pci_address:
            if g.pci_address.lower() in smi:
                smi_name, g.vram_gb = smi[g.pci_address.lower()]
                if g.source == "lspci":
                    g.name = smi_name
            else:
                g.vram_gb = _vram_from_sysfs(g.pci_address)
        if g.integrated and g.vram_gb is not None and g.vram_gb < 2.1:
            g.vram_gb = None  # small BIOS carve-out; the iGPU really shares system RAM
        if g.vram_gb is None and not g.integrated and g.vulkan:
            need_full = True
    if need_full:
        heaps = _vram_from_vulkaninfo_full()
        for g in gpus:
            if g.vram_gb is None and not g.integrated:
                for vk_name, size in heaps.items():
                    if friendly_gpu_name(vk_name) == g.name:
                        g.vram_gb = size
    specs.gpus = gpus
    if not specs.vulkaninfo_available:
        specs.notes.append(
            "vulkaninfo isn't installed, so Vulkan support couldn't be checked. "
            "Install your distro's vulkan-tools package."
        )
    if not pci_devices and not shutil.which("lspci"):
        specs.notes.append("lspci isn't installed (package pciutils); GPU detection may be incomplete.")


# --------------------------------------------------------------------------- #
# Steam libraries
# --------------------------------------------------------------------------- #

STEAM_ROOTS = (
    "~/.steam/steam",
    "~/.steam/root",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
    "~/snap/steam/common/.local/share/Steam",
)


def parse_vdf(text: str) -> dict:
    """Minimal parser for Valve's KeyValues text format."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    pos = 0

    def parse_block() -> dict:
        nonlocal pos
        out: dict = {}
        while pos < len(tokens):
            s, brace = tokens[pos]
            if brace == "}":
                pos += 1
                return out
            if brace == "{":  # stray brace
                pos += 1
                continue
            key = s
            pos += 1
            if pos >= len(tokens):
                break
            s2, brace2 = tokens[pos]
            if brace2 == "{":
                pos += 1
                out[key] = parse_block()
            else:
                out[key] = s2.replace("\\\\", "\\")
                pos += 1
        return out

    return parse_block()


def _block_device_is_ssd(path: str) -> tuple[bool | None, str | None]:
    """Find the disk behind `path` and read its 'rotational' flag."""
    try:
        target = os.path.realpath(path)
        best, source = "", None
        for line in (_read("/proc/self/mountinfo") or "").splitlines():
            parts = line.split()
            if " - " not in line or len(parts) < 5:
                continue
            mnt = parts[4].replace("\\040", " ")
            after = line.split(" - ", 1)[1].split()
            if (target == mnt or target.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best):
                best, source = mnt, after[1] if len(after) > 1 else None
        if not source or not source.startswith("/dev/"):
            return None, source
        dev = os.path.basename(os.path.realpath(source))
        return _rotational(dev), source
    except OSError:
        return None, None


def _rotational(dev: str, depth: int = 0) -> bool | None:
    sys_path = Path(f"/sys/class/block/{dev}")
    if not sys_path.exists() or depth > 4:
        return None
    real = sys_path.resolve()
    if (real / "partition").exists():
        real = real.parent
    rot = _read(str(real / "queue/rotational"))
    slaves = list((real / "slaves").glob("*")) if (real / "slaves").exists() else []
    if slaves:  # LVM / LUKS / RAID: SSD only if every member is
        results = [_rotational(s.name, depth + 1) for s in slaves]
        if any(r is None for r in results):
            return None
        return all(results)
    if rot is None:
        return None
    return rot.strip() == "0"


def _detect_libraries(specs: SystemSpecs) -> None:
    seen_paths = set()
    for root in STEAM_ROOTS:
        root_path = Path(os.path.expanduser(root))
        vdf_path = root_path / "steamapps" / "libraryfolders.vdf"
        if not vdf_path.exists():
            vdf_path = root_path / "config" / "libraryfolders.vdf"
        if not vdf_path.exists():
            continue
        specs.steam_root = specs.steam_root or str(root_path.resolve())
        try:
            data = parse_vdf(vdf_path.read_text(errors="replace"))
        except OSError:
            continue
        folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
        entries = []
        for key, val in folders.items():
            if isinstance(val, dict) and "path" in val:
                apps = val.get("apps") if isinstance(val.get("apps"), dict) else {}
                entries.append((val["path"], {int(a) for a in apps if a.isdigit()}))
            elif key.isdigit() and isinstance(val, str):  # old format
                entries.append((val, set()))
        if not entries:
            entries.append((str(root_path), set()))
        for path, apps in entries:
            real = os.path.realpath(path)
            if real in seen_paths or not os.path.isdir(real):
                continue
            seen_paths.add(real)
            try:
                usage = shutil.disk_usage(real)
            except OSError:
                continue
            ssd, device = _block_device_is_ssd(real)
            installed = set(apps)
            for manifest in glob.glob(os.path.join(real, "steamapps", "appmanifest_*.acf")):
                m = re.search(r"appmanifest_(\d+)\.acf$", manifest)
                if m:
                    installed.add(int(m.group(1)))
            specs.libraries.append(LibraryInfo(
                path=path, free_gb=usage.free / 1024**3, total_gb=usage.total / 1024**3,
                ssd=ssd, device=device, app_ids=installed,
            ))
    if not specs.libraries:
        specs.notes.append("No Steam library folders were found (is Steam installed for this user?).")


def detect() -> SystemSpecs:
    specs = SystemSpecs()
    _detect_platform(specs)
    _detect_cpu(specs)
    _detect_ram(specs)
    _detect_gpus(specs)
    _detect_libraries(specs)
    return specs
