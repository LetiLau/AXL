"""
A-X-L Spotify Connect & Local Sonos Controller.
Handles Spotify OAuth2 (via .env credentials), Spotify Connect device discovery and
playback routing, and lightweight local UPnP/SSDP Sonos room discovery on the LAN.
"""

import base64
import difflib
import html
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger("AXL-Spotify")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TOKEN_FILE = DATA_DIR / "spotify_token.json"

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

SPOTIFY_SCOPES = " ".join([
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
])

# Common prepositions and filler words stripped when resolving a spoken target name
_TARGET_FILLER_WORDS = {
    "su", "sul", "sulla", "sullo", "sui", "sugli", "sulle",
    "in", "nel", "nella", "nello", "nei", "negli", "nelle",
    "da", "dal", "dalla", "dallo", "al", "alla", "allo",
    "on", "in", "at", "to", "the", "speaker", "speakers",
    "cassa", "casse", "altoparlante", "altoparlanti", "dispositivo", "device",
}

_KIOSK_TARGET_HINTS = {
    "kiosk", "android", "telefono", "cellulare", "smartphone", "galaxy", "a33", "phone", "mobile",
}
_PC_TARGET_HINTS = {
    "pc", "computer", "slave", "fedora", "linux", "windows", "laptop", "desktop", "workstation",
}
_BLUETOOTH_TARGET_HINTS = {
    "bluetooth", "bt", "cuffie", "auricolari", "headphones", "earbuds",
}


class SpotifyController:
    """Orchestrates Spotify Connect Web API calls and local Sonos LAN discovery."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sonos_cache: List[Dict[str, Any]] = []
        self._sonos_cache_ts: float = 0.0
        self._sonos_cache_ttl: float = 20.0
        self._kiosk_audio_outputs: List[Dict[str, Any]] = []
        self._oauth_states: Dict[str, float] = {}

    @property
    def client_id(self) -> str:
        return os.getenv("SPOTIFY_CLIENT_ID", "").strip()

    @property
    def client_secret(self) -> str:
        return os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()

    @property
    def redirect_uri(self) -> str:
        return os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/spotify/callback").strip()

    @property
    def market(self) -> str:
        return os.getenv("SPOTIFY_MARKET", "IT").strip().upper() or "IT"

    def is_configured(self) -> bool:
        """Returns True if Spotify Developer credentials are present in environment."""
        cid = self.client_id
        csec = self.client_secret
        if not cid or not csec:
            return False
        if cid.startswith("your_") or csec.startswith("your_"):
            return False
        return True

    def is_authenticated(self) -> bool:
        """Returns True if a saved OAuth token file with refresh_token exists."""
        token_data = self._load_token_data()
        return bool(token_data and (token_data.get("refresh_token") or token_data.get("access_token")))

    def update_kiosk_audio_outputs(self, outputs: Optional[List[Dict[str, Any]]]):
        """Stores audio output routes (e.g. Bluetooth A2DP speakers) reported by the Android Kiosk."""
        if isinstance(outputs, list):
            self._kiosk_audio_outputs = outputs

    def get_kiosk_audio_outputs(self) -> List[Dict[str, Any]]:
        """Returns the latest audio output routes reported by the Android Kiosk."""
        return list(self._kiosk_audio_outputs)

    def _get_device_aliases(self) -> Dict[str, str]:
        """Parses optional SPOTIFY_DEVICE_ALIASES env var (e.g. 'sonos=Salotto,camera=Bedroom')."""
        raw = os.getenv("SPOTIFY_DEVICE_ALIASES", "").strip()
        aliases: Dict[str, str] = {}
        if not raw:
            return aliases
        for pair in raw.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                k_clean = k.strip().lower()
                v_clean = v.strip()
                if k_clean and v_clean:
                    aliases[k_clean] = v_clean
        return aliases

    # ------------------------------------------------------------------
    # OAuth2 Token Storage & Refresh
    # ------------------------------------------------------------------

    def _load_token_data(self) -> Optional[Dict[str, Any]]:
        if not TOKEN_FILE.exists():
            return None
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning(f"Failed reading Spotify token file: {exc}")
            return None

    def _save_token_data(self, data: Dict[str, Any]):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get_authorize_url(self) -> str:
        """Generates the Spotify OAuth2 authorization URL for one-time user login."""
        if not self.is_configured():
            raise RuntimeError("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are not configured in .env")
        state = base64.urlsafe_b64encode(os.urandom(18)).decode("ascii").rstrip("=")
        self._oauth_states[state] = time.time()
        # Prune old states older than 15 minutes
        cutoff = time.time() - 900
        self._oauth_states = {k: v for k, v in self._oauth_states.items() if v >= cutoff}

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SPOTIFY_SCOPES,
            "state": state,
            "show_dialog": "true",
        }
        return f"{SPOTIFY_AUTH_URL}?{urllib.parse.urlencode(params)}"

    def validate_oauth_state(self, state: Optional[str]) -> bool:
        """Checks whether the returned OAuth state matches an active login request."""
        if not state:
            return False
        ts = self._oauth_states.pop(state, None)
        if ts is None:
            return False
        return (time.time() - ts) <= 900

    def exchange_code(self, code: str) -> Dict[str, Any]:
        """Exchanges an OAuth2 authorization code for access and refresh tokens."""
        if not self.is_configured():
            raise RuntimeError("Spotify credentials not configured in .env")

        auth_header = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("utf-8")).decode("ascii")
        resp = requests.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            },
            headers={
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Spotify token exchange failed ({resp.status_code}): {resp.text}")

        payload = resp.json()
        expires_in = int(payload.get("expires_in", 3600))
        token_data = {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token"),
            "token_type": payload.get("token_type", "Bearer"),
            "scope": payload.get("scope", SPOTIFY_SCOPES),
            "expires_at": time.time() + expires_in,
        }
        with self._lock:
            self._save_token_data(token_data)
        logger.info("Spotify OAuth2 tokens saved successfully.")
        return token_data

    def _ensure_access_token(self) -> str:
        """Returns a valid Spotify access token, refreshing automatically when needed."""
        if not self.is_configured():
            raise RuntimeError("Spotify credentials missing in master_server/.env")

        with self._lock:
            token_data = self._load_token_data()
            if not token_data:
                raise RuntimeError("Spotify account not linked yet. Open /spotify/login in your browser.")

            access_token = token_data.get("access_token")
            expires_at = float(token_data.get("expires_at", 0))
            if access_token and (time.time() < expires_at - 60):
                return access_token

            refresh_token = token_data.get("refresh_token")
            if not refresh_token:
                raise RuntimeError("Spotify refresh token missing. Re-authorize via /spotify/login.")

            auth_header = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("utf-8")).decode("ascii")
            resp = requests.post(
                SPOTIFY_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={
                    "Authorization": f"Basic {auth_header}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=10.0,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Spotify token refresh failed ({resp.status_code}): {resp.text}")

            refreshed = resp.json()
            expires_in = int(refreshed.get("expires_in", 3600))
            token_data["access_token"] = refreshed["access_token"]
            if refreshed.get("refresh_token"):
                token_data["refresh_token"] = refreshed["refresh_token"]
            token_data["expires_at"] = time.time() + expires_in
            self._save_token_data(token_data)
            logger.debug("Spotify access token refreshed successfully.")
            return token_data["access_token"]

    def _api_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: float = 8.0,
    ) -> requests.Response:
        """Executes an authenticated request against Spotify Web API with 401 retry."""
        token = self._ensure_access_token()
        url = f"{SPOTIFY_API_BASE}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=timeout)

        # Force token refresh once on 401 Unauthorized
        if resp.status_code == 401:
            with self._lock:
                data = self._load_token_data() or {}
                data["expires_at"] = 0
                if data:
                    self._save_token_data(data)
            token = self._ensure_access_token()
            headers = {"Authorization": f"Bearer {token}"}
            resp = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=timeout)

        return resp

    # ------------------------------------------------------------------
    # Local Sonos LAN Discovery & UPnP Control (Zero External Dependencies)
    # ------------------------------------------------------------------

    def discover_sonos_speakers(self, timeout: float = 1.5, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Discovers Sonos speakers on the local Wi-Fi LAN using SSDP M-SEARCH.
        Retrieves room names, model names, and group coordinator topology.
        """
        now = time.time()
        if not force_refresh and self._sonos_cache and (now - self._sonos_cache_ts) < self._sonos_cache_ttl:
            return list(self._sonos_cache)

        ssdp_msg = (
            "M-SEARCH * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 1\r\n"
            "ST: urn:schemas-upnp-org:device:ZonePlayer:1\r\n"
            "\r\n"
        ).encode("utf-8")

        locations: Dict[str, str] = {}
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.settimeout(timeout)
            sock.sendto(ssdp_msg, ("239.255.255.250", 1900))

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = max(0.05, deadline - time.monotonic())
                sock.settimeout(remaining)
                try:
                    raw_data, addr = sock.recvfrom(4096)
                except socket.timeout:
                    break
                text = raw_data.decode("utf-8", errors="ignore")
                if "sonos" not in text.lower() and "zoneplayer" not in text.lower():
                    continue
                loc_match = re.search(r"^LOCATION:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
                if loc_match:
                    loc_url = loc_match.group(1).strip()
                    ip = addr[0]
                    locations[ip] = loc_url
        except OSError as exc:
            logger.debug(f"Sonos SSDP discovery error: {exc}")
        finally:
            sock.close()

        speakers: List[Dict[str, Any]] = []
        for ip, loc_url in locations.items():
            info = self._fetch_sonos_device_info(ip, loc_url)
            if info:
                speakers.append(info)

        # Enrich with stereo-pair / group coordinator info from the first reachable speaker
        if speakers:
            group_map = self._fetch_sonos_zone_groups(speakers[0]["ip"])
            for sp in speakers:
                uuid = sp.get("uuid", "")
                if uuid and uuid in group_map:
                    sp.update(group_map[uuid])

        self._sonos_cache = speakers
        self._sonos_cache_ts = time.time()
        if speakers:
            names = ", ".join(f"{s['room_name']} ({s['ip']})" for s in speakers)
            logger.info(f"[Sonos LAN] Discovered {len(speakers)} speaker(s): {names}")
        return list(speakers)

    def _fetch_sonos_device_info(self, ip: str, location_url: str) -> Optional[Dict[str, Any]]:
        """Reads roomName, modelName, and UUID from Sonos device_description.xml."""
        try:
            resp = requests.get(location_url, timeout=1.5)
            if resp.status_code != 200:
                return None
            root = ET.fromstring(resp.text)
            ns = {"upnp": "urn:schemas-upnp-org:device-1-0"}
            device = root.find("upnp:device", ns)
            if device is None:
                return None
            room_name = (device.findtext("upnp:roomName", default="", namespaces=ns) or "").strip()
            model_name = (device.findtext("upnp:modelName", default="Sonos", namespaces=ns) or "Sonos").strip()
            udn = (device.findtext("upnp:UDN", default="", namespaces=ns) or "").strip()
            uuid = udn.replace("uuid:", "")
            return {
                "ip": ip,
                "room_name": room_name or f"Sonos ({ip})",
                "model_name": model_name,
                "uuid": uuid,
                "is_coordinator": True,
                "visible": True,
                "group_rooms": [room_name] if room_name else [],
            }
        except Exception as exc:
            logger.debug(f"Failed fetching Sonos XML from {location_url}: {exc}")
            return None

    def _fetch_sonos_zone_groups(self, ip: str) -> Dict[str, Dict[str, Any]]:
        """Queries ZoneGroupTopology to identify stereo pairs and grouped speakers."""
        soap_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            "<s:Body>"
            '<u:GetZoneGroupState xmlns:u="urn:schemas-upnp-org:service:ZoneGroupTopology:1"/>'
            "</s:Body>"
            "</s:Envelope>"
        )
        try:
            resp = requests.post(
                f"http://{ip}:1400/ZoneGroupTopology/Control",
                data=soap_body.encode("utf-8"),
                headers={
                    "Content-Type": 'text/xml; charset="utf-8"',
                    "SOAPACTION": '"urn:schemas-upnp-org:service:ZoneGroupTopology:1#GetZoneGroupState"',
                },
                timeout=1.5,
            )
            if resp.status_code != 200:
                return {}
            env = ET.fromstring(resp.text)
            state_el = env.find(".//ZoneGroupState")
            if state_el is None or not state_el.text:
                return {}
            zg_root = ET.fromstring(html.unescape(state_el.text))
            mapping: Dict[str, Dict[str, Any]] = {}
            for group in zg_root.findall(".//ZoneGroup"):
                coord_uuid = group.attrib.get("Coordinator", "")
                members = group.findall("ZoneGroupMember")
                visible_rooms: List[str] = []
                for m in members:
                    if m.attrib.get("Invisible", "0") != "1":
                        zname = m.attrib.get("ZoneName", "").strip()
                        if zname and zname not in visible_rooms:
                            visible_rooms.append(zname)
                for m in members:
                    muuid = m.attrib.get("UUID", "")
                    if not muuid:
                        continue
                    mapping[muuid] = {
                        "coordinator_uuid": coord_uuid,
                        "is_coordinator": (muuid == coord_uuid),
                        "visible": m.attrib.get("Invisible", "0") != "1",
                        "group_rooms": visible_rooms,
                    }
            return mapping
        except Exception as exc:
            logger.debug(f"Failed querying Sonos ZoneGroupTopology on {ip}: {exc}")
            return {}

    def control_sonos_upnp(
        self,
        ip: str,
        action: str,
        volume: Optional[int] = None,
        repeat_mode: Optional[str] = None,
    ) -> bool:
        """Executes basic UPnP AVTransport/RenderingControl actions directly on a Sonos speaker."""
        action_norm = action.lower().strip()
        try:
            if action_norm in ("play", "resume"):
                service = "AVTransport"
                soap_action = "Play"
                inner = "<InstanceID>0</InstanceID><Speed>1</Speed>"
            elif action_norm in ("pause", "stop"):
                service = "AVTransport"
                soap_action = "Pause"
                inner = "<InstanceID>0</InstanceID>"
            elif action_norm == "next":
                service = "AVTransport"
                soap_action = "Next"
                inner = "<InstanceID>0</InstanceID>"
            elif action_norm in ("prev", "previous"):
                service = "AVTransport"
                soap_action = "Previous"
                inner = "<InstanceID>0</InstanceID>"
            elif action_norm == "restart":
                service = "AVTransport"
                soap_action = "Seek"
                inner = "<InstanceID>0</InstanceID><Unit>REL_TIME</Unit><Target>00:00:00</Target>"
            elif action_norm in ("loop", "repeat", "loop_track", "loop_context", "loop_playlist", "loop_off"):
                service = "AVTransport"
                soap_action = "SetPlayMode"
                rm = (repeat_mode or "").lower().strip()
                if action_norm == "loop_track" or rm == "track":
                    play_mode = "REPEAT_ONE"
                elif action_norm in ("loop_context", "loop_playlist") or rm == "context":
                    play_mode = "REPEAT_ALL"
                else:
                    play_mode = "NORMAL"
                inner = f"<InstanceID>0</InstanceID><NewPlayMode>{play_mode}</NewPlayMode>"
            elif action_norm == "set_volume" and volume is not None:
                service = "RenderingControl"
                soap_action = "SetVolume"
                vol = max(0, min(100, int(volume)))
                inner = f"<InstanceID>0</InstanceID><Channel>Master</Channel><DesiredVolume>{vol}</DesiredVolume>"
            else:
                return False

            envelope = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
                "<s:Body>"
                f'<u:{soap_action} xmlns:u="urn:schemas-upnp-org:service:{service}:1">'
                f"{inner}"
                f"</u:{soap_action}>"
                "</s:Body>"
                "</s:Envelope>"
            )
            endpoint = f"http://{ip}:1400/MediaRenderer/{service}/Control"
            resp = requests.post(
                endpoint,
                data=envelope.encode("utf-8"),
                headers={
                    "Content-Type": 'text/xml; charset="utf-8"',
                    "SOAPACTION": f'"urn:schemas-upnp-org:service:{service}:1#{soap_action}"',
                },
                timeout=2.0,
            )
            return resp.status_code == 200
        except Exception as exc:
            logger.debug(f"Sonos UPnP control '{action}' failed on {ip}: {exc}")
            return False

    # ------------------------------------------------------------------
    # Device Resolution & Spotify Connect Discovery
    # ------------------------------------------------------------------

    def get_spotify_devices(self) -> List[Dict[str, Any]]:
        """Fetches available Spotify Connect devices from GET /v1/me/player/devices."""
        resp = self._api_request("GET", "/me/player/devices")
        if resp.status_code != 200:
            raise RuntimeError(f"Spotify devices query failed ({resp.status_code}): {resp.text}")
        devices = resp.json().get("devices", [])
        if devices:
            summary = ", ".join(
                f"{d.get('name')} [{d.get('type')}, active={d.get('is_active')}]"
                for d in devices
            )
            logger.info(f"[Spotify Connect] Devices: {summary}")
        else:
            logger.info("[Spotify Connect] No active devices returned by Spotify API.")
        return devices

    def list_available_speakers(self) -> Dict[str, Any]:
        """Combines Spotify Connect devices, local Sonos LAN speakers, and Kiosk BT outputs."""
        sonos_lan = self.discover_sonos_speakers()
        spotify_devices: List[Dict[str, Any]] = []
        spotify_error: Optional[str] = None

        if self.is_configured() and self.is_authenticated():
            try:
                spotify_devices = self.get_spotify_devices()
            except Exception as exc:
                spotify_error = str(exc)
        else:
            spotify_error = "Spotify OAuth not configured/linked yet."

        return {
            "spotify_devices": spotify_devices,
            "sonos_lan": sonos_lan,
            "kiosk_audio_outputs": self.get_kiosk_audio_outputs(),
            "spotify_error": spotify_error,
        }

    @staticmethod
    def _normalize_target_text(target: Optional[str]) -> str:
        if not target:
            return ""
        clean = re.sub(r"[^\w\s\+\-]", " ", str(target).lower()).strip()
        tokens = [t for t in clean.split() if t not in _TARGET_FILLER_WORDS]
        return " ".join(tokens).strip()

    def _match_device_score(
        self,
        norm_target: str,
        device: Dict[str, Any],
        sonos_room_names: set,
    ) -> float:
        """Computes a [0.0, 1.0+] match score between a spoken target and a Spotify Connect device."""
        dev_name = (device.get("name") or "").lower().strip()
        dev_type = (device.get("type") or "").lower().strip()
        is_active = bool(device.get("is_active"))

        if not norm_target:
            return 0.0

        # Exact or full substring match on device name
        if norm_target == dev_name:
            return 1.0 + (0.05 if is_active else 0.0)
        if norm_target in dev_name or dev_name in norm_target:
            return 0.90 + (0.05 if is_active else 0.0)

        # Token overlap (e.g. "sonos salotto" vs "Salotto + Sala")
        target_tokens = set(norm_target.split())
        dev_tokens = set(re.split(r"[\s\+\-_/]+", dev_name))
        if target_tokens & dev_tokens:
            overlap = len(target_tokens & dev_tokens) / max(1, len(target_tokens))
            return 0.80 + (0.10 * overlap) + (0.03 if is_active else 0.0)

        # Special rule for "sonos": match discovered Sonos LAN room names or Speaker type
        if "sonos" in target_tokens:
            for rname in sonos_room_names:
                if rname and (rname in dev_name or dev_name in rname):
                    return 0.88 + (0.04 if is_active else 0.0)
            if dev_type == "speaker":
                return 0.78 + (0.04 if is_active else 0.0)

        # Special rule for Kiosk / Android smartphone target
        if target_tokens & _KIOSK_TARGET_HINTS:
            if dev_type == "smartphone" or any(k in dev_name for k in ("galaxy", "a33", "android", "sm-")):
                return 0.82 + (0.04 if is_active else 0.0)

        # Special rule for PC / Slave workstation target
        if target_tokens & _PC_TARGET_HINTS:
            if dev_type == "computer" or any(k in dev_name for k in ("fedora", "thinkpad", "desktop", "laptop", "pc")):
                return 0.82 + (0.04 if is_active else 0.0)

        # Special rule for Bluetooth speakers connected to Kiosk
        if target_tokens & _BLUETOOTH_TARGET_HINTS:
            for bt in self._kiosk_audio_outputs:
                bt_name = str(bt.get("name", "")).lower().strip()
                if bt_name and (bt_name in dev_name or any(tok in norm_target for tok in bt_name.split())):
                    return 0.86
            if dev_type == "smartphone":
                return 0.72

        # Fuzzy similarity fallback
        ratio = difflib.SequenceMatcher(None, norm_target, dev_name).ratio()
        return ratio if ratio >= 0.58 else 0.0

    def resolve_device(
        self,
        target: Optional[str],
        devices: Optional[List[Dict[str, Any]]] = None,
        sonos_speakers: Optional[List[Dict[str, Any]]] = None,
        prefer_speaker_default: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], str]:
        """
        Resolves a requested speaker/device name to a Spotify Connect device dictionary.
        Returns (matched_device_or_None, all_spotify_devices, normalized_target).
        """
        if devices is None:
            devices = self.get_spotify_devices()
        if sonos_speakers is None:
            sonos_speakers = self.discover_sonos_speakers()

        norm_target = self._normalize_target_text(target)
        aliases = self._get_device_aliases()
        if norm_target in aliases:
            norm_target = aliases[norm_target].lower().strip()

        sonos_room_names = set()
        for sp in sonos_speakers:
            rname = (sp.get("room_name") or "").lower().strip()
            if rname:
                sonos_room_names.add(rname)
            for gr in sp.get("group_rooms", []):
                if gr:
                    sonos_room_names.add(gr.lower().strip())

        if not devices:
            return None, devices, norm_target

        # If no specific target was requested, honor SPOTIFY_DEFAULT_DEVICE first,
        # then prefer Sonos/Speaker when starting new playback, otherwise prefer active device.
        if not norm_target or norm_target in ("default", "auto", "qui", "here"):
            env_default = os.getenv("SPOTIFY_DEFAULT_DEVICE", "").strip()
            if env_default and prefer_speaker_default:
                default_norm = self._normalize_target_text(env_default)
                if default_norm:
                    best_env_dev = None
                    best_env_score = 0.0
                    for dev in devices:
                        sc = self._match_device_score(default_norm, dev, sonos_room_names)
                        if sc > best_env_score:
                            best_env_score = sc
                            best_env_dev = dev
                    if best_env_dev and best_env_score >= 0.58:
                        return best_env_dev, devices, norm_target

            speaker = next(
                (
                    d for d in devices
                    if (d.get("type") or "").lower() == "speaker"
                    or any(r in (d.get("name") or "").lower() for r in sonos_room_names)
                ),
                None,
            )
            active = next((d for d in devices if d.get("is_active")), None)

            if prefer_speaker_default and speaker is not None:
                return speaker, devices, norm_target
            if active is not None:
                return active, devices, norm_target
            if speaker is not None:
                return speaker, devices, norm_target
            return devices[0], devices, norm_target

        best_dev = None
        best_score = 0.0
        for dev in devices:
            score = self._match_device_score(norm_target, dev, sonos_room_names)
            if score > best_score:
                best_score = score
                best_dev = dev

        if best_dev and best_score >= 0.58:
            return best_dev, devices, norm_target
        return None, devices, norm_target

    # ------------------------------------------------------------------
    # Search & Playback Execution
    # ------------------------------------------------------------------

    def search_spotify(self, query: str, preferred_kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Searches Spotify catalog and chooses the best track, artist, album, or playlist.
        """
        clean_q = (query or "").strip()
        if not clean_q:
            return None

        lower_q = clean_q.lower()
        norm_kind = (preferred_kind or "").lower().strip() or None
        search_q = clean_q

        if norm_kind is None:
            if lower_q.startswith("playlist "):
                norm_kind = "playlist"
                search_q = clean_q[len("playlist "):].strip()
            elif " playlist" in lower_q or "playlist di" in lower_q:
                norm_kind = "playlist"
            elif lower_q.startswith("album "):
                norm_kind = "album"
                search_q = clean_q[len("album "):].strip()
            elif lower_q.startswith("artista ") or lower_q.startswith("artist "):
                norm_kind = "artist"
                search_q = clean_q.split(maxsplit=1)[1].strip()
            elif lower_q.startswith("canzone ") or lower_q.startswith("track ") or lower_q.startswith("brano "):
                norm_kind = "track"
                search_q = clean_q.split(maxsplit=1)[1].strip()

        resp = self._api_request(
            "GET",
            "/search",
            params={
                "q": search_q,
                "type": "track,artist,playlist,album",
                "limit": 5,
                "market": self.market,
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Spotify search failed ({resp.status_code}): {resp.text}")

        data = resp.json()
        tracks = [t for t in data.get("tracks", {}).get("items", []) if t]
        artists = [a for a in data.get("artists", {}).get("items", []) if a]
        playlists = [p for p in data.get("playlists", {}).get("items", []) if p]
        albums = [al for al in data.get("albums", {}).get("items", []) if al]

        if norm_kind == "playlist" and playlists:
            pl = playlists[0]
            return {
                "kind": "playlist",
                "uri": pl["uri"],
                "name": pl.get("name", search_q),
                "subtitle": (pl.get("owner") or {}).get("display_name", "Spotify"),
                "is_context": True,
            }

        if norm_kind == "album" and albums:
            al = albums[0]
            al_artists = ", ".join(a.get("name", "") for a in al.get("artists", []))
            return {
                "kind": "album",
                "uri": al["uri"],
                "name": al.get("name", search_q),
                "subtitle": al_artists,
                "is_context": True,
            }

        if norm_kind == "artist" and artists:
            top_artist = artists[0]
            return {
                "kind": "artist",
                "uri": top_artist["uri"],
                "name": top_artist.get("name", search_q),
                "subtitle": "Artista Spotify",
                "is_context": True,
            }

        # Auto-ranking: choose artist context only when the query matches the artist name
        # and does NOT clash with a matching track title (e.g. "Shut Up and Dance"),
        # unless the artist is clearly more popular than the top track.
        if norm_kind != "track" and artists:
            top_artist = artists[0]
            artist_name_norm = (top_artist.get("name") or "").lower().strip()
            q_norm = search_q.lower().strip()
            artist_matches = (
                artist_name_norm == q_norm
                or difflib.SequenceMatcher(None, artist_name_norm, q_norm).ratio() >= 0.90
            )
            if artist_matches:
                top_track = tracks[0] if tracks else None
                track_name_norm = (top_track.get("name") or "").lower().strip() if top_track else ""
                track_base_norm = re.sub(r"\s*[\(\[].*?[\)\]]|\s*-\s*remaster.*$", "", track_name_norm).strip()
                track_also_matches = bool(
                    top_track and (
                        track_name_norm == q_norm
                        or track_base_norm == q_norm
                        or difflib.SequenceMatcher(None, track_base_norm, q_norm).ratio() >= 0.90
                    )
                )
                artist_pop = int(top_artist.get("popularity") or 0)
                track_pop = int((top_track or {}).get("popularity") or 0)

                if not track_also_matches or (artist_pop > 0 and artist_pop >= track_pop + 10):
                    return {
                        "kind": "artist",
                        "uri": top_artist["uri"],
                        "name": top_artist.get("name", search_q),
                        "subtitle": "Artista Spotify",
                        "is_context": True,
                    }

        if tracks:
            tr = tracks[0]
            tr_artists = ", ".join(a.get("name", "") for a in tr.get("artists", []))
            album_uri = (tr.get("album") or {}).get("uri")
            return {
                "kind": "track",
                "uri": tr["uri"],
                "album_uri": album_uri,
                "name": tr.get("name", search_q),
                "subtitle": tr_artists,
                "is_context": False,
            }

        if playlists:
            pl = playlists[0]
            return {
                "kind": "playlist",
                "uri": pl["uri"],
                "name": pl.get("name", search_q),
                "subtitle": (pl.get("owner") or {}).get("display_name", "Spotify"),
                "is_context": True,
            }

        if albums:
            al = albums[0]
            al_artists = ", ".join(a.get("name", "") for a in al.get("artists", []))
            return {
                "kind": "album",
                "uri": al["uri"],
                "name": al.get("name", search_q),
                "subtitle": al_artists,
                "is_context": True,
            }

        return None

    def _start_playback_on_device_id(
        self,
        device_id: str,
        item: Optional[Dict[str, Any]] = None,
        is_active: bool = True,
    ) -> Tuple[bool, str]:
        """
        Starts or resumes playback on a specific Spotify Connect device_id.
        Transfers playback first if the device is sleeping and verifies that the
        requested track/context actually started instead of just resuming an old queue.
        """
        body: Optional[Dict[str, Any]] = None
        if item:
            if item.get("is_context"):
                body = {"context_uri": item["uri"]}
            else:
                body = {"uris": [item["uri"]]}

        logger.info(
            f"[Spotify Play] Target device_id={device_id} (active={is_active}) | "
            f"Payload={body}"
        )

        # If an inactive/sleeping device is targeted with a specific URI, wake/transfer first
        # so Spotify Connect does not ignore the payload and resume a stale local queue.
        if body is not None and not is_active:
            tr_pre = self._api_request(
                "PUT",
                "/me/player",
                json_body={"device_ids": [device_id], "play": False},
            )
            logger.debug(f"[Spotify Play] Pre-play transfer status={tr_pre.status_code}")
            if tr_pre.status_code in (200, 202, 204):
                time.sleep(0.45)

        resp = self._api_request(
            "PUT",
            "/me/player/play",
            params={"device_id": device_id},
            json_body=body,
        )
        logger.info(f"[Spotify Play] PUT /me/player/play -> HTTP {resp.status_code}")

        if resp.status_code in (404, 502):
            tr_resp = self._api_request(
                "PUT",
                "/me/player",
                json_body={"device_ids": [device_id], "play": body is None},
            )
            if body is not None and tr_resp.status_code in (200, 202, 204):
                time.sleep(0.55)
                resp = self._api_request(
                    "PUT",
                    "/me/player/play",
                    params={"device_id": device_id},
                    json_body=body,
                )
                logger.info(f"[Spotify Play] Retry PUT /me/player/play -> HTTP {resp.status_code}")
            elif tr_resp.status_code in (200, 202, 204):
                return True, "OK"

        if resp.status_code not in (200, 202, 204):
            return False, f"HTTP {resp.status_code}: {resp.text}"

        # Guard against Spotify Connect mobile quirk: cold-waking or resuming a paused Android client
        # can return HTTP 204 while continuing the old queue instead of loading the requested URI.
        if item and item.get("uri"):
            expected_uri = item["uri"]

            def _fetch_mismatched_uri() -> Optional[str]:
                pb_resp = self._api_request("GET", "/me/player")
                if pb_resp.status_code != 200:
                    return None
                pb_data = pb_resp.json()
                playing_item_uri = (pb_data.get("item") or {}).get("uri", "")
                playing_ctx_uri = (pb_data.get("context") or {}).get("uri", "")
                if not item.get("is_context") and playing_item_uri and playing_item_uri != expected_uri:
                    return playing_item_uri
                if item.get("is_context") and playing_ctx_uri and playing_ctx_uri != expected_uri:
                    return playing_ctx_uri
                return None

            time.sleep(0.55)
            try:
                stale_uri = _fetch_mismatched_uri()
                if stale_uri:
                    logger.warning(
                        f"[Spotify Play] Device resumed stale track ({stale_uri}) "
                        f"instead of {expected_uri}; re-sending play payload."
                    )
                    # Passing parent album context_uri + offset often breaks Android Connect queue desync
                    retry_body = body
                    if not item.get("is_context") and item.get("album_uri"):
                        retry_body = {
                            "context_uri": item["album_uri"],
                            "offset": {"uri": expected_uri},
                        }
                    resp_retry = self._api_request(
                        "PUT",
                        "/me/player/play",
                        params={"device_id": device_id},
                        json_body=retry_body,
                    )
                    logger.info(f"[Spotify Play] Second-pass PUT /me/player/play -> HTTP {resp_retry.status_code}")

                    # Verify second pass; if still stuck on a single track, fall back to queue + next
                    if not item.get("is_context"):
                        time.sleep(0.55)
                        stale_uri_2 = _fetch_mismatched_uri()
                        if stale_uri_2:
                            logger.warning(
                                f"[Spotify Play] Device still on stale track ({stale_uri_2}); "
                                f"trying queue + next fallback for {expected_uri}."
                            )
                            q_resp = self._api_request(
                                "POST",
                                "/me/player/queue",
                                params={"uri": expected_uri, "device_id": device_id},
                            )
                            if q_resp.status_code in (200, 202, 204):
                                time.sleep(0.25)
                                n_resp = self._api_request(
                                    "POST",
                                    "/me/player/next",
                                    params={"device_id": device_id},
                                )
                                logger.info(
                                    f"[Spotify Play] Queue+Next fallback -> queue={q_resp.status_code}, next={n_resp.status_code}"
                                )
                                time.sleep(0.55)
                                stale_uri_3 = _fetch_mismatched_uri()
                                if stale_uri_3:
                                    return (
                                        False,
                                        f"Il dispositivo ha ripreso il brano precedente ({stale_uri_3}) ignorando '{item.get('name', expected_uri)}'.",
                                    )
            except Exception as exc:
                logger.debug(f"[Spotify Play] Post-play verification skipped: {exc}")

        return True, "OK"

    def play_on_device(
        self,
        query: str = "",
        target: Optional[str] = None,
        dispatch_kiosk_intent: Optional[Callable[[Dict[str, Any]], None]] = None,
        preferred_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Searches Spotify (if query is provided) and starts/transfers playback on the resolved speaker.
        """
        sonos_speakers = self.discover_sonos_speakers()
        devices = self.get_spotify_devices()
        matched_dev, all_devs, norm_target = self.resolve_device(
            target,
            devices=devices,
            sonos_speakers=sonos_speakers,
            prefer_speaker_default=True,
        )

        item = self.search_spotify(query, preferred_kind=preferred_kind) if query and query.strip() else None
        if query and query.strip() and not item:
            return {
                "ok": False,
                "reason": "no_results",
                "message": f"Non ho trovato nulla su Spotify per '{query}'.",
            }

        # If target refers to the Android Kiosk / phone / Bluetooth on phone and no Smartphone is active yet,
        # wake Spotify directly on the Android Kiosk via Intent and retry discovery briefly.
        target_tokens = set(norm_target.split())
        wants_kiosk = bool(target_tokens & (_KIOSK_TARGET_HINTS | _BLUETOOTH_TARGET_HINTS))
        if matched_dev is None and wants_kiosk and dispatch_kiosk_intent is not None:
            intent_payload: Dict[str, Any] = {
                "type": "INTENT",
                "action": "open_spotify",
                "query": query or "",
                "auto_play": True,
            }
            if item and item.get("uri"):
                intent_payload["uri"] = item["uri"]
            dispatch_kiosk_intent(intent_payload)
            return {
                "ok": True,
                "via": "kiosk_intent",
                "device_name": "Galaxy A33 Kiosk",
                "item": item,
                "message": (
                    f"Ho avviato Spotify sul Kiosk per riprodurre '{item['name']}'."
                    if item else "Ho aperto Spotify sul Kiosk."
                ),
            }

        if matched_dev is None:
            avail_names = [d.get("name", "?") for d in all_devs]
            sonos_names = [s.get("room_name", "?") for s in sonos_speakers if s.get("visible", True)]
            if norm_target and sonos_names and not all_devs:
                return {
                    "ok": False,
                    "reason": "spotify_devices_sleeping",
                    "sonos_lan": sonos_names,
                    "message": (
                        f"Vedo i Sonos sulla rete Wi-Fi ({', '.join(sonos_names)}), "
                        "ma Spotify Connect non mostra ancora dispositivi attivi. "
                        "Avvia Spotify una volta verso lo speaker o apri Spotify sul telefono per svegliare il collegamento."
                    ),
                }
            if norm_target:
                avail_str = ", ".join(avail_names) if avail_names else "nessuno attivo"
                return {
                    "ok": False,
                    "reason": "device_not_found",
                    "available_devices": avail_names,
                    "sonos_lan": sonos_names,
                    "message": (
                        f"Non trovo lo speaker '{target}' tra i dispositivi Spotify disponibili "
                        f"(disponibili ora: {avail_str})."
                    ),
                }
            return {
                "ok": False,
                "reason": "no_active_devices",
                "sonos_lan": sonos_names,
                "message": (
                    "Nessun dispositivo Spotify Connect attivo al momento. "
                    + (f"Sulla rete Wi-Fi vedo i Sonos: {', '.join(sonos_names)}." if sonos_names else "")
                ).strip(),
            }

        ok, err_detail = self._start_playback_on_device_id(
            matched_dev["id"],
            item=item,
            is_active=bool(matched_dev.get("is_active")),
        )
        if not ok:
            return {
                "ok": False,
                "reason": "api_error",
                "device": matched_dev,
                "message": f"Errore Spotify durante l'avvio su '{matched_dev.get('name')}': {err_detail}",
            }

        dev_name = matched_dev.get("name", "Speaker")
        if item:
            subtitle = f" di {item['subtitle']}" if item.get("subtitle") else ""
            msg = f"Riproduco '{item['name']}'{subtitle} su {dev_name}."
        else:
            msg = f"Musica spostata e avviata su {dev_name}."

        return {
            "ok": True,
            "via": "spotify_connect",
            "device": matched_dev,
            "device_name": dev_name,
            "item": item,
            "message": msg,
        }

    def transfer_playback(self, target: str, play: bool = True) -> Dict[str, Any]:
        """Transfers currently active Spotify playback to the named speaker."""
        sonos_speakers = self.discover_sonos_speakers()
        devices = self.get_spotify_devices()
        matched_dev, all_devs, _ = self.resolve_device(target, devices=devices, sonos_speakers=sonos_speakers)

        if matched_dev is None:
            avail_str = ", ".join(d.get("name", "?") for d in all_devs) or "nessuno"
            return {
                "ok": False,
                "message": f"Non trovo lo speaker '{target}' su Spotify Connect (disponibili: {avail_str}).",
            }

        resp = self._api_request(
            "PUT",
            "/me/player",
            json_body={"device_ids": [matched_dev["id"]], "play": play},
        )
        if resp.status_code in (200, 202, 204):
            return {
                "ok": True,
                "device": matched_dev,
                "device_name": matched_dev.get("name", target),
                "message": f"Riproduzione trasferita su {matched_dev.get('name', target)}.",
            }
        return {
            "ok": False,
            "message": f"Impossibile trasferire la musica su {matched_dev.get('name', target)} ({resp.status_code}).",
        }

    @staticmethod
    def _normalize_repeat_mode(mode: Optional[str]) -> str:
        """Normalizes repeat/loop mode aliases into 'track', 'context', 'off', or 'toggle'."""
        m = (mode or "").lower().strip()
        if m in ("track", "song", "canzone", "brano", "traccia", "one", "single", "uno"):
            return "track"
        if m in ("context", "playlist", "album", "all", "tutto", "tutta", "da capo"):
            return "context"
        if m in ("off", "none", "disattiva", "spegni", "spento", "false", "0"):
            return "off"
        return "toggle"

    def control_playback(
        self,
        action: str,
        target: Optional[str] = None,
        volume: Optional[int] = None,
        repeat_mode: Optional[str] = None,
        position_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Controls playback (play, pause, next, previous, restart, seek, loop/repeat, volume) on Spotify Connect or Sonos LAN."""
        act = (action or "").lower().strip()
        sonos_speakers = self.discover_sonos_speakers()
        devices = self.get_spotify_devices()
        matched_dev, _, norm_target = self.resolve_device(target, devices=devices, sonos_speakers=sonos_speakers)

        params: Dict[str, Any] = {}
        if matched_dev and matched_dev.get("id"):
            params["device_id"] = matched_dev["id"]

        dev_label = matched_dev.get("name") if matched_dev else (target or "dispositivo attivo")

        if act in ("play", "resume"):
            resp = self._api_request("PUT", "/me/player/play", params=params)
        elif act in ("pause", "stop"):
            resp = self._api_request("PUT", "/me/player/pause", params=params)
        elif act in ("next", "skip"):
            resp = self._api_request("POST", "/me/player/next", params=params)
        elif act in ("prev", "previous", "back"):
            resp = self._api_request("POST", "/me/player/previous", params=params)
        elif act in ("restart", "rewind"):
            seek_params = dict(params)
            seek_params["position_ms"] = 0
            resp = self._api_request("PUT", "/me/player/seek", params=seek_params)
            if resp.status_code in (200, 202, 204):
                return {"ok": True, "message": f"Brano riavviato dall'inizio su {dev_label}."}
        elif act == "seek" and position_ms is not None:
            seek_params = dict(params)
            seek_params["position_ms"] = max(0, int(position_ms))
            resp = self._api_request("PUT", "/me/player/seek", params=seek_params)
            if resp.status_code in (200, 202, 204):
                return {"ok": True, "message": f"Posizione aggiornata su {dev_label}."}
        elif act in ("loop", "repeat", "loop_track", "loop_context", "loop_playlist", "loop_off"):
            if act == "loop_track":
                resolved_repeat = "track"
            elif act in ("loop_context", "loop_playlist"):
                resolved_repeat = "context"
            elif act == "loop_off":
                resolved_repeat = "off"
            else:
                resolved_repeat = self._normalize_repeat_mode(repeat_mode)

            if resolved_repeat == "toggle":
                cur_state = "off"
                try:
                    cur_pb = self.get_current_playback()
                    cur_state = cur_pb.get("repeat_state", "off")
                except Exception:
                    pass
                cycle_map = {"off": "context", "context": "track", "track": "off"}
                resolved_repeat = cycle_map.get(cur_state, "context")

            rep_params = dict(params)
            rep_params["state"] = resolved_repeat
            resp = self._api_request("PUT", "/me/player/repeat", params=rep_params)
            if resp.status_code in (200, 202, 204):
                labels = {
                    "track": f"Loop attivo sul brano in corso su {dev_label}.",
                    "context": f"Loop attivo su tutta la playlist/album su {dev_label}.",
                    "off": f"Loop disattivato su {dev_label}.",
                }
                return {
                    "ok": True,
                    "repeat_state": resolved_repeat,
                    "device_name": dev_label,
                    "message": labels[resolved_repeat],
                }
            if self._try_sonos_upnp_fallback(norm_target, sonos_speakers, act, repeat_mode=resolved_repeat):
                return {
                    "ok": True,
                    "repeat_state": resolved_repeat,
                    "device_name": dev_label,
                    "message": f"Modalità loop '{resolved_repeat}' impostata direttamente su Sonos.",
                }
            return {
                "ok": False,
                "message": f"Errore Spotify ({resp.status_code}) impostando loop '{resolved_repeat}' su {dev_label}: {resp.text}",
            }
        elif act in ("shuffle", "random", "casuale"):
            # Toggle shuffle state on active playlist/album playback unless explicitly specified
            target_shuffle: Optional[bool] = None
            m_rep = (repeat_mode or "").lower().strip()
            if m_rep in ("on", "true", "1", "attivo"):
                target_shuffle = True
            elif m_rep in ("off", "false", "0", "spento"):
                target_shuffle = False
            else:
                try:
                    cur_pb = self.get_current_playback()
                    target_shuffle = not bool(cur_pb.get("shuffle_state", False))
                except Exception:
                    target_shuffle = True

            shuf_params = dict(params)
            shuf_params["state"] = "true" if target_shuffle else "false"
            resp = self._api_request("PUT", "/me/player/shuffle", params=shuf_params)
            if resp.status_code in (200, 202, 204):
                return {
                    "ok": True,
                    "shuffle_state": target_shuffle,
                    "device_name": dev_label,
                    "message": (
                        f"Riproduzione casuale attivata su {dev_label}."
                        if target_shuffle
                        else f"Riproduzione casuale disattivata su {dev_label}."
                    ),
                }
            return {
                "ok": False,
                "message": f"Riproduzione casuale non disponibile su {dev_label} ({resp.status_code}).",
            }
        elif act in ("volume_up", "volume_down", "set_volume", "mute"):
            if matched_dev and matched_dev.get("supports_volume") is False:
                # Try direct Sonos UPnP first in case this is a Sonos room, otherwise report device limitation honestly
                if self._try_sonos_upnp_fallback(norm_target, sonos_speakers, "set_volume", volume):
                    return {"ok": True, "device_name": dev_label, "message": f"Volume Sonos impostato."}
                return {
                    "ok": False,
                    "volume_unsupported": True,
                    "device_name": dev_label,
                    "message": (
                        f"Spotify Connect non permette la regolazione remota del volume su {dev_label} "
                        "(limitazione del dispositivo/app)."
                    ),
                }
            target_vol = volume
            if target_vol is None:
                cur_vol = int((matched_dev or {}).get("volume_percent") or 50)
                if act == "volume_up":
                    target_vol = min(100, cur_vol + 15)
                elif act == "volume_down":
                    target_vol = max(0, cur_vol - 15)
                elif act == "mute":
                    target_vol = 0
                else:
                    target_vol = cur_vol
            target_vol = max(0, min(100, int(target_vol)))
            vol_params = dict(params)
            vol_params["volume_percent"] = target_vol
            resp = self._api_request("PUT", "/me/player/volume", params=vol_params)
            if resp.status_code in (200, 202, 204):
                return {"ok": True, "device_name": dev_label, "message": f"Volume su {dev_label} impostato al {target_vol}%."}
            # Local Sonos UPnP volume fallback if Spotify Connect volume is restricted
            if self._try_sonos_upnp_fallback(norm_target, sonos_speakers, "set_volume", target_vol):
                return {"ok": True, "device_name": dev_label, "message": f"Volume Sonos impostato al {target_vol}%."}
            if resp.status_code == 403:
                return {
                    "ok": False,
                    "volume_unsupported": True,
                    "device_name": dev_label,
                    "message": (
                        f"Spotify Connect non permette la regolazione remota del volume su {dev_label} "
                        "(403: controllo volume disabilitato dal dispositivo)."
                    ),
                }
            return {"ok": False, "device_name": dev_label, "message": f"Errore regolazione volume su {dev_label} ({resp.status_code})."}
        else:
            return {"ok": False, "message": f"Azione multimediale non supportata: {action}"}

        if resp.status_code in (200, 202, 204):
            return {"ok": True, "message": f"Comando '{act}' eseguito su {dev_label}."}

        # Fallback to direct Sonos UPnP if Spotify Connect failed for a Sonos speaker
        if self._try_sonos_upnp_fallback(norm_target, sonos_speakers, act):
            return {"ok": True, "message": f"Comando '{act}' eseguito direttamente su Sonos."}

        return {
            "ok": False,
            "message": f"Errore Spotify ({resp.status_code}) eseguendo '{act}' su {dev_label}: {resp.text}",
        }

    def _try_sonos_upnp_fallback(
        self,
        norm_target: str,
        sonos_speakers: List[Dict[str, Any]],
        action: str,
        volume: Optional[int] = None,
        repeat_mode: Optional[str] = None,
    ) -> bool:
        """Attempts direct local UPnP control if the target refers to a discovered Sonos speaker."""
        if not sonos_speakers:
            return False
        coordinators = [s for s in sonos_speakers if s.get("is_coordinator", True)] or sonos_speakers
        target_sp = None
        if not norm_target or "sonos" in norm_target:
            target_sp = coordinators[0]
        else:
            for sp in coordinators:
                if norm_target in sp.get("room_name", "").lower():
                    target_sp = sp
                    break
        if not target_sp:
            return False
        return self.control_sonos_upnp(target_sp["ip"], action, volume=volume, repeat_mode=repeat_mode)

    def get_current_playback(self) -> Dict[str, Any]:
        """Fetches the currently playing track, repeat/shuffle state, and active Spotify Connect device."""
        resp = self._api_request("GET", "/me/player")
        if resp.status_code == 204:
            return {"playing": False, "repeat_state": "off", "shuffle_state": False, "message": "Nessuna riproduzione Spotify attiva al momento."}
        if resp.status_code != 200:
            raise RuntimeError(f"Spotify playback query failed ({resp.status_code}): {resp.text}")

        data = resp.json()
        item = data.get("item") or {}
        album = item.get("album") or {}
        images = album.get("images") or []
        album_art = images[0].get("url", "") if images else ""
        device = data.get("device") or {}
        artists = ", ".join(a.get("name", "") for a in item.get("artists", []))
        return {
            "playing": bool(data.get("is_playing")),
            "track": item.get("name", ""),
            "track_uri": item.get("uri", ""),
            "artists": artists,
            "album": album.get("name", ""),
            "album_art": album_art,
            "device_id": device.get("id", ""),
            "device_name": device.get("name", ""),
            "device_type": device.get("type", ""),
            "supports_volume": device.get("supports_volume"),
            "volume_percent": device.get("volume_percent"),
            "repeat_state": data.get("repeat_state", "off"),
            "shuffle_state": bool(data.get("shuffle_state", False)),
            "progress_ms": data.get("progress_ms", 0),
            "duration_ms": item.get("duration_ms", 0),
            "timestamp": int(time.time() * 1000),
        }


# Singleton instance shared across commands, FastAPI endpoints, and admin console
spotify_controller = SpotifyController()
