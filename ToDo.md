Sviluppare su Fedora/Windows in una nuova repository Git e fare `git pull` sul ThinkPad Debian 12 a sviluppo ultimato è esattamente il workflow corretto. Entrambi i sistemi condividono l'architettura `x86_64` (con supporto AVX2): ciò che testi sul tuo PC principale girerà in modo identico sul ThinkPad, permettendoti anche di lavorare direttamente sul server tramite l'estensione **Remote - SSH** di VSCode.

Il microfono del Galaxy A33 diventa una periferica di rete: cattura chunk PCM grezzi e li spara via WebSocket al ThinkPad con latenza sub-10ms su LAN.

---

### Architettura di Riferimento (Master su Debian 12)

Per massimizzare stabilità e velocità di sviluppo senza sacrificare le performance su i5-4300U, il Master abbandona il C++ custom (niente più `MFCCExtractor` o gestione manuale dell'arena TFLite) a favore di un **Core Python Asincrono (`asyncio` + `FastAPI`)** che orchestra librerie con **backend C++ precompilato (`onnxruntime`)**:

* **Audio Transport:** Raw PCM 16kHz, 16-bit, Mono (32 KB/s di banda, irrilevante su Wi-Fi).
* **VAD (Voice Activity Detection):** `silero-vad` (ONNX) — filtra il silenzio prima di attivare qualsiasi pipeline pesante.
* **Wake-Word:** `openWakeWord` (ONNX) — rileva l'attivazione senza dover estrarre MFCC a mano.


* **Speaker Verification (Diffidenza):** `sherpa-onnx` (modello `wespeaker` o `3dspeaker` in ONNX) — estrae l'embedding vocale (vettore 256-dim) e calcola la *Cosine Similarity* in <15ms su CPU.
* **Cognitive & STT/TTS:** `google-genai` (SDK Python ufficiale per Gemini 2.0 Flash con input audio nativo) + `piper-tts` (binario C++/ONNX locale per generare la voce di A-X-L a latenza zero senza costi API).

---

### Struttura della Nuova Repository (`axl-system`)

```text
axl-system/
├── master_server/               # Gira su ThinkPad Debian 12
│   ├── models/                  # File .onnx (Silero, openWakeWord, SpeakerID, Piper)
│   ├── src/
│   │   ├── main.py              # Entrypoint FastAPI + WebSocket Server
│   │   ├── audio_pipeline.py    # RingBuffer -> VAD -> WakeWord -> SpeakerID
│   │   ├── brain_gemini.py      # Client Gemini API + System Prompt (Lore A-X-L)
│   │   ├── network_router.py    # Dispatcher comandi verso Slave PC e Android
│   │   └── tts_engine.py        # Wrapper Piper TTS (genera stream PCM di risposta)
│   ├── web_ui/                  # Il Visore (HTML/CSS/JS con pixel-shifting)
│   └── requirements.txt
├── android_kiosk/               # App Kotlin minimale per Galaxy A33 (NO NDK/JNI)
│   └── app/src/main/java/com/axl/kiosk/
│       ├── MainActivity.kt      # WebView Fullscreen + KeepScreenOn
│       ├── AudioStreamService.kt# Foreground Service: AudioRecord <-> WebSocket
│       └── IntentExecutor.kt    # Esegue gli OS Intents ricevuti dal ThinkPad
└── slave_node/                  # Gira su PC Fedora / Windows
    ├── slave_daemon.py          # FastAPI + psutil + esecuzione script
    └── requirements.txt

```

---

### Roadmap Operativa (In Ordine Cronologico)

Usa questa scaletta passo-passo nella nuova chat per costruire il sistema modulo per modulo, testando ogni singolo blocco prima di passare al successivo.

#### FASE 1: Infrastruttura Base e Ingestione Audio (PC / ThinkPad)

1. **Setup Repo e Ambiente Virtuale:** Creazione della struttura directory, `requirements.txt` (`fastapi`, `uvicorn`, `websockets`, `numpy`, `onnxruntime`, `openwakeword`, `sherpa-onnx`, `google-genai`) e script di download automatico dei modelli `.onnx`.
2. **Server WebSocket Audio & RingBuffer:** Scrittura di `main.py` con endpoint WebSocket `/ws/audio` che riceve frame binari da 640 byte (20ms di audio a 16kHz 16-bit mono) e li inserisce in un buffer circolare `numpy` thread-safe.
3. **Client di Test Locale (Simulatore Android):** Scrittura di un piccolo script Python (`test_mic_client.py` usando `sounddevice` o `pyaudio`) per catturare l'audio dal microfono del tuo PC Fedora/Windows e inviarlo al server WebSocket. *(Questo ti permette di sviluppare e testare tutto il Master sul tuo PC senza toccare il telefono).*

#### FASE 2: Pipeline Percettiva Locale (VAD + Wake-Word + Diffidenza)

4. **Filtro Silero VAD:** Integrazione in `audio_pipeline.py` di Silero VAD. Il sistema scarta i frame di silenzio/rumore bianco risparmiando CPU.
5. **Motore Wake-Word:** Aggancio di `openWakeWord` in cascata al VAD. Quando rileva la keyword, cambia lo stato della macchina a stati finiti (FSM) da `IDLE` a `LISTENING_COMMAND` e invia un evento JSON alla Web UI (es. `{"state": "WAKE"}`).
6. **Speaker Verification (Sistema di Diffidenza):**
* Creazione dello script `enroll_voice.py` per registrare 5 secondi della tua voce e salvare il tuo tensore di riferimento (`owner_embedding.npy`).
* Integrazione di `sherpa-onnx` nella pipeline: mentre parli dopo la wake-word, calcola la *Cosine Similarity* tra l'audio in ingresso e `owner_embedding.npy`. Sotto la soglia (es. `< 0.65`), marca il flag `is_stranger = True`.



#### FASE 3: Cervello Cognitivo (Gemini + TTS Locale)

7. **End-of-Speech & Bufferizzazione Comando:** Logica che accumula l'audio parlato dopo la wake-word finché Silero VAD non rileva 800ms di silenzio continuo (fine frase).
8. **Integrazione Gemini Multimodal:** Invio del buffer audio grezzo (convertito in WAV in-memory) + contesto (`is_stranger`, telemetria Slave) a Gemini 2.0 Flash tramite SDK. Configurazione dei *Structured Outputs* (JSON) o *Function Calling* affinché Gemini restituisca sia la risposta testuale/sarcastica di A-X-L, sia l'eventuale comando da eseguire (`target: "fedora" | "android"`, `action: "..."`).
9. **Sintesi Vocale Locale (Piper TTS):** Configurazione di Piper in `tts_engine.py` per convertire la risposta testuale di Gemini in uno stream PCM e rispedirlo indietro sullo stesso WebSocket `/ws/audio` verso il client.

#### FASE 4: Il Visore Web e i Nodi Esecutivi (Slave)

10. **Web UI (Il Visore):** Creazione dell'interfaccia HTML/JS servita da FastAPI su `/ui` e collegata via WebSocket a `/ws/ui`. Implementazione delle animazioni di stato (Idle, Listening, Thinking, Speaking, Stranger Alert) e del timer CSS/JS per il pixel-shifting anti burn-in.
11. **Nodo Slave (Fedora/Windows):** Scrittura di `slave_daemon.py` per esporre telemetria (`psutil`: CPU, RAM, temperature) e ricevere dal Master i webhook per eseguire script o comandi di sistema.

#### FASE 5: App Android "Thin Kiosk" (Galaxy A33)

12. **Progetto Android Puro Kotlin (Zero NDK):** Creazione della nuova app leggera senza CMake né C++.


13. **Servizio Audio Bidirezionale (`AudioStreamService.kt`):** Implementazione di un *Foreground Service* con `OkHttp WebSocket`:
* Thread 1: Legge da `AudioRecord` e invia byte binari al ThinkPad --> cambiare in caso da test_mic_client.py al nuovo nel master?.
* Thread 2: Riceve byte binari dal ThinkPad e li scrive direttamente su `AudioTrack` per far parlare A-X-L dal telefono.


14. **WebView Kiosk & Intent Receiver:** Configurazione di `MainActivity.kt` per mostrare la Web UI ospitata sul ThinkPad e ricezione di comandi JSON sul WebSocket per lanciare `Intent` Android (sveglie, apertura app, controlli media).

#### FASE 6: Deploy su ThinkPad Debian 12

15. **Provisioning e Systemd:** `git clone` della repo sul ThinkPad, creazione del servizio `axl-master.service` su `systemd` per l'avvio automatico al boot, assegnazione di IP statico sul router e test di stress end-to-end.
