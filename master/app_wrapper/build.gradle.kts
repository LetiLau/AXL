android {
    namespace = "com.axl.core"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.axl.core"
        minSdk = 28 // Requisito minimo per operazioni audio a bassa latenza (Android 9)
        targetSdk = 34
        
        externalNativeBuild {
            cmake {
                // Passiamo il flag ad Android per compilare il blocco if(ANDROID)
                arguments += "-DANDROID=TRUE"
                // Ottimizzazione C++ per CPU ARM a 64 bit (A33)
                abiFilters += "arm64-v8a"
            }
        }
    }

    externalNativeBuild {
        cmake {
            // Puntiamo al CMakeLists che hai già scritto in Fedora
            path = file("../../../core_native/CMakeLists.txt")
            version = "3.22.1"
        }
    }
}