#!/usr/bin/env python3
"""
A-X-L Slave Node Daemon
Runs as a lightweight background daemon on secondary workstations (Fedora Linux / Windows).
Exposes:
1. Real-time hardware telemetry (CPU, RAM, Disks, Temperatures, Battery) via psutil.
2. Webhook execution endpoint (/execute) for remote automated commands triggered by A-X-L.
"""

import logging
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [AXL-Slave]: %(message)s",
)
logger = logging.getLogger("AXL-Slave")

app = FastAPI(title="A-X-L Slave Node Daemon", version="1.0.0")

START_TIME = time.time()
SYSTEM_OS = platform.system()  # 'Linux', 'Windows', 'Darwin'


class CommandPayload(BaseModel):
    command: str
    args: Optional[List[str]] = None
    target: Optional[str] = "local"


def get_cpu_temperatures() -> Dict[str, Any]:
    """Retrieves CPU/package temperatures safely across platforms."""
    temps = {}
    try:
        if hasattr(psutil, "sensors_temperatures"):
            sensor_data = psutil.sensors_temperatures()
            if sensor_data:
                for name, entries in sensor_data.items():
                    temps[name] = [
                        {"label": entry.label or name, "current": entry.current, "high": entry.high}
                        for entry in entries
                    ]
    except Exception as exc:
        logger.debug(f"Temperature polling unavailable: {exc}")
    return temps


def get_primary_cpu_temp() -> Optional[float]:
    """Extracts primary CPU package / die temperature in Celsius."""
    try:
        if hasattr(psutil, "sensors_temperatures"):
            s = psutil.sensors_temperatures()
            for key in ["coretemp", "k10temp", "cpu_thermal", "zenpower", "acpitz"]:
                if key in s and s[key]:
                    return round(float(s[key][0].current), 1)
            if s:
                first_key = next(iter(s))
                if s[first_key]:
                    return round(float(s[first_key][0].current), 1)
    except Exception:
        pass
    return None


def get_fan_info() -> List[Dict[str, Any]]:
    """Polls hardware cooling fans and RPM speeds."""
    fans = []
    try:
        if hasattr(psutil, "sensors_fans"):
            s = psutil.sensors_fans()
            for name, entries in s.items():
                for e in entries:
                    lbl = getattr(e, "label", None) or name
                    fans.append({
                        "label": lbl.replace("_", " ").title(),
                        "rpm": int(e.current)
                    })
    except Exception as exc:
        logger.debug(f"Fan polling unavailable: {exc}")
    return fans


def get_gpu_info() -> List[Dict[str, Any]]:
    """
    Detects dedicated (NVIDIA/AMD/Intel Arc) and integrated (Intel/AMD APU) GPUs,
    returning usage %, temperatures, frequencies, and VRAM utilization across laptops and desktops.
    """
    gpus = []
    seen_names = set()

    # 1. Dedicated GPU: NVIDIA via nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits"
                ],
                text=True,
                timeout=2.0
            )
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 5:
                    g_name = parts[0]
                    seen_names.add(g_name.lower())
                    try:
                        u_pct = float(parts[1])
                    except (ValueError, TypeError):
                        u_pct = 0.0
                    try:
                        t_c = float(parts[2])
                    except (ValueError, TypeError):
                        t_c = None
                    try:
                        m_used = float(parts[3])
                    except (ValueError, TypeError):
                        m_used = None
                    try:
                        m_tot = float(parts[4])
                    except (ValueError, TypeError):
                        m_tot = None

                    gpus.append({
                        "name": g_name,
                        "type": "dedicated",
                        "usage_percent": round(u_pct, 1),
                        "temperature_c": t_c,
                        "memory_used_mb": m_used,
                        "memory_total_mb": m_tot,
                    })
        except Exception as exc:
            logger.debug(f"nvidia-smi polling error: {exc}")

    # 2. Linux DRM Driver Cards (Intel Integrated / Arc, AMD Radeon / APU)
    if SYSTEM_OS == "Linux":
        drm_path = Path("/sys/class/drm")
        if drm_path.exists():
            for card in sorted(drm_path.glob("card[0-9]*")):
                if "-" in card.name:
                    continue

                uevent = card / "device" / "uevent"
                if not uevent.exists():
                    continue

                try:
                    content = uevent.read_text()
                except Exception:
                    content = ""

                # Skip NVIDIA cards already harvested via nvidia-smi
                if "DRIVER=nvidia" in content:
                    continue

                # --- 2A. Intel Integrated Graphics or Intel Arc (i915 or xe driver) ---
                if "DRIVER=i915" in content or "DRIVER=xe" in content:
                    cur_freq = None
                    max_freq = None

                    for fc in [
                        card / "gt_cur_freq_mhz",
                        card / "gt_act_freq_mhz",
                        card / "gt" / "gt0" / "act_freq_mhz",
                        card / "gt" / "gt0" / "cur_freq_mhz",
                        card / "gt" / "cur_freq_mhz",
                    ]:
                        if fc.exists():
                            try:
                                cur_freq = float(fc.read_text().strip())
                                break
                            except Exception:
                                pass

                    for mc in [
                        card / "gt_max_freq_mhz",
                        card / "gt_RP0_freq_mhz",
                        card / "gt" / "gt0" / "max_freq_mhz",
                        card / "gt" / "max_freq_mhz",
                    ]:
                        if mc.exists():
                            try:
                                max_freq = float(mc.read_text().strip())
                                break
                            except Exception:
                                pass

                    usage = 0.0
                    if cur_freq and max_freq and max_freq > 0:
                        usage = round(min(100.0, max(0.0, (cur_freq / max_freq) * 100)), 1)

                    is_arc = "arc" in content.lower()
                    gpus.append({
                        "name": "Intel Arc Graphics" if is_arc else "Intel UHD / Iris Xe Graphics",
                        "type": "dedicated" if is_arc else "integrated",
                        "usage_percent": usage,
                        "temperature_c": None,
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
                    v_used = card / "device" / "mem_info_vram_used"
                    v_total = card / "device" / "mem_info_vram_total"
                    if v_used.exists() and v_total.exists():
                        try:
                            vram_used_mb = round(int(v_used.read_text().strip()) / (1024**2), 1)
                            vram_total_mb = round(int(v_total.read_text().strip()) / (1024**2), 1)
                        except Exception:
                            pass

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

    # 3. Windows Multi-GPU Detection Fallback
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


def get_system_telemetry() -> Dict[str, Any]:
    """Collects comprehensive real-time system metrics."""
    # CPU
    cpu_percent = psutil.cpu_percent(interval=None)
    cpu_cores = psutil.cpu_percent(percpu=True)
    cpu_freq = psutil.cpu_freq()
    cpu_temp = get_primary_cpu_temp()

    # Memory
    mem = psutil.virtual_memory()

    # Disk
    root_path = "C:\\" if SYSTEM_OS == "Windows" else "/"
    disk = psutil.disk_usage(root_path)

    # Battery (if laptop)
    battery_info = None
    try:
        if hasattr(psutil, "sensors_battery"):
            bat = psutil.sensors_battery()
            if bat:
                battery_info = {
                    "percent": round(bat.percent, 1),
                    "power_plugged": bat.power_plugged,
                    "secsleft": bat.secsleft,
                }
    except Exception:
        pass

    uptime_sec = int(time.time() - START_TIME)

    return {
        "hostname": platform.node(),
        "platform": SYSTEM_OS,
        "platform_release": platform.release(),
        "uptime_seconds": uptime_sec,
        "cpu": {
            "usage_percent": round(cpu_percent, 1),
            "temperature_c": cpu_temp,
            "cores_percent": cpu_cores,
            "core_count": psutil.cpu_count(logical=True),
            "freq_mhz": round(cpu_freq.current, 1) if cpu_freq else None,
        },
        "gpus": get_gpu_info(),
        "fans": get_fan_info(),
        "memory": {
            "total_gb": round(mem.total / (1024**3), 2),
            "used_gb": round(mem.used / (1024**3), 2),
            "available_gb": round(mem.available / (1024**3), 2),
            "percent": mem.percent,
        },
        "disk": {
            "total_gb": round(disk.total / (1024**3), 2),
            "used_gb": round(disk.used / (1024**3), 2),
            "free_gb": round(disk.free / (1024**3), 2),
            "percent": disk.percent,
        },
        "temperatures": get_cpu_temperatures(),
        "battery": battery_info,
    }


def execute_system_action(cmd_name: str, args: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Cross-platform dispatcher for privileged and automation tasks.
    """
    clean_cmd = cmd_name.lower().strip()
    logger.info(f"Dispatching system action: '{clean_cmd}' on {SYSTEM_OS}")

    # 1. Screen Lock
    if clean_cmd in ["lock", "lock_screen", "screen_lock"]:
        if SYSTEM_OS == "Linux":
            # Attempt loginctl first, fallback to xdg-screensaver
            try:
                subprocess.run(["loginctl", "lock-session"], check=True)
                return {"status": "success", "message": "Session locked via loginctl."}
            except Exception:
                subprocess.run(["xdg-screensaver", "lock"], check=True)
                return {"status": "success", "message": "Session locked via xdg-screensaver."}
        elif SYSTEM_OS == "Windows":
            subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], check=True)
            return {"status": "success", "message": "Windows workstation locked."}

    # 2. Suspend / Sleep
    elif clean_cmd in ["suspend", "sleep"]:
        if SYSTEM_OS == "Linux":
            subprocess.run(["systemctl", "suspend"], check=True)
            return {"status": "success", "message": "System suspended."}
        elif SYSTEM_OS == "Windows":
            subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=True)
            return {"status": "success", "message": "Windows system entered sleep."}

    # 3. Media Controls
    elif clean_cmd in ["play", "pause", "play_pause", "toggle_media"]:
        if SYSTEM_OS == "Linux":
            subprocess.run(["playerctl", "play-pause"], check=True)
            return {"status": "success", "message": "Media play/pause toggled via playerctl."}

    elif clean_cmd == "mute":
        if SYSTEM_OS == "Linux":
            subprocess.run(["amixer", "-D", "pulse", "set", "Master", "1+", "toggle"], check=True)
            return {"status": "success", "message": "Audio mute toggled."}

    # 4. Custom Allowed Commands / Scripts
    elif clean_cmd in ["open_terminal", "terminal"]:
        if SYSTEM_OS == "Linux":
            subprocess.Popen(["x-terminal-emulator"])
            return {"status": "success", "message": "Terminal emulator launched."}
        elif SYSTEM_OS == "Windows":
            subprocess.Popen(["wt.exe"])
            return {"status": "success", "message": "Windows Terminal launched."}

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unrecognized or unauthorized slave command: '{cmd_name}'",
    )


@app.get("/health")
async def health_check():
    """Basic health probe."""
    return {
        "status": "online",
        "node": "AXL-Slave",
        "platform": SYSTEM_OS,
        "uptime_sec": int(time.time() - START_TIME),
    }


@app.get("/telemetry")
async def get_telemetry():
    """Exposes real-time system metrics to Master Server and UI."""
    return get_system_telemetry()


@app.post("/execute")
async def execute_command(payload: CommandPayload):
    """Executes a validated remote automation command."""
    try:
        result = execute_system_action(payload.command, payload.args)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error executing command '{payload.command}': {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Execution error: {exc}",
        )


if __name__ == "__main__":
    port = int(os.environ.get("AXL_SLAVE_PORT", 8001))
    logger.info(f"Starting A-X-L Slave Daemon on port {port} ({SYSTEM_OS})...")
    uvicorn.run("slave_daemon:app", host="0.0.0.0", port=port, reload=False)
