"""
A-X-L Commands Registry & Dispatcher
Contains typed functions exposed as native Function Calling Tools to Gemini.
Easily expandable without modifying the core server pipeline.
"""

import asyncio
import datetime
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional

import psutil
import requests
from telemetry_utils import collect_system_telemetry
from task_manager import task_manager
from tts_engine import detect_language

logger = logging.getLogger("AXL-Commands")

# In-memory mock/active timer tasks store for local tracking
_ACTIVE_TIMERS: List[asyncio.Task] = []

# Optional listener for real-time UI telemetry events
_TELEMETRY_LISTENER: Optional[Callable[[Dict[str, Any]], Any]] = None


_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None

# Active utterance biometric context set per turn before LLM execution
_AUTH_CONTEXT: Dict[str, Any] = {
    "is_authorized": True,
    "identity": "owner",
    "security_mode": "friendly",
    "similarity_score": 1.0,
}


def set_command_auth_context(
    is_authorized: bool,
    identity: str,
    security_mode: str,
    similarity_score: float = 1.0,
):
    """Updates the active utterance biometric context before LLM tool execution."""
    global _AUTH_CONTEXT
    _AUTH_CONTEXT = {
        "is_authorized": bool(is_authorized),
        "identity": str(identity),
        "security_mode": str(security_mode).lower(),
        "similarity_score": float(similarity_score),
    }


def get_command_auth_context() -> Dict[str, Any]:
    global _AUTH_CONTEXT
    return dict(_AUTH_CONTEXT)


def register_telemetry_listener(callback: Callable[[Dict[str, Any]], Any]):
    """Registers a listener (e.g. Master StreamManager) to broadcast telemetry events to the Web UI."""
    global _TELEMETRY_LISTENER, _MAIN_LOOP
    _TELEMETRY_LISTENER = callback
    try:
        _MAIN_LOOP = asyncio.get_running_loop()
    except RuntimeError:
        _MAIN_LOOP = None


def notify_telemetry(payload: Dict[str, Any]):
    """
    Dispatches a telemetry event to the registered listener if present.
    Tools invoked by Gemini AFC run in worker threads (asyncio.to_thread) where no event loop
    is running, so the coroutine is scheduled thread-safely on the main server loop.
    """
    if _TELEMETRY_LISTENER:
        try:
            res = _TELEMETRY_LISTENER(payload)
            if asyncio.iscoroutine(res):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(res)
                except RuntimeError:
                    if _MAIN_LOOP and _MAIN_LOOP.is_running():
                        asyncio.run_coroutine_threadsafe(res, _MAIN_LOOP)
                    else:
                        res.close()
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

    # Dispatch native Android Intent to Kiosk if connected
    notify_telemetry({
        "type": "INTENT",
        "action": "set_timer",
        "seconds": int(duration_seconds),
        "message": label,
    })

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
    hour = 8
    minutes = 0
    try:
        parts = time_str.strip().split(":")
        hour = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        pass

    # Dispatch native Android Intent to Kiosk
    notify_telemetry({
        "type": "INTENT",
        "action": "set_alarm",
        "hour": hour,
        "minutes": minutes,
        "message": label,
    })
    return f"Alarm '{label}' scheduled for {hour:02d}:{minutes:02d}."


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
    notify_telemetry({
        "type": "INTENT",
        "action": "media_control",
        "command": action,
        "target": target,
    })
    return f"Media command '{action}' dispatched to target '{target}'."


def play_music(query: str, target: str = "kiosk") -> str:
    """
    Searches and plays music, artists, albums, or playlists on Spotify.
    Invoke this tool whenever the user asks to play music, a song, an artist, or a playlist.
    
    Args:
        query: Song name, artist, album, genre, or playlist (e.g. 'Queen Bohemian Rhapsody', 'Lazza 100 Messaggi', 'musica rilassante').
        target: Target playback device ('kiosk' for Galaxy A33 smartphone, 'slave' for remote PC). Default is 'kiosk'.
    """
    logger.info(f"[COMMAND: play_music] Query: '{query}' | Target: '{target}'")
    notify_telemetry({
        "type": "INTENT",
        "action": "open_spotify",
        "query": query,
        "target": target,
    })
    return f"Ho avviato la ricerca musicale di '{query}' su Spotify per {target}."


def add_task(text: str) -> str:
    """
    Adds a new task to the user's to-do task list.
    
    Args:
        text: Description of the task to add (e.g. 'comprare il latte', 'studiare per l'esame').
    """
    res = task_manager.add_task(text)
    if "error" in res:
        return f"Errore nell'aggiunta del compito: {res['error']}"
    return f"Ho aggiunto '{text}' alla tua lista dei compiti (ID #{res['id']})."


def list_tasks() -> str:
    """
    Returns the list of active to-do tasks and reminders.
    """
    tasks = task_manager.list_tasks(include_completed=False)
    if not tasks:
        return "Non hai compiti in sospeso nella tua lista."
    task_lines = [f"- #{t['id']}: {t['text']}" for t in tasks]
    return f"Ecco i tuoi compiti attivi:\n" + "\n".join(task_lines)


def complete_task(task_id: int) -> str:
    """
    Marks a task as completed given its numeric ID.
    
    Args:
        task_id: The numeric ID of the task to complete.
    """
    success = task_manager.complete_task(task_id)
    if success:
        return f"Compito #{task_id} contrassegnato come completato."
    return f"Nessun compito trovato con ID #{task_id}."


def create_calendar_event(title: str, date_time: str = "", description: str = "") -> str:
    """
    Creates an event or appointment in the calendar and synchronizes it with Android / Google Calendar.
    
    Args:
        title: Title of the event (e.g. 'Visita dentista', 'Riunione con Marco').
        date_time: Date and time of the event (e.g. '2026-11-12 15:30' or '17:00').
        description: Optional notes or description.
    """
    ev = task_manager.add_calendar_event(title, date_time_str=date_time, description=description)
    if "error" in ev:
        return f"Errore nella creazione dell'evento: {ev['error']}"

    # Dispatch to Android Kiosk over WebSocket
    notify_telemetry({
        "type": "INTENT",
        "action": "add_calendar_event",
        "title": ev["title"],
        "description": ev.get("description", ""),
        "begin_time": ev["begin_time_ms"],
        "end_time": ev["end_time_ms"],
    })
    return f"Evento '{title}' programmato per {ev['date_time']} e inviato al calendario."


def get_calendar_events(today_only: bool = True) -> str:
    """
    Returns the scheduled calendar events and appointments (defaults to today's events).
    
    Args:
        today_only: If True, returns only events scheduled for today. If False, returns all upcoming events.
    """
    events = task_manager.list_calendar_events(today_only=today_only)
    scope = "per oggi" if today_only else "in programma"
    if not events:
        return f"Non ci sono eventi sul calendario {scope}."
    lines = [f"- #{e['id']}: {e['title']} alle {e['date_time']}" for e in events]
    return f"Eventi sul calendario {scope}:\n" + "\n".join(lines)


def execute_slave_command(command: str, target: str = "fedora") -> str:
    """
    Executes an automation script or system command on a remote slave node (e.g. Fedora or Windows workstation).
    
    Args:
        command: Command or action identifier to run (e.g. 'lock_screen', 'sleep', 'mute', 'open_terminal').
        target: Target slave node (default: 'fedora').
    """
    global _AUTH_CONTEXT
    sec_mode = _AUTH_CONTEXT.get("security_mode", "friendly")
    is_auth = _AUTH_CONTEXT.get("is_authorized", True)
    identity = _AUTH_CONTEXT.get("identity", "stranger")

    if sec_mode in ["restrict", "paranoid"] and not is_auth:
        logger.warning(
            f"[COMMAND: execute_slave_command] BLOCKED! Speaker '{identity}' is UNAUTHORIZED to execute slave commands in '{sec_mode}' mode."
        )
        return (
            f"COMANDO RIFIUTATO: L'utente '{identity}' non è autorizzato ad eseguire comandi di sistema sullo slave "
            f"in modalità {sec_mode.upper()}."
        )

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


def get_server_telemetry() -> str:
    """
    Retrieves real-time CPU, RAM, thermal, fan, and multi-GPU telemetry directly from the Master Server host machine,
    and displays the Server Telemetry HUD panel on the left of the Visor.
    Call this when the user asks for server telemetry, master stats, or master hardware status (e.g. 'telemetria server', 'stato server', 'mostra telemetria server').
    """
    logger.info("[COMMAND: get_server_telemetry] Fetching Master Server telemetry...")
    try:
        data = collect_system_telemetry(node_name="master_server")
        notify_telemetry({"type": "TELEMETRY_MODE", "enabled": True, "target": "server", "data": data})

        cpu = data.get("cpu", {})
        cpu_usage = cpu.get("usage_percent", 0)
        cpu_temp = cpu.get("temperature_c")
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
                g_temp = f" ({g.get('temperature_c')}°C)" if g.get("temperature_c") is not None else ""
                gpu_parts.append(f"{g.get('name', 'GPU')} al {g.get('usage_percent', 0)}%{g_temp}")
            if gpu_parts:
                gpu_str = f", {', '.join(gpu_parts)}"

        fan_str = ""
        fans = data.get("fans", [])
        if fans and fans[0].get("rpm"):
            fan_str = f", ventola a {fans[0]['rpm']} RPM"

        return (
            f"Master Server Telemetry: CPU al {cpu_usage}%{temp_str}, "
            f"RAM al {mem_pct}% ({mem_used} su {mem_total} GB){gpu_str}{fan_str}. "
            f"Pannello telemetria del server attivato sul visore."
        )
    except Exception as exc:
        notify_telemetry({"type": "TELEMETRY_MODE", "enabled": True, "target": "server", "error": str(exc)})
        return f"Errore nel recupero della telemetria del server: {exc}"


def get_slave_telemetry(target: str = "slave") -> str:
    """
    Retrieves real-time CPU, RAM, GPU, thermals, and fan telemetry from a remote slave node (workstation),
    and displays the Slave Telemetry HUD panel on the left of the Visor.
    Call this when the user asks for slave telemetry, workstation stats, or remote PC monitors (e.g. 'telemetria slave', 'telemetria pc', 'mostra telemetria slave').
    """
    clean_target = str(target).lower().strip()
    if clean_target in ["server", "master", "thinkpad", "locale", "local"]:
        return get_server_telemetry()

    resolved_target = "slave"
    logger.info(f"[COMMAND: get_slave_telemetry] Target: '{resolved_target}'")
    slave_url = os.environ.get("AXL_SLAVE_URL", "http://127.0.0.1:8001")
    try:
        resp = requests.get(f"{slave_url}/telemetry", timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            notify_telemetry({"type": "TELEMETRY_MODE", "enabled": True, "data": data, "target": resolved_target})

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
                f"Slave Telemetry: CPU al {cpu_usage}%{temp_str}, "
                f"RAM al {mem_pct}% ({mem_used} su {mem_total} GB){gpu_str}{fan_str}. "
                f"Pannello telemetrico slave attivato sul visore."
            )
        notify_telemetry({"type": "TELEMETRY_MODE", "enabled": True, "target": resolved_target})
        return f"Il nodo slave ha risposto con codice {resp.status_code}. Pannello telemetrico attivato sul visore."
    except Exception as exc:
        notify_telemetry({"type": "TELEMETRY_MODE", "enabled": True, "target": resolved_target, "error": str(exc)})
        return f"Il nodo slave risulta offline o non raggiungibile ({exc}). Pannello telemetrico aperto sul visore."


def toggle_telemetry_hud(show: bool = True, target: str = "slave") -> str:
    """
    Shows or hides the live hardware telemetry HUD monitor on the Visor screen.
    Call this when the user asks to see/show/open or hide/close telemetry, system stats, or hardware monitors.

    Args:
        show: True to show the telemetry monitor panel on the Visor, False to hide it.
        target: Target node to monitor: 'server' (Master Server host), 'slave' (remote PC / workstation node), or 'kiosk' (Galaxy A33 kiosk). Default is 'slave'.
    """
    clean_target = str(target).lower().strip()
    if clean_target in ["server", "master", "thinkpad", "locale", "local"]:
        resolved_target = "server"
    elif clean_target in ["kiosk", "android", "telefono", "cellulare", "a33", "phone"]:
        resolved_target = "kiosk"
    else:
        resolved_target = "slave"

    logger.info(f"[COMMAND: toggle_telemetry_hud] Show: {show} | Target: '{resolved_target}' (raw: '{target}')")
    if not show:
        notify_telemetry({"type": "TELEMETRY_MODE", "enabled": False, "target": resolved_target})
        if resolved_target == "server":
            node_label = "del server"
        elif resolved_target == "kiosk":
            node_label = "del kiosk"
        else:
            node_label = "dello slave"
        return f"Schermata telemetrica {node_label} chiusa. Visore ripristinato alla visuale classica."

    if resolved_target == "server":
        return get_server_telemetry()
    else:
        return get_slave_telemetry(target="slave")


def set_security_mode(mode: str) -> str:
    """
    Imposta e modifica la modalità di sicurezza e diffidenza di A-X-L.
    Invocare SEMPRE questo tool quando l'utente chiede di cambiare o impostare la modalità:
    - 'friendly' (o 'amichevole', 'aperta', 'libera'): disattiva la diffidenza ed esegue i comandi di chiunque.
    - 'restrict' (o 'restrittiva', 'sicura'): autorizza solo il proprietario e gli amici registrati.
    - 'paranoid' (o 'paranoica', 'massima sicurezza'): obbedienza esclusiva al solo proprietario.
    
    Args:
        mode: La modalità da attivare: 'friendly', 'restrict' o 'paranoid'.
    """
    global _AUTH_CONTEXT
    sec_mode = _AUTH_CONTEXT.get("security_mode", "friendly")
    is_auth = _AUTH_CONTEXT.get("is_authorized", True)
    identity = _AUTH_CONTEXT.get("identity", "stranger")
    sim_score = _AUTH_CONTEXT.get("similarity_score", 0.0)

    # Biometric privilege enforcement:
    # 1. In FRIENDLY mode: any user can change security mode.
    # 2. In RESTRICT mode: ONLY authorized users (owner or registered friends) can change mode.
    # 3. In PARANOID mode: ONLY the owner can change mode.
    is_privilege_granted = False
    if sec_mode == "friendly":
        is_privilege_granted = True
    elif sec_mode == "restrict":
        is_privilege_granted = is_auth
    elif sec_mode == "paranoid":
        is_privilege_granted = is_auth and (identity.lower() in ["owner", "admin_terminal"])

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

    if not is_privilege_granted:
        logger.warning(
            f"[COMMAND: set_security_mode] BLOCKED! Speaker '{identity}' is UNAUTHORIZED to switch security mode "
            f"from '{sec_mode}' to '{resolved_mode}' (biometric score: {sim_score:.3f})."
        )
        return (
            f"COMANDO RIFIUTATO: L'utente '{identity}' non dispone dell'autorizzazione biometrica per cambiare la modalità "
            f"di sicurezza (modalità corrente: {sec_mode.upper()}). Ordine respinto con diffidenza."
        )

    if resolved_mode in ["friendly", "restrict", "paranoid"]:
        logger.info(f"[COMMAND: set_security_mode] Switched to '{resolved_mode}' by authorized speaker '{identity}'")
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
    Attiva o disattiva la modalità muto di A-X-L (interrompe l'ingestione microfonica).
    Invocare SEMPRE questo tool quando l'utente chiede di mutare o fare silenzio, ad esempio:
    'muto', 'vai in muto', 'silenzio', 'disattiva microfono', 'microfono disattivato', 'spegni microfono', 'mute', 'microphone off', 'turn off the microphone', 'be quiet'.
    
    Args:
        enabled: True per silenziare i microfoni, False per riattivare l'ascolto.
    """
    global _AUTH_CONTEXT
    sec_mode = _AUTH_CONTEXT.get("security_mode", "friendly")
    is_auth = _AUTH_CONTEXT.get("is_authorized", True)
    identity = _AUTH_CONTEXT.get("identity", "stranger")

    if enabled and sec_mode in ["restrict", "paranoid"] and not is_auth:
        logger.warning(
            f"[COMMAND: set_mute_mode] BLOCKED! Speaker '{identity}' is UNAUTHORIZED to mute A-X-L in '{sec_mode}' mode."
        )
        return (
            f"COMANDO RIFIUTATO: L'utente '{identity}' non è autorizzato a disattivare l'ascolto di A-X-L "
            f"in modalità {sec_mode.upper()}. Ordine ignorato."
        )

    notify_telemetry({"type": "SET_MUTE", "muted": bool(enabled)})
    if enabled:
        logger.info("[COMMAND: set_mute_mode] Mute mode engaged via voice command.")
        return "Modalità muto attivata. Ho disattivato i microfoni. Potrai riattivarmi solo premendo il pulsante MUTE sullo schermo."
    logger.info("[COMMAND: set_mute_mode] Mute mode disengaged.")
    return "Microfoni riattivati."


def enroll_voice_profile(profile_name: str = "owner") -> str:
    """
    Avvia la procedura di calibrazione e registrazione biometrica della voce (enrollment vocale).
    Usare questo tool quando:
    1. L'Owner chiede di aggiornare, modificare o ricalibrare la propria voce ('owner').
    2. L'Owner chiede di registrare o aggiungere un amico ('luca', 'marco', ecc.).

    Vincoli di sicurezza biometrica:
    - La modifica del profilo 'owner' è consentita SOLO all'Owner attualmente autenticato.
    - La registrazione di un nuovo amico è consentita SOLO all'Owner (o utenti autorizzati in friendly).
    - Agli estranei non autorizzati è severamente vietato registrare o modificare profili.

    Args:
        profile_name: Nome del profilo da calibrare/registrare (es. 'owner', 'luca', 'marco').
    """
    global _AUTH_CONTEXT
    sec_mode = _AUTH_CONTEXT.get("security_mode", "friendly")
    is_auth = _AUTH_CONTEXT.get("is_authorized", True)
    identity = _AUTH_CONTEXT.get("identity", "stranger")

    clean_name = re.sub(r"[^a-zA-Z0-9_\-]", "", str(profile_name).lower().strip())
    if not clean_name:
        clean_name = "owner"

    logger.info(
        f"[COMMAND: enroll_voice_profile] Request for '{clean_name}' by '{identity}' "
        f"(mode: '{sec_mode}', authorized: {is_auth})"
    )

    # 1. Modifica del profilo Owner:
    # L'Owner può essere modificato solo da un utente già riconosciuto come 'owner'
    if clean_name == "owner":
        if identity.lower() not in ["owner", "admin_terminal"]:
            logger.warning(
                f"[ENROLL SECURITY REJECTED] Caller '{identity}' cannot modify 'owner' profile in mode '{sec_mode}'."
            )
            return (
                "COMANDO RIFIUTATO: Accesso biometrico negato. Solo il proprietario autenticato "
                "può aggiornare o ricalibrare la firma vocale principale dell'Owner."
            )
    else:
        # 2. Registrazione di un amico:
        if sec_mode in ["restrict", "paranoid"] and identity.lower() not in ["owner", "admin_terminal"]:
            logger.warning(
                f"[ENROLL SECURITY REJECTED] Caller '{identity}' cannot enroll friend '{clean_name}' in mode '{sec_mode}'."
            )
            return (
                f"COMANDO RIFIUTATO: Solo l'Owner può autorizzare la registrazione di un nuovo amico ('{clean_name}')."
            )
        if not is_auth:
            return (
                "COMANDO RIFIUTATO: Accesso negato per mancanza di privilegi biometrici."
            )

    logger.info(f"[ENROLL PRIVILEGE GRANTED] Starting voice calibration session for '{clean_name}'...")
    notify_telemetry({
        "type": "START_VOICE_ENROLLMENT",
        "profile_name": clean_name,
    })
    return (
        f"Calibrazione vocale autorizzata per il profilo '{clean_name}'. "
        f"Avvio la procedura di registrazione dei campioni audio."
    )


# ==============================================================================
# INTENT PARSING & GUARDRAILS
# Guarantees that critical system commands execute even if an LLM hallucinates
# a textual acknowledgment without emitting an explicit function call.
# ==============================================================================

def is_unmute_intent(text: str) -> bool:
    """Detects if user utterance expressed intent to unmute / turn on mic / resume listening."""
    if not text:
        return False
    lower = text.lower().strip()
    patterns = [
        # Italian
        r"\b(togli|rimuovi|disattiva|cancella|annulla|spegni|leva|levami|esci\s+dal?)\s+(il\s+)?muto\b",
        r"\b(smuta|smutati|smutare)\b",
        r"\b(riattiva|riattivare|attiva|accendi|apri|sblocca)\s+(il\s+|l['\s])?(microfon[oi]|mic|ascolto|audio)\b",
        r"\b(microfon[oi]|mic|ascolto)\s+(attivat[oi]|acces[oi]|abilitat[oi]|on|apert[oi])\b",
        r"\bnon\s+(essere\s+|stare\s+|andare\s+in\s+|metterti\s+in\s+)?muto\b",
        r"\bnon\s+mutarti\b",
        r"\btorna\s+ad\s+ascoltare\b",
        r"\bricomincia\s+ad\s+ascoltare\b",
        # English
        r"\b(remove|cancel|disable|turn\s+off|switch\s+off|stop|clear|take\s+off|exit)\s+(the\s+)?mute\b",
        r"\bun-?mute\b",
        r"\b(turn|switch)\s+on\s+(the\s+|your\s+)?(mic|microphone|listening)\b",
        r"\b(enable|activate|reactivate|resume)\s+(the\s+|your\s+)?(mic|microphone|listening)\b",
        r"\b(mic|microphone)\s+(on|enabled|activated|active)\b",
        r"\b(start|resume)\s+listening\b",
        r"\bdon'?t\s+mute\b",
        r"\bdo\s+not\s+mute\b",
    ]
    return any(re.search(p, lower) for p in patterns)


def is_mute_intent(text: str) -> bool:
    """Detects if user utterance expressed intent to mute / silence A-X-L."""
    if not text:
        return False
    lower = text.lower().strip()

    # 1. Critical guardrail: Never mute if user intent is to unmute or remove mute!
    if is_unmute_intent(lower):
        return False

    # 2. Exclude queries about current mute status
    if any(q in lower for q in [
        "sei in muto", "sei muto", "perché sei muto", "stato muto", "stato del muto",
        "are you muted", "are you on mute", "why are you muted",
    ]):
        return False

    cleaned = re.sub(r"[,:;!?]", " ", lower)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    patterns = [
        # Italian
        r"^(axl\s+|jarvis\s+|hey\s+jarvis\s+)?muto$",
        r"\b(vai\s+in|mettiti\s+in|metti\s+in|imposta(\s+in)?|attiva(\s+la)?\s+modalit[aà])\s+muto\b",
        r"\bmodalit[aà]\s+muto\b",
        r"\b(disattiva|disattivare|disabilita|spegni|spegnere|chiudi|blocca)\s+(il\s+|l['\s])?(microfon[oi]|mic|ascolto|audio)\b",
        r"\b(microfon[oi]|mic|ascolto)\s+(disattivat[oi]|spent[oi]|disabilitat[oi]|off|chius[oi])\b",
        r"\bsilenzio\b",
        r"\bstai\s+(zitto|buono|in silenzio)\b",
        r"\btaci\b",
        r"\bnon\s+ascoltare\b",
        r"\bsmetti\s+di\s+ascoltare\b",
        # English
        r"^(axl\s+|jarvis\s+|hey\s+jarvis\s+)?mute$",
        r"\b(go\s+(on\s+)?mute|put\s+(on\s+)?mute|mute\s+(yourself|the\s+mic|your\s+mic|system))\b",
        r"\b(turn|switch|shut)\s+off\s+(the\s+|your\s+)?(mic|microphone|listening)\b",
        r"\b(disable|deactivate|silence)\s+(the\s+|your\s+)?(mic|microphone|listening)\b",
        r"\b(mic|microphone)\s+(off|disabled|deactivated|muted)\b",
        r"\b(be\s+quiet|shut\s+up|stay\s+silent|keep\s+quiet)\b",
        r"\bstop\s+listening\b",
    ]
    return any(re.search(p, cleaned) for p in patterns)


def is_mute_in_reply(reply: str) -> bool:
    """Detects if A-X-L's vocal response explicitly states that it is muting or is currently in mute."""
    if not reply:
        return False
    lower = reply.lower().strip()
    patterns = [
        r"\bsono\s+in\s+muto\b",
        r"\bmi\s+metto\s+in\s+muto\b",
        r"\bvado\s+in\s+muto\b",
        r"\bmetto\s+in\s+muto\b",
        r"\bmodalit[aà]\s+muto\b",
        r"\b(disattivo|spengo|chiudo)\s+(i|il)\s+microfon[oi]\b",
        r"\bmicrofon[oi]\s+(disattivat[oi]|spent[oi])\b",
        r"\b(muting|going\s+on\s+mute|mic(rophone)?\s+(is\s+)?(off|disabled|muted))\b",
        r"\b(i'?m|i\s+am)\s+(now\s+)?(muted|on\s+mute)\b",
    ]
    return any(re.search(p, lower) for p in patterns)


def detect_security_mode_in_reply(reply: str) -> Optional[str]:
    """Detects if A-X-L's vocal response explicitly confirms setting a new security mode."""
    if not reply:
        return None
    lower = reply.lower().strip()
    if any(re.search(p, lower) for p in [
        r"\b(imposto|impostata|passo in|passato in|attivata|passo a)\s+(la\s+modalit[aà]\s+)?(friendly|amichevole)\b",
        r"\bmodalit[aà]\s+(friendly|amichevole)\s+attivata\b",
        r"\bmodalit[aà]\s+(friendly|amichevole)\b.*(attiva|impostata)",
    ]):
        return "friendly"
    if any(re.search(p, lower) for p in [
        r"\b(imposto|impostata|passo in|passato in|attivata|passo a)\s+(la\s+modalit[aà]\s+)?(restrict|restrittiva)\b",
        r"\bmodalit[aà]\s+(restrict|restrittiva)\s+attivata\b",
        r"\bmodalit[aà]\s+(restrict|restrittiva)\b.*(attiva|impostata)",
    ]):
        return "restrict"
    if any(re.search(p, lower) for p in [
        r"\b(imposto|impostata|passo in|passato in|attivata|passo a)\s+(la\s+modalit[aà]\s+)?(paranoid|paranoica)\b",
        r"\bmodalit[aà]\s+(paranoid|paranoica)\s+attivata\b",
        r"\bmodalit[aà]\s+(paranoid|paranoica)\b.*(attiva|impostata)",
    ]):
        return "paranoid"
    return None


def detect_security_mode_intent(text: str) -> Optional[str]:
    """Detects if user utterance expressed intent to set or switch security mode."""
    if not text:
        return None
    lower = text.lower().strip()
    # Exclude questions about current status
    if any(q in lower for q in ["in che modalità", "quale modalità", "che modalità sei", "stato modalità", "dimmi la modalità"]):
        return None

    friendly_patterns = [
        r"\b(modalit[aà]|passa|imposta|metti|attiva)\b.*?\b(friendly|amichevole|aperta|libera)\b",
        r"\b(friendly|amichevole)\s+mode\b",
        r"^(hey\s+jarvis\s+|axl\s+)?(modalit[aà]\s+)?(friendly|amichevole)\.?$",
    ]
    if any(re.search(p, lower) for p in friendly_patterns):
        return "friendly"

    restrict_patterns = [
        r"\b(modalit[aà]|passa|imposta|metti|attiva)\b.*?\b(restrict|restrittiva|ristretta|limitata|sicura)\b",
        r"\b(restrict|restrittiva)\s+mode\b",
        r"^(hey\s+jarvis\s+|axl\s+)?(modalit[aà]\s+)?(restrict|restrittiva)\.?$",
    ]
    if any(re.search(p, lower) for p in restrict_patterns):
        return "restrict"

    paranoid_patterns = [
        r"\b(modalit[aà]|passa|imposta|metti|attiva)\b.*?\b(paranoid|paranoica|paranoico|massima\s+sicurezza)\b",
        r"\b(paranoid|paranoica)\s+mode\b",
        r"^(hey\s+jarvis\s+|axl\s+)?(modalit[aà]\s+)?(paranoid|paranoica)\.?$",
    ]
    if any(re.search(p, lower) for p in paranoid_patterns):
        return "paranoid"

    return None


def detect_telemetry_intent(text: str) -> Optional[Dict[str, Any]]:
    """Detects if user utterance expressed intent to show (True) or hide (False) telemetry HUD for server, slave, or kiosk."""
    if not text:
        return None
    lower = text.lower().strip()

    # Determine target if mentioned (defaults to slave if unspecified)
    if any(w in lower for w in ["server", "master", "thinkpad", "locale", "local"]):
        target = "server"
    elif any(w in lower for w in ["kiosk", "android", "telefono", "cellulare", "a33", "phone", "batteria"]):
        target = "kiosk"
    else:
        target = "slave"

    # Close / Hide telemetry patterns
    if any(re.search(p, lower) for p in [
        r"\b(chiudi|nascondi|disattiva|togli|spegni)\s+(la\s+)?telemetria\b",
        r"\btelemetria\s+(off|chiusa|spenta)\b",
        r"\bchiudi\s+(il\s+)?pannello(\s+telemetria)?\b",
    ]):
        return {"show": False, "target": target}

    # Open / Show telemetry patterns
    if any(re.search(p, lower) for p in [
        r"\b(mostra|apri|attiva|visualizza|proietta|fammi\s+vedere)\s+(la\s+)?telemetria\b",
        r"\btelemetria\s+(del\s+|dello\s+)?(server|master|slave|pc|thinkpad|workstation|kiosk|telefono|cellulare)\b",
        r"\b(server|slave|master|pc|kiosk|android)\s+telemetr(ia|y)\b",
        r"\btelemetria\s+(on|aperta|attiva)\b",
        r"\b(stato|monitor)\s+(del\s+)?(server|master|slave|pc|hardware|kiosk|telefono|batteria)\b",
        r"^(hey\s+jarvis\s+|axl\s+)?(server\s+telemetry|slave\s+telemetry|kiosk\s+telemetry|telemetria\s+server|telemetria\s+slave|telemetria\s+pc|telemetria\s+kiosk|telemetria\s+telefono)\.?$",
    ]):
        return {"show": True, "target": target}

    return None


def detect_enrollment_intent(text: str) -> Optional[Dict[str, str]]:
    """
    Rileva se l'utente ha espresso l'intento di aggiornare la propria voce (owner)
    o registrare/aggiungere la voce di un amico.
    Restituisce un dizionario con 'profile_name' se rilevato, altrimenti None.
    """
    if not text:
        return None
    lower = text.lower().strip()

    # 1. Pattern per registrazione amico / terzo (priorità a nomi specifici)
    # Es. "registra la voce di Luca", "registra un nuovo amico: Marco", "aggiungi amico Giovanni"
    friend_match_alt = re.search(
        r"\b(?:registra|calibra|salva|aggiungi)\s+(?:la\s+voce\s+di|il\s+profilo\s+di|l['\s]amico|l['\s]amica|amico|amica)\s*(?:di|per|:)?\s+([a-zA-Z0-9_\-]+)\b",
        lower
    )
    if friend_match_alt:
        name = friend_match_alt.group(1).strip()
        if name and name not in ["la", "il", "un", "una", "mia", "mio", "owner"]:
            return {"profile_name": name}

    friend_match = re.search(
        r"\b(?:registra|aggiungi|salva|crea|memorizza)\s+(?:un\s+)?(?:nuovo\s+)?(?:profilo|amico|amica)\s*(?:di|per|:)?\s+([a-zA-Z0-9_\-]+)\b",
        lower
    )
    if friend_match:
        name = friend_match.group(1).strip()
        stop_words = ["la", "il", "un", "una", "nuovo", "nuova", "profilo", "voce", "amico", "owner", "mia", "mio"]
        if name and name not in stop_words:
            return {"profile_name": name}

    # 2. Pattern per aggiornamento voce proprietario (owner)
    owner_patterns = [
        r"\b(aggiorn\w*|modific\w*|ricalibr\w*|cambi\w*|riconfigur\w*)\s+(la\s+)?(mia\s+)?voce\b",
        r"\b(aggiorn\w*|modific\w*|ricalibr\w*|cambi\w*|registr\w*|riconfigur\w*)\s+(il\s+)?(mio\s+)?profilo(\s+vocale)?\b",
        r"\b(registr\w*)\s+(la\s+)?mia\s+voce\b",
        r"\b(calibr\w*|calibrazione)\s+(della\s+|la\s+)?(mia\s+)?voce\b",
        r"\bnuova\s+calibrazione(\s+vocale)?\b",
        r"\b(update|change|recalibrate)\s+my\s+voice\b",
        r"\bvoice\s+enrollment\b",
    ]
    if any(re.search(p, lower) for p in owner_patterns):
        return {"profile_name": "owner"}

    return None


WORD_NUMBERS = {
    # Italian
    "zero": 0, "un": 1, "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4,
    "cinque": 5, "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10,
    "undici": 11, "dodici": 12, "tredici": 13, "quattordici": 14, "quindici": 15,
    "sedici": 16, "diciassette": 17, "diciotto": 18, "diciannove": 19, "venti": 20,
    "venticinque": 25, "trenta": 30, "quaranta": 40, "cinquanta": 50, "sessanta": 60,
    # English
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "twenty-five": 25, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60,
}


def _parse_it_number(token: str) -> Optional[int]:
    if not token:
        return None
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return WORD_NUMBERS.get(token)


def detect_timer_intent(text: str) -> Optional[Dict[str, Any]]:
    """
    Rileva se l'utente ha chiesto di impostare un timer o conto alla rovescia (Italiano o Inglese).
    Esempi: 'metti un timer di 5 minuti', 'timer 10 minuti per la pasta', 'set a timer for 5 minutes'.
    Resiliente anche ad errori fonetici STT (es. 'time-dix in 5 secondi').
    Restituisce {'duration_seconds': int, 'label': str} oppure None.
    """
    if not text:
        return None
    lower = text.lower().replace("-", " ").strip()

    if not any(k in lower for k in ["timer", "conto alla rovescia", "cronometro", "time"]):
        return None

    # Esclude domande informative (es. 'quanto manca al timer?', 'how much time is left?')
    if any(q in lower for q in ["quanto manca", "stato timer", "a che punto", "how much time", "timer status"]):
        return None

    duration = 0

    # 1. Ore
    hr_match = re.search(r"(\d+|un|uno|due|tre|one|two|three)\s*(?:or[ae]|hour[s]?)\b", lower)
    if hr_match:
        val = _parse_it_number(hr_match.group(1)) or 1
        duration += val * 3600

    # 2. Minuti
    min_match = re.search(
        r"(\d+|un|uno|due|tre|quattro|cinque|sei|sette|otto|nove|dieci|quindici|venti|venticinque|trenta|quaranta|cinquanta|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty|fifty)\s*(?:minut[oi]|min\b|minute[s]?)\b",
        lower
    )
    if min_match:
        val = _parse_it_number(min_match.group(1)) or 0
        duration += val * 60
    elif "mezz'ora" in lower or "mezza ora" in lower or "half an hour" in lower:
        duration += 1800

    # 3. Secondi
    sec_match = re.search(
        r"(\d+|un|uno|due|tre|quattro|cinque|dieci|quindici|venti|trenta|quaranta|cinquanta|one|two|three|four|five|ten|fifteen|twenty|thirty|forty|fifty)\s*(?:second[oi]|sec\b|second[s]?)\b",
        lower
    )
    if sec_match:
        val = _parse_it_number(sec_match.group(1)) or 0
        duration += val

    # 4. Forma breve: 'timer 5', 'timer di 10', 'timer in 5', 'timer for 10'
    if duration == 0:
        bare_match = re.search(r"\btime(?:r)?\s+(?:da|di|per|in|for)?\s*(\d+)\b", lower)
        if bare_match:
            duration = int(bare_match.group(1)) * 60

    if duration <= 0:
        return None

    # Estrazione etichetta opzionale (es. 'per la pasta', 'for the pasta')
    label = "Timer"
    label_match = re.search(r"(?:per|di|for)\s+(?:la\s+|il\s+|le\s+|lo\s+|the\s+)?([a-zA-Z0-9_\-]+)(?:\s+di|\s+da|\s+for|$)", lower)
    if label_match:
        extracted = label_match.group(1).strip()
        stop_words = [
            "minuti", "minuto", "secondi", "secondo", "ore", "ora", "timer", "time", "dix",
            "in", "da", "di", "per", "un", "una", "due", "second", "seconds", "minute", "minutes",
            "for", "the", "one", "two"
        ]
        if extracted not in stop_words:
            label = extracted

    return {"duration_seconds": duration, "label": label}


def detect_alarm_intent(text: str) -> Optional[Dict[str, Any]]:
    """
    Rileva se l'utente ha chiesto di impostare una sveglia (Italiano o Inglese).
    Esempi: 'metti una sveglia alle 7', 'sveglia alle 07:30', 'set an alarm for 7 AM'.
    Restituisce {'time_str': 'HH:MM', 'label': str} oppure None.
    """
    if not text:
        return None
    lower = text.lower().strip()

    if not any(k in lower for k in ["sveglia", "svegliami", "alarm", "wake me up"]):
        return None

    # Esclude domande informative
    if any(q in lower for q in ["a che ora", "quali sveglie", "ho sveglie", "elimina", "cancella", "what time is the alarm", "delete alarm"]):
        return None

    hour = None
    minute = 0

    # 1. Match formato HH:MM es. '07:30', '7:30', '7.30', '7:30 AM'
    m_num = re.search(r"\b([01]?\d|2[0-3])[:\.]([0-5]\d)(?:\s*(am|pm))?\b", lower)
    if m_num:
        hour = int(m_num.group(1))
        minute = int(m_num.group(2))
        meridiem = m_num.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
    else:
        # 2. Match Italiano: 'alle 7', 'per le 8', 'alle sette'
        m_hr = re.search(
            r"(?:alle|per\s+le|verso\s+le|ore)\s+([01]?\d|2[0-3]|un[oa]?|due|tre|quattro|cinque|sei|sette|otto|nove|dieci|undici|dodici)\b",
            lower
        )
        if m_hr:
            hour = _parse_it_number(m_hr.group(1))
            if hour is not None:
                if "mezza" in lower or "mezzo" in lower:
                    minute = 30
                elif "un quarto" in lower or "quindici" in lower:
                    minute = 15
                elif "tre quarti" in lower or "quarantacinque" in lower:
                    minute = 45
                else:
                    m_min = re.search(r"\be\s+(\d+|dieci|quindici|venti|venticinque|trenta|quaranta|cinquanta)\b", lower)
                    if m_min:
                        minute = _parse_it_number(m_min.group(1)) or 0
        else:
            # 3. Match Inglese: 'at 7', 'for 7 am', 'at 8 pm', 'wake me up at 7'
            m_en = re.search(
                r"(?:at|for)\s+([01]?\d|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*(am|pm)?\b",
                lower
            )
            if m_en:
                hour = _parse_it_number(m_en.group(1))
                meridiem = m_en.group(2)
                if hour is not None:
                    if meridiem == "pm" and hour < 12:
                        hour += 12
                    elif meridiem == "am" and hour == 12:
                        hour = 0

    if hour is None:
        return None

    time_str = f"{hour:02d}:{minute:02d}"
    label = "Alarm" if detect_language(text) == "en" else "Sveglia"
    label_match = re.search(r"(?:per|di|for)\s+(?:il\s+|la\s+|the\s+)?([a-zA-Z0-9_\-]+)$", lower)
    if label_match:
        ext = label_match.group(1).strip()
        if ext not in ["mezza", "quarto", "mattina", "sera", "sveglia", "domani", "oggi", "alarm", "morning"]:
            label = ext

    return {"time_str": time_str, "label": label}


def _add_months(sourcedate: datetime.date, months: int) -> datetime.date:
    """Aggiunge o sottrae un numero di mesi a una data gestendo i giorni variabili."""
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    max_days = [
        31,
        29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
        31, 30, 31, 30, 31, 31, 30, 31, 30, 31
    ][month - 1]
    day = min(sourcedate.day, max_days)
    return datetime.date(year, month, day)


def parse_relative_date(text: str, base_date: Optional[datetime.date] = None) -> Dict[str, Any]:
    """
    Interpreta espressioni temporali relative e assolute (Italiano e Inglese) come:
    'domani', 'dopodomani', 'ieri', 'l'altro ieri', 'tra 3 giorni', 'tra una settimana',
    'tra 2 mesi', 'il 25 dicembre', 'tomorrow', 'in 3 days', 'in a week', 'next month'.
    Restituisce un dizionario con:
    - 'target_date': datetime.date
    - 'desc_it': str (es. 'domani', 'tra 3 giorni')
    - 'desc_en': str (es. 'tomorrow', 'in 3 days')
    - 'is_explicit': bool
    """
    if base_date is None:
        base_date = datetime.date.today()
    if not text:
        return {"target_date": base_date, "desc_it": "oggi", "desc_en": "today", "is_explicit": False}

    lower = text.lower().strip()
    target_date = None
    desc_it = ""
    desc_en = ""
    is_explicit = False

    # 1. Dopodomani / The day after tomorrow
    if re.search(r"\b(dopodomani|dopo\s+domani|l['\s]altro\s+domani)\b", lower) or "day after tomorrow" in lower:
        target_date = base_date + datetime.timedelta(days=2)
        desc_it = "dopodomani"
        desc_en = "the day after tomorrow"

    # 2. Domani / Tomorrow
    elif re.search(r"\bdomani\b", lower) or re.search(r"\btomorrow\b", lower):
        target_date = base_date + datetime.timedelta(days=1)
        desc_it = "domani"
        desc_en = "tomorrow"

    # 3. L'altro ieri / The day before yesterday
    elif re.search(r"\b(l['\s]altro\s+ieri|ieri\s+l['\s]altro|altro\s+ieri)\b", lower) or "day before yesterday" in lower:
        target_date = base_date - datetime.timedelta(days=2)
        desc_it = "l'altro ieri"
        desc_en = "the day before yesterday"

    # 4. Ieri / Yesterday
    elif re.search(r"\bieri\b", lower) or re.search(r"\byesterday\b", lower):
        target_date = base_date - datetime.timedelta(days=1)
        desc_it = "ieri"
        desc_en = "yesterday"

    # 5. Oggi / Today
    elif re.search(r"\boggi\b", lower) or re.search(r"\btoday\b", lower):
        target_date = base_date
        desc_it = "oggi"
        desc_en = "today"

    # 6. Offset Giorni (Days)
    elif m := re.search(r"\b(?:tra|fra)\s+(\d+|[a-zA-Z]+)\s+giorn[io]\b", lower):
        n = _parse_it_number(m.group(1)) or 1
        target_date = base_date + datetime.timedelta(days=n)
        desc_it = f"tra {n} giorni" if n > 1 else "tra un giorno"
        desc_en = f"in {n} days" if n > 1 else "in 1 day"
    elif m := re.search(r"\b(\d+|[a-zA-Z]+)\s+giorn[io]\s+fa\b", lower):
        n = _parse_it_number(m.group(1)) or 1
        target_date = base_date - datetime.timedelta(days=n)
        desc_it = f"{n} giorni fa" if n > 1 else "un giorno fa"
        desc_en = f"{n} days ago" if n > 1 else "1 day ago"
    elif m := re.search(r"\bin\s+(\d+|[a-zA-Z]+)\s+days?\b", lower):
        n = _parse_it_number(m.group(1)) or 1
        target_date = base_date + datetime.timedelta(days=n)
        desc_it = f"tra {n} giorni" if n > 1 else "tra un giorno"
        desc_en = f"in {n} days" if n > 1 else "in 1 day"
    elif m := re.search(r"\b(\d+|[a-zA-Z]+)\s+days?\s+ago\b", lower):
        n = _parse_it_number(m.group(1)) or 1
        target_date = base_date - datetime.timedelta(days=n)
        desc_it = f"{n} giorni fa" if n > 1 else "un giorno fa"
        desc_en = f"{n} days ago" if n > 1 else "1 day ago"

    # 7. Offset Settimane (Weeks)
    elif m := re.search(r"\b(?:tra|fra)\s+(\d+|[a-zA-Z]+)\s+settiman[ae]\b", lower):
        n = _parse_it_number(m.group(1)) or 1
        target_date = base_date + datetime.timedelta(days=n * 7)
        desc_it = f"tra {n} settimane" if n > 1 else "tra una settimana"
        desc_en = f"in {n} weeks" if n > 1 else "in a week"
    elif re.search(r"\b(la\s+prossima\s+settimana|settimana\s+prossima|next\s+week)\b", lower):
        target_date = base_date + datetime.timedelta(days=7)
        desc_it = "la prossima settimana"
        desc_en = "next week"
    elif m := re.search(r"\bin\s+(\d+|[a-zA-Z]+)\s+weeks?\b", lower):
        n = _parse_it_number(m.group(1)) or 1
        target_date = base_date + datetime.timedelta(days=n * 7)
        desc_it = f"tra {n} settimane" if n > 1 else "tra una settimana"
        desc_en = f"in {n} weeks" if n > 1 else "in a week"
    elif m := re.search(r"\b(\d+|[a-zA-Z]+)\s+settiman[ae]\s+fa\b", lower):
        n = _parse_it_number(m.group(1)) or 1
        target_date = base_date - datetime.timedelta(days=n * 7)
        desc_it = f"{n} settimane fa" if n > 1 else "una settimana fa"
        desc_en = f"{n} weeks ago" if n > 1 else "1 week ago"
    elif m := re.search(r"\b(\d+|[a-zA-Z]+)\s+weeks?\s+ago\b", lower):
        n = _parse_it_number(m.group(1)) or 1
        target_date = base_date - datetime.timedelta(days=n * 7)
        desc_it = f"{n} settimane fa" if n > 1 else "una settimana fa"
        desc_en = f"{n} weeks ago" if n > 1 else "1 week ago"

    # 8. Offset Mesi (Months)
    elif m := re.search(r"\b(?:tra|fra)\s+(\d+|[a-zA-Z]+)\s+mes[ie]\b", lower):
        n = _parse_it_number(m.group(1)) or 1
        target_date = _add_months(base_date, n)
        desc_it = f"tra {n} mesi" if n > 1 else "tra un mese"
        desc_en = f"in {n} months" if n > 1 else "in a month"
    elif m := re.search(r"\bin\s+(\d+|[a-zA-Z]+)\s+months?\b", lower):
        n = _parse_it_number(m.group(1)) or 1
        target_date = _add_months(base_date, n)
        desc_it = f"tra {n} mesi" if n > 1 else "tra un mese"
        desc_en = f"in {n} months" if n > 1 else "in a month"
    elif re.search(r"\b(il\s+prossimo\s+mese|mese\s+prossimo|next\s+month)\b", lower):
        target_date = _add_months(base_date, 1)
        desc_it = "il mese prossimo"
        desc_en = "next month"
    elif m := re.search(r"\b(\d+|[a-zA-Z]+)\s+mes[ie]\s+fa\b", lower):
        n = _parse_it_number(m.group(1)) or 1
        target_date = _add_months(base_date, -n)
        desc_it = f"{n} mesi fa" if n > 1 else "un mese fa"
        desc_en = f"{n} months ago" if n > 1 else "1 month ago"
    elif m := re.search(r"\b(\d+|[a-zA-Z]+)\s+months?\s+ago\b", lower):
        n = _parse_it_number(m.group(1)) or 1
        target_date = _add_months(base_date, -n)
        desc_it = f"{n} mesi fa" if n > 1 else "un mese fa"
        desc_en = f"{n} months ago" if n > 1 else "1 month ago"

    # 9. Offset Anni (Years)
    elif m := re.search(r"\b(?:tra|fra)\s+(\d+|[a-zA-Z]+)\s+ann[oi]\b", lower):
        n = _parse_it_number(m.group(1)) or 1
        target_date = _add_months(base_date, n * 12)
        desc_it = f"tra {n} anni" if n > 1 else "tra un anno"
        desc_en = f"in {n} years" if n > 1 else "in a year"
    elif m := re.search(r"\bin\s+(\d+|[a-zA-Z]+)\s+years?\b", lower):
        n = _parse_it_number(m.group(1)) or 1
        target_date = _add_months(base_date, n * 12)
        desc_it = f"tra {n} anni" if n > 1 else "tra un anno"
        desc_en = f"in {n} years" if n > 1 else "in a year"

    # 10. Data di calendario esplicita (es. 25 dicembre, December 25th)
    if target_date is None:
        it_months = {
            "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
            "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12
        }
        for m_name, m_num in it_months.items():
            if m_name in lower:
                m_exp = re.search(r"\b(\d{1,2})\s+" + m_name + r"\b", lower) or re.search(r"\b" + m_name + r"\s+(\d{1,2})\b", lower)
                if m_exp:
                    day = int(m_exp.group(1))
                    year = base_date.year
                    try:
                        target_date = datetime.date(year, m_num, day)
                        desc_it = f"il {day} {m_name}"
                        desc_en = f"{m_name.capitalize()} {day}"
                        is_explicit = True
                        break
                    except ValueError:
                        pass

        if target_date is None:
            en_months = {
                "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
                "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12
            }
            for m_name, m_num in en_months.items():
                if m_name in lower:
                    m_exp = re.search(r"\b" + m_name + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b", lower) or re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + m_name + r"\b", lower)
                    if m_exp:
                        day = int(m_exp.group(1))
                        year = base_date.year
                        try:
                            target_date = datetime.date(year, m_num, day)
                            desc_it = f"il {day} {m_name}"
                            desc_en = f"{m_name.capitalize()} {day}"
                            is_explicit = True
                            break
                        except ValueError:
                            pass

    # Default su oggi se nessuna specifica temporale è fornita
    if target_date is None:
        target_date = base_date
        desc_it = "oggi"
        desc_en = "today"

    return {
        "target_date": target_date,
        "desc_it": desc_it,
        "desc_en": desc_en,
        "is_explicit": is_explicit,
    }


def detect_clock_date_intent(text: str) -> Optional[str]:
    """
    Rileva se l'utente chiede l'ora esatta ('time'), la data/giorno corrente o relativo ('date'),
    oppure entrambi combinati ('both') in Italiano o Inglese.
    """
    if not text:
        return None
    lower = text.lower().strip()

    time_patterns = [
        # Italian
        r"\bche\s+(?:ore\s+sono|ora\s+[eè]|orario\s+[eè])\b",
        r"\bdimmi\s+l['\s]ora\b",
        r"\b(?:qual\s+[eè]\s+l['\s]|dammi\s+l['\s])ora\b",
        r"^l['\s]ora(\s+esatta)?\.?$",
        r"\borario\b",
        # English
        r"\bwhat\s+(?:time\s+is\s+it|is\s+the\s+time)\b",
        r"\bwhat['\s]s\s+the\s+time\b",
        r"\btell\s+me\s+the\s+time\b",
        r"\bcurrent\s+time\b",
        r"\bwhat\s+time\b",
        r"^time\.?$",
    ]

    date_patterns = [
        # Italian
        r"\bche\s+(?:giorno|data)\b",
        r"\bqual\s+[eè]\s+(?:il\s+giorno|la\s+data)\b",
        r"\bquale\s+giorno\b",
        r"\bdata\s+di\s+oggi\b",
        r"\bdimmi\s+la\s+data\b",
        r"\bquanti\s+ne\s+abbiamo\b",
        r"\ba\s+che\s+giorno\s+siamo\b",
        r"\bquando\s+viene\b",
        r"\bquando\s+[eè]\b",
        r"\bdomani\s+che\s+giorno\b",
        r"\bdopodomani\s+che\s+giorno\b",
        r"\bieri\s+che\s+giorno\b",
        # English
        r"\bwhat\s+(?:day|date)\b",
        r"\bwhich\s+(?:day|date)\b",
        r"\bwhat['\s]s\s+today['\s]s\s+date\b",
        r"\bwhat\s+is\s+today['\s]s\s+date\b",
        r"\btell\s+me\s+the\s+date\b",
        r"\btell\s+me\s+the\s+day\b",
        r"\btoday['\s]s\s+date\b",
        r"\btomorrow\s+what\s+day\b",
        r"\byesterday\s+what\s+day\b",
        r"^date\.?$",
    ]

    has_time = any(re.search(p, lower) for p in time_patterns)
    has_date = any(re.search(p, lower) for p in date_patterns)

    if has_time and has_date:
        return "both"
    elif has_time:
        return "time"
    elif has_date:
        return "date"

    return None


def is_compound_ai_query(text: str) -> bool:
    """
    Rileva se una richiesta dell'utente è un comando composto multi-azione
    o combina azioni locali con capacità avanzate dell'AI (musica, calendario, meteo, PC slave),
    richiedendo l'orchestrazione cognitiva di Gemini invece del Fast Path locale.
    """
    if not text:
        return False
    lower = text.lower().strip()

    # Presenza di congiunzioni coordinative di azioni
    conjunction_patterns = [
        r"\b(e\s+poi|e\s+anche|poi|inoltre)\b",
        r"\b(and\s+then|and\s+also|after\s+that)\b",
        r"\b(and|e|ed)\b",
    ]
    has_conjunction = any(re.search(p, lower) for p in conjunction_patterns)
    if not has_conjunction:
        return False

    # Presenza di domini esterni gestiti dall'AI
    external_domains = [
        # Musica / Spotify
        r"\b(play|music|song|spotify|canzone|musica|suona|riproduci|traccia)\b",
        # Calendario & Task
        r"\b(calendar|calendario|eventi|appuntament[oi]|promemoria|task[s]?|compit[oi]|to-?do|impegn[oi])\b",
        # Meteo & Notizie
        r"\b(weather|meteo|previsioni|piove|sole|temperatura|notizie|news)\b",
        # Domande generali & Conoscenza
        r"\b(chi [eè]|who is|perch[eé]|why|come mai|spiegami|explain|cerca|search|raccontami|tell me about|cos['\s][eè]|what is)\b",
        # Comandi Slave PC
        r"\b(slave|computer|pc|spegni il pc|apri youtube|open youtube)\b",
    ]
    if any(re.search(p, lower) for p in external_domains):
        return True

    # Se sono concatenati più intenti fast-path distinti (es. Timer + Sveglia, Timer + Muto)
    intents_count = 0
    if detect_timer_intent(lower):
        intents_count += 1
    if detect_alarm_intent(lower):
        intents_count += 1
    if is_mute_intent(lower) or is_unmute_intent(lower):
        intents_count += 1
    if detect_security_mode_intent(lower):
        intents_count += 1
    if detect_telemetry_intent(lower):
        intents_count += 1

    if intents_count > 1:
        return True

    cd_intent = detect_clock_date_intent(lower)
    if cd_intent in ["time", "date"] and intents_count >= 1:
        return True

    return False


def handle_local_fast_path(
    text: str,
    is_authorized: bool = True,
    identity: str = "owner",
    security_mode: str = "friendly",
    similarity_score: float = 1.0,
    lang: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Tier-1 Local Fast Path / Intent Router (Bilingual Italian / English).
    Intercetta comandi deterministici (ora, data, ora+data, timer, sveglia, muto, smuto, telemetria, sicurezza)
    ed esegue l'azione all'istante in locale (<5ms) senza chiamare Gemini.
    Se rileva comandi composti complessi o multi-azione, cede il passo all'AI di Gemini.
    """
    if not text or not text.strip():
        return None

    # Guardrail: se il comando è composto e richiede l'orchestrazione di più azioni o capacità AI,
    # delega direttamente a Gemini Cognitive Brain
    if is_compound_ai_query(text):
        logger.info(f"[LOCAL FAST PATH] Compound multi-action query detected ('{text}'). Delegating to Gemini AI Brain.")
        return None

    if lang is None:
        lang = detect_language(text)

    # 1. Ora esatta e Data (supporta ora, data corrente, date relative come domani/tra n giorni/settimane, ed entrambe combinate)
    c_type = detect_clock_date_intent(text)
    if c_type == "both":
        now = datetime.datetime.now()
        time_str = now.strftime('%H:%M')
        base_d = now.date()
        date_info = parse_relative_date(text, base_date=base_d)
        tgt_date = date_info["target_date"]
        desc_it = date_info["desc_it"]
        desc_en = date_info["desc_en"]
        if lang == "en":
            f_en = tgt_date.strftime('%A, %B %d, %Y')
            if tgt_date == base_d:
                reply = f"It's {time_str} and today is {f_en}."
            elif tgt_date < base_d:
                reply = f"It's {time_str} and {desc_en} was {f_en}."
            else:
                reply = f"It's {time_str} and {desc_en} will be {f_en}."
        else:
            giorni = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
            mesi = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
            g_str = giorni[tgt_date.weekday()]
            m_str = mesi[tgt_date.month - 1]
            f_it = f"{g_str} {tgt_date.day} {m_str} {tgt_date.year}"
            if tgt_date == base_d:
                reply = f"Sono le {time_str} e oggi è {f_it}."
            elif tgt_date < base_d:
                reply = f"Sono le {time_str} e {desc_it} era {f_it}."
            else:
                reply = f"Sono le {time_str} e {desc_it} sarà {f_it}."
        return {
            "handled": True,
            "type": "time_and_date",
            "reply": reply
        }
    elif c_type == "time":
        now = datetime.datetime.now()
        time_str = now.strftime('%H:%M')
        reply = f"It's {time_str}." if lang == "en" else f"Sono le {time_str}."
        return {
            "handled": True,
            "type": "time",
            "reply": reply
        }
    elif c_type == "date":
        now = datetime.datetime.now()
        base_d = now.date()
        date_info = parse_relative_date(text, base_date=base_d)
        tgt_date = date_info["target_date"]
        desc_it = date_info["desc_it"]
        desc_en = date_info["desc_en"]
        is_exp = date_info.get("is_explicit", False)

        if lang == "en":
            f_en = tgt_date.strftime('%A, %B %d, %Y')
            if is_exp:
                weekday_en = tgt_date.strftime('%A')
                reply = f"{desc_en}, {tgt_date.year} will be {weekday_en}."
            elif tgt_date == base_d:
                reply = f"Today is {f_en}."
            elif tgt_date < base_d:
                cap = desc_en.capitalize()
                reply = f"{cap} was {f_en}."
            else:
                cap = desc_en.capitalize()
                if desc_en.startswith("in "):
                    reply = f"{cap} it will be {f_en}."
                else:
                    reply = f"{cap} will be {f_en}."
        else:
            giorni = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
            mesi = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
            g_str = giorni[tgt_date.weekday()]
            m_str = mesi[tgt_date.month - 1]
            f_it = f"{g_str} {tgt_date.day} {m_str} {tgt_date.year}"

            if is_exp:
                cap = desc_it[0].upper() + desc_it[1:]
                reply = f"{cap} {tgt_date.year} sarà {g_str}."
            elif tgt_date == base_d:
                reply = f"Oggi è {f_it}."
            elif tgt_date < base_d:
                cap = desc_it[0].upper() + desc_it[1:]
                reply = f"{cap} era {f_it}."
            else:
                cap = desc_it[0].upper() + desc_it[1:]
                reply = f"{cap} sarà {f_it}."

        return {
            "handled": True,
            "type": "date",
            "reply": reply
        }

    # 2. Timer
    t_info = detect_timer_intent(text)
    if t_info:
        duration = t_info["duration_seconds"]
        lbl = t_info["label"]
        set_timer(duration_seconds=duration, label=lbl)
        mins = duration // 60
        secs = duration % 60
        if lang == "en":
            parts = []
            if mins:
                parts.append(f"{mins} minutes" if mins > 1 else "one minute")
            if secs:
                parts.append(f"{secs} seconds" if secs > 1 else "one second")
            dur_text = " and ".join(parts) if parts else f"{duration} seconds"
            lbl_text = f" for {lbl}" if lbl and lbl.lower() != "timer" else ""
            reply = f"*bark* Timer set for {dur_text}{lbl_text}."
        else:
            if mins and not secs:
                dur_text = f"{mins} minuti" if mins > 1 else "un minuto"
            elif secs and not mins:
                dur_text = f"{secs} secondi"
            else:
                dur_text = f"{mins} minuti e {secs} secondi"
            lbl_text = f" per {lbl}" if lbl and lbl != "Timer" else ""
            reply = f"*bark* Timer impostato a {dur_text}{lbl_text}."
        return {
            "handled": True,
            "type": "set_timer",
            "reply": reply
        }

    # 3. Sveglia
    a_info = detect_alarm_intent(text)
    if a_info:
        t_str = a_info["time_str"]
        lbl = a_info["label"]
        set_alarm(time_str=t_str, label=lbl)
        if lang == "en":
            lbl_text = f" for {lbl}" if lbl and lbl.lower() not in ["alarm", "sveglia"] else ""
            reply = f"*bark* Alarm set for {t_str}{lbl_text}."
        else:
            lbl_text = f" per {lbl}" if lbl and lbl != "Sveglia" else ""
            reply = f"*bark* Sveglia impostata per le {t_str}{lbl_text}."
        return {
            "handled": True,
            "type": "set_alarm",
            "reply": reply
        }

    # 4. Smuto / Riattivazione microfono (Priorità su muto per evitare fraintendimenti)
    if is_unmute_intent(text):
        if not is_authorized and security_mode in ["restrict", "paranoid"]:
            return {
                "handled": True,
                "type": "unmute_rejected",
                "reply": "*growl* Access denied. Only authorized users can unmute the microphone." if lang == "en" else "*growl* Accesso negato. Solo persone autorizzate possono riattivare il microfono."
            }
        set_mute_mode(enabled=False)
        return {
            "handled": True,
            "type": "set_unmute",
            "reply": "*bark* Microphones reactivated. I am listening again." if lang == "en" else "*bark* Ricevuto, microfoni riattivati. Sono di nuovo in ascolto."
        }

    # 5. Muto / Disattiva microfono
    if is_mute_intent(text):
        if not is_authorized and security_mode in ["restrict", "paranoid"]:
            return {
                "handled": True,
                "type": "mute_rejected",
                "reply": "*growl* Access denied. Only authorized users can mute the microphone." if lang == "en" else "*growl* Accesso negato. Solo persone autorizzate possono disattivare il microfono."
            }
        set_mute_mode(enabled=True)
        return {
            "handled": True,
            "type": "set_mute",
            "reply": "*bark* Understood, going on mute. Remember you can only unmute me by tapping the button on the visor." if lang == "en" else "*bark* Ricevuto, vado in muto. Ricordati che puoi riattivarmi solo premendo il pulsante sul visore."
        }

    # 6. Cambio Modalità di Sicurezza
    req_mode = detect_security_mode_intent(text)
    if req_mode:
        can_switch = False
        if security_mode == "friendly":
            can_switch = True
        elif security_mode == "restrict":
            can_switch = is_authorized
        elif security_mode == "paranoid":
            can_switch = is_authorized and (identity.lower() in ["owner", "admin_terminal"])

        if can_switch:
            set_security_mode(req_mode)
            reply = f"*bark* Security mode switched to {req_mode.upper()}." if lang == "en" else f"*bark* Modalità di sicurezza impostata su {req_mode.upper()}."
            return {
                "handled": True,
                "type": "security_mode",
                "reply": reply
            }
        else:
            reply = f"*growl* Access denied. You are not authorized to modify security level in {security_mode.upper()} mode." if lang == "en" else f"*growl* Accesso negato. Non sei autorizzato a modificare il livello di sicurezza in modalità {security_mode.upper()}."
            return {
                "handled": True,
                "type": "security_mode_rejected",
                "reply": reply
            }

    # 7. Telemetria HUD
    telem = detect_telemetry_intent(text)
    if telem is not None:
        show = telem.get("show", True)
        tgt = telem.get("target", "slave")
        toggle_telemetry_hud(show=show, target=tgt)
        if not show:
            reply = "*bark* Telemetry HUD closed." if lang == "en" else "*bark* Schermata telemetria chiusa."
            return {
                "handled": True,
                "type": "telemetry",
                "reply": reply
            }
        else:
            if lang == "en":
                node_str = "server" if tgt == "server" else ("kiosk" if tgt == "kiosk" else "slave computer")
                reply = f"*bark* {node_str.capitalize()} telemetry projected on visor."
            else:
                node_str = "del server" if tgt == "server" else ("del kiosk" if tgt == "kiosk" else "del computer slave")
                reply = f"*bark* Telemetria {node_str} proiettata sul visore."
            return {
                "handled": True,
                "type": "telemetry",
                "reply": reply
            }

    # 8. Voice Enrollment
    enroll = detect_enrollment_intent(text)
    if enroll is not None:
        pname = enroll.get("profile_name", "owner")
        can_enroll = is_authorized if security_mode == "friendly" else (is_authorized and identity.lower() in ["owner", "admin_terminal"])
        if can_enroll:
            enroll_voice_profile(profile_name=pname)
            reply = f"*bark* Voice calibration session started for {pname}. Look at the visor." if lang == "en" else f"*bark* Sessione di calibrazione vocale avviata per {pname}. Guarda il visore."
            return {
                "handled": True,
                "type": "enrollment",
                "reply": reply
            }
        else:
            reply = "*growl* Access denied. You cannot recalibrate voice profiles." if lang == "en" else "*growl* Accesso negato. Non puoi ricalibrare i profili vocali."
            return {
                "handled": True,
                "type": "enrollment_rejected",
                "reply": reply
            }

    return None


# Registry of tools exposed to Gemini function calling
REGISTERED_TOOLS: List[Callable[..., Any]] = [
    get_current_time,
    set_timer,
    set_alarm,
    get_system_status,
    get_server_telemetry,
    get_slave_telemetry,
    toggle_telemetry_hud,
    media_control,
    play_music,
    execute_slave_command,
    set_security_mode,
    set_mute_mode,
    enroll_voice_profile,
    add_task,
    list_tasks,
    complete_task,
    create_calendar_event,
    get_calendar_events,
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
