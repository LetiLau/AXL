"""
A-X-L Spotify Connect Zeroconf Helper (Pure Python + cryptography).
Supports:
1. One-time local pairing (`/spotify pair`): advertises a temporary `_spotify-connect._tcp`
   receiver via `avahi-publish-service`, captures `addUser` from the Spotify mobile app,
   and saves reusable credentials in `data/spotify_zeroconf_credentials.json` (gitignored).
2. LAN Wake-up (`send_add_user`): wakes dormant Sonos / Sky / AVR Spotify Connect devices
   with `activeUser='none'` directly over Wi-Fi LAN.
"""

import base64
import hashlib
import hmac
import http.server
import json
import logging
import os
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger("AXL-SpotifyZC")

# Oakley Group 1 768-bit Diffie-Hellman prime used by Spotify Connect Zeroconf
_DH_PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A63A3620FFFFFFFFFFFFFFFF",
    16,
)
_DH_GEN = 2


def _require_cryptography():
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        return Cipher, algorithms, modes
    except ImportError as exc:
        raise RuntimeError(
            "Pacchetto 'cryptography' mancante. Installa con: pip install cryptography"
        ) from exc


def _dh_generate_keypair() -> Tuple[int, bytes]:
    priv = int.from_bytes(os.urandom(95), "big") | (1 << 750)
    pub = pow(_DH_GEN, priv, _DH_PRIME)
    return priv, pub.to_bytes(96, "big")


def _derive_device_aes_key(device_id: str) -> bytes:
    """Derives the 24-byte AES-192-ECB key from a Spotify Connect deviceID."""
    secret = hashlib.sha1(device_id.encode("utf-8")).digest()
    dk = hashlib.pbkdf2_hmac("sha1", secret, secret, 256, 20)
    return dk + struct.pack(">I", 20)


def _read_varint(buf: bytes, idx: int) -> Tuple[int, int]:
    val = 0
    shift = 0
    while idx < len(buf):
        b = buf[idx]
        idx += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, idx
        shift += 7
    return val, idx


def _write_varint(val: int) -> bytes:
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _unwrap_auth_blob(blob_b64: str, client_key_b64: str, priv_key: int, local_device_id: str) -> Dict[str, Any]:
    """Decrypts the incoming `addUser` blob from the Spotify app into reusable credentials."""
    Cipher, algorithms, modes = _require_cryptography()

    raw = base64.b64decode(blob_b64)
    iv = raw[:16]
    encrypted = raw[16:-20]
    checksum = raw[-20:]

    remote_pub = int.from_bytes(base64.b64decode(client_key_b64), "big")
    shared = pow(remote_pub, priv_key, _DH_PRIME).to_bytes(96, "big")
    base_key = hashlib.sha1(shared).digest()[:16]

    checksum_key = hmac.new(base_key, b"checksum", hashlib.sha1).digest()
    if not hmac.compare_digest(hmac.new(checksum_key, encrypted, hashlib.sha1).digest(), checksum):
        raise ValueError("Zeroconf checksum mismatch")

    enc_key = hmac.new(base_key, b"encryption", hashlib.sha1).digest()[:16]
    ctr_dec = Cipher(algorithms.AES(enc_key), modes.CTR(iv)).decryptor()
    inner_b64 = ctr_dec.update(encrypted) + ctr_dec.finalize()

    inner_enc = base64.b64decode(inner_b64)
    aes_key = _derive_device_aes_key(local_device_id)
    ecb_dec = Cipher(algorithms.AES(aes_key), modes.ECB()).decryptor()
    plain = bytearray(ecb_dec.update(inner_enc) + ecb_dec.finalize())

    for i in range(len(plain) - 1, 0x10 - 1, -1):
        plain[i] ^= plain[i - 0x10]

    # Parse Spotify blob fields: 0x49=username, 0x50=auth_type, 0x51=auth_data
    idx = 0
    username = ""
    auth_type = 1
    auth_data = b""
    while idx < len(plain):
        tag = plain[idx]
        idx += 1
        if tag == 0x49:
            ln, idx = _read_varint(plain, idx)
            username = bytes(plain[idx : idx + ln]).decode("utf-8", errors="replace")
            idx += ln
        elif tag == 0x50:
            auth_type, idx = _read_varint(plain, idx)
        elif tag == 0x51:
            ln, idx = _read_varint(plain, idx)
            auth_data = bytes(plain[idx : idx + ln])
            idx += ln
            break
        else:
            break

    return {
        "username": username,
        "auth_type": auth_type,
        "auth_data": base64.b64encode(auth_data).decode("ascii"),
    }


def _wrap_auth_blob(
    creds: Dict[str, Any],
    remote_device_id: str,
    remote_pubkey_b64: str,
) -> Tuple[str, str]:
    """Re-encrypts stored reusable credentials for a target Spotify Connect speaker."""
    Cipher, algorithms, modes = _require_cryptography()

    username = creds["username"].encode("utf-8")
    auth_type = int(creds.get("auth_type", 1))
    auth_data = base64.b64decode(creds["auth_data"])

    payload = bytearray()
    payload.append(0x49)
    payload.extend(_write_varint(len(username)))
    payload.extend(username)
    payload.append(0x50)
    payload.extend(_write_varint(auth_type))
    payload.append(0x51)
    payload.extend(_write_varint(len(auth_data)))
    payload.extend(auth_data)

    # Pad to 16-byte AES block boundary
    pad_len = (-len(payload)) % 16
    if pad_len:
        payload.extend(b"\x00" * pad_len)

    for i in range(0x10, len(payload)):
        payload[i] ^= payload[i - 0x10]

    aes_key = _derive_device_aes_key(remote_device_id)
    ecb_enc = Cipher(algorithms.AES(aes_key), modes.ECB()).encryptor()
    inner_enc = ecb_enc.update(bytes(payload)) + ecb_enc.finalize()
    inner_b64 = base64.b64encode(inner_enc)

    priv, our_pub = _dh_generate_keypair()
    remote_pub = int.from_bytes(base64.b64decode(remote_pubkey_b64), "big")
    shared = pow(remote_pub, priv, _DH_PRIME).to_bytes(96, "big")
    base_key = hashlib.sha1(shared).digest()[:16]

    checksum_key = hmac.new(base_key, b"checksum", hashlib.sha1).digest()
    enc_key = hmac.new(base_key, b"encryption", hashlib.sha1).digest()[:16]

    iv = os.urandom(16)
    ctr_enc = Cipher(algorithms.AES(enc_key), modes.CTR(iv)).encryptor()
    encrypted = ctr_enc.update(inner_b64) + ctr_enc.finalize()
    checksum = hmac.new(checksum_key, encrypted, hashlib.sha1).digest()

    blob_b64 = base64.b64encode(iv + encrypted + checksum).decode("ascii")
    client_key_b64 = base64.b64encode(our_pub).decode("ascii")
    return blob_b64, client_key_b64


def send_add_user(
    ip: str,
    port: int,
    cpath: str,
    remote_device_id: str,
    remote_pubkey_b64: str,
    creds: Dict[str, Any],
) -> bool:
    """Sends a Zeroconf `addUser` request to wake a dormant Sonos/Sky/AVR speaker."""
    if not creds.get("username") or not creds.get("auth_data"):
        return False
    blob_b64, client_key_b64 = _wrap_auth_blob(creds, remote_device_id, remote_pubkey_b64)
    url = f"http://{ip}:{port}{cpath}"
    resp = requests.post(
        url,
        data={
            "action": "addUser",
            "userName": creds["username"],
            "blob": blob_b64,
            "clientKey": client_key_b64,
            "deviceName": "A-X-L Master",
        },
        timeout=3.5,
    )
    if resp.status_code == 200:
        try:
            body = resp.json()
            return body.get("status") == 101 or body.get("spotifyError", 0) == 0
        except Exception:
            return True
    logger.warning(f"[Spotify ZC] addUser HTTP {resp.status_code}: {resp.text[:160]}")
    return False


def run_pairing_server(creds_path: Path, timeout_sec: int = 60) -> Dict[str, Any]:
    """
    Temporarily advertises 'A-X-L Pair' via `avahi-publish-service` on the local LAN
    so the user can tap it once in the Spotify app to store Zeroconf credentials.
    """
    _require_cryptography()
    if not shutil.which("avahi-publish-service"):
        return {"ok": False, "message": "avahi-publish-service non trovato sul sistema."}

    local_device_id = hashlib.sha1(socket.gethostname().encode("utf-8")).hexdigest()
    priv_key, pub_bytes = _dh_generate_keypair()
    pub_b64 = base64.b64encode(pub_bytes).decode("ascii")
    result_holder: Dict[str, Any] = {}
    done_event = threading.Event()

    class _PairingHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def _send_json(self, obj: Dict[str, Any], code: int = 200):
            raw = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            action = (qs.get("action") or [""])[0]
            if action == "getInfo":
                self._send_json({
                    "status": 101,
                    "statusString": "ERROR-OK",
                    "spotifyError": 0,
                    "version": "2.9.0",
                    "deviceID": local_device_id,
                    "remoteName": "A-X-L Pair",
                    "activeUser": "",
                    "publicKey": pub_b64,
                    "deviceType": "Speaker",
                    "libraryVersion": "0.1.0",
                    "accountReq": "PREMIUM",
                    "brandDisplayName": "A-X-L",
                    "modelDisplayName": "Master",
                    "voiceSupport": "NO",
                })
            else:
                self._send_json({"status": 101, "statusString": "ERROR-OK", "spotifyError": 0})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            form = urllib.parse.parse_qs(body)
            action = (form.get("action") or [""])[0]
            if action == "addUser":
                try:
                    blob = (form.get("blob") or [""])[0]
                    client_key = (form.get("clientKey") or [""])[0]
                    creds = _unwrap_auth_blob(blob, client_key, priv_key, local_device_id)
                    if not creds.get("username"):
                        creds["username"] = (form.get("userName") or [""])[0]
                    creds_path.parent.mkdir(parents=True, exist_ok=True)
                    creds_path.write_text(json.dumps(creds, indent=2), encoding="utf-8")
                    try:
                        os.chmod(creds_path, 0o600)
                    except OSError:
                        pass
                    result_holder["ok"] = True
                    result_holder["username"] = creds.get("username", "")
                    self._send_json({"status": 101, "statusString": "ERROR-OK", "spotifyError": 0})
                    done_event.set()
                    return
                except Exception as exc:
                    logger.error(f"[Spotify Pair] Failed decoding addUser: {exc}")
                    result_holder["ok"] = False
                    result_holder["message"] = f"Errore decodifica credenziali: {exc}"
                    self._send_json({"status": 102, "spotifyError": 1}, code=400)
                    done_event.set()
                    return
            self._send_json({"status": 101, "statusString": "ERROR-OK", "spotifyError": 0})

    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", 0), _PairingHandler)
    port = httpd.server_address[1]
    srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    srv_thread.start()

    avahi_proc = subprocess.Popen(
        [
            "avahi-publish-service",
            "A-X-L Pair",
            "_spotify-connect._tcp",
            str(port),
            "CPath=/spotifyzc",
            "VERSION=1.0",
            "Stack=SP",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        print(
            f"[Spotify Pair] Ricevitore 'A-X-L Pair' attivo sulla rete (porta {port}) per {timeout_sec}s.\n"
            "Apri l'app Spotify sul telefono -> Dispositivi -> tocca 'A-X-L Pair'!"
        )
        signaled = done_event.wait(timeout=timeout_sec)
        if not signaled:
            return {"ok": False, "message": "Timeout pairing (nessun tocco ricevuto su 'A-X-L Pair')."}
        if result_holder.get("ok"):
            return {
                "ok": True,
                "message": f"Pairing completato per '{result_holder.get('username')}'! Salvato in {creds_path.name}.",
            }
        return {"ok": False, "message": result_holder.get("message", "Pairing fallito.")}
    finally:
        avahi_proc.terminate()
        try:
            avahi_proc.wait(timeout=2)
        except Exception:
            avahi_proc.kill()
        httpd.shutdown()
        httpd.server_close()
