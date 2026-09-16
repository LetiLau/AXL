# AXL Core Native - Setup Guide (Binari & Dipendenze)

Questo file traccia i comandi manuali necessari per ripristinare le dipendenze esterne e i binari non tracciati da Git (`.gitignore`).

## 1. Dipendenze Android (TFLite ARM64 JNI)
Eseguire dalla root del progetto per ottenere libtensorflowlite_jni.so:

```bash
curl -f -L -o tflite_tmp.aar "[https://repo1.maven.org/maven2/org/tensorflow/tensorflow-lite/2.16.1/tensorflow-lite-2.16.1.aar](https://repo1.maven.org/maven2/org/tensorflow/tensorflow-lite/2.16.1/tensorflow-lite-2.16.1.aar)"
unzip -j tflite_tmp.aar "jni/arm64-v8a/libtensorflowlite_jni.so" -d AXL/master/core_native/third_party/tflite/lib/android/arm64-v8a/
rm tflite_tmp.aar
```

## 2. mancavano i flatbuffer
``` bash
# 1. Posizionati nella directory che contiene AXL
cd ~

# 2. Scarica il sorgente di FlatBuffers (v23.5.26)
curl -f -L -o fb_tmp.zip "https://github.com/google/flatbuffers/archive/refs/tags/v23.5.26.zip"

# 3. Estrai l'archivio nell'area temporanea di sistema
unzip -q fb_tmp.zip "flatbuffers-23.5.26/include/flatbuffers/*" -d /tmp/fb_extract

# 4. Sposta esclusivamente la cartella target nella nostra alberatura include/
mv /tmp/fb_extract/flatbuffers-23.5.26/include/flatbuffers AXL/master/core_native/third_party/tflite/include/

# 5. Esecuzione protocollo di pulizia
rm -rf /tmp/fb_extract fb_tmp.zip
```