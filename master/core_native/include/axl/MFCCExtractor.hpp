#pragma once

#include <vector>
#include <cstddef>
#include <cstdint>

// Forward declaration of KissFFT structures to avoid polluting our header
// with C macros from kiss_fft.h
struct kiss_fftr_state;
typedef struct kiss_fftr_state* kiss_fftr_cfg;

namespace axl {

    /**
     * @brief Configuration parameters for the MFCC pipeline.
     * Defaulted to standard Speech Recognition presets (16kHz).
     */
    struct MFCCConfig {
        uint32_t sample_rate = 16000;
        std::size_t frame_size_samples = 400; // 25ms window
        std::size_t hop_size_samples = 160;   // 10ms step
        uint32_t num_mel_bins = 40;
        uint32_t num_ceps = 13;               // Number of output coefficients per frame
        float pre_emphasis_coeff = 0.97f;
    };

    /**
     * @brief High-performance MFCC extractor. 
     * Pre-computes window functions and filterbanks to guarantee zero-allocation 
     * during the compute() phase.
     */
    class MFCCExtractor {
    public:
        explicit MFCCExtractor(const MFCCConfig& config);
        ~MFCCExtractor(); // Required to free KissFFT pre-allocated memory

        // Delete copy semantics to prevent expensive filterbank duplication
        MFCCExtractor(const MFCCExtractor&) = delete;
        MFCCExtractor& operator=(const MFCCExtractor&) = delete;

        /**
         * @brief Computes MFCC features for a given audio window.
         * 
         * @param audio_window Pointer to the raw PCM float data (e.g., from RingBuffer).
         * @param num_samples Total samples in the window.
         * @param out_features Pre-allocated flattened 2D vector [num_frames * num_ceps].
         * @return std::size_t The number of frames successfully processed.
         */
        std::size_t compute(const float* audio_window, std::size_t num_samples, std::vector<float>& out_features);

    private:
        MFCCConfig config_;
        // KissFFT state for Real-to-Complex transforms
        kiss_fftr_cfg fft_cfg_;
        
        // Pre-computed tables
        std::vector<float> hamming_window_;
        std::vector<float> mel_filterbank_; // [num_mel_bins][fft_size/2 + 1]
        
        // Working memory for the active frame to avoid allocation in the loop
        std::vector<float> frame_buffer_;
        std::vector<float> power_spectrum_;


        /*usage of kissFFT*/
        // KissFFT specific output buffer (Complex numbers: Real and Imaginary pairs)
        std::vector<float> complex_fft_out_;
        
        // Pre-computed DCT-II matrix for MFCC compression [num_ceps][num_mel_bins]
        std::vector<std::vector<float>> dct_matrix_;
        
        // Internal initialization stages
        void initializeMelFilterbank();
        void initializeDCTMatrix();
        /*end*/

        
        // Pipeline stages
        void initializeHammingWindow();
        void initializeMelFilterbank();
        void applyPreEmphasisAndWindow(const float* input_frame);
    };

}//namespace axl