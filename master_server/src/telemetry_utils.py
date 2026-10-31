"""
A-X-L Universal Telemetry & Hardware Inspection Engine
Provides cross-platform, universal detection of multi-GPU configurations,
CPU thermals, frequencies, RAM, and cooling fans for laptops, desktops, and servers.
"""

import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

logger = logging.getLogger("AXL-Telemetry")

SYSTEM_OS = platform.system()


def get_universal_gpu_info() -> List[Dict[str, Any]]:
    """
    Universally detects dedicated (NVIDIA, AMD, Intel Arc) and integrated (Intel HD/Iris, AMD APU)
    graphics processors across all form factors (laptops with hybrid dual-GPU, desktops, workstations).
    Returns a list of structured GPU metric objects.
    """
    gpus: List[Dict[str, Any]] = []
    seen_pcis: set = set()
    seen_names: set = set()

    # -------------------------------------------------------------------------
    # 1. Dedicated NVIDIA GPUs (Desktops, Laptops with Optimus, Workstations)
    # -------------------------------------------------------------------------
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=pci.bus_id,name,utilization.gpu,temperature.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                timeout=2.0,
            )
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 6:
                    pci_id = parts[0].lower()
                    name = parts[1]
                    seen_pcis.add(pci_id)
                    seen_names.add(name.lower())
                    try:
                        u_pct = float(parts[2])
                    except (ValueError, TypeError):
                        u_pct = 0.0
                    try:
                        t_c = float(parts[3])
                    except (ValueError, TypeError):
                        t_c = None
                    try:
                        m_used = float(parts[4])
                    except (ValueError, TypeError):
                        m_used = None
                    try:
                        m_total = float(parts[5])
                    except (ValueError, TypeError):
                        m_total = None

                    gpus.append({
                        "name": name,
                        "type": "dedicated",
                        "usage_percent": round(u_pct, 1),
                        "temperature_c": t_c,
                        "memory_used_mb": m_used,
                        "memory_total_mb": m_total,
                    })
        except Exception as exc:
            logger.debug(f"nvidia-smi polling error: {exc}")

    # -------------------------------------------------------------------------
    # 2. Linux DRM Driver Cards (Intel Integrated / Arc, AMD Radeon / APU)
    # -------------------------------------------------------------------------
    if SYSTEM_OS == "Linux":
        drm_path = Path("/sys/class/drm")
        if drm_path.exists():
            for card in sorted(drm_path.glob("card[0-9]*")):
                # Skip connectors like card0-DP-1, card1-eDP-1
                if "-" in card.name:
                    continue

                uevent_path = card / "device" / "uevent"
                if not uevent_path.exists():
                    continue

                try:
                    content = uevent_path.read_text()
                except Exception:
                    content = ""

                # Skip NVIDIA cards already harvested via nvidia-smi
                if "DRIVER=nvidia" in content:
                    continue

                # --- 2A. Intel Integrated Graphics or Intel Arc (i915 or xe driver) ---
                if "DRIVER=i915" in content or "DRIVER=xe" in content:
                    cur_freq = None
                    max_freq = None

                    # Probe frequency sysfs paths across kernel versions
                    freq_candidates = [
                        card / "gt_cur_freq_mhz",
                        card / "gt_act_freq_mhz",
                        card / "gt" / "gt0" / "act_freq_mhz",
                        card / "gt" / "gt0" / "cur_freq_mhz",
                        card / "gt" / "cur_freq_mhz",
                    ]
                    for fc in freq_candidates:
                        if fc.exists():
                            try:
                                cur_freq = float(fc.read_text().strip())
                                break
                            except Exception:
                                pass

                    max_candidates = [
                        card / "gt_max_freq_mhz",
                        card / "gt_RP0_freq_mhz",
                        card / "gt" / "gt0" / "max_freq_mhz",
                        card / "gt" / "max_freq_mhz",
                    ]
                    for mc in max_candidates:
                        if mc.exists():
                            try:
                                max_freq = float(mc.read_text().strip())
                                break
                            except Exception:
                                pass

                    usage = 0.0
                    if cur_freq and max_freq and max_freq > 0:
                        usage = round(min(100.0, max(0.0, (cur_freq / max_freq) * 100)), 1)

                    # Determine if it's Arc dGPU or Intel iGPU
                    is_arc = "arc" in content.lower()
                    gpus.append({
                        "name": "Intel Arc Graphics" if is_arc else "Intel UHD / Iris Xe Graphics",
                        "type": "dedicated" if is_arc else "integrated",
                        "usage_percent": usage,
                        "temperature_c": None,  # thermally tied to CPU package
                        "freq_mhz": round(cur_freq, 1) if cur_freq else None,
                        "max_freq_mhz": round(max_freq, 1) if max_freq else None,
                    })

                # --- 2B. AMD Radeon Dedicated dGPU or Integrated APU (amdgpu or radeon driver) ---
                elif "DRIVER=amdgpu" in content or "DRIVER=radeon" in content:
                    busy_path = card / "device" / "gpu_busy_percent"
                    usage = 0.0
                    if busy_path.exists():
                        try:
                            usage = float(busy_path.read_text().strip())
                        except Exception:
                            pass

                    vram_used_mb = None
                    vram_total_mb = None
                    v_used_path = card / "device" / "mem_info_vram_used"
                    v_total_path = card / "device" / "mem_info_vram_total"
                    if v_used_path.exists() and v_total_path.exists():
                        try:
                            vram_used_mb = round(int(v_used_path.read_text().strip()) / (1024**2), 1)
                            vram_total_mb = round(int(v_total_path.read_text().strip()) / (1024**2), 1)
                        except Exception:
                            pass

                    # Probe AMD hardware temperature
                    amd_temp = None
                    for hwmon in (card / "device").glob("hwmon/hwmon*"):
                        for tf in hwmon.glob("temp*_input"):
                            try:
                                amd_temp = round(int(tf.read_text().strip()) / 1000.0, 1)
                                break
                            except Exception:
                                pass
                        if amd_temp is not None:
                            break

                    is_dedicated = bool(vram_total_mb and vram_total_mb > 1024)
                    gpus.append({
                        "name": "AMD Radeon Dedicated GPU" if is_dedicated else "AMD Radeon Graphics (APU)",
                        "type": "dedicated" if is_dedicated else "integrated",
                        "usage_percent": round(usage, 1),
                        "temperature_c": amd_temp,
                        "memory_used_mb": vram_used_mb,
                        "memory_total_mb": vram_total_mb,
                    })

    # -------------------------------------------------------------------------
    # 3. Windows Multi-GPU Detection Fallback
    # -------------------------------------------------------------------------
    if SYSTEM_OS == "Windows" and not gpus:
        try:
            cmd = ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"]
            out = subprocess.check_output(cmd, text=True, timeout=2.0)
            for line in out.strip().splitlines():
                name = line.strip()
                if name and name.lower() not in seen_names:
                    seen_names.add(name.lower())
                    is_ded = any(k in name.lower() for k in ["geforce", "rtx", "gtx", "radeon rx", "quadro", "arc "])
                    gpus.append({
                        "name": name,
                        "type": "dedicated" if is_ded else "integrated",
                        "usage_percent": 0.0,
                        "temperature_c": None,
                    })
        except Exception as exc:
            logger.debug(f"Windows GPU discovery error: {exc}")

    return gpus


def get_cpu_temperatures() -> Dict[str, Any]:
    """Retrieves CPU/package temperatures safely across platforms."""
    temps: Dict[str, Any] = {}
    try:
        if hasattr(psutil, "sensors_temperatures"):
            sensor_data = psutil.sensors_temperatures()
            if sensor_data:
                for name, entries in sensor_data.items():
                    temps[name] = [
                        {"label": getattr(entry, "label", None) or name, "current": entry.current, "high": getattr(entry, "high", None)}
                        for entry in entries
                    ]
    except Exception:
        pass
    return temps


def get_primary_cpu_temp() -> Optional[float]:
    """Extracts primary CPU package or core temperature in Celsius."""
    try:
        if hasattr(psutil, "sensors_temperatures"):
            s = psutil.sensors_temperatures()
            if s:
                for key in ["coretemp", "k10temp", "cpu_thermal", "zenpower", "acpitz", "soc_thermal"]:
                    if key in s and s[key]:
                        return round(float(s[key][0].current), 1)
                first_key = next(iter(s))
                if s[first_key]:
                    return round(float(s[first_key][0].current), 1)
    except Exception:
        pass
    return None


def get_fan_info() -> List[Dict[str, Any]]:
    """Polls hardware cooling fans and RPM speeds."""
    fans: List[Dict[str, Any]] = []
    try:
        if hasattr(psutil, "sensors_fans"):
            s = psutil.sensors_fans()
            if s:
                for name, entries in s.items():
                    for e in entries:
                        lbl = getattr(e, "label", None) or name
                        fans.append({
                            "label": lbl.replace("_", " ").title(),
                            "rpm": int(e.current),
                        })
    except Exception:
        pass
    return fans


def collect_system_telemetry(node_name: str = "master_server") -> Dict[str, Any]:
    """
    Gathers comprehensive, real-time hardware telemetry across all subsystems:
    CPU cores, frequencies, temperatures, RAM, cooling fans, and universal multi-GPU detection.
    """
    cpu_pct = psutil.cpu_percent(interval=None)
    cpu_freq = psutil.cpu_freq()
    mem = psutil.virtual_memory()
    cpu_temp = get_primary_cpu_temp()
    fans = get_fan_info()
    gpus = get_universal_gpu_info()

    return {
        "node": node_name,
        "cpu": {
            "usage_percent": round(cpu_pct, 1),
            "temperature_c": cpu_temp,
            "core_count": psutil.cpu_count(logical=True),
            "freq_mhz": round(cpu_freq.current, 1) if cpu_freq else None,
        },
        "memory": {
            "total_gb": round(mem.total / (1024**3), 2),
            "used_gb": round(mem.used / (1024**3), 2),
            "available_gb": round(mem.available / (1024**3), 2),
            "percent": mem.percent,
        },
        "gpus": gpus,
        "fans": fans,
        "temperatures": get_cpu_temperatures(),
    }
