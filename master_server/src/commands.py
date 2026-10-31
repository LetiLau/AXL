"""
A-X-L Commands Registry & Dispatcher
Contains typed functions exposed as native Function Calling Tools to Gemini.
Easily expandable without modifying the core server pipeline.
"""

import asyncio
import datetime
import logging
import os
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger("AXL-Commands")

# In-memory mock/active timer tasks store for local tracking
_ACTIVE_TIMERS: List[asyncio.Task] = []

# Optional listener for real-time UI telemetry events
_TELEMETRY_LISTENER: Optional[Callable[[Dict[str, Any]], Any]] = None


def register_telemetry_listener(callback: Callable[[Dict[str, Any]], Any]):
    """Registers a listener (e.g. Master StreamManager) to broadcast telemetry events to the Web UI."""
    global _TELEMETRY_LISTENER
    _TELEMETRY_LISTENER = callback


def notify_telemetry(payload: Dict[str, Any]):
    """Dispatches a telemetry event to the registered listener if present."""
    if _TELEMETRY_LISTENER:
        try:
            res = _TELEMETRY_LISTENER(payload)
            if asyncio.iscoroutine(res):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(res)
                except RuntimeError:
                    pass
        except Exception as exc:
            logger.debug(f"Telemetry notification dispatch error: {exc}")


def set_timer(duration_seconds: int, label: str = "Timer") -> str:
    """
    Sets a countdown timer.
    
    Args:
        duration_seconds: Timer duration in seconds (e.g. 300 for 5 minutes, 60 for 1 minute).
        label: Label or reason for the timer (e.g. 'pasta', 'study', 'nap').
    """
    logger.info(f"[COMMAND: set_timer] Duration: {duration_seconds}s | Label: '{label}'")

    async def _timer_worker():
        try:
            await asyncio.sleep(duration_seconds)
            logger.info(f">>> [TIMER EXPIRED] '{label}' ({duration_seconds}s) completed! <<<")
        except asyncio.CancelledError:
            logger.info(f"[TIMER CANCELLED] '{label}' cancelled.")

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_timer_worker())
        _ACTIVE_TIMERS.append(task)
    except RuntimeError:
        pass

    mins = duration_seconds // 60
    secs = duration_seconds % 60
    time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
    return f"Timer '{label}' successfully set for {time_str}."


def set_alarm(time_str: str, label: str = "Alarm") -> str:
    """
    Sets an alarm for a specific time.
    
    Args:
        time_str: Alarm time in 24-hour format 'HH:MM' (e.g. '07:30', '18:00').
        label: Reason or label for the alarm (e.g. 'Work', 'Workout').
    """
    logger.info(f"[COMMAND: set_alarm] Time: {time_str} | Label: '{label}'")
    # Will be dispatched to Android Intent (AlarmClock) in Phase 5
    return f"Alarm '{label}' scheduled for {time_str}."


def get_current_time() -> str:
    """
    Returns the exact current local time, date, day of week, and timezone from the device hardware clock.
    Use this whenever the user asks for the time, date, or day.
    """
    now = datetime.datetime.now().astimezone()
    time_str = now.strftime("%H:%M:%S")
    date_str = now.strftime("%A, %B %d, %Y")
    tz_str = now.strftime("%Z (UTC%z)")
    logger.info(f"[COMMAND: get_current_time] Exact device time: {time_str} ({tz_str})")
    return f"Exact Local Time: {time_str} | Date: {date_str} | Timezone: {tz_str}"


def get_system_status() -> str:
    """
    Returns current system health status, local date/time, and runtime diagnostics.
    """
    now = datetime.datetime.now()
    now_str = now.strftime("%A, %B %d, %Y, %H:%M:%S")
    logger.info("[COMMAND: get_system_status] Querying system status...")
    return f"A-X-L Status: Operational. Host timestamp: {now_str}. Master server active."


def media_control(action: str, target: str = "local") -> str:
    """
    Controls media playback or system audio volume.
    
    Args:
        action: Requested action: 'play', 'pause', 'stop', 'next', 'previous', 'volume_up', 'volume_down', 'mute'.
        target: Target playback device ('local', 'fedora', 'android').
    """
    logger.info(f"[COMMAND: media_control] Action: '{action}' | Target: '{target}'")
    return f"Media command '{action}' dispatched to target '{target}'."


def execute_slave_command(command: str, target: str = "fedora") -> str:
    """
    Executes an automation script or system command on a remote slave node (e.g. Fedora or Windows workstation).
    
    Args:
        command: Command or action identifier to run (e.g. 'lock_screen', 'sleep', 'mute', 'open_terminal').
        target: Target slave node (default: 'fedora').
    """
    logger.info(f"[COMMAND: execute_slave_command] Target: '{target}' | Command: '{command}'")
    slave_url = os.environ.get("AXL_SLAVE_URL", "http://127.0.0.1:8001")
    try:
        resp = requests.post(f"{slave_url}/execute", json={"command": command, "target": target}, timeout=3.0)
        if resp.status_code == 200:
            res_data = resp.json()
            return f"Slave [{target}]: {res_data.get('message', 'Command executed successfully.')}"
        else:
            return f"Slave [{target}] returned error code {resp.status_code}: {resp.text}"
    except Exception as exc:
        return f"Unable to reach slave [{target}] at {slave_url}: {exc}"


def get_slave_telemetry(target: str = "fedora") -> str:
    """
    Retrieves real-time CPU, RAM, GPU, thermals, and fan telemetry from a remote slave node (workstation),
    and displays the live telemetry HUD panel on the left of the Visor.
    """
    logger.info(f"[COMMAND: get_slave_telemetry] Target: '{target}'")
    slave_url = os.environ.get("AXL_SLAVE_URL", "http://127.0.0.1:8001")
    try:
        resp = requests.get(f"{slave_url}/telemetry", timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            notify_telemetry({"type": "TELEMETRY_MODE", "enabled": True, "data": data, "target": target})

            cpu = data.get("cpu", {})
            cpu_usage = cpu.get("usage_percent", 0)
            cpu_temp = cpu.get("temperature_c")
            if cpu_temp is None:
                raw_t = data.get("temperatures", {})
                for k in ["coretemp", "k10temp", "cpu_thermal", "zenpower", "acpitz"]:
                    if raw_t.get(k):
                        cpu_temp = raw_t[k][0].get("current")
                        break
            temp_str = f" a {cpu_temp}°C" if cpu_temp is not None else ""

            mem = data.get("memory", {})
            mem_used = mem.get("used_gb", 0)
            mem_total = mem.get("total_gb", 0)
            mem_pct = mem.get("percent", 0)

            gpu_str = ""
            gpus = data.get("gpus", [])
            if gpus:
                gpu_parts = []
                for g in gpus:
                    if g.get("type") == "dedicated":
                        g_temp = f" ({g.get('temperature_c')}°C)" if g.get("temperature_c") is not None else ""
                        gpu_parts.append(f"{g.get('name', 'GPU')} al {g.get('usage_percent', 0)}%{g_temp}")
                if gpu_parts:
                    gpu_str = f", {', '.join(gpu_parts)}"

            fan_str = ""
            fans = data.get("fans", [])
            if fans and fans[0].get("rpm"):
                fan_str = f", ventola a {fans[0]['rpm']} RPM"

            return (
                f"Slave [{target}] Telemetry: CPU al {cpu_usage}%{temp_str}, "
                f"RAM al {mem_pct}% ({mem_used} su {mem_total} GB){gpu_str}{fan_str}. "
                f"Pannello telemetrico attivato sul visore."
            )
        notify_telemetry({"type": "TELEMETRY_MODE", "enabled": True, "target": target})
        return f"Slave [{target}] ha risposto con codice {resp.status_code}."
    except Exception as exc:
        notify_telemetry({"type": "TELEMETRY_MODE", "enabled": True, "target": target, "error": str(exc)})
        return f"Il nodo slave [{target}] risulta offline o non raggiungibile ({exc})."


def toggle_telemetry_hud(show: bool = True, target: str = "fedora") -> str:
    """
    Shows or hides the live hardware telemetry HUD monitor on the left of the Visor screen.
    Call this when the user asks to see/show/open or hide/close telemetry, system stats, or hardware monitors.

    Args:
        show: True to show the telemetry monitor panel on the left, False to hide it.
        target: Target workstation node (default: 'fedora').
    """
    logger.info(f"[COMMAND: toggle_telemetry_hud] Show: {show} | Target: '{target}'")
    if not show:
        notify_telemetry({"type": "TELEMETRY_MODE", "enabled": False, "target": target})
        return "Schermata telemetrica chiusa. Visore ripristinato alla visuale classica."

    # If opening, fetch initial telemetry data
    slave_url = os.environ.get("AXL_SLAVE_URL", "http://127.0.0.1:8001")
    try:
        resp = requests.get(f"{slave_url}/telemetry", timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            notify_telemetry({"type": "TELEMETRY_MODE", "enabled": True, "data": data, "target": target})
            return f"Telemetria in tempo reale di {target} proiettata a sinistra del visore."
        notify_telemetry({"type": "TELEMETRY_MODE", "enabled": True, "target": target})
        return f"Telemetria aperta sul visore, in attesa di risposta dal nodo {target}."
    except Exception as exc:
        notify_telemetry({"type": "TELEMETRY_MODE", "enabled": True, "target": target, "error": str(exc)})
        return f"Pannello telemetria aperto sul visore, ma il nodo [{target}] non risponde al momento."


def set_security_mode(mode: str) -> str:
    """
    Sets A-X-L security and distrust mode.
    
    Args:
        mode: The security mode to activate: 'friendly' (trusts everyone), 'restrict' (owner and enrolled friends only), 'paranoid' (strictly owner only).
    """
    clean_mode = str(mode).lower().strip()
    mapping = {
        "friendly": "friendly",
        "amichevole": "friendly",
        "amico": "friendly",
        "amici": "friendly",
        "aperta": "friendly",
        "normale": "friendly",
        "libera": "friendly",
        "restrict": "restrict",
        "restrittiva": "restrict",
        "ristretta": "restrict",
        "limitata": "restrict",
        "sicura": "restrict",
        "paranoid": "paranoid",
        "paranoica": "paranoid",
        "paranoico": "paranoid",
        "massima": "paranoid",
        "massima sicurezza": "paranoid",
    }
    resolved_mode = mapping.get(clean_mode, clean_mode)
    if resolved_mode in ["friendly", "restrict", "paranoid"]:
        logger.info(f"[COMMAND: set_security_mode] Switched to '{resolved_mode}'")
        os.environ["AXL_SECURITY_MODE"] = resolved_mode
        notify_telemetry({"type": "SECURITY_MODE", "mode": resolved_mode})
        labels = {
            "friendly": "amichevole (Friendly: tutti gli utenti sono autorizzati)",
            "restrict": "restrittiva (Restrict: autorizzati solo il proprietario e gli amici registrati)",
            "paranoid": "paranoica (Paranoid: obbedienza esclusiva al proprietario)",
        }
        return f"Modalità di sicurezza aggiornata con successo a {labels[resolved_mode]}."
    return f"Modalità non valida: '{mode}'. Scegli tra 'friendly', 'restrict' o 'paranoid'."


def set_mute_mode(enabled: bool = True) -> str:
    """
    Activates or deactivates A-X-L mute mode (silences acoustic ingestion).
    When muted, A-X-L will completely ignore voice input until physically unmuted via the display button.
    
    Args:
        enabled: True to mute, False to unmute.
    """
    notify_telemetry({"type": "SET_MUTE", "muted": bool(enabled)})
    if enabled:
        logger.info("[COMMAND: set_mute_mode] Mute mode engaged via voice command.")
        return "Modalità muto attivata. Ho disattivato i microfoni. Potrai riattivarmi solo premendo il pulsante MUTE sullo schermo."
    logger.info("[COMMAND: set_mute_mode] Mute mode disengaged.")
    return "Microfoni riattivati."


# Registry of tools exposed to Gemini function calling
REGISTERED_TOOLS: List[Callable[..., Any]] = [
    get_current_time,
    set_timer,
    set_alarm,
    get_system_status,
    get_slave_telemetry,
    toggle_telemetry_hud,
    media_control,
    execute_slave_command,
    set_security_mode,
    set_mute_mode,
]

# Lookup map for runtime dynamic dispatching
_TOOL_MAP: Dict[str, Callable[..., Any]] = {
    func.__name__: func for func in REGISTERED_TOOLS
}


def dispatch_tool_call(name: str, args: Dict[str, Any]) -> str:
    """
    Safely executes the requested tool function and returns output as string.
    """
    func = _TOOL_MAP.get(name)
    if not func:
        err_msg = f"Unknown command: '{name}'"
        logger.error(err_msg)
        return err_msg

    try:
        logger.info(f"[DISPATCH] Executing tool: '{name}' with args: {args}")
        result = func(**args)
        return str(result)
    except Exception as exc:
        err_msg = f"Error executing command '{name}': {exc}"
        logger.error(err_msg, exc_info=True)
        return err_msg
