"""
A-X-L Task & Calendar Manager
Provides local persistent storage (JSON) for Tasks and Calendar events,
with hooks ready for Google Calendar/Tasks API and Microsoft To Do synchronization.
"""

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AXL-TaskManager")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TASKS_FILE = DATA_DIR / "tasks.json"
CALENDAR_FILE = DATA_DIR / "calendar.json"


class TaskManager:
    """Manages persistent to-do tasks and calendar events."""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.tasks: List[Dict[str, Any]] = self._load_json(TASKS_FILE, default=[])
        self.calendar: List[Dict[str, Any]] = self._load_json(CALENDAR_FILE, default=[])

    def _load_json(self, path: Path, default: Any) -> Any:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Error loading {path.name}: {e}. Initializing default.")
        return default

    def _save_tasks(self):
        try:
            with open(TASKS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.tasks, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save tasks: {e}")

    def _save_calendar(self):
        try:
            with open(CALENDAR_FILE, "w", encoding="utf-8") as f:
                json.dump(self.calendar, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save calendar: {e}")

    # --- TASK METHODS ---

    def add_task(self, text: str) -> Dict[str, Any]:
        """Adds a new task to the persistent list."""
        clean_text = text.strip()
        if not clean_text:
            return {"error": "Task text cannot be empty."}

        new_id = max([t["id"] for t in self.tasks], default=0) + 1
        now_iso = datetime.datetime.now().isoformat(timespec="seconds")
        task = {
            "id": new_id,
            "text": clean_text,
            "completed": False,
            "created_at": now_iso,
        }
        self.tasks.append(task)
        self._save_tasks()
        logger.info(f"[TASK ADDED] #{new_id}: '{clean_text}'")
        return task

    def list_tasks(self, include_completed: bool = False, today_only: bool = False) -> List[Dict[str, Any]]:
        """Returns active tasks. If today_only=True, filters to tasks created or due today."""
        candidates = self.tasks if include_completed else [t for t in self.tasks if not t.get("completed", False)]
        if not today_only:
            return list(candidates)
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        return [t for t in candidates if t.get("created_at", "").startswith(today_str) or t.get("due_date", "").startswith(today_str)]

    def complete_task(self, task_id: int) -> bool:
        """Marks a task as completed."""
        for t in self.tasks:
            if t["id"] == task_id:
                t["completed"] = True
                t["completed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                self._save_tasks()
                logger.info(f"[TASK COMPLETED] #{task_id}: '{t['text']}'")
                return True
        return False

    def remove_task(self, task_id: int) -> bool:
        """Removes a task by its ID."""
        initial_len = len(self.tasks)
        self.tasks = [t for t in self.tasks if t["id"] != task_id]
        if len(self.tasks) < initial_len:
            self._save_tasks()
            logger.info(f"[TASK REMOVED] #{task_id}")
            return True
        return False

    def clear_completed(self) -> int:
        """Removes all completed tasks from storage."""
        active = [t for t in self.tasks if not t.get("completed", False)]
        removed_count = len(self.tasks) - len(active)
        self.tasks = active
        self._save_tasks()
        return removed_count

    # --- CALENDAR METHODS ---

    def add_calendar_event(
        self,
        title: str,
        date_time_str: str = "",
        description: str = "",
    ) -> Dict[str, Any]:
        """
        Adds a new calendar event.
        Calculates begin and end timestamps for Android CalendarContract Intent dispatch.
        """
        clean_title = title.strip()
        if not clean_title:
            return {"error": "Event title cannot be empty."}

        new_id = max([e["id"] for e in self.calendar], default=0) + 1
        now = datetime.datetime.now()

        # Parse date_time or default to 1 hour from now
        begin_dt = now + datetime.timedelta(hours=1)
        if date_time_str:
            clean_dt = date_time_str.strip()
            # Try parsing formats: 'YYYY-MM-DD HH:MM', 'DD/MM/YYYY HH:MM', 'HH:MM'
            parsed = False
            for fmt in ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M", "%H:%M"):
                try:
                    dt = datetime.datetime.strptime(clean_dt, fmt)
                    if fmt == "%H:%M":
                        begin_dt = now.replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
                        if begin_dt <= now:
                            begin_dt += datetime.timedelta(days=1)
                    else:
                        begin_dt = dt
                    parsed = True
                    break
                except ValueError:
                    continue
            if not parsed:
                logger.info(f"Could not strictly parse date '{date_time_str}', using fallback: +1h")

        end_dt = begin_dt + datetime.timedelta(hours=1)
        begin_ms = int(begin_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)

        event = {
            "id": new_id,
            "title": clean_title,
            "description": description.strip(),
            "date_time": begin_dt.strftime("%Y-%m-%d %H:%M"),
            "begin_time_ms": begin_ms,
            "end_time_ms": end_ms,
            "created_at": now.isoformat(timespec="seconds"),
        }
        self.calendar.append(event)
        self._save_calendar()
        logger.info(f"[CALENDAR ADDED] #{new_id}: '{clean_title}' at {event['date_time']}")
        return event

    def list_calendar_events(self, today_only: bool = True) -> List[Dict[str, Any]]:
        """Returns stored calendar events. Defaults to today's events unless today_only=False."""
        if not today_only:
            return list(self.calendar)
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        return [e for e in self.calendar if e.get("date_time", "").startswith(today_str)]

    def remove_calendar_event(self, event_id: int) -> bool:
        """Removes a calendar event by ID."""
        initial_len = len(self.calendar)
        self.calendar = [e for e in self.calendar if e["id"] != event_id]
        if len(self.calendar) < initial_len:
            self._save_calendar()
            logger.info(f"[CALENDAR REMOVED] #{event_id}")
            return True
        return False


# Global singleton instance
task_manager = TaskManager()
