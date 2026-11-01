# --- Work in progress ---

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
- **Visor UI:** Houses a lightweight HTML5/CSS/JS frontend inside a full-screen WebView (featuring OLED pixel-shifting protection to prevent burn-in and dynamic client identification: displaying `// KIOSK` on Android and `// NODE-n` on browser terminals).
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

#### Android Kiosk App Setup (`android_kiosk`)
The Android companion application is built as a pure Kotlin Thin-Client Kiosk:
- **Project Template:** Empty Views Activity (Android Studio)
- **Application Name:** `AXL Kiosk`
- **Package Name:** `com.axl.kiosk`
- **Language:** Kotlin with Kotlin DSL (`build.gradle.kts`)
- **Minimum SDK:** `API 26` (Android 8.0 Oreo) — provides maximum stability and ensures compatibility with approximately **99.1%** of active Android devices worldwide.
- **Target / Compile SDK:** Modern Android SDK (API 34+)
- **Architecture:** Pure Kotlin (Zero NDK/JNI overhead). Employs `AudioRecord` (16kHz 16-bit Mono PCM) and `AudioTrack` paired with `OkHttp` WebSockets for ultra-low latency bidirectional audio transport with the Master Server.
- **Key Modules:**
  - `MainActivity.kt`: Immersive sticky fullscreen kiosk WebView hosting the Master HUD (`/ui`) with OLED burn-in protection. Integrates pure HTML/JS HUD quick controls via JavascriptInterface (`window.AXLKiosk` for reload, IP config dialog, and clean exit), eliminating native floating buttons.
  - `AudioStreamService.kt`: Persistent Foreground Service orchestrating raw PCM capture and Piper TTS audio playback. Supports `ACTION_STOP` intent broadcast to cleanly release microphone hardware locks on app exit.
  - `IntentExecutor.kt`: Native Android Intent bridge (Media/Volume control, Spotify launch, alarms, smart appliance app dispatch).
  - `KioskTelemetry.kt`: Hardware health monitor streaming battery level, battery charging state, battery temperature (°C) and RAM usage directly into A-X-L's HUD.

#### Technical Mechanism
##### WakeWord and command detection
`Note: The wakeWord is` _AXL_ `pronunced as `**"ÀK-səl"**` or `**"ÀK-s'l"**` without the 'e'.`
The flow operates via a state accumulator with time hysteresis:
1. **Idle Condition (`IDLE`):** Silero VAD discards frames. The `openWakeWord` engine **does not perform any inferences**, preserving CPU cycles on the i5-4300U.
2. **VAD Activation (`SPEECH_START`):**
   - The speech start time offset is recorded in the `RingBuffer` (with ~300ms of audio pre-roll to avoid clipping the consonant attack).
   - The audio flows to `openWakeWord`.
3. **Activate the wake word:**
   - The network recognizes _"AXL"_ while the user is still speaking the sentence.
   - The system enters the `COLLECTING_COMMAND` state.
4. **Speech Ending (`SPEECH_END` with Hangover):**
   - As soon as Silero detects continuous silence for a tolerance window (e.g., **600–700 ms**, to avoid truncations on physiological pauses between words), the utterance is formally closed.
   - The server extracts the entire PCM block (from the beginning of the sentence to the end of the silence) from the `RingBuffer`: the audio array contains both the voice for Speaker Verification and the complete payload ready for Gemini.

##### Speaker Verification
Using the `wespeaker_en_voxceleb_CAM++_LM.onnx` model downloaded as `models/speaker_id.onnx`. 
The model extracts L2-normalized embedding vectors on which we apply Cosine Similarity: 
$$\text{Similarity}(\mathbf{u}, \mathbf{v}) = \frac{\mathbf{u} \cdot \mathbf{v}}{\Vert{}\mathbf{u}\Vert{}_2 \Vert{}\mathbf{v}\Vert{}_2}$$
- $\ge 0.65$: Owner confirmed.
- $< 0.65$: Stranger detected / Alert.

##### Security & Distrust Modes
A-X-L operates under 3 security tiers configured via `AXL_SECURITY_MODE` in `.env`:
- `friendly` (active by default for development and testing): Listens to and executes commands for everyone without biometric rejection. Eliminates accidental false-negatives and saves API quotas during development.
- `restrict`: Accepts and executes commands for the enrolled owner and recognized friends (`models/profiles/*.npy`), rejecting total strangers.
- `paranoid`: Obeys strictly and exclusively the enrolled owner. If a recognized friend speaks, A-X-L explains that paranoid mode is active and refuses commands. Total strangers are rejected with aggressive distrust.

#### Voice Profile Enrollment: Dual-Mode (Online & Offline)
A-X-L features a **Dual-Mode Biometric Enrollment Architecture**, allowing you to record and calibrate voice profiles either live in production via voice commands or offline via terminal CLI tools.

The directory `master_server/models/profiles/` contains the `.npy` embedding centroids and debug audio files for the owner and enrolled friends. All voice feature extraction (Silero VAD + CAM++ ONNX via `sherpa-onnx`) is **100% on-device, private and offline**: neural embeddings never touch the cloud.

##### 1. Online Conversational Enrollment (In-System Live Voice Command)
When the Master Server is running and connected to the Android Kiosk (or `test_mic_client.py`), you can manage profiles entirely hands-free:
- **Updating the Owner Profile:**
  - Command: *"A-X-L, aggiorna la mia voce"*, *"modifica la mia voce"*, or *"ricalibra la mia voce"*.
  - **Biometric Security Privilege:** Overwriting or recalibrating the primary `owner.npy` profile is **strictly guarded**. The speaker MUST be recognized as the `owner` across all security modes (`friendly`, `restrict`, `paranoid`). Unauthorized strangers and guests are immediately blocked from tampering with the owner's identity.
- **Enrolling New Friends:**
  - Command: *"A-X-L, registra un nuovo amico: Luca"*, *"aggiungi amico Marco"*, or *"registra la voce di Sara"*.
  - In `restrict` and `paranoid` modes, only the authenticated Owner can authorize adding new friends.
- **Interactive Multi-Take Procedure:**
  - A-X-L guides the user through 3 vocal takes (~3 seconds of pure human speech per take, isolated in real time by Silero VAD).
  - Neural CAM++ embeddings are extracted, unit-normalized, and merged into a cosine centroid (`models/profiles/<name>.npy`).
  - **Hot Reload in RAM:** `SpeakerVerifier.load_profiles()` is invoked dynamically in memory, making the new voice profile immediately active **with zero downtime and without restarting the server**.
  - **Interactive STOP Button:** Tapping the pulsing red STOP button on the Visor HUD at any moment cancels the enrollment session instantly and safely resets the state to `IDLE`.

##### 2. Offline Standalone CLI Enrollment (`tools/enroll_voice.py`)
For initial setup, headless server provisioning, or when the Master Server is not running:
``` bash
# In master_server with .venv activated:
python tools/enroll_voice.py --name owner --lang both
```
- Parameters:
  - `-n`, `--name`: Profile name (`owner`, `luca`, `marco`...)
  - `-l`, `--lang`: Prompt language set (`it`, `en`, or `both`)
  - `-d`, `--device`: Optional hardware audio input index
- Runs completely offline without internet or Gemini API access, utilizing your local PC microphone via `sounddevice`.

Then running 
``` bash
python tools/calibrate_speaker.py
```
the user is able to test their voice and determine the optimal threshold (e.g. `0.48`).

Instead of hardcoding this value in Python files, the threshold is configured dynamically via the environment variable `AXL_SPEAKER_THRESHOLD` in `master_server/.env` (default: `0.48`):
```bash
# In master_server/.env
AXL_SPEAKER_THRESHOLD=0.48
```
This is loaded automatically on startup by both `speaker_verifier.py` and `main.py`.

#### Environment Variables & API Keys (`.env`)
To protect private credentials and API keys, the file `master_server/.env` is strictly untracked by Git via `.gitignore`.
A template file `master_server/.env.example` is available in the repository.

To configure your environment:
1. Create `master_server/.env` (or copy from `.env.example`).
2. Add your Google AI Studio API key and system settings:
```env
# Gemini API Configuration
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-flash-lite-latest
GEMINI_PRO_MODEL=gemini-3.1-pro-preview

# Security Mode: 'friendly', 'restrict', or 'paranoid'
AXL_SECURITY_MODE=friendly

# Biometric Speaker Verification Threshold (calibrated via tools/calibrate_speaker.py)
# Controls sensitivity dynamically without modifying Python code (default: 0.48)
AXL_SPEAKER_THRESHOLD=0.48
```

#### Phase 3: Cognitive Brain & Dynamic Tools (`brain_gemini.py` & `commands.py`)
In Phase 3, A-X-L gains its cognitive reasoning and practical actuation capabilities:
- **Multimodal Audio Ingestion:** The in-memory WAV buffer generated after VAD End-of-Speech is dispatched directly to Gemini (`google-genai` SDK) without any disk I/O latency.
  - **Native Automatic Function Calling (AFC) & Biometric Privilege Guardrails:** All system actions are neatly registered in `master_server/src/commands.py`:
  - `set_timer(duration_seconds, label)`
  - `set_alarm(time_str, label)`
  - `get_system_status()`
  - `media_control(action, target)`
  - `execute_slave_command(command, target)`
  - `set_security_mode(mode)` (switches between `friendly`, `restrict`, and `paranoid` distrust profiles)
  - `set_mute_mode(enabled)` (silences acoustic ingestion until physically unmuted via touchscreen)
  - `toggle_telemetry_hud(show, target)` (shows/hides live telemetry HUD)
  Gemini automatically invokes the right function based on natural language intent. Crucially, critical tools (`set_security_mode`, `set_mute_mode`, `execute_slave_command`) enforce **Strict Biometric Privilege Verification** directly at execution time: if an unauthorized speaker tries to switch security modes or mute the system in `restrict` or `paranoid` mode, the tool deterministically rejects execution (`COMANDO RIFIUTATO: Accesso negato`). Furthermore, a synchronous intent guardrail in `main.py` and turn-level meta-prompt alert in `brain_gemini.py` ensure complete end-to-end immunity from LLM hallucinations or unauthorized voice overrides.
- **Biometric Distrust Integration:** The speaker identity evaluated by CAM++ (`is_stranger` flag) is injected into the cognitive prompt:
  - If **Owner**: A-X-L behaves with loyal efficiency and executes commands.
  - If **Stranger**: A-X-L enters distrust mode, barking or sarcastically refusing to execute sensitive commands or access personal data.
- **Dynamic Model Escalation & Switching:** Standard interactions run on ultra-low-latency flash models (`gemini-flash-lite-latest` / `gemini-3.5-flash`), with planned runtime model switching (`GeminiBrain.set_model`) to escalate to Pro models (`gemini-3.1-pro-preview`) via voice or UI when complex tasks are requested.
- **Clean Terminal Telemetry & Model Awareness:** Terminal output is streamlined to eliminate dialogue duplication. The Master server centrally prints single authoritative dialogue events (`>>> [USER HEARD] <<<` and `>>> [A-X-L SPEAKS] <<<`), while the Cognitive Brain explicitly logs model readiness and invocation (`[AXL-Brain]: Cognitive Core ready | Active Model: ...`). Pre-trigger wake-word probes and third-party SDK chatter are silenced from the standard terminal stream.
- **Hybrid Neural TTS Engine & Authentic Canine SFX (`tts_engine.py`):** 
  - Studio-grade speech synthesis combining Microsoft Edge Neural TTS (**Diego** for Italian with tuned `+10%` pacing and tight conversational cadence) with Piper ONNX (**Bryce** for English with Tony Stark/Jarvis persona). Includes on-device local Piper fallback (**Miro** / **Riccardo**), text normalization, and acoustic echo feedback prevention.
  - **Authentic Canine SFX In-Line Sequencing & Compound Tag Parsing:** Rather than phonetically pronouncing sound tags or stage directions (e.g. `*Bark*`, `*Growl*`, `*GRRR-WOOF!*`, `*whirr*`, `*pant*`, `*Luce rossa lampeggiante*`), `PiperTTSEngine` parses incoming replies, strips non-verbal stage directions, and automatically interleaves authentic 16kHz PCM audio samples (`bark.wav`, `growl.wav`, `pant.wav`) directly from RAM (`self.sfx_cache`) with 100ms natural acoustic silence gaps. Compound menacing tags (such as `*GRRR-WOOF!*` or `*GRR-WOOF*`) strictly trigger `growl.wav` rather than the friendly `bark.wav`. Multi-part sentences containing sounds in the middle are synthesized concurrently with `asyncio.gather` and assembled in seamless temporal sequence.
  - **Canine Acoustic Profiles (Bark, Growl, Pant) & Strict System Prompt Palette:** Features an open-source hybrid acoustic blend combining the energetic high-frequency yip attack of a young puppy with the chest resonance and growl body of an adult guard dog synchronized at a 320ms double-bark cadence (`bark.wav`). For distrust and threat warnings, it features an aggressive guttural cybernetic growl (`growl.wav`, derived from `442816__qubodup__goblin-scream.wav`). For chill moments and relaxed posture, it incorporates a rhythmic dog panting/breathing sequence (`pant.wav`, 2.20s, extracted from `574183__bloodybill__doc-the-aussie-growls-dog.wav`), seamlessly replacing mechanical stage directions like `*whirr*` and `*pant*`. The Cognitive Brain system prompt strictly limits acoustic cues to these 3 canonical tags, prohibiting LLM hallucinations of unsupported sounds (`*sbuffo idraulico*`, `*fischio acuto*`), with defensive regex safeguards in `tts_engine.py`.


#### Phase 4: Cybernetic Visor & Slave Node Architecture
Phase 4 introduces visual feedback and workstation orchestration:
- **Cybernetic Visor Web UI (`master_server/web_ui/`):**
  - Served directly from FastAPI on `/ui` with real-time state synchronization via WebSocket `/ws/ui`.
  - Tactical HUD states: `IDLE` (passive surveillance), `LISTENING` (wake-word triggered), `THINKING` (neural cognitive inference), `SPEAKING` (vocal streaming audio visualizer), and `ALERT` (biometric breach / stranger warning).
  - **AMOLED Pixel-Shifting & Power-Saving Dimming:** Built-in burn-in protection specifically designed for the Samsung Galaxy A33 Super AMOLED display, periodically shifting UI coordinates and automatically entering subdued power-saving mode (softly lowering brightness and opacity) after 45 seconds of idle inactivity. Background audio telemetry and passive streams are filtered so they do not inadvertently prevent screen dimming, and the display wakes up instantaneously upon wake-word detection, voice interaction, touchscreen tap, or mouse movement.
  - Live subtitles display showing transcribed user queries, A-X-L vocal responses, and speaker biometric verification scores.
  - **Modern Card UI & Interactive Controls:**
    - **Top Tactical Status Tray & Cyberpunk Gear Menu:** Restyled status tray with modern rounded pill/card buttons (`border-radius: 10px`), smooth hover/tap feedback, and full mouse + touchscreen support:
      - `SERVER TELEM: ON/OFF` in top-left and `SLAVE TELEM: ON/OFF` in status tray with mutually exclusive hardware monitors.
      - One-way security mode lock `FRIENDLY -> RESTRICT / PARANOID` that prevents unauthorized downgrade once locked.
      - **A-X-L Cyberpunk Gear Menu (`#settings-btn`):** High-tech animated gear icon that opens a glassmorphic dropdown popup matching the security mode style, giving instant access to:
        - 🔄 **Reload UI** (Ricarica rapida del visore)
        - ⚙️ **Configura / Info IP Master** (Direct bridge to native Android IP config dialog on Kiosk, and IP prompt on PC)
        - 🔁 **Riconnetti WebSocket** (Forza la riconnessione istantanea dei canali di controllo e audio)
        - ⛶ **Schermo Intero** (Toggle fullscreen)
      - Dedicated Fullscreen shortcut button and WebSocket connectivity indicator.
      - **Real-Time Playback Synchronization & Tactical STOP Button:** When A-X-L speaks, the ASK ME button transforms dynamically into an interactive pulsating red `STOP` button. The server keeps `SPEAKING` state active for the *exact, complete duration of the physical audio playback* (paced streaming with lead buffer). Pressing STOP immediately halts network streaming, purges server audio buffers, and transmits `"STOP"` to the client peripheral (triggering hardware `AudioTrack.flush()` on Android Kiosk and `sd.stop()` on PC), silencing A-X-L instantaneously. Acoustic feedback loops (microphone hearing A-X-L's own speaker) are fully inhibited during vocal playback, ensuring long replies are never cut short.
    - **Dual Independent Telemetry (Master Server vs Slave Node):**
      - Allows inspecting hardware metrics for either the Master Server host machine (e.g. ThinkPad) or the remote Slave workstation (e.g. Fedora/Windows rig) on the exact same HUD panel.
      - **Mutually Exclusive Display:** Only one telemetry panel can be active at a time; clicking one opens it and automatically closes the other. Clicking the active one toggles it closed.
      - **Universal Multi-GPU Support:** Detects any combination of dedicated (NVIDIA RTX/GTX via `nvidia-smi`, AMD Radeon, Intel Arc) and integrated GPUs (Intel UHD/Iris Xe, AMD Radeon APU) across desktops and hybrid dual-GPU laptops, displaying individual card loads, thermals, frequencies, and VRAM.
      - **Voice Commands:** Spoken voice commands (*"mostra telemetria server"*, *"telemetria master"*, *"mostra telemetria slave"*, *"telemetria pc"*, *"chiudi telemetria"*) trigger real-time telemetry streaming for the requested machine via native tool invocation and dual-layer regex guardrails.
    - **Dead-Center Tactical Clock:** Absolute geometric centering (`left: 50%; transform: translateX(-50%)`) ensuring the top clock (`#clock-time`) is permanently aligned to the exact horizontal center of the display, independent of asymmetric brand text or status button widths.
    - **Tactical MUTE Button & Voice Protocol:** Dedicated rounded action button above `SPEAKER ID` with dynamic cyan glow (`MIC: ATTIVO`) and pulsing red alert state (`MIC: MUTO`). Supports voice command muting ("AXL muto", "disattiva microfono") which drops incoming WebSocket PCM packets at the network layer. **Strict Security Constraint:** Unmuting can ONLY be performed by physically tapping the UI button, preventing malicious voice overrides.
    - **"ASK ME..." Push-to-Ask Button:** Large action button opposite the MIC button that starts listening without saying the wake-word (`ASK_START` WebSocket message → `AudioPipeline.force_listen()`). Its colour follows the security mode (green in `friendly`, orange in `restrict`, red in `paranoid`); speaker verification still applies in protected modes. Strictly disabled and unclickable while microphone mute is active (ensuring total audio sensor silence until explicitly unmuted via the MUTE button); it is also disabled while A-X-L is listening/thinking/speaking. MIC and ASK ME are sized for easy visibility/tapping on the kiosk.
    - **Live Security Mode Voice Synchronization:** Real-time bi-directional sync: commands like "passa in modalità paranoica" or "metti restrict" update `speaker_verifier.py`, `os.environ`, and instantly broadcast WebSocket events to update the UI badge without reload.
    - **Modernized Hardware Telemetry Panel:** Redesigned in a 2x2 glassmorphic card grid with rounded corners (`border-radius: 16px-20px`), dedicated monitor cards for CPU core load, RAM memory, dedicated & integrated GPUs, and cooling fans with rounded progress bars, preserving A-X-L's cyberpunk palette.
    - **Dynamic Split-Stage Layout & Live/Fallback Telemetry:** Responsive stage geometry that prevents visor-to-subtitle overlap across all aspect ratios, cleanly hides/shows the telemetry panel on toggle, and streams live hardware telemetry (with automatic local `psutil` and dynamic browser fallback when the external slave daemon is offline).
- **Workstation Slave Daemon (`slave_node/slave_daemon.py`):**
  - Standalone lightweight daemon running on Fedora or Windows workstations on port `8001`.
  - Exposes real-time hardware telemetry (`psutil`: CPU load, RAM usage, storage, thermal sensors, battery status).
  - Handles authenticated automation webhooks (`/execute`) for commands like `lock_screen`, `sleep`, `mute`, and application launching triggered by A-X-L.
