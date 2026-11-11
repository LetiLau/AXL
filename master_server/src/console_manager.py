"""
A-X-L Server Admin Console (Minecraft-style Terminal REPL)
Runs as an interactive command-line interface in the Master Server terminal.
Allows full administrative control with direct root privileges (bypassing biometric guards).
Supports commands and subcommands in both English and Italian aliases.
"""

import asyncio
import logging
import os
import sys
import threading
from typing import Any, Optional

from task_manager import task_manager
from tts_engine import detect_language

logger = logging.getLogger("AXL-Console")


class ConsoleManager:
    """Manages the background interactive REPL for Master Server administration."""

    def __init__(self, stream_manager: Any, loop: asyncio.AbstractEventLoop):
        self.stream_manager = stream_manager
        self.loop = loop
        self.running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Starts the console reader thread if running in an interactive environment."""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._console_loop, name="AXL-AdminConsole", daemon=True)
        self._thread.start()
        logger.info("[AXL-Console] Admin REPL interactive thread started.")

    def stop(self):
        self.running = False

    def _console_loop(self):
        """Background thread reading from sys.stdin."""
        if not sys.stdin:
            return

        # Short pause so server startup banners print first
        import time
        time.sleep(1.0)

        print("\n" + "=" * 72)
        print(" [AXL-CONSOLE] Server Admin Terminal Active (Minecraft-style CLI)")
        print(" Type /help for available admin commands. (Admin privileges enabled)")
        print("=" * 72 + "\n", flush=True)

        while self.running:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                if line.startswith("/"):
                    asyncio.run_coroutine_threadsafe(
                        self.handle_command(line),
                        self.loop,
                    )
                else:
                    # Regular text without leading slash -> send as /ask query
                    asyncio.run_coroutine_threadsafe(
                        self.handle_command(f"/ask {line}"),
                        self.loop,
                    )
            except Exception as e:
                logger.debug(f"[AXL-Console] Stdin read exception: {e}")
                break

    async def handle_command(self, raw_cmd: str):
        """Dispatches an admin slash command on the asyncio event loop."""
        parts = raw_cmd.strip().split(maxsplit=1)
        cmd_name = parts[0].lower()
        args_str = parts[1].strip() if len(parts) > 1 else ""

        try:
            # For actionable admin commands, broadcast terminal admin identity to Visor HUD
            if cmd_name not in ["/help", "/h", "/?", "/aiuto", "/clear", "/cls", "/status", "/info", "/stato"]:
                await self.stream_manager.broadcast_ui({
                    "type": "AUTH_EVENT",
                    "identity": "ADMIN_TERMINAL",
                    "score": 1.0,
                    "authorized": True,
                })

            # 1. HELP
            if cmd_name in ["/help", "/h", "/?", "/aiuto"]:
                self._print_help()

            # 2. SPEAK / ANSWER (with auto-detect and optional --en / --it language flags)
            elif cmd_name in ["/speak", "/say", "/answer", "/parla", "/rispondi"]:
                await self._handle_speak_command(args_str)

            # 3. ASK A-X-L (Unified Two-Tier Processing: Fast Path -> Gemini Fallback)
            elif cmd_name in ["/ask", "/query", "/q", "/chiedi", "/domanda"]:
                if not args_str:
                    print("[Console] Usage: /ask <query for A-X-L>")
                    return
                print(f"[Console -> A-X-L]: \"{args_str}\"")
                sec_mode = self.stream_manager.verifier.security_mode if self.stream_manager.verifier else "friendly"
                cmd_lang = detect_language(args_str)
                reply = await self.stream_manager.process_text_command(
                    transcript=args_str,
                    identity="ADMIN_TERMINAL",
                    is_authorized=True,
                    sec_mode=sec_mode,
                    sim_score=1.0,
                    lang=cmd_lang,
                )
                print(f"[A-X-L Reply]: {reply}")

            # 4. SECURITY MODE
            elif cmd_name in ["/mode", "/security", "/modalita", "/sicurezza"]:
                target_mode = args_str.lower().strip()
                # Italian alias mapping
                mode_map = {
                    "amichevole": "friendly", "amico": "friendly", "aperta": "friendly", "friendly": "friendly",
                    "restrittiva": "restrict", "sicura": "restrict", "restrict": "restrict",
                    "paranoica": "paranoid", "massima": "paranoid", "paranoid": "paranoid"
                }
                resolved_mode = mode_map.get(target_mode, target_mode)
                if resolved_mode not in ["friendly", "restrict", "paranoid"]:
                    print("[Console] Usage: /mode <friendly | restrict | paranoid>")
                    return
                await self.stream_manager.handle_telemetry_event({
                    "type": "SECURITY_MODE",
                    "mode": resolved_mode,
                })
                print(f"[Console] Security mode updated to: {resolved_mode.upper()}")

            # 5. MUTE / UNMUTE
            elif cmd_name in ["/mute", "/muto"]:
                is_en = (cmd_name == "/mute")
                req_text = "go on mute" if is_en else "vai in muto"
                reply = await self.stream_manager.process_text_command(
                    req_text,
                    identity="ADMIN_TERMINAL",
                    is_authorized=True,
                    sim_score=1.0,
                    lang="en" if is_en else "it",
                )
                print(f"[Console] {reply}")

            elif cmd_name in ["/unmute", "/smuta"]:
                self.stream_manager.is_muted = False
                await self.stream_manager.broadcast_ui({"type": "MUTE_STATE", "muted": False})
                is_en = (cmd_name == "/unmute")
                reply = "*bark* Microphones reactivated. I am listening again." if is_en else "*bark* Microfono riattivato. Sono di nuovo in ascolto."
                await self.stream_manager.broadcast_ui({
                    "type": "TRANSCRIPT",
                    "user_text": f"[{cmd_name} from console]" if is_en else f"[{cmd_name} da console]",
                    "axl_text": reply,
                })
                if self.stream_manager.tts and self.stream_manager.active_client:
                    await self.stream_manager.speak_text(reply, lang="en" if is_en else "it")
                print(f"[Console] {reply}")

            # 6. TELEMETRY HUD
            elif cmd_name in ["/telem", "/telemetry", "/telemetria"]:
                target = args_str.lower().strip() or "slave"
                if target in ["off", "chiudi", "hide", "close"]:
                    await self.stream_manager.set_telemetry_mode(False)
                    print("[Console] Telemetry HUD closed on Visor.")
                elif target in ["server", "master", "slave", "pc", "kiosk", "android"]:
                    norm_target = "server" if target in ["server", "master"] else ("kiosk" if target in ["kiosk", "android"] else "slave")
                    await self.stream_manager.set_telemetry_mode(True, target=norm_target)
                    print(f"[Console] Telemetry HUD opened for: {norm_target.upper()}")
                else:
                    print("[Console] Usage: /telem <server | slave | kiosk | off>")

            # 7. TASKS
            elif cmd_name in ["/tasks", "/task", "/compiti", "/promemoria", "/todo"]:
                await self._handle_tasks_command(args_str)

            # 8. CALENDAR
            elif cmd_name in ["/calendar", "/cal", "/calendario"]:
                await self._handle_calendar_command(args_str)

            # 9. AUDIO LINK
            elif cmd_name == "/audio":
                subcmd = args_str.lower().strip()
                if subcmd in ["status", "info", "stato"]:
                    ac = self.stream_manager.active_client
                    if ac and ac.client:
                        print(f"[Console] Active audio client: {ac.client.host}:{ac.client.port}")
                    else:
                        print("[Console] No audio source currently connected to /ws/audio.")
                elif subcmd in ["kick", "disconnect", "stacca"]:
                    ac = self.stream_manager.active_client
                    if ac:
                        await ac.close(code=1000, reason="Kicked by server admin")
                        self.stream_manager.active_client = None
                        print("[Console] Active audio client disconnected successfully.")
                    else:
                        print("[Console] No active audio client to disconnect.")
                else:
                    print("[Console] Usage: /audio <status | kick>")

            # 10. STOP SPEECH
            elif cmd_name in ["/stop", "/halt", "/zitto", "/taci"]:
                self.stream_manager.stop_speech()
                print("[Console] Vocal speech aborted immediately.")

            # 11. STATUS
            elif cmd_name in ["/status", "/info", "/stato"]:
                self._print_status()

            # 12. CLEAR SCREEN
            elif cmd_name in ["/clear", "/cls"]:
                os.system("clear" if os.name != "nt" else "cls")

            else:
                print(f"[Console] Unknown command '{cmd_name}'. Type /help for available commands.")
        except Exception as exc:
            print(f"[Console Error]: {exc}")

    async def _handle_speak_command(self, args_str: str):
        if not args_str:
            print("[Console] Usage: /speak [--en|--it] <text to speak>")
            return

        lang_override = None
        text_to_speak = args_str

        # Check for optional language override flag
        if text_to_speak.startswith("--en ") or text_to_speak.startswith("-en "):
            lang_override = "en"
            text_to_speak = text_to_speak.split(maxsplit=1)[1]
        elif text_to_speak.startswith("--it ") or text_to_speak.startswith("-it "):
            lang_override = "it"
            text_to_speak = text_to_speak.split(maxsplit=1)[1]

        detected = lang_override or self.stream_manager.tts._detect_language(text_to_speak)
        lang_label = "Italian (Diego)" if detected == "it" else "English (Bryce/Christopher)"
        print(f"[Console] Vocalizing [{lang_label}]: \"{text_to_speak}\"...")

        await self.stream_manager.broadcast_ui({
            "type": "TRANSCRIPT",
            "user_text": "[Admin Console]",
            "axl_text": text_to_speak,
        })
        success = await self.stream_manager.speak_text(text_to_speak, lang=lang_override)
        if success:
            print("[Console] Audio streamed successfully to client.")
        else:
            print("[Console] Notice: No audio client connected or synthesis failed.")

    async def _handle_tasks_command(self, args_str: str):
        subparts = args_str.split(maxsplit=1)
        sub = subparts[0].lower() if subparts else "list"
        arg = subparts[1].strip() if len(subparts) > 1 else ""

        if sub in ["add", "new", "aggiungi", "crea"]:
            if not arg:
                print("[Console] Usage: /tasks add <task description>")
                return
            t = task_manager.add_task(arg)
            print(f"[Console] Task #{t['id']} added: \"{t['text']}\"")
        elif sub in ["list", "ls", "show", "lista", "elenco"]:
            all_flag = "--all" in arg or "-a" in arg
            tasks = task_manager.list_tasks(include_completed=False, today_only=not all_flag)
            scope_label = "ALL ACTIVE" if all_flag else "TODAY"
            if not tasks:
                if all_flag:
                    print("[Console] No pending tasks found.")
                else:
                    print("[Console] No tasks found for today. (Use '/tasks list --all' to view all tasks)")
                return
            print(f"\n--- A-X-L TASKS [{scope_label}] ---")
            for t in tasks:
                status = "[X]" if t.get("completed") else "[ ]"
                print(f" {status} #{t['id']}: {t['text']} (created: {t['created_at']})")
            print("--------------------\n")
        elif sub in ["done", "complete", "fatto", "completa"]:
            try:
                tid = int(arg)
                if task_manager.complete_task(tid):
                    print(f"[Console] Task #{tid} marked as completed.")
                else:
                    print(f"[Console] Task #{tid} not found.")
            except ValueError:
                print("[Console] Usage: /tasks done <task_id>")
        elif sub in ["remove", "rm", "delete", "elimina", "rimuovi"]:
            try:
                tid = int(arg)
                if task_manager.remove_task(tid):
                    print(f"[Console] Task #{tid} deleted.")
                else:
                    print(f"[Console] Task #{tid} not found.")
            except ValueError:
                print("[Console] Usage: /tasks remove <task_id>")
        elif sub in ["clear", "pulisci"]:
            count = task_manager.clear_completed()
            print(f"[Console] Cleared {count} completed tasks.")
        else:
            print("[Console] Usage: /tasks <add <text> | list [--all] | done <id> | remove <id> | clear>")

    async def _handle_calendar_command(self, args_str: str):
        subparts = args_str.split(maxsplit=1)
        sub = subparts[0].lower() if subparts else "list"
        arg = subparts[1].strip() if len(subparts) > 1 else ""

        if sub in ["add", "new", "aggiungi", "crea"]:
            if not arg:
                print("[Console] Usage: /calendar add <title> [date/time (e.g. 2026-11-12 15:30 or 18:00)]")
                return
            title = arg
            dt_str = ""
            words = arg.rsplit(maxsplit=2)
            if len(words) >= 3 and (":" in words[-1] or "-" in words[-2]):
                title = words[0]
                dt_str = f"{words[1]} {words[2]}"
            elif len(words) >= 2 and ":" in words[-1]:
                title = arg.rsplit(maxsplit=1)[0]
                dt_str = words[-1]

            ev = task_manager.add_calendar_event(title, date_time_str=dt_str)
            print(f"[Console] Event #{ev['id']} scheduled: \"{ev['title']}\" at {ev['date_time']}")

            # Dispatch Intent to connected Android Kiosk over WebSocket (CalendarContract)
            await self.stream_manager.broadcast_ui({
                "type": "INTENT",
                "action": "add_calendar_event",
                "title": ev["title"],
                "description": ev.get("description", ""),
                "begin_time": ev["begin_time_ms"],
                "end_time": ev["end_time_ms"],
            })
            print("[Console] Intent dispatched via WebSocket to Android Kiosk (CalendarContract).")

        elif sub in ["list", "ls", "show", "lista", "elenco"]:
            all_flag = "--all" in arg or "-a" in arg
            events = task_manager.list_calendar_events(today_only=not all_flag)
            scope_label = "ALL SCHEDULED" if all_flag else "TODAY"
            if not events:
                if all_flag:
                    print("[Console] No events scheduled in calendar.")
                else:
                    print("[Console] No events scheduled for today. (Use '/calendar list --all' to view all events)")
                return
            print(f"\n--- A-X-L CALENDAR [{scope_label}] ---")
            for e in events:
                print(f" #{e['id']}: {e['title']} - {e['date_time']}")
            print("-----------------------\n")
        elif sub in ["remove", "rm", "delete", "elimina", "rimuovi"]:
            try:
                eid = int(arg)
                if task_manager.remove_calendar_event(eid):
                    print(f"[Console] Event #{eid} deleted.")
                else:
                    print(f"[Console] Event #{eid} not found.")
            except ValueError:
                print("[Console] Usage: /calendar remove <event_id>")
        else:
            print("[Console] Usage: /calendar <add <title> [datetime] | list [--all] | remove <id>>")

    def _print_status(self):
        sec_mode = self.stream_manager.verifier.security_mode if self.stream_manager.verifier else "unknown"
        profiles = list(self.stream_manager.verifier.enrolled_profiles.keys()) if self.stream_manager.verifier else []
        ac = self.stream_manager.active_client
        ac_str = f"{ac.client.host}:{ac.client.port}" if (ac and ac.client) else "None (Offline)"
        ui_count = len(self.stream_manager.ui_clients)
        node_tags = [info.get("node_tag", "UNKNOWN") for info in self.stream_manager.ui_client_info.values()]

        print("\n" + "=" * 55)
        print(" A-X-L CORE STATUS (Admin Inspection)")
        print("=" * 55)
        print(f" * Security Mode:     {sec_mode.upper()}")
        print(f" * Microphone State:  {'MUTED' if self.stream_manager.is_muted else 'ACTIVE (Listening)'}")
        print(f" * Telemetry HUD:     {'ON (' + self.stream_manager.telemetry_target.upper() + ')' if self.stream_manager.telemetry_active else 'OFF'}")
        print(f" * Active Audio Link: {ac_str}")
        print(f" * Connected Visors:  {ui_count} screen(s) -> {', '.join(node_tags) if node_tags else 'None'}")
        print(f" * Voice Profiles:    {', '.join(profiles) if profiles else 'None'}")
        print("=" * 55 + "\n")

    def _print_help(self):
        print("\n--- ADMIN CONSOLE COMMANDS (Minecraft-style) ---")
        print(" /speak <text>            : Synthesizes TTS and plays on audio client (/say, /answer, /parla)")
        print("                            (Auto-detects Italian or English; use --en or --it to force)")
        print(" /ask <query>             : Queries Gemini Brain as Admin and speaks the answer (/q, /chiedi)")
        print(" /tasks [add|list|done|rm]: Manages to-do task list (/tasks add buy milk, /tasks done 1)")
        print(" /calendar [add|list|rm]  : Manages events & dispatches Intent to Android/Google Calendar")
        print(" /mode <mode>             : Sets security mode (friendly | restrict | paranoid)")
        print(" /mute / /unmute          : Disables or re-enables microphone audio listening (/muto)")
        print(" /telem <target|off>      : Toggles telemetry HUD on Visor (server | slave | kiosk | off)")
        print(" /audio <status|kick>     : Inspects or disconnects the active audio stream client")
        print(" /status                  : Shows system health, connected screens, and biometric profiles")
        print(" /stop                    : Stops A-X-L vocal speech immediately (/halt, /zitto)")
        print(" /clear                   : Clears the terminal screen")
        print(" /help                    : Displays this command manual")
        print("------------------------------------------------\n")
