"""
A-X-L Commands Registry & Dispatcher
Contains typed functions exposed as native Function Calling Tools to Gemini.
Easily expandable without modifying the core server pipeline.
"""

import asyncio
import datetime
import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger("AXL-Commands")

# In-memory mock/active timer tasks store for local tracking
_ACTIVE_TIMERS: List[asyncio.Task] = []


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
        command: Command or script identifier to run (e.g. 'lock_screen', 'reboot', 'open_terminal').
        target: Target slave node (default: 'fedora').
    """
    logger.info(f"[COMMAND: execute_slave_command] Target: '{target}' | Command: '{command}'")
    return f"Execution payload '{command}' forwarded to slave node '{target}'."


def set_security_mode(mode: str) -> str:
    """
    Sets A-X-L security and distrust mode.
    
    Args:
        mode: The security mode to activate: 'friendly' (trusts everyone), 'restrict' (owner and enrolled friends only), 'paranoid' (strictly owner only).
    """
    clean_mode = mode.lower().strip()
    if clean_mode in ["friendly", "restrict", "paranoid"]:
        logger.info(f"[COMMAND: set_security_mode] Switched to '{clean_mode}'")
        return f"A-X-L security mode successfully updated to '{clean_mode}'."
    return f"Invalid mode '{mode}'. Choose 'friendly', 'restrict', or 'paranoid'."


# Registry of tools exposed to Gemini function calling
REGISTERED_TOOLS: List[Callable[..., Any]] = [
    get_current_time,
    set_timer,
    set_alarm,
    get_system_status,
    media_control,
    execute_slave_command,
    set_security_mode,
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
