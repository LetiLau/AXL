### 1. Puro Python o anche C++?

Il codice che scriverai sarà **100% Python**, ma l'esecuzione a runtime sarà **sostanzialmente C++ nativo**.

Scrivere codice C++ proprietario (come fatto con `MFCCExtractor` o la gestione manuale dei tensori TFLite) sul ThinkPad introduce overhead di sviluppo ingiustificato. Il pattern architetturale corretto per questo nodo è:

* **Control Plane (Logica applicativa, FSM, routing e I/O di rete):** Python asincrono (`asyncio`, `FastAPI`, `websockets`). La CPU i5-4300U non va in saturazione gestendo socket TCP o dispatching di payload JSON; l'overhead dell'interprete è trascurabile (nell'ordine dei microsecondi).
* **Data Plane (DSP, inferenza tensoriale, embedding vocali):** Librerie Python con **binding C/C++ e backend SIMD (AVX2/FMA)** precompilati.

In questo modo ottieni la velocità di sviluppo di Python mantenendo l'inferenza compilata in C++ a zero memory-leak e massime prestazioni vettoriali. Se in futuro vorrai riscrivere un componente in C++ per requisiti embedded rigidi (es. microcontrollori o rientro su Android NDK), basterà isolare quel singolo microservizio ed esporlo su socket Unix/TCP.

---

### 2. Stack Tecnologico e Librerie Pronte

Evita PyTorch o TensorFlow completi: trascinerebbero gigabyte di dipendenze e un consumo di RAM proibitivo. Il runtime di riferimento per tutta l'inferenza locale è **ONNX Runtime (C++ engine)**.

```
                  [ Stream PCM 16kHz da A33 ]
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ PIPELINE PERCETTIVA LOCALE (CPU ThinkPad - AVX2)             │
│                                                             │
│ 1. Silero VAD (ONNX)       ──> Filtra frame silenziosi      │
│ 2. openWakeWord (ONNX)     ──> Rileva keyword "AXL"          │
│ 3. sherpa-onnx (3D-Speaker)──> Cosine Sim (Speaker ID)      │
└──────────────────────────────┬──────────────────────────────┘
                               │ (Se Owner + Comando catturato)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ CORE COGNITIVO & ROUTING                                    │
│                                                             │
│ 4. google-genai (SDK)      ──> Invio audio grezzo a Gemini  │
│ 5. Intent Router           ──> Dispatching su Slave / A33   │
│ 6. Piper TTS (ONNX binary) ──> Sintesi voce locale (PCM)    │
└─────────────────────────────────────────────────────────────┘

```

#### I componenti nel dettaglio:

* **VAD (Voice Activity Detection): `silero-vad` (ONNX Runtime)**
* *Perché:* Pesa ~2 MB, consuma meno dell'1% di CPU su Haswell ed elabora chunk da 30 ms in sub-millisecondo. Elimina la necessità di analizzare soglie RMS grezze o calcolare energie spettrali manuali.


* **Wake-Word Engine: `openWakeWord**`
* *Perché:* Sviluppato nativamente su ONNX/TFLite. Include feature extraction integrata (niente calcoli manuali di finestre di Hanning o banchi di filtri Mel) e permette di usare modelli pre-addestrati o esportarne di personalizzati senza scrivere una riga di C++.


* **Speaker Verification (Diffidenza): `sherpa-onnx` (con modelli `3D-Speaker` o `WeSpeaker`)**
* *Perché:* È la libreria più efficiente per audio edge. Il core è C++ puro senza dipendenze pesanti, con binding Python leggeri. Carica un modello x-vector/resnet precompilato in ONNX (~15–30 MB), estrae un embedding a 192 o 256 dimensioni e ne calcola la *Cosine Similarity* con la tua impronta registrata in <15 ms su Haswell.


* **TTS Locale (Feedback vocale immediato): `piper-tts**`
* *Perché:* Binario C++ standalone (con libreria/wrapper Python) basato su VITS/ONNX. Su un i5-4300U sintetizza l'audio a velocità 4x-6x Real-Time (circa 200 ms per generare una frase di 2 secondi), con impronta RAM inferiore a 100 MB.


* **Client Cloud: `google-genai**`
* *Perché:* SDK Python ufficiale aggiornato per Gemini 2.0 Flash. Supporta streaming audio diretto verso le API e output strutturato (JSON Schema / Function Calling) per serializzare i comandi da inviare ai nodi Slave.


* **Server Audio/WebSocket: `FastAPI` + `uvicorn` (con loop `uvloop`)**
* *Perché:* Gestione concorrente asincrona non bloccante. Riceve il flusso binario PCM dal socket di rete e notifica lo stato alla Web UI.



