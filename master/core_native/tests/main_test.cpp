#include <iostream>
#include <string>
#include <sstream>
#include <fstream>
#include <vector>
#include <cstdint>
#include <iomanip>
#include <cstring>
#include "axl/EventDispatcher.hpp"
#include "axl/RingBuffer.hpp"
#include "axl/MFCCExtractor.hpp"
#include "axl/WakeWordEngine.hpp"

#pragma pack(push, 1)
struct WavHeader {
    char riff_tag[4];
    uint32_t riff_length;
    char wave_tag[4];
    char fmt_tag[4];
    uint32_t fmt_length;
    uint16_t audio_format;
    uint16_t num_channels;
    uint32_t sample_rate;
    uint32_t byte_rate;
    uint16_t block_align;
    uint16_t bits_per_sample;
    char data_tag[4];
    uint32_t data_length;
};
#pragma pack(pop)

axl::RingBuffer g_audio_buffer(32000); 
std::vector<float> g_mock_audio_memory; 

// Instantiate the MFCC Extractor with default speech config (16kHz, 13 ceps)
axl::MFCCConfig mfcc_cfg;
axl::MFCCExtractor g_mfcc_extractor(mfcc_cfg);

// Instantiate WakeWordEngine in Mock Mode with a threshold of 0.3
axl::WakeWordEngine g_neural_engine("dummy_model.tflite", 0.30f);

void dspSubsystemHandler(const axl::Event& event) {
    std::visit([](auto&& arg) {
        using T = std::decay_t<decltype(arg)>;
        
        if constexpr (std::is_same_v<T, axl::AudioBufferEvent>) {
            g_audio_buffer.push(arg.raw_data, arg.num_samples);
        } 
        else if constexpr (std::is_same_v<T, axl::CommandEvent>) {
            
            // CMD 98: Simulate User Enrollment
            if (arg.action_id == 98) {
                std::cout << "[SYS-MODULE] Enrolling root user...\n";
                // Create a mock 128D embedding of the owner
                std::vector<float> my_voice(128, 0.1f);
                // Make it slightly unique
                my_voice[0] = -97.8824f; // Matches our earlier test MFCC frame 0 output
                g_neural_engine.setOwnerEmbedding(my_voice);
            }
            
            // CMD 99: Run full pipeline (Buffer -> MFCC -> TFLite)
            else if (arg.action_id == 99) {
                std::vector<float> window(16000); 
                try {
                    g_audio_buffer.getRecentWindow(window.data(), 16000);
                    
                    std::vector<float> mfcc_features;
                    g_mfcc_extractor.compute(window.data(), 16000, mfcc_features);
                    
                    // Pass features to the Neural Engine
                    axl::InferenceResult result = g_neural_engine.process(mfcc_features);
                    
                    std::cout << "[NEURAL-OUT] Wake-Word Detected: " << (result.is_wake_word_detected ? "YES" : "NO") 
                              << " (Conf: " << result.trigger_confidence << ")\n";
                              
                    std::cout << "[NEURAL-OUT] Speaker Distance: " << result.speaker_match_distance << "\n";
                    std::cout << "[NEURAL-OUT] Auth Granted: " << (result.is_authorized_user ? "GRANTED" : "DENIED") << "\n";
                    
                } catch (const std::exception& e) {
                    std::cout << "[ERR] Pipeline failed: " << e.what() << "\n";
                }
            }
        }
    }, event.payload);
}

bool loadWavAndSimulateStream(const std::string& filepath, axl::EventDispatcher& bus) {
    std::ifstream file(filepath, std::ios::binary);
    if (!file) {
        std::cerr << "[ERR] Cannot open file: " << filepath << "\n";
        return false;
    }

    WavHeader header;
    file.read(reinterpret_cast<char*>(&header), sizeof(WavHeader));

    if (std::strncmp(header.riff_tag, "RIFF", 4) != 0 || std::strncmp(header.wave_tag, "WAVE", 4) != 0) {
        return false;
    }

    std::size_t num_samples = header.data_length / sizeof(int16_t);
    std::vector<int16_t> pcm_data(num_samples);
    file.read(reinterpret_cast<char*>(pcm_data.data()), header.data_length);

    g_mock_audio_memory.assign(num_samples, 0.0f);
    for (std::size_t i = 0; i < num_samples; ++i) {
        g_mock_audio_memory[i] = static_cast<float>(pcm_data[i]) / 32768.0f;
    }

    const std::size_t CHUNK_SIZE = 512;
    for (std::size_t i = 0; i < g_mock_audio_memory.size(); i += CHUNK_SIZE) {
        std::size_t current_chunk = std::min(CHUNK_SIZE, g_mock_audio_memory.size() - i);
        bus.enqueue(axl::Event(axl::AudioBufferEvent{g_mock_audio_memory.data() + i, current_chunk}));
    }

    return true;
}

int main() {
    std::cout << "=========================================\n";
    std::cout << " A-X-L Core Native - DSP Integration Test\n";
    std::cout << "=========================================\n\n";

    axl::EventDispatcher bus;
    bus.subscribe(dspSubsystemHandler);
    bus.start();

    std::cout << "[SYS] Bus active. Commands: 'wav <filepath>', 'cmd 99 <test_extract>', 'exit'\n";

    std::string input_line;
    while (true) {
        std::cout << "> ";
        if (!std::getline(std::cin, input_line)) break;

        std::istringstream iss(input_line);
        std::string command;
        iss >> command;

        if (command == "exit") {
            break;
        } else if (command == "wav") {
            std::string filepath;
            if (iss >> filepath) loadWavAndSimulateStream(filepath, bus);
        } else if (command == "cmd") {
            uint32_t id;
            std::string data;
            if (iss >> id) {
                std::getline(iss, data);
                bus.enqueue(axl::Event(axl::CommandEvent{id, data}));
            }
        }
    }

    bus.stop();
    return 0;
}