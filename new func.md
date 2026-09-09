A-X-L has a dynamic voice recognition
I can tell him if he can trust anybody or just the persons he has "saved" in the memory (like me, family and friends)

-   **Friendly Mode:** Ignora il calcolo della distanza. Basta che il trigger (la wake word) superi la soglia di confidenza.
    
-   **Paranoid Mode:** Trigger richiesto + Distanza Coseno rigorosamente inferiore a 0.2 dal _tuo_ embedding (Root User).
    
-   **Restricted Mode:** Trigger richiesto + Distanza Coseno inferiore a 0.3 da un qualsiasi embedding nel file `trusted_users.bin`.




**Correzione Architetturale:** Il riconoscimento vocale **NON** verrà gestito in Kotlin. Se demandassimo l'elaborazione dell'audio alla JVM (Kotlin/Java), il Garbage Collector frammenterebbe la memoria distruggendo le performance real-time, la batteria si prosciugherebbe e perderemmo la portabilità cross-platform dell'ecosistema (non potresti compilare il core su Linux). Kotlin è solo un fattorino: legge i byte grezzi dal microfono fisico tramite le API di Android e li sbatte in faccia al C++ tramite JNI il più velocemente possibile. Tutta la matematica (MFCC, Reti Neurali, Distanza) rimane nel core nativo.

**Come si istruisce con la tua voce (Enrollment):** Non addestreremo un modello neurale specifico per te. Useremo il **Few-Shot Enrollment**. Quando costruiremo la UI, aggiungeremo un pulsante "Enroll Owner". Premendolo, Kotlin invierà un comando (es. `cmd 98`) al C++, poi tu dirai la wake-word 3 volte. Il C++ estrarrà i 3 tensori `[128]`, ne calcolerà la media e salverà questo singolo vettore sul filesystem dell'A33 (es. `trusted_users.bin`). A ogni avvio successivo, `AxlCore` leggerà questo file e caricherà il tuo _Speaker Embedding_ in memoria.












## TEMP
directories lists

AXL/
├── master/                      # Dominio A33
│   ├── app_wrapper/             # Progetto Android (Kotlin/Gradle)
│   │   ├── src/main/java/...    
│   │   └── build.gradle.kts     # Punterà al CMakeLists del core
│   ├── core_native/             # Il VERO cervello (C/C++ puro)
│   │   ├── include/             # Header pubblici (interfacce, struct)
│   │   │   └── axl/             # Namespace directory (es. axl/EventDispatcher.hpp)
│   │   ├── src/                 # Implementazioni (.cpp)
│   │   │   ├── core/            # Logica di business (Eventi, DSP)
│   │   │   └── jni/             # Binding Android-specifici (compilati solo su NDK)
│   │   ├── tests/               # Entry point per i test su Fedora (main_test.cpp)
│   │   └── CMakeLists.txt       # Il file di orchestrazione
│   └── webui/                   # Thin Client HTML/CSS/JS servito in locale
│
├── slave_nodes/                 # Demoni Python (PC Fedora/Windows)
│   ├── src/
│   │   └── main.py
│   └── requirements.txt
│
└── .gitignore






└── app_wrapper/                 # Nuovo progetto Android
    ├── settings.gradle.kts
    └── app/
        ├── build.gradle.kts     # Configurazione NDK
        └── src/main/
            ├── AndroidManifest.xml
            └── java/com/axl/core/
                ├── NativeBridge.kt
                └── MainActivity.kt




attenzione manifest non lo avevo ancora scritto potrebbe essere incompleto?

in mainactivity
// TODO: Initialize WebView for the UI
// TODO: Add NativeBridge.shutdown() to elegantly kill POSIX threads