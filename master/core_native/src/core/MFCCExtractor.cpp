#include "axl/MFCCExtractor.hpp"
#include "kiss_fftr.h"
//#include <numbers> //math constant --> not used cuz made gradle crash
#include <cmath>
#include <stdexcept>
#include <algorithm>

//defining PI
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

namespace axl {

    //std::numbers::pi_v<float>

// --- Math Helpers for Mel Scale ---
inline float hzToMel(float hz) {
    return 2595.0f * std::log10(1.0f + hz / 700.0f);
}

inline float melToHz(float mel) {
    return 700.0f * (std::pow(10.0f, mel / 2595.0f) - 1.0f);
}

// --- Constructor & Destructor ---

MFCCExtractor::MFCCExtractor(const MFCCConfig& config) : config_(config) {
    if (config_.frame_size_samples == 0 || config_.num_mel_bins == 0 || config_.num_ceps == 0) {
        throw std::invalid_argument("[AXL-DSP] Invalid MFCC configuration parameters.");
    }

    // 1. Allocate KissFFT state (Real to Complex)
    fft_cfg_ = kiss_fftr_alloc(config_.frame_size_samples, 0, nullptr, nullptr);
    if (!fft_cfg_) {
        throw std::runtime_error("[AXL-DSP] Failed to allocate KissFFT state.");
    }

    // 2. Pre-allocate all working memory to guarantee zero runtime allocation
    frame_buffer_.resize(config_.frame_size_samples, 0.0f);
    
    // KissFFT outputs (N/2 + 1) complex numbers. We use a float array sized 2x to hold Re/Im pairs.
    std::size_t num_fft_bins = config_.frame_size_samples / 2 + 1;
    complex_fft_out_.resize(num_fft_bins * 2, 0.0f);
    power_spectrum_.resize(num_fft_bins, 0.0f);

    // 3. Pre-compute static lookup tables
    initializeHammingWindow();
    initializeMelFilterbank();
    initializeDCTMatrix();
}

MFCCExtractor::~MFCCExtractor() {
    if (fft_cfg_) {
        kiss_fft_free(fft_cfg_);
        fft_cfg_ = nullptr;
    }
}

// --- Initialization Stages ---

void MFCCExtractor::initializeHammingWindow() {
    hamming_window_.resize(config_.frame_size_samples);
    for (std::size_t i = 0; i < config_.frame_size_samples; ++i) {
        hamming_window_[i] = 0.54f - 0.46f * std::cos((2.0f * M_PI * i) / (config_.frame_size_samples - 1));
    }
}

void MFCCExtractor::initializeMelFilterbank() {
    std::size_t num_fft_bins = config_.frame_size_samples / 2 + 1;
    mel_filterbank_.resize(config_.num_mel_bins, std::vector<float>(num_fft_bins, 0.0f));

    float min_mel = hzToMel(0.0f);
    float max_mel = hzToMel(static_cast<float>(config_.sample_rate) / 2.0f); // Nyquist
    float mel_step = (max_mel - min_mel) / (config_.num_mel_bins + 1);

    std::vector<float> filter_edges_hz(config_.num_mel_bins + 2);
    for (std::size_t i = 0; i < filter_edges_hz.size(); ++i) {
        filter_edges_hz[i] = melToHz(min_mel + i * mel_step);
    }

    // Map Hz to FFT bin indices
    std::vector<std::size_t> bin_indices(filter_edges_hz.size());
    for (std::size_t i = 0; i < filter_edges_hz.size(); ++i) {
        bin_indices[i] = static_cast<std::size_t>(
            std::floor((config_.frame_size_samples + 1) * filter_edges_hz[i] / config_.sample_rate)
        );
    }

    // Construct triangular filters
    for (std::size_t m = 1; m <= config_.num_mel_bins; ++m) {
        std::size_t left_bin   = bin_indices[m - 1];
        std::size_t center_bin = bin_indices[m];
        std::size_t right_bin  = bin_indices[m + 1];

        for (std::size_t k = left_bin; k < center_bin; ++k) {
            mel_filterbank_[m - 1][k] = static_cast<float>(k - left_bin) / (center_bin - left_bin);
        }
        for (std::size_t k = center_bin; k < right_bin; ++k) {
            mel_filterbank_[m - 1][k] = static_cast<float>(right_bin - k) / (right_bin - center_bin);
        }
    }
}

void MFCCExtractor::initializeDCTMatrix() {
    dct_matrix_.resize(config_.num_ceps, std::vector<float>(config_.num_mel_bins, 0.0f));
    float normalizer = std::sqrt(2.0f / config_.num_mel_bins);
    
    for (std::size_t i = 0; i < config_.num_ceps; ++i) {
        for (std::size_t j = 0; j < config_.num_mel_bins; ++j) {
            dct_matrix_[i][j] = normalizer * std::cos(M_PI * i * (j + 0.5f) / config_.num_mel_bins);
        }
    }
}

// --- DSP Runtime ---

void MFCCExtractor::applyPreEmphasisAndWindow(const float* input_frame) {
    // Start from 1 because pre-emphasis needs (x[t] - alpha * x[t-1])
    frame_buffer_[0] = input_frame[0] * hamming_window_[0];
    
    for (std::size_t i = 1; i < config_.frame_size_samples; ++i) {
        float emphasized = input_frame[i] - config_.pre_emphasis_coeff * input_frame[i - 1];
        frame_buffer_[i] = emphasized * hamming_window_[i];
    }
}

std::size_t MFCCExtractor::compute(const float* audio_window, std::size_t num_samples, std::vector<float>& out_features) {
    if (num_samples < config_.frame_size_samples) {
        return 0; // Not enough data for a single frame
    }

    std::size_t num_frames = 1 + (num_samples - config_.frame_size_samples) / config_.hop_size_samples;
    out_features.assign(num_frames * config_.num_ceps, 0.0f); // Flattened 2D array output

    std::vector<float> mel_energies(config_.num_mel_bins, 0.0f);

    for (std::size_t f = 0; f < num_frames; ++f) {
        const float* current_frame_ptr = audio_window + (f * config_.hop_size_samples);

        // 1. Time-domain shaM_PIng
        applyPreEmphasisAndWindow(current_frame_ptr);

        // 2. Fast Fourier Transform
        kiss_fftr(fft_cfg_, frame_buffer_.data(), reinterpret_cast<kiss_fft_cpx*>(complex_fft_out_.data()));

        // 3. Power Spectrum calculation (Magnitude squared)
        std::size_t num_fft_bins = config_.frame_size_samples / 2 + 1;
        for (std::size_t k = 0; k < num_fft_bins; ++k) {
            float real = complex_fft_out_[2 * k];
            float imag = complex_fft_out_[2 * k + 1];
            power_spectrum_[k] = (real * real + imag * imag) / config_.frame_size_samples;
        }

        // 4. Mel Filterbank application & Logarithm
        for (std::size_t m = 0; m < config_.num_mel_bins; ++m) {
            float energy = 0.0f;
            for (std::size_t k = 0; k < num_fft_bins; ++k) {
                energy += power_spectrum_[k] * mel_filterbank_[m][k];
            }
            // Add epsilon to prevent log(0)
            mel_energies[m] = std::log(std::max(energy, 1e-10f));
        }

        // 5. Discrete Cosine Transform (DCT-II) for feature decorrelation
        std::size_t out_offset = f * config_.num_ceps;
        for (std::size_t i = 0; i < config_.num_ceps; ++i) {
            float sum = 0.0f;
            for (std::size_t j = 0; j < config_.num_mel_bins; ++j) {
                sum += mel_energies[j] * dct_matrix_[i][j];
            }
            out_features[out_offset + i] = sum;
        }
    }

    return num_frames;
}

}//namespace axl