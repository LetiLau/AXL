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
├── master_server/                # Gira su ThinkPad Debian 12
│   ├── models/                   # File .onnx (Silero, openWakeWord, SpeakerID, Piper)
│   ├── src/ 
│   │   ├── main.py               # Entrypoint FastAPI + WebSocket Server
│   │   ├── audio_pipeline.py     # RingBuffer -> VAD -> WakeWord -> SpeakerID
│   │   ├── brain_gemini.py       # Client Gemini API + System Prompt (Lore A-X-L)
│   │   ├── network_router.py     # Dispatcher comandi verso Slave PC e Android
│   │   └── tts_engine.py         # Wrapper Piper TTS (genera stream PCM di risposta)
│   ├── web_ui/                   # Il Visore (HTML/CSS/JS con pixel-shifting)
│   └── requirements.txt
├── android_kiosk/                # App Kotlin minimale per Galaxy A33 (NO NDK/JNI)
│   └── app/src/main/java/com/axl/kiosk/
│       ├── MainActivity.kt       # WebView Fullscreen + KeepScreenOn
│       ├── AudioStreamService.kt # Foreground Service: AudioRecord <-> WebSocket
│       └── IntentExecutor.kt     # Controller nativo: AlarmClock, Calendar, App launcher
└── slave_node/                   # Gira su PC Fedora / Windows
    ├── slave_daemon.py           # FastAPI + psutil + esecuzione script
    └── requirements.txt

```

---

### Roadmap Operativa (In Ordine Cronologico)

Usa questa scaletta passo-passo nella nuova chat per costruire il sistema modulo per modulo, testando ogni singolo blocco prima di passare al successivo.

#### FASE 1: Infrastruttura Base e Ingestione Audio (PC / ThinkPad)

1. [x] **Setup Repo e Ambiente Virtuale:** Creazione della struttura directory, `requirements.txt` (`fastapi`, `uvicorn`, `websockets`, `numpy`, `onnxruntime`, `openwakeword`, `sherpa-onnx`, `google-genai`) e script di download automatico dei modelli `.onnx`.
2. [x] **Server WebSocket Audio & RingBuffer:** Scrittura di `main.py` con endpoint WebSocket `/ws/audio` che riceve frame binari da 640 byte (20ms di audio a 16kHz 16-bit mono) e li inserisce in un buffer circolare `numpy` thread-safe.
3. [x] **Client di Test Locale (Simulatore Android):** Scrittura di un piccolo script Python (`test_mic_client.py` usando `sounddevice` o `pyaudio`) per catturare l'audio dal microfono del tuo PC Fedora/Windows e inviarlo al server WebSocket. *(Questo ti permette di sviluppare e testare tutto il Master sul tuo PC senza toccare il telefono).*

#### FASE 2: Pipeline Percettiva Locale (VAD + Wake-Word + Diffidenza)

4. [x] **Filtro Silero VAD:** Integrazione in `audio_pipeline.py` di Silero VAD. Il sistema scarta i frame di silenzio/rumore bianco risparmiando CPU.
5. [x] **Motore Wake-Word:** Aggancio di `openWakeWord` in cascata al VAD. Quando rileva la keyword, cambia lo stato della macchina a stati finiti (FSM) da `IDLE` a `LISTENING_COMMAND` e invia un evento JSON alla Web UI (es. `{"state": "WAKE"}`).
6. [x] **Speaker Verification (Sistema di Diffidenza):**
   * Creazione dello script `enroll_voice.py` per registrare 5 secondi della tua voce e salvare il tuo tensore di riferimento (`owner_embedding.npy`).
   * Integrazione di `sherpa-onnx` nella pipeline: mentre parli dopo la wake-word, calcola la *Cosine Similarity* tra l'audio in ingresso e `owner_embedding.npy`. Sotto la soglia (es. `< 0.65`), marca il flag `is_stranger = True`.



#### FASE 3: Cervello Cognitivo (Gemini + TTS Locale)

7. [x] **End-of-Speech & Bufferizzazione Comando:** Logica che accumula l'audio parlato dopo la wake-word finché Silero VAD non rileva 800ms di silenzio continuo (fine frase).
8. [x] **Integrazione Gemini Multimodal:** Invio del buffer audio grezzo (convertito in WAV in-memory) + contesto (`is_stranger`, telemetria Slave) a Gemini tramite SDK (`google-genai`). Implementazione di *Automatic Function Calling* con modulo `commands.py` modulare, gestione diffidenza biometrica e prompt di personalità A-X-L.
9. [x] **Sintesi Vocale Locale (Piper TTS):** Configurazione di Piper in `tts_engine.py` per convertire la risposta testuale di Gemini in uno stream PCM e rispedirlo indietro sullo stesso WebSocket `/ws/audio` verso il client. Supporto bilingue (italiano ed inglese), text normalization e protezione dal loop acustico (echo feedback).

#### FASE 4: Il Visore Web e i Nodi Esecutivi (Slave)

10. [x] **Web UI (Il Visore):** Creazione dell'interfaccia HTML/JS servita da FastAPI su `/ui` e collegata via WebSocket a `/ws/ui`. Implementazione delle animazioni di stato (Idle, Listening, Thinking, Speaking, Stranger Alert) e del timer CSS/JS per il pixel-shifting anti burn-in.
    * **Modernizzazione Bottoni & Card Style:** Restyling moderno e arrotondato (`border-radius: 10px`) dei bottoni della barra superiore con supporto intelligente sia per cursore mouse che touch, mantenendo i colori e i font di A-X-L.
    * **Blocco Unidirezionale Modalità di Sicurezza (`MODE`):** Il pulsante `MODE` è cliccabile solo quando si trova su `FRIENDLY` per permettere di impostare `RESTRICT` o `PARANOID`. Una volta attivata una modalità protetta, il pulsante si blocca (`🔒`) impedendo a estranei di disattivare la verifica vocale dal display.
    * **Verifica e Switch Modalità di Sicurezza Vocale:** Sincronizzazione completa del comando vocale `set_security_mode` tra Gemini Brain, `commands.py`, `StreamManager` (`speaker_verifier.py`) e Web UI WebSocket: pronunciare "passa in modalità paranoica" o "metti restrict" aggiorna dinamicamente la logica di diffidenza biometrica e la UI in tempo reale.
    * **Pulsante MUTE Tattico & Vincolo Sblocco Esclusivo da Schermo:** Aggiunto pulsante MUTE moderno direttamente sopra la scritta `SPEAKER ID` nel footer HUD. Supporta il comando vocale ("vai in muto", "silenzio", "disattiva microfono") che arresta l'ingestione dei pacchetti audio a livello WebSocket. **Regola di sicurezza:** la modalità muto può essere disattivata solo ed esclusivamente premendo il pulsante fisico/touch sul display, rendendo impossibile lo sblocco accidentale o non autorizzato a voce.
    * **Centratura Geometrica Assoluta dell'Orologio:** Utilizzo di posizionamento assoluto (`left: 50%; transform: translateX(-50%)`) per garantire che l'orario rimanga perfettamente e matematicamente al centro della barra superiore, a prescindere dalle larghezze asimmetriche del brand A-X-L a sinistra e dei pulsanti a destra.
    * **Riquadro Telemetria Moderno (Griglia 2x2 Glassmorphic):** Nuovo design a card arrotondate (`border-radius: 16px-20px`) per CPU, RAM, GPU e ventole, con scomparsa totale alla chiusura, riposizionamento fluido del visore al centro e aggiornamento continuo dei valori (tramite nodo slave, `psutil` locale o fallback dinamico).
11. [x] **Nodo Slave (Fedora/Windows):** Scrittura di `slave_daemon.py` per esporre telemetria (`psutil`: CPU, RAM, temperature, GPU dedicate/integrate, ventole) e ricevere dal Master i webhook per eseguire script o comandi di sistema. Visualizzazione split-layout HUD in tempo reale sul visore.

#### FASE 5: App Android "Thin Kiosk" (Galaxy A33)

12. **Progetto Android Puro Kotlin (Zero NDK):** Creazione della nuova app leggera senza CMake né C++.


13. **Servizio Audio Bidirezionale (`AudioStreamService.kt`):** Implementazione di un *Foreground Service* con `OkHttp WebSocket`:
* Thread 1: Legge da `AudioRecord` e invia byte binari al ThinkPad --> cambiare in caso da test_mic_client.py al nuovo nel master?.
* Thread 2: Riceve byte binari dal ThinkPad e li scrive direttamente su `AudioTrack` per far parlare A-X-L dal telefono.


14. **WebView Kiosk & Intent Receiver:** Configurazione di `MainActivity.kt` per mostrare la Web UI ospitata sul ThinkPad e ricezione di comandi JSON sul WebSocket per lanciare `Intent` Android (sveglie, apertura app, controlli media).
""" Dettaglio esplicito delle integrazioni Android native: `AlarmClock` (sveglie/timer), `CalendarContract` (eventi Google) e `AudioManager` (gestione volume/media)."""

#### FASE 6: Animazioni Espressive Web UI (Schizzi 2D / Lottie / Avatar)

15. [ ] **Avatar Dinamico A-X-L nel Visore:** Integrazione degli schizzi/animazioni 2D (Lottie / SVG / WebM) nello stage centrale del Visore Web per reagire ai diversi stati e comandi (dorme in Idle, orecchie tese in Listening, ringhio su Stranger, console DJ quando c'è musica, ecc.).

#### FASE 7: Deploy su ThinkPad Debian 12

16. [ ] **Provisioning e Systemd:** `git clone` della repo sul ThinkPad, creazione del servizio `axl-master.service` su `systemd` per l'avvio automatico al boot, assegnazione di IP statico sul router e test di stress end-to-end.

---

#### NOTE FUTURE & OTTIMIZZAZIONI POST-SVILUPPO
- [ ] **Ottimizzazione Riconoscimento Vocale (Speaker Verification):** 
  - Migliorare l'acquisizione dell'enrollment (multi-take a distanze diverse e con rumore ambientale) e tarare la soglia di Cosine Similarity per evitare falsi positivi/negativi quando si parla piano o vicino al microfono.
  - Testare modelli alternativi (es. 3D-Speaker o modelli più resilienti) dopo lo sviluppo dell'app Kotlin.
- [x] **Tre Modalità di Sicurezza e Diffidenza (Security Modes):**
  - `friendly` (attiva di default per sviluppo e test): A-X-L ascolta ed esegue i comandi di chiunque, eliminando rifiuti accidentali e risparmiando chiamate/quote API.
  - `restrict`: A-X-L ascolta ed esegue ordini solo per l'Owner e gli amici registrati nei profili (`models/profiles/*.npy`), respingendo gli estranei.
  - `paranoid`: A-X-L obbedisce solo ed esclusivamente all'Owner registrato; se parla un amico, rifiuta spiegando che è in modalità paranoica.
- [x] **Nuova voce**: Trovare un nuovo modello vocale che pronunci le parole in maniera più sensata. 
- [x] **Pulizia e Ottimizzazione Log Terminale (Master & Brain):**
  - Eliminazione delle stampe duplicate del parlato e della trascrizione: solo il Master stampa authoritative `>>> [USER HEARD] <<<` e `>>> [A-X-L SPEAKS] <<<`.
  - Spostamento di `[BRAIN RAW OUTPUT]` e `[WAKEWORD_PROBE]` a livello `DEBUG` per evitare rumore prima dell'effettivo trigger.
  - Silenziamento chatter interno di terze parti (`google_genai`, `httpx`, `websockets`).
  - Notifica esplicita di avvio del Brain e modello neurale attivo (`[AXL-Brain]: Cognitive Core ready | Active Model: ...` all'avvio e ad ogni inferenza).
- [ ] **Fix e Robustezza Comandi Vocali (`muto` e cambio modalità `friendly`/`restrict`/`paranoid`):**
  - Investigare e correggere il mancato dispatch dei tool quando si utilizzano modelli compatti (`gemini-flash-lite`), che talvolta allucinano la risposta testuale ("mi metto in muto") senza emettere la `function_call` (`set_mute_mode`).
  - Gestione privilegi biometrici: se il sistema è in `restrict` o `paranoid` e il punteggio vocale assegna `STRANGER`, il system prompt vieta l'invocazione di `set_security_mode`.
- [ ] **Switch Dinamico tra Modelli Gemini (Dynamic Model Switching):**
  - Consentire ad A-X-L di muoversi dinamicamente tra i vari modelli Gemini (`gemini-flash-lite`, `gemini-3.5-flash`, `gemini-3.1-pro`...) sia a caldo via Web UI / configurazione, sia tramite comando vocale ("passa a Gemini Pro") o per escalation automatica a seconda della complessità del task richiesto.
- [ ] Aggiungi suoni di abbaio o ringhio vero
- [ ] **Antigravity**: Spiega o correggi il codice che ho scritto. correzione (esercizi degli esami, altri progetti...)


