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
    * **Doppia Telemetria Hardware Indipendente (Master Server vs Slave Node):**
      - Aggiunto pulsante tattico `SERVER TELEM` in alto a sinistra accanto al logo *A-X-L* e ribattezzato il pulsante in alto a destra in `SLAVE TELEM`.
      - **Mutua Esclusività**: È possibile visualizzare una sola telemetria alla volta sullo stesso pannello HUD sinistro; l'attivazione di una chiude automaticamente l'altra, mentre un secondo click sulla telemetria attiva chiude il pannello.
      - **Comandi Vocali Bivalenti**: Implementati i comandi vocali dedicati per entrambe le macchine (*"mostra telemetria server"*, *"telemetria master"*, *"mostra telemetria slave"*, *"telemetria pc"*, *"chiudi telemetria"*) con estrazione diretta delle metriche locali (`psutil`) per il Master Server e polling HTTP verso il demone remoto per il nodo Slave.
      - **Rilevamento Multi-GPU Universale (Dedicated + Integrated)**: Creato motore di ispezione hardware `telemetry_utils.py` integrato sia nel Master Server che nel demone Slave. Rileva contemporaneamente e senza limiti GPU dedicate NVIDIA (`nvidia-smi`), GPU integrate Intel HD/Iris Xe (`i915`/`xe`), GPU dedicate e APU AMD Radeon (`amdgpu`/`radeon`) e fallback Windows, visualizzando tutte le schede grafiche presenti sul sistema con carico %, temperature, frequenze e memoria VRAM.
    * **Ripristino & Ottimizzazione Attenuazione Colori AMOLED (`hud-subdued`):**
      - Risolto il blocco causato dallo streaming continuo dell'audio del microfono (`MIC_LEVEL` a 20-30Hz) e dai frame di telemetria passiva (`TELEMETRY_UPDATE`), che azzeravano in loop continuo il timer di inattività impedendo allo schermo di attenuarsi.
      - Disaccoppiati i messaggi di telemetria e mic passivi: il timer di inattività (impostato a 45 secondi in stato `IDLE`) si attiva all'avvio e viene resettato unicamente da azioni intenzionali (wake-word vocale, cambio stato, trascrizione del parlato, risposta vocale di A-X-L, click sui pulsanti, tocco touch o movimento del mouse).
      - Transizione fluida CSS (`filter: brightness(0.55); opacity: 0.35;` con transizione a 1.2s) che riduce luminosità e contrasto a riposo per massima durata del pannello Super AMOLED del Galaxy A33 ed azzeramento del rischio burn-in.
      - Risveglio istantaneo a piena luminosità appena l'utente parla o interagisce con il visore.
    * **Menu Rapido Impostazioni Cyberpunk (Gear Dropdown) & Tasto STOP Sincronizzato:**
      - **Ingranaggio Cyberpunk & Menu a Tendina (`#settings-popup`):** Sostituito il semplice tasto reload con un'icona ingranaggio animata in stile HUD ciano, che apre un menu a scomparsa glassmorphic coerente con lo stile di `MODE: FRIENDLY`. Opzioni con icone vettoriali SVG cyberpunk dedicate (eliminati tutti gli emoji generici di sistema): Ricarica Visore (doppia freccia circolare), Configura IP Master (chiave inglese tattica), Riconnetti WebSocket (data-link di rete per handshake a caldo immediato), Lingua UI (reticolo sferico) e Arresta Kiosk & Mic (power stand-by rosso allerta). Micro-animazioni interattive hover per ciascun pulsante.
      - **Rimozione Schermo Intero dal Menu Settings:** Rimosso il duplicato dal popup poiché esiste già il pulsante dedicato `#fullscreen-btn` nella barra superiore.
      - **Predisposizione Switch Lingua UI (`#setting-opt-lang`):** Predisposto pulsante per commutare la lingua dell'interfaccia (default ENG con placeholder informativo in vista delle localizzazioni future).
      - **Pulsante Arresta Kiosk & Mic con Blocco Dinamico PC/Android (`#setting-opt-kill-kiosk`):** Aggiunta opzione per arrestare completamente l'app Kotlin Kiosk e rilasciare il microfono in background (`exitKiosk()`). La Web UI riconosce automaticamente se si trova su browser desktop/localhost (in tal caso il tasto è disabilitato e oscurato con cursore vietato) oppure all'interno del Kiosk Android (tasto attivo, evidenziato in rosso allerta `opt-kill-kiosk` per chiudere l'app ed evitare consumi anomali del mic).
      - **Identificazione Dinamica Dispositivo e Naming Tattico (`// KIOSK` vs `// NODE-n`):**
        - La Web UI e il Master Server cooperano per identificare la natura del client connesso al WebSocket `/ws/ui`.
        - Se la connessione proviene dall'app Kotlin Android, la dicitura sotto ad *A-X-L* passa automaticamente a `TACTICAL AI // KIOSK`.
        - Se la connessione proviene da browser web desktop o mobile, il Master Server assegna progressivamente uno slot dinamico (`NODE-01`, `NODE-02`, ecc.) in base all'ordine di collegamento, riciclando i numeri di nodo non appena le schede o finestre vengono chiuse.
        - **Stile Cromatico Muted Uniforme:** Uniformato il colore di `KIOSK` e `NODE-n` al grigio tattico (`var(--text-muted)`) di `TACTICAL AI //`.
      - **Riposizionamento Stato WebSocket Minimalista (Sotto al Brand A-X-L):**
        - Rimosso il riquadro pulsante `WS: ONLINE/OFFLINE` dalla tray superiore destra dei comandi per evitare confusione con i bottoni interattivi.
        - Spostato direttamente sotto alla scritta `TACTICAL AI // NODE-01` in alto a sinistra sotto forma di linea di stato essenziale, con punto luminoso e testo ciano brillante (`WS: ONLINE`), rosso allerta (`WS: OFFLINE`) o ambra (`WS: CONNECTING`), privo di cornici o bottoni ingombranti.
      - **Sincronizzazione Reale Tasto STOP & Durata Riproduzione:** Corretto il ciclo di vita dello stato `SPEAKING`. In precedenza la trasmissione WiFi terminava in 0.3s e riportava il visore in `IDLE`, facendo sparire il tasto STOP mentre l'altoparlante del client continuava a parlare per 10 secondi. Ora `speak_text()` mantiene `is_speaking = True` e il tasto rosso STOP pulsante per l'intera durata effettiva dell'audio.
      - **Silenziamento Istantaneo (Vocal Abort):** La pressione di STOP sul visore invia il comando di abort a livello di rete, azzera i buffer del server e invia `"STOP"` al client audio, scatenando lo svuotamento hardware (`AudioTrack.flush()` su Android e `sd.stop()` su PC) per zittire A-X-L all'istante.
      - **Risoluzione Troncamento Messaggi Lunghi:** Eliminata l'alterazione forzata della punteggiatura (sostituzione dei punti con virgole) che causava l'esaurimento del buffer prosodico neurale di Edge-TTS e Piper, e inibito l'audio microfonico per tutta la durata del parlato per evitare che A-X-L si auto-ascoltasse e interrompesse le proprie risposte lunghe.
11. [x] **Nodo Slave (Fedora/Windows):** Scrittura di `slave_daemon.py` per esporre telemetria (`psutil`: CPU, RAM, temperature, GPU dedicate/integrate, ventole) e ricevere dal Master i webhook per eseguire script o comandi di sistema. Visualizzazione split-layout HUD in tempo reale sul visore.

#### FASE 5: App Android "Thin Kiosk" (Galaxy A33)

12. [x] **Progetto Android Puro Kotlin (Zero NDK):** Creazione della nuova app leggera senza CMake né C++.


13. [x]  **Servizio Audio Bidirezionale (`AudioStreamService.kt`):** Implementazione di un *Foreground Service* con `OkHttp WebSocket`:
* Thread 1: Legge da `AudioRecord` e invia byte binari al ThinkPad --> cambiare in caso da test_mic_client.py al nuovo nel master?.
* Thread 2: Riceve byte binari dal ThinkPad e li scrive direttamente su `AudioTrack` per far parlare A-X-L dal telefono.


14. [x] **WebView Kiosk, Javascript Bridge & Intent Receiver:** Configurazione di `MainActivity.kt` per mostrare la Web UI ospitata sul ThinkPad e ricezione di comandi JSON sul WebSocket per lanciare `Intent` Android (sveglie, apertura app, controlli media).
      - **Interfaccia Bridge Javascript (`window.AXLKiosk`):** Esposizione dei metodi `reload()`, `showIpConfig()`, `exitKiosk()` e `getKioskTelemetry()`.
      - [ ] """ Dettaglio esplicito delle integrazioni Android native: `AlarmClock` (sveglie/timer), `CalendarContract` (eventi Google) e `AudioManager` (gestione volume/media)."""

15. [x] **Telemetria Hardware Kiosk (Galaxy A33) & Tasto Dedicato Visore:**
    * Monitoraggio metriche fisiche del dispositivo Android tramite API native (`BatteryManager`, `ActivityManager` in `KioskTelemetry.kt`):
      - Livello batteria (%), stato di ricarica (AC/Batteria), temperatura batteria (°C, essenziale per prevenire surriscaldamenti sul kiosk fisso).
      - RAM usata/disponibile e potenza segnale Wi-Fi RSSI (dBm).
    * **Pulsante KIOSK TELEM su Schermo (Esclusivo Android Kiosk):** Aggiunto pulsante `KIOSK TELEM` nella tray superiore tra `SLAVE TELEM` e `SECURITY MODE`. Il pulsante appare unicamente quando il visore è aperto all'interno dell'app Kiosk Android, rimanendo invisibile su normali browser PC.
    * **Pannello HUD Kiosk Dedicato:** Mappatura automatica sui 4 riquadri glassmorphic (Info Dispositivo, RAM, Stato Batteria/Carica e Segnale Wi-Fi RSSI).
    * **Comandi Vocali Bivalenti:** Integrazione in `commands.py` e `brain_gemini.py` dei comandi vocali (*"mostra telemetria kiosk"*, *"telemetria telefono"*, *"stato batteria"*, ecc.).

15. [ ] adding terminal commands linke mc server
16. [ ] lingua ui dall'ingranaggio

#### FASE 6: Animazioni Espressive Web UI (Video Neon / Avatar Reattivo)

15. [ ] **Avatar Dinamico A-X-L nel Visore (Video / Animazioni Contestuali):**
    * [x] **Video Avatar Neon A-X-L nello Stage Centrale:** Integrato il video `neon_axl_robo_dog.mp4` al posto del cerchio procedurale Canvas, con crop responsive quadrato 1:1 hardware (`object-fit: cover`), 
    * [ ] cornice con bracket tattici e glow neon reattivo agli stati FSM (`IDLE`, `LISTENING`, `THINKING`, `SPEAKING`, `ALERT`).
    * [ ] **Switch Dinamico Animazioni basato su Stato, Azione e Sottotitoli:**
      - Analisi semantica in tempo reale della risposta generata, dell'azione o dei sottotitoli (`TRANSCRIPT` / function call eseguita o in corso):
        - `idle` / `sleep`: animazione cane che dorme / respiro calmo
        - `listening`: animazione cane vigile con orecchie ritte
        - `stranger` / `alert`: animazione cane aggressivo / snarl ringhio verso estranei
        - `music` / `balletto`: cane con console DJ o balletto / movimento a ritmo quando suona musica o in festeggiamenti
        - `speaking`: muso che dialoga sincronizzato con la voce Piper TTS

#### FASE 7: Gestione Audio Avanzata, Riproduzione Musicale & Integrazioni Dispositivi

16. [ ] **Routing Audio e Riproduzione Musicale Dinamica (Slave vs Galaxy A33):**
    * Selezione del target di riproduzione vocale e musicale ("suona su PC" vs "suona sul visore A33"):
      - Routing su Slave (Fedora/Windows): controllo riproduzione multimediale locale (player di sistema, Spotify desktop, daemon multimediale).
      - Routing su Galaxy A33: dispatch comandi Intent/MediaSession su Android per riproduzione su smartphone.
    * Gestione periferiche di output audio:
      - Collegamento e gestione casse esterne Bluetooth o via jack AUX / auricolari (configurazione audio sink, prevenzione loopback acustico tra casse e microfono A33).
    * Integrazione Spotify & Smart Apps:
      - Supporto comandi vocali A-X-L per avvio playlist/brani Spotify (via Spotify Connect, Web API, o Android Intent).
      - Predisposizione comandi per app smart domestiche (es. app lavastoviglie, smart home appliance con notifiche di stato e avvio programmato).
17. [x] **Comando Vocale Biometric Enrollment & Calibrazione Dual-Mode (Online & Offline):**
    * **Architettura Duale (Online Conversazionale vs Offline CLI):**
      - **Modalità Online (In-System Live Voice Enrollment - Opzione A):**
        - Attivabile a voce direttamente dal Kiosk Android o PC mic (*"A-X-L aggiorna la mia voce"*, *"ricalibra la mia voce"*, *"registra un nuovo amico: Luca"*, *"aggiungi amico Marco"*).
        - Gestita dal tool Gemini Function Calling `enroll_voice_profile` unito a Guardrail deterministico sincrono di sicurezza su intent (`detect_enrollment_intent`).
        - Procedura guidata a 3 take da 3.0s di pura voce isolata da Silero VAD, con prompt vocali Piper TTS intercalati da feedback canino chill (`*pant*`).
        - Calcolo centroide CAM++ neurale (`sherpa-onnx`), salvataggio profilo `models/profiles/<nome>.npy` + WAV di audit, e **ricaricamento a caldo in memoria RAM (`verifier.load_profiles()`) senza alcun riavvio del server**.
      - **Modalità Offline (CLI Standalone Tool `tools/enroll_voice.py`):**
        - Tool Python autonomo da eseguire via terminale quando il server non è attivo, senza connessione a internet né chiamate API Gemini.
        - Identico motore matematico (Silero VAD + CAM++ ONNX) per garantire una corrispondenza biometrica al 100% tra profili generati offline e online.
    * **Biometric Privilege Enforcement sull'Enrollment:**
      - **Protezione Assoluta del Profilo Owner:** Per aggiornare, ricalibrare o modificare la voce dell'Owner, il parlante DEVE essere riconosciuto come `owner` in TUTTE le modalità di sicurezza (`friendly`, `restrict`, `paranoid`). A estranei e ospiti è vietata la sovrascrittura del profilo principale.
      - **Registrazione Nuovi Amici:** Nelle modalità protette (`restrict` e `paranoid`), solo l'Owner può abilitare l'enrollment di un nuovo amico. Richieste da parte di sconosciuti vengono respinte con ringhio (`*growl*`).
    * **Integrazione con Tasto STOP:** Se premuto durante l'enrollment, il tasto rosso STOP interrompe all'istante l'acquisizione audio e ripristina la macchina a stati finiti su `IDLE`.
    * **Evoluzione Futura (Opzione C - Modal HUD Touchscreen):** Predisposta interfaccia visiva con barra di caricamento Take (0-100%) da integrare nel Visore una volta riorganizzato il layout responsive dei bottoni sul Kiosk.

#### FASE 8: Deploy su ThinkPad Debian 12

18. [ ] **Provisioning e Systemd:** `git clone` della repo sul ThinkPad, creazione del servizio `axl-master.service` su `systemd` per l'avvio automatico al boot, assegnazione di IP statico sul router e test di stress end-to-end.

---

#### NOTE FUTURE & OTTIMIZZAZIONI POST-SVILUPPO
- [ ] **Switch Dinamico Animazioni Avatar A-X-L da Sottotitoli e Azioni:**
  - Sistema di selezione automatica del clip video avatar (da `web_ui/dog_animation/`) in base al contenuto dei sottotitoli (`user_text` e `axl_text`), allo stato di sicurezza o all'azione in svolgimento (es. dorme in idle, ringhio su estraneo, balletto/console DJ quando c'è musica).
- [ ] **Ottimizzazione Riconoscimento Vocale (Speaker Verification):** 
  - Migliorare l'acquisizione dell'enrollment (multi-take a distanze diverse e con rumore ambientale) e tarare la soglia di Cosine Similarity per evitare falsi positivi/negativi quando si parla piano o vicino al microfono.
  - Testare modelli alternativi (es. 3D-Speaker o modelli più resilienti) dopo lo sviluppo dell'app Kotlin.
- [ ] switch tra diversi kiosk (tablet/cell) a comando
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
- [x] **Fix e Robustezza Comandi Vocali (`muto` e cambio modalità `friendly`/`restrict`/`paranoid`):**
  - **Triplo Livello di Sicurezza (Tool Privilege Enforcement + Intent Guardrails + Prompt Alert):**
    - **Livello 0 (Tool-Level Biometric Enforcement in `commands.py`):** I tool critici invocati via Function Calling (`set_security_mode`, `set_mute_mode`, `execute_slave_command`) sono ora vincolati a `_AUTH_CONTEXT` (aggiornato da `main.py` per ogni turno in base al verifier CAM++). Se un utente non autorizzato o estraneo tenta di passare a `friendly` in modalità `restrict` o `paranoid`, il tool **blocca l'operazione alla radice** restituendo un rifiuto esplicito (`COMANDO RIFIUTATO: Accesso negato per mancanza di privilegi biometrici`) senza modificare l'ambiente né notificare la telemetria/UI.
    - **Livello 1 (Turn-Level Meta Prompt Alert in `brain_gemini.py`):** Se il parlante è non autorizzato in modalità protetta, il prompt del turno ordina esplicitamente a Gemini di respingere con ringhio (`*growl*`) e diffidenza la richiesta, vietando di invocare i tool amministrativi.
    - **Livello 2 (Synchronous Intent Guardrails in `main.py`):** Guardrail di sicurezza deterministico su trascrizione e parlato che blocca qualsiasi allucinazione testuale e applica il cambio solo se i privilegi biometrici sono confermati (`owner` o amici in `restrict`, solo `owner` in `paranoid`).
    - **Disattivazione fisica dell'ascolto:** quando il muto è attivo (`is_muted = True`), l'ingestione dei chunk audio WebSocket viene interrotta alla radice, impedendo a monte il trigger della wake-word finché non si preme il pulsante sul visore.
    - **Bug root cause (tool in thread & bypass AFC):** l'SDK Gemini esegue i tool sincroni in un thread (`asyncio.to_thread`) dove `notify_telemetry` necessitava di `_MAIN_LOOP` threadsafe; inoltre, i tool stessi non controllavano l'identità del parlante, permettendo ad AFC di bypassare i guardrail a valle. Risolto a monte in `commands.py`.
    - Pattern muto ampliati IT/EN ("microfono disattivato", "microphone off", "turn off the mic", "be quiet"...).
    - Nota di esecuzione: i file Python modificati richiedono il riavvio del server (`python src/main.py`) per essere caricati in memoria.
- [x] **Pulsante "ASK ME..." (push-to-ask senza wake-word):**
  - Nuovo bottone nella barra del visore, a destra (MIC resta a sinistra); entrambi ingranditi (`min-height: 48px`, font e icone maggiori) per essere ben visibili/toccabili dal kiosk A33.
  - Colore legato alla modalità di sicurezza: verde in `friendly`, arancione in `restrict`, rosso in `paranoid` (stessi colori del badge MODE). Nelle modalità protette la verifica vocale resta attiva: il bottone salta solo la wake-word.
  - Server: messaggio WebSocket `ASK_START` → `AudioPipeline.force_listen()` avvia subito la raccolta comando; l'end-of-speech scatta solo dopo che inizi a parlare, con timeout di 5s se non dici nulla.
  - Blocco assoluto quando il mic è muto: se la modalità MUTE è attiva, il bottone ASK ME è disabilitato e non cliccabile (con icona 🔇, label "MIC MUTO", stile oscurato a basso contrasto e tooltip esplicativo), per garantire totale coerenza di sicurezza con il muto dei sensori audio. Il messaggio `ASK_START` viene ignorato anche lato backend se il server è in stato mutato. È inoltre disabilitato mentre A-X-L ascolta/pensa/parla. La verifica del parlante resta comunque attiva nelle modalità protette.
- [x] **Memoria Conversazionale Text-Only & Prevenzione Audio Bloat (`brain_gemini.py`):**
  - Risolto il problema di accumulo delle tracce audio WAV binarie nello storico della chat SDK `chats.create`. In precedenza, ogni turno ri-inviava tutti i file audio dei turni precedenti a Gemini, causando errori 429 di quota, picchi di latenza di codifica e confusione semantica su audio vecchi/faint.
  - Conversione automatica della memoria conversazionale in puro testo leggero: `[User Spoke]: "..."` e `TRANSCRIPT: ... REPLY: ...`.
  - Rigenerazione della sessione ad ogni turno con solo lo storico testuale, inviando via rete solo il buffer WAV del turno corrente e liberando istantaneamente la RAM.
  - Finestra scorrevole limitata agli ultimi 8 turni (16 messaggi) per mantenere basso il consumo di token ed evitare allucinazioni su contesti obsoleti.
- [ ] **Switch Dinamico tra Modelli Gemini (Dynamic Model Switching):**
  - Consentire ad A-X-L di muoversi dinamicamente tra i vari modelli Gemini (`gemini-flash-lite`, `gemini-3.5-flash`, `gemini-3.1-pro`...) sia a caldo via Web UI / configurazione, sia tramite comando vocale ("passa a Gemini Pro") o per escalation automatica a seconda della complessità del task richiesto.
- [x] **Integrazione Effetti Sonori Canini Autentici (SFX Pre-Roll in `tts_engine.py`):**
  - **Analisi Acustica e Sintesi Ibrida Open Source:** 
    - Abbaio: Analisi spettrale e temporale dei sample Freesound (`small-dog-bark-02` per attacco brillante e yip a ~1560 Hz + `dog-bark-9` per risonanza toracica da cane adulto a ~590 Hz). Sintetizzato blend armonico a 16kHz 16-bit mono (`bark.wav` a doppia battuta cadenzata a 320ms, unendo il timbro giovane ed energico a quello profondo del cane da guardia).
    - Ringhio/Minaccia: Elaborazione del growl gutturale goblin (`442816__qubodup__goblin-scream.wav`), ricampionato a 16kHz mono, normalizzato a -0.5 dBFS e rifinito con fade-in/fade-out naturale (`growl.wav`, 1.20s), perfetto per il ringhio robotico/alieno di A-X-L contro gli estranei.
    - Respiro Chill / Ansito Lingua Fuori (`pant.wav`): Estratti i primi 2.20 secondi del sample `574183__bloodybill__doc-the-aussie-growls-dog.wav` (Aussie Dog), isolando 2 cicli completi di respirazione/ansito rilassato con la lingua fuori prima dell'inizio del ringhio aggressivo. Convertito da 24-bit 48kHz stereo a 16kHz 16-bit mono con fade naturale, agganciato a `*whirr*`, `*pant*`, `*ansima*`, `*respiro*`.
    - [ ] palette acustica di singolo bark per *bark * e doppio bark per *bark bark *
  - **Zero-Latency In-Memory SFX Pre-Roll:** Pre-caricamento all'avvio in RAM (`self.sfx_cache`) di `bark.wav`, `growl.wav` e `pant.wav` senza I/O su disco.
  - **Parsing Regex, Gestione Tag Composti (*GRRR-WOOF!*) & Sequenziamento In-Line:**
    - Priorità al ringhio minaccioso: tag ibridi o composti come `*GRRR-WOOF!*`, `*GRR-WOOF*`, `grrwoof` mappano rigorosamente a `growl` (`growl.wav`), escludendo `bark` (che è un abbaio amichevole/di allerta e non cattivo).
    - Riproduzione In-Line nel parlato: supporto per tag canini posizionati in mezzo alla frase (es. *"Vuoi sentire un ringhio? Subito, capo. \*GRRR-WOOF!\* Meglio di un dobermann..."*). Il testo viene suddiviso in segmenti sintetizzati concorrentemente con `asyncio.gather`, intercalando il verso e gap di 100ms nell'esatta sequenza temporale della conversazione.
  - **Palette Acustica Rigida nel System Prompt (`brain_gemini.py`):**
    - Vincolo esplicito e tassativo nel Cognitive Core: A-X-L è istruito a generare **solo ed esclusivamente i 3 tag canonici** supportati dall'hardware (`*bark*`, `*growl*`, `*pant*`), con divieto assoluto di allucinare o inventare descrizioni di scena fantasiose (es. `*sbuffo idraulico*`, `*fischio acuto*`, `*luce rossa lampeggiante*`).
    - Filtro difensivo di fallback in `tts_engine.py`: intercetta e sopprime qualsiasi eventuale didascalia spuria residua per preservare la pulizia della sintesi vocale.
  - **Sequenziamento e GAP Naturale:** Inserimento di 100ms di silenzio digitale prima e dopo i campioni canini. Nessun ritardo percepito: il visore Kiosk riceve lo stream PCM via WebSocket e riproduce istantaneamente audio e voce.
- [ ] **Strada 3: Sound Design Ibrido 'A-X-L Cyber-Canine' (Layering Meccatronico Stile Film):**
  - Layering avanzato con SFX meccatronici (sibilo attuatori idraulici/pneumatici, ronzio di condensatori, servomotori di puntamento e clank metallico) sovrapposti o alternati ai versi canini organici, per replicare la fedeltà timbrica e sci-fi del cane robotico da guerra del film cinematografico A.X.L.
- [ ] **Antigravity**: Spiega o correggi il codice che ho scritto. correzione (esercizi degli esami, altri progetti...)
- [ ] **Modalità "Sentinella" (Sentry Mode) e "Sentinella Segreta" (Stealth Sentry):**
  - **Funzionamento Base (Sorveglianza Periodica Locale a Basso Consumo):**
    - Feature on-demand attivabile manualmente (es. quando si esce di casa per la giornata).
    - Il Galaxy A33 scatta uno snapshot fotografico periodico (ogni ~15 secondi) tramite CameraX/WebView e lo invia al Master ThinkPad.
    - Elaborazione ultraleggera locale: confronto tra frame consecutivi (frame differencing dinamico) o rilevamento sagoma umana (YOLOv8-nano / MobileNet ONNX) per evitare falsi allarmi causati dal naturale cambio di luce del sole durante il giorno.
    - Se viene rilevata una presenza: salva lo scatto con timestamp (`captures/guard_YYYYMMDD_HHMMSS.jpg`) e registra l'evento in `guard_events.json`.
    - Canali di avviso: notifica immediata opzionale con foto su bot Telegram privato, oppure resoconto vocale locale al rientro su richiesta ("AXL, novità oggi?").
  - **Modalità Sentinella Standard (Palese / Deterrente):**
    - Il Visore Web/Kiosk mostra chiaramente a schermo lo stato `"SENTINELLA ATTIVA"` (effetto deterrente visivo).
    - Può essere disattivata via voce o tramite apposito toggle su UI.
  - **Modalità "Sentinella Segreta" (Stealth / Infiltrata - Non si fa beccare):**
    - Pensata per monitorare senza far insospettire chi entra nella stanza.
    - **Camuffamento UI:** Sullo schermo del Visore NON compare alcuna scritta o indicatore di sentinella/sorveglianza; la UI mostra semplicemente lo stato di **MIC MUTO** (icona microfono disattivato), simulando che A-X-L sia solo a riposo/disattivato.
    - **Zero Feedback Sonoro:** Nessun beep, abbaio o suono di scatto; silenzio radio totale.
    - **Disattivazione Protetta in Due Passaggi:** Poiché a schermo non c'è traccia della modalità segreta, per disattivarla l'Owner deve prima toccare manualmente il bottone "MIC" per riattivare l'ascolto, e poi pronunciare il comando vocale di disattivazione (confermato dalla verifica biometrica vocale dell'Owner).
