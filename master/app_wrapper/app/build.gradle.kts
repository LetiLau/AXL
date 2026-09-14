plugins {
    id("com.android.application") version "8.1.0"
    id("org.jetbrains.kotlin.android") version "1.9.0"
}

android {
    namespace = "com.axl.core"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.axl.core"
        minSdk = 28
        targetSdk = 34
        
        externalNativeBuild {
            cmake {
                arguments += "-DANDROID=TRUE"
                // Supporto duale: Galaxy A33 (ARM) ed Emulatore Fedora (x86_64)
                abiFilters += listOf("arm64-v8a", "x86_64")
            }
        }
    }

    // A-X-L FIX: AGP 8.1.0 richiede strettamente Java 17
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    externalNativeBuild {
        cmake {
            // A-X-L FIX: Path corretto (app/ -> app_wrapper/ -> master/core_native)
            path = file("../../core_native/CMakeLists.txt")
            version = "3.22.1"
        }
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