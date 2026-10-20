# --- Work in progress ---

## General Info
**A-X-L** is the acronym for **A**daptive e**X**ecution **L**ogistic. A sort of tribute to A.X.L. movie from Global Road Entertainment. 
It's a hybrid AI personal assistant.
It has the personality of a loyal, high-tech robotic dog, combined with the sarcastic efficiency of *Tony Stark*'s systems.


#### Meaning of the acronym
- *Adaptive:* Chooses whether to process locally (privacy/latency) or in the cloud (Gemini Pro/Flash).
- *eXecution:* Executes practical commands (OS automation, local scripts).
- *Logistics:* Manages the ecosystem, PC resources, and local home automation.

And also
- *Distrust:* Reacts to strangers via local voice recognition (Speaker Verification).

## Architectural and Coding Info
The project is vibe-coded : ) and designed to run in an old pc with Debian 12 LTS.

*Note: It will work anyway in a Windows PC --> see below*

I'll use an old Lenovo ThinkPad:
- Intel i5 4300U .. 
- integrated GPU ..
- 8 GB DDR3 RAM

The project is designed to use an old android smartphone as kiosk. I'll use a *Samsung Galaxy A33 5G*. Anyway the kiosk can be also moved to other devices.


### Architecture: Decoupled Master-Slave
The system is built on a distributed, event-driven microservices architecture:
- **Master Server (Brain):** Runs on the ThinkPad (or any Linux/Windows machine). Built with asynchronous Python (`FastAPI`, `asyncio`), it orchestrates native C++ SIMD inference engines via **ONNX Runtime** for real-time edge processing (Silero VAD, openWakeWord, and Sherpa-ONNX for Speaker Verification). High-level reasoning is offloaded to the **Gemini API**, while local TTS feedback is handled by **Piper**.
- **Slave Nodes (Execution Daemons):** Lightweight Python daemons running on client machines (Fedora workstation, Windows rigs) using `FastAPI` and `psutil`. They passively expose local OS telemetry, execute system automation scripts, and listen for execution payloads dispatched by the Master.
- **Single-Machine Co-location (Standalone Mode):** For portable use without dedicated server hardware, both the Master and the Slave can run on the exact same laptop. They communicate over the local loopback interface (`127.0.0.1`) via TCP/HTTP. The Master treats the local host as just another network node, keeping the codebase completely decoupled and 100% agnostic to deployment topology.



### Thin Client & Fault Tolerance (Galaxy A33 Kiosk)
The Galaxy A33 runs a pure Kotlin application without NDK/JNI overhead, acting solely as an I/O peripheral:
- **Audio Pipeline:** Streams bidirectional raw 16kHz 16-bit mono PCM audio over a dedicated WebSocket (`/ws/audio`).
- **Visor UI:** Houses a lightweight HTML5/CSS/JS frontend inside a full-screen WebView (featuring OLED pixel-shifting protection to prevent burn-in).
- **Graceful Offline Fallback:** If the Master is powered off, the UI detects the broken socket and drops into a local offline state (monochrome grayscale overlay + "Close" button) without crashing or displaying `ERR_CONNECTION_REFUSED` errors.
- **Multi-Master Failover:** The A33 maintains the volatile conversational context client-side (last $N$ interactions). If the ThinkPad shuts down and the primary workstation takes over as the new Master, the A33 reconnects and hands over the session history during the initial handshake, preserving context transparently.



### Network Topology & "Travel Mode"
A-X-L operates strictly over local network sockets (Raw PCM streaming and JSON control frames via WebSockets and REST APIs):
- **Home Setup:** All nodes connect to the local home Wi-Fi LAN with static IP mapping or local mDNS resolution.
- **The "Hotel Wi-Fi" Bottleneck:** Public/hotel networks enforce **L2 Client Isolation (AP Isolation)**, preventing connected devices from discovering or talking to each other directly, alongside captive portals that break headless background traffic.
- **Travel Mode Solution:** When traveling without the ThinkPad server, the system forms a zero-configuration portable private LAN using the **personal smartphone's Wi-Fi Hotspot** (not the slave smartphone android_kiosk)
  - The hotspot acts as a full hardware router/DHCP gateway (typically on `192.168.43.0/24`).
  - Both the laptop (running Master + Local Slave in Standalone Mode) and the Galaxy A33 connect to this private AP.
  - Zero router port-forwarding, zero AP-isolation issues, sub-millisecond local latency, and seamless cellular passthrough for Gemini API calls.

