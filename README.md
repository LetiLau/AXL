# --- Work in progress ---
Testin branches

## General Info
**A-X-L** is the acronym for **A**daptive e**X**ecution **L**ogistic. A sort of tribute to A.X.L. movie from Global Road Entertainment. 
It's a hybrid AI personal assistant.
It has the personality of a loyal, high-tech robotic dog, combined with the sarcastic efficiency of *Tony Stark*'s systems.

As a proud Italian student I decided to make A-X-L bilingual so it speaks both 🇮🇹Italian and 🇬🇧English.


#### Meaning of the acronym
- *Adaptive:* Chooses whether to process locally (privacy/latency) or in the cloud (Gemini Pro/Flash).
- *eXecution:* Executes practical commands (OS automation, local scripts).
- *Logistics:* Manages the ecosystem, PC resources, and local home automation.

And also
- *Distrust:* Reacts to strangers via local voice recognition (Speaker Verification).

#### Overview
See the links below to read specific part of `README.md`.

[Architectural and Coding Info](#Architectural-and-Coding-Info)

[setUp and progress Diary](#How-to-setUp-and-progress-Diary)

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


## How to _setUp_ and *progress Diary*
#### ToDo
The `ToDo.md` file present inside the reposotory is the list of phases I went through. I decided to leave it as a specific description of the files I created.
Sorry it's in Italian I needed it that way.
 
#### Python virtual environment to not mess with dependencies
.venv in master_server to install master requirements (command to activate .venv --> `source .venv/bin/activate`)

#### Models used
In the repository there is the directory `/master_server/models` which contains only a `.gitkeep` file. This because I din't want to push inside the GitHub all the models I used in the code.

The user can easly download them by running `dowload_models.py` in the specific virtual environment (.venv) he must create inside `master_server`.

List of the models for `master_server`
- Silero VAD v5 (`silero_vad.onnx`): Gatekeeper and energy profiling
- openWakeWord (`*.onnx`): _Keyword Spotting_ (KWS) text-dependent
- CAM++ Speaker Verification (`wespeaker_en_voxceleb_CAM++_LM.onnx`): _Speaker Verification_ (Distrust)
- Piper TTS (`it_IT-riccardo-x_low.onnx` and `en_GB-alan-low.onnx`): Local neural Acoustic & Waveform Synthesis

#### OS system libraries
I made the entire project in Fedora Linux.
I used the library `portaudio` specific for Fedora and correlated Linux distro to manage audio input and output in real time.
If you are in my same situation install it to test the code by yourself.
If you already have it no problem the command will update to last version or tell you it's ok.
``` bash
#Fedora and correlated distribution
sudo dnf install -y portaudio
```

#### Testing
The `master_server/tools` directory contains the file `test_mic_client.py`. I have used it to test the system before I made the Kotlin app to record the audio.
