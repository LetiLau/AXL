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
    
    //std::cout << "[AXL-CORE] Neural pipeline active and listening.\n";
    LOGI("Neural pipeline active and listening.");
}

void AxlCore::shutdown() {
    if (is_initialized_) {
        //std::cout << "[AXL-CORE] Initiating subsystem shutdown...\n";
        LOGI("[AXL-CORE] Initiating subsystem shutdown...\n");
        dispatcher_->stop();
        is_initialized_ = false;
    }
}

void AxlCore::pushAudioChunk(const float* pcm_data, std::size_t size) {
    if (!is_initialized_) return;
    dispatcher_->enqueue(Event(AudioBufferEvent{pcm_data, size}));
}

void AxlCore::enqueueCommand(uint32_t action_id, const std::string& payload) {
    if (!is_initialized_) return;
    dispatcher_->enqueue(Event(CommandEvent{action_id, payload}));
}

void AxlCore::handleCoreEvents(const Event& event) {
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
                    
                    // std::cout << "--- [INFERENCE RESULT] ---\n";
                    // std::cout << "Trigger: " << (result.is_wake_word_detected ? "DETECTED" : "NEGATIVE") 
                    //           << " | Conf: " << result.trigger_confidence << "\n";
                    // std::cout << "Speaker Distance: " << result.speaker_match_distance << "\n";
                    // std::cout << "Identity: " << (result.is_authorized_user ? "ROOT_USER" : "UNKNOWN_ENTITY") << "\n";
                    // std::cout << "--------------------------\n";

                    // LOGI("--- [INFERENCE RESULT] ---\n");
                    // LOGI("Trigger: ", (result.is_wake_word_detected ? "DETECTED" : "NEGATIVE"), " | Conf: ", result.trigger_confidence, "\n");
                    // LOGI("Speaker Distance: ", result.speaker_match_distance, "\n");
                    // LOGI("Identity: ", (result.is_authorized_user ? "ROOT_USER" : "UNKNOWN_ENTITY"), "\n");
                    // LOGI("--------------------------\n");

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

}//namespace axl