A-X-L has a dynamic voice recognition
I can tell him if he can trust anybody or just the persons he has "saved" in the memory (like me, family and friends)

-   **Friendly Mode:** Ignora il calcolo della distanza. Basta che il trigger (la wake word) superi la soglia di confidenza.
    
-   **Paranoid Mode:** Trigger richiesto + Distanza Coseno rigorosamente inferiore a 0.2 dal _tuo_ embedding (Root User).
    
-   **Restricted Mode:** Trigger richiesto + Distanza Coseno inferiore a 0.3 da un qualsiasi embedding nel file `trusted_users.bin`.




**Correzione Architetturale:** Il riconoscimento vocale **NON** verrà gestito in Kotlin. Se demandassimo l'elaborazione dell'audio alla JVM (Kotlin/Java), il Garbage Collector frammenterebbe la memoria distruggendo le performance real-time, la batteria si prosciugherebbe e perderemmo la portabilità cross-platform dell'ecosistema (non potresti compilare il core su Linux). Kotlin è solo un fattorino: legge i byte grezzi dal microfono fisico tramite le API di Android e li sbatte in faccia al C++ tramite JNI il più velocemente possibile. Tutta la matematica (MFCC, Reti Neurali, Distanza) rimane nel core nativo.

**Come si istruisce con la tua voce (Enrollment):** Non addestreremo un modello neurale specifico per te. Useremo il **Few-Shot Enrollment**. Quando costruiremo la UI, aggiungeremo un pulsante "Enroll Owner". Premendolo, Kotlin invierà un comando (es. `cmd 98`) al C++, poi tu dirai la wake-word 3 volte. Il C++ estrarrà i 3 tensori `[128]`, ne calcolerà la media e salverà questo singolo vettore sul filesystem dell'A33 (es. `trusted_users.bin`). A ogni avvio successivo, `AxlCore` leggerà questo file e caricherà il tuo _Speaker Embedding_ in memoria.


# running su A33
 adb shell am start -n com.axl.core/.MainActivity
# installing on A33
adb install -r -t app/build/outputs/apk/debug/app-debug.apk
# logcat su altro terminale per testing
adb logcat -c && adb logcat -v color | grep -E "AXL_MAIN|AXL_NATIVE|AXL_KOTLIN|AXL_AUDIO|AndroidRuntime|CRASH"





# x
attenzione alla versione XML per UI in kiosk mode



## TEMP
directories lists

AXL/
├── master/                      # Dominio A33
│   ├── app_wrapper/             # Progetto Android (Kotlin/Gradle)
│   │   ├── app/src/main/
│   │   │            ├─ assets   #contiene dummy_model.tflite (che non ho capito che fa)
│   │   │            ├─ java/come/axl/core #contiene androidmanifest.xml
│   │   │                              ├─ audio #contiene audioCaptureManager.kt, mainactivity.kt, nativebridge.kt
│   │   │                              ├─ utils #contiene asset helper
│   │   └── build.gradle.kts     # Punterà al CMakeLists del core (dentro ci sono anche altri vari file gradle es gradlewrapper)
│   ├── core_native/             # Il VERO cervello (C/C++ puro)
│   │   ├── include/             # Header pubblici (interfacce, struct)
│   │   │   └── axl/             # Namespace directory (es. axl/EventDispatcher.hpp)
│   │   ├── src/                 # Implementazioni (.cpp)
│   │   │   ├── core/            # Logica di business (Eventi, DSP) --> file .cpp
│   │   │   └── jni/             # Binding Android-specifici (compilati solo su NDK)
│   │   ├── tests/               # Entry point per i test su Fedora (main_test.cpp)
│   │   └── CMakeLists.txt       # Il file di orchestrazione
│   └── webui/                   # Thin Client HTML/CSS/JS servito in locale
│
├── slave_nodes/                 # Demoni Python (PC Fedora/Windows, vuoto per ora)
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



AXL/master/core_native/
├── third_party/
│   └── tflite/
│       ├── include/            # Header ufficiali (tensorflow/lite/ e flatbuffers/)
│       └── lib/
│           ├── android/
│           │   └── arm64-v8a/
│           │       └── libtensorflowlite_jni.so  # Estratto dall'.aar
│           └── linux/
│               └── libtensorflowlite.so          # Da /usr/lib64/ (Fedora)
├── include/
├── src/
├── tests/
└── CMakeLists.txt




in mainactivity
// TODO: Initialize WebView for the UI
// TODO: Add NativeBridge.shutdown() to elegantly kill POSIX threads (manca?)
// TODO: Avvia routing audio verso socket/API per l'elaborazione del comando ????
// TODO: azioni asincrone di sistema (es. aprire socket, settare timer)





## Reminder: correggere bug pk cmake non trova tensorflowlite
L'errore `Could not find TFLITE_LIB` su Fedora è banale: `find_library` sta cercando il file `.so` nei percorsi standard di sistema (`/usr/lib64`), ma la libreria installata tramite DNF potrebbe chiamarsi `libtensorflow-lite.so` (con il trattino) invece di `libtensorflowlite.so`.

Puoi tranquillamente ignorarlo per ora. Quando torneremo su Linux, modificheremo `CMakeLists.txt` per puntare staticamente a `third_party/tflite/lib/linux/libtensorflowlite.so` esattamente come abbiamo fatto per Android, bypassando completamente il gestore di pacchetti di sistema.





# SISTEMA I BUG DI BUILD PER INIZIARE IL MODULO B DELLA CHAT
https://gemini.google.com/gem/b7e21080aaec/2d6e6faeae184995



#### nota errori log
09-16 12:02:12.438 31210 31251 I AXL_NATIVE: [AXL-NEURAL] Identity REJECTED (Intruder). Distance: 1.0000
09-16 12:02:12.498 32343 32343 D DIAGMON_SDK[605068][oi0yad25xb] : CRASH_LOG_PATH : /data/user/0/com.samsung.android.providers.contacts/exception/diagmon.log
