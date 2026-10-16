"""Hardware census — "let's see what we're working with here."

The first step of the BuildBot wizard: look at the machine and report, plainly,
what it is. RAM is the binding constraint (RAM-first fitting), so it is the
number everything else keys off; CPU, accelerator, disk and board identity round
out the picture. The brain menu (:mod:`puffpai.model_catalog`) consumes this to
decide which local models actually fit.

Two hard rules shape every function here:

* **Dependency-free.** Detection uses only the standard library so the census
  runs on a bare install. ``psutil`` is used as a fast path *if it happens to be
  present*, never required.
* **Never raises, always answers.** Every probe degrades to ``None`` and a
  recorded caveat rather than an exception. Where detection fails the wizard
  falls back to asking the owner (:func:`apply_stated`), and a stated answer is
  first-class — recorded as ``source: "stated"``, not a degraded mode.

Nothing here is a benchmark. The census reports *capacity* (how much RAM, which
accelerator); measured speed lives in the model catalog, and unbenched hardware
says "untested — reports welcome" there, never here.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess

from puffpai import procflags
from typing import Any, Optional

SCHEMA = "hardware_census/v1"

_BYTES_PER_GB = 1024 ** 3

# Reserve headroom the model may not eat: the OS, PuffPAI itself, the embedding
# model and the knowledge index cache all need to live alongside the brain. We
# hold back 20% of RAM, clamped to a sane floor/ceiling, and fit models against
# what is left. This is what "fits your 64GB with room for the library" means.
_RESERVE_FRACTION = 0.20
_RESERVE_MIN_GB = 2.0
_RESERVE_MAX_GB = 8.0


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _bytes_to_gb(n: Optional[int]) -> Optional[float]:
    """Bytes -> GiB rounded to one decimal, or None."""
    if n is None or n <= 0:
        return None
    return round(n / _BYTES_PER_GB, 1)


def _run(cmd: list[str], timeout: int = 3) -> Optional[str]:
    """Run a probe command, returning stripped stdout or None on any failure.

    Best-effort and silent: a missing binary, non-zero exit, or timeout all
    collapse to None. Never raises.
    """
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
            **procflags.hidden(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    return out or None


def _psutil() -> Any:
    """Return the psutil module if importable, else None (optional fast path)."""
    try:
        import psutil  # type: ignore
        return psutil
    except Exception:
        return None


# ---------------------------------------------------------------------------
# memory (the binding constraint)
# ---------------------------------------------------------------------------

def _total_ram_bytes() -> Optional[int]:
    """Total physical RAM in bytes, tried across platforms. None if unknown."""
    ps = _psutil()
    if ps is not None:
        try:
            return int(ps.virtual_memory().total)
        except Exception:
            pass

    # POSIX (Linux + macOS): pages * page size. os.sysconf is absent on Windows,
    # so reach it through getattr — that keeps this branch off the Windows path
    # at both runtime and type-check time.
    sysconf = getattr(os, "sysconf", None)
    sysconf_names = getattr(os, "sysconf_names", {})
    if callable(sysconf) and "SC_PHYS_PAGES" in sysconf_names:
        try:
            pages = sysconf("SC_PHYS_PAGES")
            page_size = sysconf("SC_PAGE_SIZE")
            if pages > 0 and page_size > 0:
                return int(pages) * int(page_size)
        except (ValueError, OSError):
            pass

    if platform.system() == "Windows":
        win = _windows_mem_bytes()
        if win is not None:
            return win[0]

    # Linux fallback: parse /proc/meminfo directly.
    meminfo = _read_file("/proc/meminfo")
    if meminfo:
        match = re.search(r"^MemTotal:\s+(\d+)\s+kB", meminfo, re.MULTILINE)
        if match:
            return int(match.group(1)) * 1024
    return None


def _windows_mem_bytes() -> Optional[tuple[int, int]]:
    """(total, available) physical RAM on Windows via GlobalMemoryStatusEx.

    Uses ctypes only (stdlib), so it needs no third-party package. None on any
    failure or off-Windows.
    """
    try:
        import ctypes

        class _MemStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MemStatusEx()
        stat.dwLength = ctypes.sizeof(_MemStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
            return int(stat.ullTotalPhys), int(stat.ullAvailPhys)
    except Exception:
        return None
    return None


def _available_ram_bytes() -> Optional[int]:
    """Currently-available RAM in bytes (best effort), or None."""
    ps = _psutil()
    if ps is not None:
        try:
            return int(ps.virtual_memory().available)
        except Exception:
            pass

    if platform.system() == "Windows":
        win = _windows_mem_bytes()
        if win is not None:
            return win[1]

    meminfo = _read_file("/proc/meminfo")
    if meminfo:
        match = re.search(r"^MemAvailable:\s+(\d+)\s+kB", meminfo, re.MULTILINE)
        if match:
            return int(match.group(1)) * 1024
    return None


# ---------------------------------------------------------------------------
# cpu / soc
# ---------------------------------------------------------------------------

def _read_file(path: str) -> Optional[str]:
    """Read a text file, returning None on any failure (never raises)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except (OSError, ValueError):
        return None


def _cpu_model() -> Optional[str]:
    """Human-readable CPU/SoC model string, best effort per platform."""
    system = platform.system()

    if system == "Linux":
        cpuinfo = _read_file("/proc/cpuinfo")
        if cpuinfo:
            # x86 exposes "model name"; many ARM boards expose "Model" instead.
            for key in ("model name", "Hardware", "Model"):
                match = re.search(rf"^{key}\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
                if match:
                    return match.group(1).strip()

    if system == "Darwin":
        brand = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if brand:
            return brand

    if system == "Windows":
        # The marketing name ("13th Gen Intel(R) Core(TM) i7-13850HX") is in
        # the registry; the first screen shows it to the owner, and the family
        # string below ("Intel64 Family 6 Model 183") means nothing to anyone.
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
                                ) as key:
                name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0])
            if name.strip():
                return " ".join(name.split())
        except (ImportError, OSError, ValueError):
            pass
        # PROCESSOR_IDENTIFIER is set on every Windows box; platform.processor()
        # returns the same family string without a shell-out.
        ident = os.environ.get("PROCESSOR_IDENTIFIER")
        if ident:
            return ident.strip()

    proc = platform.processor()
    return proc.strip() or None


def _detect_soc() -> dict[str, Any]:
    """Detect a named System-on-Chip / board (Jetson today; extensible)."""
    model = _read_file("/proc/device-tree/model")
    if model:
        model = model.replace("\x00", "").strip()
        if model:
            is_jetson = "jetson" in model.lower() or "tegra" in model.lower()
            return {"model": model, "is_jetson": is_jetson, "source": "detected"}

    tegra = _read_file("/etc/nv_tegra_release")
    if tegra:
        return {"model": "NVIDIA Tegra", "is_jetson": True, "source": "detected"}

    return {"model": None, "is_jetson": False, "source": "detected"}


# ---------------------------------------------------------------------------
# accelerator
# ---------------------------------------------------------------------------

def _detect_accelerator(is_jetson: bool) -> dict[str, Any]:
    """Best-effort accelerator probe.

    Returns ``kind`` in {nvidia_dgpu, jetson_integrated, apple_silicon, none,
    unknown}, an optional ``name`` and ``vram_gb``. On unified-memory boards
    (Jetson, Apple Silicon) the GPU shares system RAM, so ``vram_gb`` is null and
    a note says so — the RAM number already covers it.
    """
    system = platform.system()

    if is_jetson:
        return {
            "kind": "jetson_integrated",
            "name": "NVIDIA integrated (Tegra)",
            "vram_gb": None,
            "unified_memory": True,
            "source": "detected",
        }

    if system == "Darwin" and platform.machine() in ("arm64", "aarch64"):
        return {
            "kind": "apple_silicon",
            "name": _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple Silicon",
            "vram_gb": None,
            "unified_memory": True,
            "source": "detected",
        }

    # Discrete NVIDIA GPU: nvidia-smi is the honest, driver-backed probe.
    smi = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total",
        "--format=csv,noheader,nounits",
    ])
    if smi:
        first = smi.splitlines()[0]
        parts = [p.strip() for p in first.split(",")]
        name = parts[0] if parts else "NVIDIA GPU"
        vram_gb: Optional[float] = None
        if len(parts) >= 2:
            try:
                vram_gb = round(int(float(parts[1])) / 1024, 1)  # MiB -> GiB
            except ValueError:
                vram_gb = None
        return {
            "kind": "nvidia_dgpu",
            "name": name,
            "vram_gb": vram_gb,
            "unified_memory": False,
            "source": "detected",
        }

    # No probe succeeded. We do not claim "none" — absence of nvidia-smi is not
    # absence of a GPU (AMD/Intel/Windows dGPUs go undetected here by design, to
    # avoid shelling into deprecated tooling). Say "unknown" honestly.
    return {
        "kind": "unknown",
        "name": None,
        "vram_gb": None,
        "unified_memory": False,
        "source": "detected",
    }


# ---------------------------------------------------------------------------
# disk / os
# ---------------------------------------------------------------------------

def _detect_disk(path: str) -> dict[str, Any]:
    """Total/free space on the volume holding ``path`` (the data dir).

    A first run checks before the data folder exists, so the space is measured
    on the nearest folder above it that does (the same drive); the path
    recorded is still the data folder.
    """
    probe = os.path.abspath(path) if path else path
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        usage = shutil.disk_usage(probe or path)
        return {
            "path": path,
            "total_gb": _bytes_to_gb(usage.total),
            "free_gb": _bytes_to_gb(usage.free),
            "source": "detected",
        }
    except OSError:
        return {"path": path, "total_gb": None, "free_gb": None, "source": "detected"}


def _detect_os() -> dict[str, Any]:
    """OS/arch/python identity — never fails."""
    return {
        "system": platform.system() or None,
        "release": platform.release() or None,
        "machine": platform.machine() or None,
        "python": platform.python_version(),
        "source": "detected",
    }


# ---------------------------------------------------------------------------
# classification + budget
# ---------------------------------------------------------------------------

def usable_model_budget_gb(total_ram_gb: Optional[float]) -> Optional[float]:
    """RAM a local model may occupy after reserving headroom for everything else.

    ``None`` in -> ``None`` out (unknown RAM means we cannot fit honestly and the
    wizard must ask). The reserve is 20% of RAM, floored at 2GB and capped at
    8GB, so big boxes keep room for the library and small boxes are not starved.
    """
    if total_ram_gb is None or total_ram_gb <= 0:
        return None
    reserve = min(max(total_ram_gb * _RESERVE_FRACTION, _RESERVE_MIN_GB), _RESERVE_MAX_GB)
    return round(max(total_ram_gb - reserve, 0.0), 1)


def classify_hardware(total_ram_gb: Optional[float], accelerator_kind: str) -> str:
    """Bucket the machine into an honest capacity class, RAM-first.

    Classes name capacity, not worth: ``unknown`` (RAM undetected — the wizard
    asks), ``minimal`` (<6GB), ``entry`` (6-15), ``mid`` (16-31), ``high``
    (32-47), ``workstation`` (>=48, e.g. the reference Orin 64GB). The
    accelerator does not change the class — RAM binds — but the brain menu uses
    it to speak to speed.
    """
    if total_ram_gb is None or total_ram_gb <= 0:
        return "unknown"
    if total_ram_gb < 6:
        return "minimal"
    if total_ram_gb < 16:
        return "entry"
    if total_ram_gb < 32:
        return "mid"
    if total_ram_gb < 48:
        return "high"
    return "workstation"


# ---------------------------------------------------------------------------
# the census
# ---------------------------------------------------------------------------

def run_census(data_dir: Optional[str] = None) -> dict[str, Any]:
    """Look at the machine and return a plain, honest census dict.

    Never raises. Every field carries a ``source`` of ``detected`` (or later
    ``stated``/``default`` once the owner answers). ``notes`` collects
    human-readable caveats where a probe could not see something.
    """
    if data_dir is None:
        try:
            from puffpai.config import get_config
            data_dir = get_config().DATA_DIR
        except Exception:
            data_dir = os.getcwd()

    notes: list[str] = []

    total_bytes = _total_ram_bytes()
    total_ram_gb = _bytes_to_gb(total_bytes)
    if total_ram_gb is None:
        notes.append("Could not read total RAM automatically, so the wizard will ask.")

    avail_bytes = _available_ram_bytes()

    soc = _detect_soc()
    accelerator = _detect_accelerator(bool(soc.get("is_jetson")))
    if accelerator["kind"] == "unknown":
        notes.append(
            "No NVIDIA GPU detected via nvidia-smi. If this box has a discrete "
            "GPU (AMD/Intel, or NVIDIA without drivers on the PATH), detection "
            "missed it, and you can tell the wizard."
        )
    if accelerator.get("unified_memory"):
        notes.append(
            "This board shares one pool of memory between CPU and GPU, so the "
            "RAM figure already covers the accelerator."
        )

    memory = {
        "total_gb": total_ram_gb,
        "available_gb": _bytes_to_gb(avail_bytes),
        "source": "detected",
    }

    cpu = {
        "model": _cpu_model(),
        "arch": platform.machine() or None,
        "logical_cores": os.cpu_count(),
        "source": "detected",
    }

    census: dict[str, Any] = {
        "schema": SCHEMA,
        "os": _detect_os(),
        "cpu": cpu,
        "memory": memory,
        "accelerator": accelerator,
        "soc": soc,
        "disk": _detect_disk(data_dir),
        "hardware_class": classify_hardware(total_ram_gb, str(accelerator["kind"])),
        "model_budget_gb": usable_model_budget_gb(total_ram_gb),
        "notes": notes,
    }
    return census


# ---------------------------------------------------------------------------
# the fallback path — stated answers are first-class
# ---------------------------------------------------------------------------

def apply_stated(
    census: dict[str, Any],
    *,
    total_ram_gb: Optional[float] = None,
    accelerator_kind: Optional[str] = None,
    accelerator_vram_gb: Optional[float] = None,
    cpu_model: Optional[str] = None,
) -> dict[str, Any]:
    """Overlay owner-stated values onto a census, marking them ``stated``.

    Used when detection failed or the owner corrects it ("How much RAM does this
    machine have?"). Stated values re-derive the class and budget so the brain
    menu fits against the corrected number. Returns a new dict; does not mutate.
    """
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in census.items()}

    if total_ram_gb is not None:
        mem = dict(out.get("memory") or {})
        mem["total_gb"] = round(float(total_ram_gb), 1)
        mem["source"] = "stated"
        out["memory"] = mem

    if cpu_model is not None:
        cpu = dict(out.get("cpu") or {})
        cpu["model"] = cpu_model
        cpu["source"] = "stated"
        out["cpu"] = cpu

    if accelerator_kind is not None:
        accel = dict(out.get("accelerator") or {})
        accel["kind"] = accelerator_kind
        if accelerator_vram_gb is not None:
            accel["vram_gb"] = round(float(accelerator_vram_gb), 1)
        accel["source"] = "stated"
        out["accelerator"] = accel

    # Re-derive everything RAM-first from the (possibly corrected) numbers.
    mem_now = out.get("memory") or {}
    accel_now = out.get("accelerator") or {}
    ram_now = mem_now.get("total_gb")
    out["hardware_class"] = classify_hardware(ram_now, str(accel_now.get("kind", "unknown")))
    out["model_budget_gb"] = usable_model_budget_gb(ram_now)
    return out


# ---------------------------------------------------------------------------
# display
# ---------------------------------------------------------------------------

def census_summary_lines(census: dict[str, Any]) -> list[str]:
    """Plain-language lines describing the census, for the CLI/API to show.

    Honest about gaps: an undetected field reads "not detected — we'll ask",
    never a fabricated default.
    """
    def _stated(section: dict[str, Any]) -> str:
        return " (you told us)" if section.get("source") == "stated" else ""

    mem = census.get("memory") or {}
    cpu = census.get("cpu") or {}
    accel = census.get("accelerator") or {}
    disk = census.get("disk") or {}
    soc = census.get("soc") or {}

    lines: list[str] = []

    ram = mem.get("total_gb")
    if ram is not None:
        lines.append(f"Memory: {ram} GB{_stated(mem)}")
    else:
        lines.append("Memory: not detected, we'll ask")

    if cpu.get("model"):
        cores = cpu.get("logical_cores")
        core_str = f", {cores} logical cores" if cores else ""
        lines.append(f"Processor: {cpu['model']}{core_str}{_stated(cpu)}")

    if soc.get("is_jetson") and soc.get("model"):
        lines.append(f"Board: {soc['model']}")

    kind = accel.get("kind")
    if kind == "nvidia_dgpu":
        vram = accel.get("vram_gb")
        vram_str = f" with {vram} GB VRAM" if vram else ""
        lines.append(f"Accelerator: {accel.get('name', 'NVIDIA GPU')}{vram_str}{_stated(accel)}")
    elif kind in ("jetson_integrated", "apple_silicon"):
        lines.append(f"Accelerator: {accel.get('name')} (shared memory){_stated(accel)}")
    elif kind == "none":
        lines.append("Accelerator: none, CPU only")
    else:
        lines.append("Accelerator: not detected, we'll ask")

    total = disk.get("total_gb")
    free = disk.get("free_gb")
    if total is not None and free is not None:
        lines.append(f"Disk: {free} GB free of {total} GB")

    budget = census.get("model_budget_gb")
    if budget is not None:
        lines.append(f"Room for a local model: about {budget} GB after headroom")

    return lines


__all__ = [
    "SCHEMA",
    "run_census",
    "apply_stated",
    "classify_hardware",
    "usable_model_budget_gb",
    "census_summary_lines",
]
