# A-X-L Terminal Admin Commands (Server Console CLI)

Il Master Server di A-X-L include una **Console Interattiva REPL (in stile Minecraft Server)** che gira direttamente nel terminale principale (`python src/main.py`).

Chi digita in questa console ha accesso fisico o via SSH alla macchina Master (ThinkPad), pertanto opera con **pieni privilegi di Amministratore (Admin / Root)**:
*   I comandi della console **non richiedono autenticazione biometrica vocale**.
*   Bypassa le restrizioni delle modalità `restrict` o `paranoid`.
*   Supporta comandi e sottocomandi bilingui (**Inglese** e **Italiano**).
*   Se digiti testo senza il prefisso `/`, viene automaticamente inoltrato come domanda ad AXL (`/ask <testo>`).

---

## Tabella dei Comandi Disponibili

| Azione | Comandi accettati (Alias EN / IT) | Sintassi ed Esempi |
|---|---|---|
| **Parlare via TTS** | `/speak`, `/say`, `/answer`, `/parla`, `/rispondi` | `/speak <testo>`<br>`/speak --it Ciao io sono A-X-L`<br>`/speak --en Systems online` |
| **Interrogare Gemini** | `/ask`, `/query`, `/q`, `/chiedi`, `/domanda` | `/ask <domanda>`<br>*(oppure digita direttamente la frase senza `/`)* |
| **Gestione Task** | `/tasks`, `/task`, `/compiti`, `/promemoria`, `/todo` | `/tasks add <descrizione>`<br>`/tasks list` *(mostra compiti di oggi)*<br>`/tasks list --all` *(tutti i compiti)*<br>`/tasks done <id>`<br>`/tasks remove <id>`<br>`/tasks clear` |
| **Calendario** | `/calendar`, `/cal`, `/calendario` | `/calendar add <titolo> [data/ora]`<br>`/calendar list` *(mostra eventi di oggi)*<br>`/calendar list --all` *(tutti gli eventi)*<br>`/calendar remove <id>` |
| **Modalità Sicurezza** | `/mode`, `/modalita`, `/security`, `/sicurezza` | `/mode friendly` (o `amichevole`)<br>`/mode restrict` (o `restrittiva`)<br>`/mode paranoid` (o `paranoica`) |
| **Muto / Ascolto** | `/mute`, `/muto` / `/unmute`, `/smuta` | `/mute` *(disattiva microfoni)*<br>`/unmute` *(riattiva microfoni)* |
| **Telemetria Visore** | `/telem`, `/telemetry`, `/telemetria` | `/telem server`<br>`/telem slave`<br>`/telem kiosk`<br>`/telem off` |
| **Client Audio** | `/audio` | `/audio status` *(mostra sorgente PCM attiva)*<br>`/audio kick` *(disconnette client audio)* |
| **Stato Sistema** | `/status`, `/info`, `/stato` | `/status` *(ispezione runtime e nodi connessi)* |
| **Stop Voce** | `/stop`, `/halt`, `/zitto`, `/taci` | `/stop` *(interrompe l'altoparlante all'istante)* |
| **Pulisci Schermo** | `/clear`, `/cls` | `/clear` |
| **Manuale Help** | `/help`, `/h`, `/?`, `/aiuto` | `/help` |

---

## Dettaglio Funzionamento

### 1. Voce e Rilevamento della Lingua (`/speak`, `/say`, `/answer`)
Il motore di sintesi vocale neurale ([`tts_engine.py`](master_server/src/tts_engine.py)) analizza in tempo reale le parole della frase per selezionare automaticamente la voce migliore:
*   **Italiano automatico**: se il testo contiene parole o token italiani, attiva la voce naturale **Diego** (`it-IT-DiegoNeural` o fallback locale Miro).
    ```bash
    /speak Ciao A-X-L, prova dell'altoparlante.
    ```
*   **Inglese automatico**: se il testo contiene token inglesi, attiva la voce **Bryce / Christopher** (in stile Jarvis).
    ```bash
    /speak Welcome back, all core systems are operational.
    ```
*   **Override manuale**: puoi forzare una specifica voce usando il flag `--it` o `--en`:
    ```bash
    /speak --en Ciao mondo
    /speak --it Hello world
    ```

---

### 2. Gestione Task (`/tasks`)
I compiti sono salvati in modo permanente su `master_server/data/tasks.json`:
*   Aggiungere un compito:
    ```bash
    /tasks add Comprare il pane
    /tasks add Studiare per l'esame di reti
    ```
*   Elencare i compiti:
    *   `/tasks list` mostra **solo i compiti di oggi**.
    *   `/tasks list --all` mostra **tutti i compiti attivi**.
*   Completare o eliminare un compito:
    ```bash
    /tasks done 1
    /tasks remove 2
    /tasks clear   # Rimuove tutti i compiti completati
    ```
*(Funziona anche a voce con Gemini tramite i tool `add_task`, `list_tasks`, `complete_task`).*

---

### 3. Calendario & Google Calendar (`/calendar`)
Gli eventi sono registrati su `master_server/data/calendar.json` e inoltrati al telefono Galaxy A33:
*   Aggiungere un evento:
    ```bash
    /calendar add Dentista 2026-11-12 15:30
    /calendar add Allenamento 18:00
    ```
*   Quando viene aggiunto un evento:
    1. Viene memorizzato nel database locale.
    2. Viene inviato un pacchetto WebSocket `INTENT` all'app Kiosk dell'A33.
    3. L'A33 sfrutta `CalendarContract.Events` nativo di Android per inserirlo nell'app Samsung/Google Calendar, che lo sincronizza sul cloud.
*   Elencare gli eventi:
    *   `/calendar list` mostra **solo gli eventi di oggi**.
    *   `/calendar list --all` mostra **tutti gli eventi in programma**.
*   Eliminare un evento:
    ```bash
    /calendar remove 1
    ```

---

### 4. Sincronizzazione Cloud e API Esterne

1.  **Google Calendar API (Cloud)**:
    *   Attualmente la scrittura su Google Calendar avviene tramite l'app Android Kiosk (che sincronizza Samsung Calendar con l'account Google).
    *   Per la lettura diretta dal cloud sul Master Server (senza passare dall'app o dal DB locale), è possibile integrare le credenziali OAuth di Google Cloud (`credentials.json`), pianificate nelle note post-sviluppo.
2.  **Promemoria Samsung & Microsoft To Do**:
    *   L'app nativa Promemoria di Samsung si sincronizza con Microsoft To Do tramite Samsung Cloud.
    *   Nelle note post-sviluppo è predisposta l'integrazione delle API Microsoft Graph / To Do per leggere e aggiornare direttamente i promemoria del telefono.
