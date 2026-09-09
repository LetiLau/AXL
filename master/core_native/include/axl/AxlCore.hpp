#pragma once

#include <string>
#include <memory>
#include <vector>
#include "axl/EventDispatcher.hpp"
#include "axl/RingBuffer.hpp"
#include "axl/MFCCExtractor.hpp"
#include "axl/WakeWordEngine.hpp"

namespace axl {

    /**
     * @brief Main orchestrator for the A-X-L Native Core.
     * Implements the Façade and Singleton patterns to provide a unified, 
     * clean API for the JNI layer (Android) or the local test harness (Fedora).
     */
    class AxlCore {
    public:
        // Meyer's Singleton access point
        static AxlCore& getInstance() {
            static AxlCore instance;
            return instance;
        }

        // Delete copy/move semantics to enforce Singleton property
        AxlCore(const AxlCore&) = delete;
        AxlCore& operator=(const AxlCore&) = delete;
        AxlCore(AxlCore&&) = delete;
        AxlCore& operator=(AxlCore&&) = delete;

        /**
         * @brief Initializes the core subsystems. Must be called once at boot.
         * @param model_path Path to the TFLite wake-word model.
         */
        void init(const std::string& model_path);

        /**
         * @brief Shuts down the background threads and cleans up resources.
         */
        void shutdown();

        /**
         * @brief Ingests raw PCM audio from the hardware microphone.
         * Non-blocking. Quickly copies data to the RingBuffer and returns.
         */
        void pushAudioChunk(const float* pcm_data, std::size_t size);

        /**
         * @brief Pushes a command (e.g., from WebUI or Android Intents) to the Event Bus.
         */
        void enqueueCommand(uint32_t action_id, const std::string& payload);

    private:
        // Private constructor for Singleton
        AxlCore();
        ~AxlCore();

        // Internal subsystem references
        std::unique_ptr<EventDispatcher> dispatcher_;
        std::unique_ptr<RingBuffer> audio_buffer_;
        std::unique_ptr<MFCCExtractor> mfcc_extractor_;
        std::unique_ptr<WakeWordEngine> neural_engine_;

        // Configuration
        MFCCConfig mfcc_cfg_;
        bool is_initialized_;

        /**
         * @brief Internal event handler attached to the Dispatcher.
         * Routes DSP requests and neural inference.
         */
        void handleCoreEvents(const Event& event);
    };

}//namespace axl