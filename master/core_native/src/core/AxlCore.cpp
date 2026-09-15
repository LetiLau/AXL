#include "axl/AxlCore.hpp"
#include <stdexcept>
#include <vector>


#ifdef ANDROID
#include <android/log.h>
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, "AXL_NATIVE", __VA_ARGS__)
#else
#include <cstdio>
#define LOGI(...) do { printf("[AXL_NATIVE] "); printf(__VA_ARGS__); printf("\n"); } while(0)
#endif


namespace axl {

AxlCore::AxlCore() : is_initialized_(false) {
    // 1. Allocate subsystem memory strictly once
    dispatcher_ = std::make_unique<EventDispatcher>();
    audio_buffer_ = std::make_unique<RingBuffer>(32000); // 2 seconds @ 16kHz
    mfcc_extractor_ = std::make_unique<MFCCExtractor>(mfcc_cfg_);
    
    // Neural engine is instantiated with a dummy path initially. 
    // Will be overridden during init() when Android provides the real asset path.
    neural_engine_ = std::make_unique<WakeWordEngine>("dummy_boot.tflite", 0.30f);
}

AxlCore::~AxlCore() {
    shutdown();
}

void AxlCore::init(const std::string& model_path) {
    if (is_initialized_) {
        //std::cout << "[AXL-CORE] Warning: Core already initialized. Ignoring request.\n";
        LOGI("Warning: Core already initialized.");
        return;
    }

    //std::cout << "[AXL-CORE] Booting primary systems...\n";
    LOGI("[AXL-CORE] Booting primary systems...\n");


    LOGI("Booting primary C++ subsystems...");
    LOGI("Target model mapped to: %s", model_path.c_str());
    // Reload the neural engine with the actual model path provided by the OS
    neural_engine_ = std::make_unique<WakeWordEngine>(model_path, 0.30f);

    // Bind the internal event router to the background dispatcher
    // Lambda captures 'this' safely since AxlCore is a Singleton and outlives the thread
    dispatcher_->subscribe([this](const Event& event) {
        this->handleCoreEvents(event);
    });

    dispatcher_->start();
    is_initialized_ = true;

    // Start background inference loop
    keep_inferring_ = true;
    inference_thread_ = std::thread(&AxlCore::inferenceLoop, this);
    
    //std::cout << "[AXL-CORE] Neural pipeline active and listening.\n";
    LOGI("Neural pipeline active and listening.");
}

void AxlCore::shutdown() {
    if (is_initialized_) {
        //std::cout << "[AXL-CORE] Initiating subsystem shutdown...\n";
        LOGI("[AXL-CORE] Initiating subsystem shutdown...\n");
        // Stop inference thread safely
        keep_inferring_ = false;
        inference_cv_.notify_one(); // Wake it up if it's sleeping
        if (inference_thread_.joinable()) {
            inference_thread_.join();
        }

        dispatcher_->stop();
        is_initialized_ = false;
    }
}

void AxlCore::pushAudioChunk(const float* pcm_data, std::size_t size) {
    if (!is_initialized_) return;
    //dispatcher_->enqueue(Event(AudioBufferEvent{pcm_data, size}));
    
    // Scrittura sincrona e thread-safe. Zero event overhead.
    audio_buffer_->push(pcm_data, size);

    // Controlliamo se abbiamo accumulato abbastanza dati per innescare l'inferenza
    {
        std::lock_guard<std::mutex> lock(inference_mutex_);
        new_samples_accumulated_ += size;
    }

    if (new_samples_accumulated_ >= STRIDE_SIZE) {
        inference_cv_.notify_one(); // Sveglia il thread AI
    }
}

void AxlCore::enqueueCommand(uint32_t action_id, const std::string& payload) {
    if (!is_initialized_) return;
    //dispatcher_->enqueue(Event(CommandEvent{action_id, payload}));
    
    // Da espandere. Per ora il comando 99 (Inference Manuale) è obsoleto
    // in quanto la rete neurale gira in background. Lo terremo per debug.
}

void AxlCore::handleCoreEvents(const Event& event) {
    //cmd 98 and 99 are obsolete -->
    // TODO: azioni asincrone di sistema (es. aprire socket, settare timer)

    std::visit([this](auto&& arg) {
        using T = std::decay_t<decltype(arg)>;
        
        if constexpr (std::is_same_v<T, AudioBufferEvent>) {
            // Hot path: Route audio directly to the sliding window
            audio_buffer_->push(arg.raw_data, arg.num_samples);
        } 
        else if constexpr (std::is_same_v<T, CommandEvent>) {
            
            // CMD 98: Enrollment
            if (arg.action_id == 98) {
                //std::cout << "[AXL-CORE] Processing enrollment vector...\n";
                LOGI("[AXL-CORE] Processing enrollment vector...\n");
                std::vector<float> my_voice(128, 0.1f);
                my_voice[0] = -97.8824f;
                neural_engine_->setOwnerEmbedding(my_voice);
            }
            
            // CMD 99: Inference trigger
            else if (arg.action_id == 99) {
                std::vector<float> window(16000);
                try {
                    audio_buffer_->getRecentWindow(window.data(), 16000);
                    
                    std::vector<float> mfcc_features;
                    mfcc_extractor_->compute(window.data(), 16000, mfcc_features);
                    
                    InferenceResult result = neural_engine_->process(mfcc_features);

                    LOGI("--- [INFERENCE RESULT] ---");
                    LOGI("Trigger: %s | Conf: %.2f", 
                         (result.is_wake_word_detected ? "DETECTED" : "NEGATIVE"), 
                         result.trigger_confidence);
                    LOGI("Speaker Distance: %.4f", result.speaker_match_distance);
                    LOGI("Identity: %s", (result.is_authorized_user ? "ROOT_USER" : "UNKNOWN_ENTITY"));
                    LOGI("--------------------------");
                    
                } catch (const std::exception& e) {
                    LOGI("[AXL-CORE] Inference dropped: %s", e.what());
                }
                // } catch (const std::exception& e) {
                //     //std::cout << "[AXL-CORE] Inference dropped: " << e.what() << "\n";
                //     LOGI("[AXL-CORE] Inference dropped: ", e.what(), "\n");

                // }
            }
        }
    }, event.payload);
}

void AxlCore::inferenceLoop() {
    // Pre-allocazione per non scomodare mai il memory allocator nel ciclo vitale (zero leak)
    std::vector<float> window(WINDOW_SIZE, 0.0f);
    std::vector<float> mfcc_features;

    while (keep_inferring_) {
        // --- 1. Sincronizzazione ed Attesa ---
        {
            std::unique_lock<std::mutex> lock(inference_mutex_);
            inference_cv_.wait(lock, [this] {
                return (new_samples_accumulated_ >= STRIDE_SIZE) || !keep_inferring_;
            });

            if (!keep_inferring_) break;
            
            // Consuma lo stride e abbassa il counter
            new_samples_accumulated_ -= STRIDE_SIZE;
        }

        // --- 2. Estrazione dati dal RingBuffer ---
        try {
            audio_buffer_->getRecentWindow(window.data(), WINDOW_SIZE);
        } catch (const std::out_of_range&) {
            // Avviene solo nei primi istanti di vita, quando il RingBuffer non 
            // ha ancora accumulato WINDOW_SIZE (16000) campioni[cite: 7]. Ignoriamo e continuiamo a raccogliere.
            continue; 
        }

        // --- 3. Pipeline Neurale (Zero Allocation) ---
        mfcc_extractor_->compute(window.data(), WINDOW_SIZE, mfcc_features);
        InferenceResult result = neural_engine_->process(mfcc_features);

        // --- 4. Risoluzione e Trigger ---
        if (result.is_wake_word_detected) {
            LOGI("[AXL-NEURAL] WAKE WORD DETECTED! Conf: %.2f", result.trigger_confidence);
            
            if (result.is_authorized_user) {
                 LOGI("[AXL-NEURAL] Identity confirmed: MASTER. Distance: %.4f", result.speaker_match_distance);
                 
                 // 5. Invia segnale all'EventDispatcher per attivare l'UI / Nodi Python
                 // axl::Event trigger_event{axl::EventType::WAKE_WORD_DETECTED, ...};
                 // dispatcher_->enqueue(trigger_event);

                 // Debounce hardware: Evita loop di rilevamento continuo svuotando lo storico[cite: 7]
                 audio_buffer_->reset(); 
                 
                 // Reset the sample tracker safely
                 std::lock_guard<std::mutex> lock(inference_mutex_);
                 new_samples_accumulated_ = 0;
            } else {
                 LOGI("[AXL-NEURAL] Identity REJECTED (Intruder). Distance: %.4f", result.speaker_match_distance);
            }
        }
    }
}

}//namespace axl