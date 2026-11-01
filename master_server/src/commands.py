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
    Shows or hides the live hardware telemetry HUD monitor on the left of the Visor screen.
    Call this when the user asks to see/show/open or hide/close telemetry, system stats, or hardware monitors.

    Args:
        show: True to show the telemetry monitor panel on the left, False to hide it.
        target: Target node to monitor: 'server' (for the Master Server host machine) or 'slave' (for the remote PC / workstation node). Default is 'slave'.
    """
    clean_target = str(target).lower().strip()
    if clean_target in ["server", "master", "thinkpad", "locale", "local"]:
        resolved_target = "server"
    else:
        resolved_target = "slave"

    logger.info(f"[COMMAND: toggle_telemetry_hud] Show: {show} | Target: '{resolved_target}' (raw: '{target}')")
    if not show:
        notify_telemetry({"type": "TELEMETRY_MODE", "enabled": False, "target": resolved_target})
        node_label = "del server" if resolved_target == "server" else "dello slave"
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
        is_privilege_granted = is_auth and (identity.lower() == "owner")

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
        if identity.lower() != "owner":
            logger.warning(
                f"[ENROLL SECURITY REJECTED] Caller '{identity}' cannot modify 'owner' profile in mode '{sec_mode}'."
            )
            return (
                "COMANDO RIFIUTATO: Accesso biometrico negato. Solo il proprietario autenticato "
                "può aggiornare o ricalibrare la firma vocale principale dell'Owner."
            )
    else:
        # 2. Registrazione di un amico:
        if sec_mode in ["restrict", "paranoid"] and identity.lower() != "owner":
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

def is_mute_intent(text: str) -> bool:
    """Detects if user utterance expressed intent to mute / silence A-X-L."""
    if not text:
        return False
    lower = text.lower().strip()
    if any(q in lower for q in [
        "sei in muto", "sei muto", "perché sei muto", "stato muto", "stato del muto",
        "are you muted", "are you on mute", "why are you muted",
    ]):
        return False
    patterns = [
        # Italian
        r"\b(vai\s+in\s+|mettiti\s+in\s+|metti\s+in\s+|modalit[aà]\s+)?muto\b",
        r"\b(disattiva|disattivare|disabilita|spegni|spegnere|chiudi|blocca)\s+(il\s+|l['\s])?(microfon[oi]|mic|ascolto|audio)\b",
        r"\b(microfon[oi]|mic|ascolto)\s+(disattivat[oi]|spent[oi]|disabilitat[oi]|off|chius[oi])\b",
        r"\bsilenzio\b",
        r"\bstai\s+(zitto|buono|in silenzio)\b",
        r"\btaci\b",
        r"\bnon\s+ascoltare\b",
        r"\bsmetti\s+di\s+ascoltare\b",
        # English
        r"\bmute\b",
        r"\bgo\s+(on\s+)?mute\b",
        r"\b(turn|switch|shut)\s+off\s+(the\s+|your\s+)?(mic|microphone|listening)\b",
        r"\b(disable|deactivate|silence)\s+(the\s+|your\s+)?(mic|microphone|listening)\b",
        r"\b(mic|microphone)\s+(off|disabled|deactivated|muted)\b",
        r"\b(be\s+quiet|shut\s+up|stay\s+silent|keep\s+quiet)\b",
        r"\bstop\s+listening\b",
    ]
    return any(re.search(p, lower) for p in patterns)


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
        r"\b(modalit[aà]|passa in|passa a|imposta|metti|attiva)\s+(friendly|amichevole|aperta|libera)\b",
        r"\b(friendly|amichevole)\s+mode\b",
        r"\bmodalit[aà]\s+friendly\b",
        r"^(hey\s+jarvis\s+|axl\s+)?(modalit[aà]\s+)?(friendly|amichevole)\.?$",
    ]
    if any(re.search(p, lower) for p in friendly_patterns):
        return "friendly"

    restrict_patterns = [
        r"\b(modalit[aà]|passa in|passa a|imposta|metti|attiva)\s+(restrict|restrittiva|ristretta|limitata|sicura)\b",
        r"\b(restrict|restrittiva)\s+mode\b",
        r"\bmodalit[aà]\s+restrict\b",
        r"^(hey\s+jarvis\s+|axl\s+)?(modalit[aà]\s+)?(restrict|restrittiva)\.?$",
    ]
    if any(re.search(p, lower) for p in restrict_patterns):
        return "restrict"

    paranoid_patterns = [
        r"\b(modalit[aà]|passa in|passa a|imposta|metti|attiva)\s+(paranoid|paranoica|paranoico|massima\s+sicurezza)\b",
        r"\b(paranoid|paranoica)\s+mode\b",
        r"\bmodalit[aà]\s+paranoica\b",
        r"^(hey\s+jarvis\s+|axl\s+)?(modalit[aà]\s+)?(paranoid|paranoica)\.?$",
    ]
    if any(re.search(p, lower) for p in paranoid_patterns):
        return "paranoid"

    return None


def detect_telemetry_intent(text: str) -> Optional[Dict[str, Any]]:
    """Detects if user utterance expressed intent to show (True) or hide (False) telemetry HUD for server or slave."""
    if not text:
        return None
    lower = text.lower().strip()

    # Determine target if mentioned (defaults to slave if unspecified)
    target = "server" if any(w in lower for w in ["server", "master", "thinkpad", "locale", "local"]) else "slave"

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
        r"\btelemetria\s+(del\s+|dello\s+)?(server|master|slave|pc|thinkpad|workstation)\b",
        r"\b(server|slave|master|pc)\s+telemetr(ia|y)\b",
        r"\btelemetria\s+(on|aperta|attiva)\b",
        r"\b(stato|monitor)\s+(del\s+)?(server|master|slave|pc|hardware)\b",
        r"^(hey\s+jarvis\s+|axl\s+)?(server\s+telemetry|slave\s+telemetry|telemetria\s+server|telemetria\s+slave|telemetria\s+pc)\.?$",
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
    execute_slave_command,
    set_security_mode,
    set_mute_mode,
    enroll_voice_profile,
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
