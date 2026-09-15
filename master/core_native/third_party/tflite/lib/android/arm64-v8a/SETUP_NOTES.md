# AXL Core Native - Setup Guide (Binari & Dipendenze)

Questo file traccia i comandi manuali necessari per ripristinare le dipendenze esterne e i binari non tracciati da Git (`.gitignore`).

## 1. Dipendenze Android (TFLite ARM64 JNI)
Eseguire dalla root del progetto per ottenere libtensorflowlite_jni.so:

```bash
curl -f -L -o tflite_tmp.aar "[https://repo1.maven.org/maven2/org/tensorflow/tensorflow-lite/2.16.1/tensorflow-lite-2.16.1.aar](https://repo1.maven.org/maven2/org/tensorflow/tensorflow-lite/2.16.1/tensorflow-lite-2.16.1.aar)"
unzip -j tflite_tmp.aar "jni/arm64-v8a/libtensorflowlite_jni.so" -d AXL/master/core_native/third_party/tflite/lib/android/arm64-v8a/
rm tflite_tmp.aar