#pragma once

#include <vector>
#include <string>
#include <cstdint>
#include <memory>
// L'API C garantisce simboli visibili e ABI stabile nella libtensorflowlite_jni.so
#include "tensorflow/lite/c/c_api.h"


// // Forward declaration to hide TFLite headers from the rest of the project
// namespace tflite {
//     class FlatBufferModel;
//     class Interpreter;
// }

// #include "tensorflow/lite/model.h"
// #include "tensorflow/lite/interpreter.h"


namespace axl {

    /**
     * @brief Result payload from the Wake-Word inference engine.
     */
    struct InferenceResult {
        bool is_wake_word_detected;
        float trigger_confidence;     // Range [0.0, 1.0]
        float speaker_match_distance; // Euclidean or Cosine distance from owner's embedding
        bool is_authorized_user;      // True if distance < threshold
    };

    /**
     * @brief Neural engine for Wake-Word spotting and Speaker Verification.
     * Utilizes TensorFlow Lite C++ API for zero-copy tensor inference.
     */
    class WakeWordEngine {
    public:
        /**
         * @brief Constructs the engine and loads the quantized TFLite model.
         * @param model_path Path to the .tflite model file on the host filesystem.
         * @param speaker_threshold Max allowed distance to classify as the owner.
         */
        WakeWordEngine(const std::string& model_path, float speaker_threshold = 0.5f);
        ~WakeWordEngine();

        WakeWordEngine(const WakeWordEngine&) = delete;
        WakeWordEngine& operator=(const WakeWordEngine&) = delete;

        /**
         * @brief Runs inference on the extracted MFCC features.
         * @param mfcc_tensor Flattened 2D tensor [num_frames * num_ceps].
         * @return InferenceResult containing detection and authorization state.
         */
        InferenceResult process(const std::vector<float>& mfcc_tensor);

        /**
         * @brief Updates the owner's reference embedding (e.g., during setup phase).
         * @param reference_embedding The ground-truth voice footprint.
         */
        void setOwnerEmbedding(const std::vector<float>& reference_embedding);

    private:
        // std::unique_ptr<tflite::FlatBufferModel> model_;
        // std::unique_ptr<tflite::Interpreter> interpreter_;
        
        // C API Pointers
        TfLiteModel* model_ = nullptr;
        TfLiteInterpreterOptions* options_ = nullptr;
        TfLiteInterpreter* interpreter_ = nullptr;
        
        std::vector<float> owner_embedding_;
        float speaker_threshold_;

        // Helper to calculate distance between output embedding and owner_embedding_
        float calculateCosineDistance(const float* out_embedding, std::size_t size) const;
    };

}//namespace axl