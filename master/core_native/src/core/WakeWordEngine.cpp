#include "axl/WakeWordEngine.hpp"
#include <cmath>
#include <numeric>
#include <iostream>
#include <stdexcept>
#include <algorithm>

// ========================================================================
// MOCK TYPES FOR TFLITE
// ========================================================================
// Since we are mocking the neural engine and not linking the real TFLite headers,
// we must define these dummy classes so std::unique_ptr has a complete type to destroy.
namespace tflite {
    class FlatBufferModel {};
    class Interpreter {};
}

namespace axl {

// Fix: Initializer list now strictly matches the declaration order in the Header
WakeWordEngine::WakeWordEngine(const std::string& model_path, float speaker_threshold)
    : model_(nullptr), interpreter_(nullptr), speaker_threshold_(speaker_threshold) {
    
    std::cout << "[AXL-NEURAL] Initializing WakeWord Engine (MOCK MODE).\n";
    std::cout << "[AXL-NEURAL] Target model: " << model_path << "\n";
    std::cout << "[AXL-NEURAL] Verification threshold set to: " << speaker_threshold_ << "\n";
}

WakeWordEngine::~WakeWordEngine() {
    // Unique pointers will now safely destroy the dummy TFLite objects.
}

void WakeWordEngine::setOwnerEmbedding(const std::vector<float>& reference_embedding) {
    owner_embedding_ = reference_embedding;
    std::cout << "[AXL-NEURAL] Owner embedding updated. Dimensionality: " << owner_embedding_.size() << "\n";
}

float WakeWordEngine::calculateCosineDistance(const float* out_embedding, std::size_t size) const {
    if (owner_embedding_.empty() || owner_embedding_.size() != size) {
        return 1.0f; 
    }

    float dot_product = 0.0f;
    float norm_a = 0.0f;
    float norm_b = 0.0f;

    for (std::size_t i = 0; i < size; ++i) {
        dot_product += out_embedding[i] * owner_embedding_[i];
        norm_a += out_embedding[i] * out_embedding[i];
        norm_b += owner_embedding_[i] * owner_embedding_[i];
    }

    if (norm_a == 0.0f || norm_b == 0.0f) {
        return 1.0f; 
    }

    float cosine_similarity = dot_product / (std::sqrt(norm_a) * std::sqrt(norm_b));
    cosine_similarity = std::max(-1.0f, std::min(1.0f, cosine_similarity));
    
    return 1.0f - cosine_similarity;
}

InferenceResult WakeWordEngine::process(const std::vector<float>& mfcc_tensor) {
    if (mfcc_tensor.empty()) {
        throw std::invalid_argument("[AXL-NEURAL] Empty MFCC tensor provided.");
    }

    InferenceResult result;
    result.trigger_confidence = 0.95f;
    result.is_wake_word_detected = (result.trigger_confidence > 0.80f);

    constexpr std::size_t EMBEDDING_SIZE = 128;
    std::vector<float> mock_output_embedding(EMBEDDING_SIZE, 0.1f);
    
    if (!mfcc_tensor.empty()) {
        mock_output_embedding[0] = mfcc_tensor[0];
    }

    if (owner_embedding_.empty()) {
        result.speaker_match_distance = 1.0f;
        result.is_authorized_user = false;
    } else {
        result.speaker_match_distance = calculateCosineDistance(mock_output_embedding.data(), EMBEDDING_SIZE);
        result.is_authorized_user = (result.speaker_match_distance <= speaker_threshold_);
    }

    return result;
}

}//namespace axl