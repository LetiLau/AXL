plugins {
    id("com.android.application") version "8.1.0"
    id("org.jetbrains.kotlin.android") version "1.9.0"
}

android {
    namespace = "com.axl.core"
    compileSdk = 34 // O la versione target che stai usando

    defaultConfig {
        applicationId = "com.axl.core"
        minSdk = 28 // Galaxy A33 supporta tranquillamente versioni recenti
        targetSdk = 34

        // 1. Vincolo ABI: Compila C++ SOLO per il target fisico (A33)
        ndk {
            abiFilters.add("arm64-v8a")
        }

        // 2. Parametri diretti a CMake
        externalNativeBuild {
            cmake {
                // Impone lo standard C++20 e abilita le ottimizzazioni di Clang
                cppFlags("-std=c++20", "-O3", "-flto")
                // Usa la libreria standard C++ statica per evitare dipendenze a runtime mancanti
                arguments("-DANDROID_STL=c++_static") 
            }
        }
    }

    // 3. Orchestrazione: Punta al tuo CMakeLists.txt fuori dalla cartella app
    externalNativeBuild {
        cmake {
            // Path relativo da 'app_wrapper/app/' a 'core_native/'
            path("../../core_native/CMakeLists.txt")
            version = "3.22.1" // Assicurati di avere questa versione SDK in Android Studio
        }
    }


    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }


}

dependencies {
    // Core per le classi base e la compatibilità
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("androidx.activity:activity-ktx:1.8.0")
    
    // UI (se usi layout XML standard anziché Compose per il Kiosk mode)
    implementation("com.google.android.material:material:1.11.0")
}