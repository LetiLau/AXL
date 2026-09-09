# A-X-L AI Assistant

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

## Coding Info
The project is designed to run on an old Android smartphone. In particular I'll use an old *Samsung Galaxy A33 5G*.


#### System Architecture 

The system has a strict separation of responsibilities (Hardware Abstraction Layer) to ensure future portability from Android to Linux/Windows servers.

1. **The Brain (MASTER - Galaxy A33 in Kiosk Mode):**
   - **Low Level (C/C++ via NDK/CMake):** This is the actual logic "core." It handles wake-words, speaker verification, intensive audio processing, and networking (sockets/WebSockets). Written natively, it should be able to compile on non-Android machines in the future.
   - **Thin Wrapper (Kotlin via JNI):** A thin interface between the Android hardware and the C++ core. It handles *only* OS intents (e.g., opening apps), permissions, microphone/display access, and HTTP calls to the Gemini API. It does not contain heavy business logic.
2. **The Viewer (Multi-Screen UI via Web):**
The A33 hosts a local web server. The interface (HTML/CSS/JS) is a thin client served over the local network. The A33 displays it via WebView (with pixel-shifting to prevent OLED burn-in) and allows external tablets/displays to access it. It handles automatic fallback.
3. **Executive Nodes (SLAVE - Fedora/Windows PCs):**
   Computers on the network run lightweight Python daemons (`psutil`, `FastAPI`). They are passive: they receive input from the A33, execute local commands, and send system telemetry.


#### Coding Rules:

1. **Strict Compartmentalization:** Communication between Kotlin and C++ occurs exclusively through well-defined JNI signatures. Modules do not depend on each other's internal implementation.
2. **Optimization:** As the A33 hardware is limi it's a priority some extreme efficiency (no memory leaks in NDK code), also because the system is always-on.
