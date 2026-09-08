#include <iostream>
#include <string>
#include <cstring>
#include <sstream>
#include <fstream>
#include <vector>
#include <cstdint>
#include <iomanip>
#include "axl/EventDispatcher.hpp"
#include "axl/RingBuffer.hpp"

#pragma pack(push, 1)
struct WavHeader {
    char riff_tag[4];        // "RIFF"
    uint32_t riff_length;
    char wave_tag[4];        // "WAVE"
    char fmt_tag[4];         // "fmt "
    uint32_t fmt_length;
    uint16_t audio_format;   // 1 for PCM
    uint16_t num_channels;   // 1 for Mono
    uint32_t sample_rate;    // 16000
    uint32_t byte_rate;
    uint16_t block_align;
    uint16_t bits_per_sample;// 16
    char data_tag[4];        // "data"
    uint32_t data_length;
};
#pragma pack(pop)

// Global state for test environment
// In a real system, RingBuffer belongs to a dedicated DSP Module class
axl::RingBuffer g_audio_buffer(32000); // 2 seconds at 16kHz
std::vector<float> g_mock_audio_memory; // Keeps audio memory alive during async dispatch

/**
 * @brief Subsystem callback simulating the DSP extraction engine
 */
void dspSubsystemHandler(const axl::Event& event) {
    std::visit([](auto&& arg) {
        using T = std::decay_t<decltype(arg)>;
        
        if constexpr (std::is_same_v<T, axl::AudioBufferEvent>) {
            // Push incoming hardware chunk to our circular buffer
            g_audio_buffer.push(arg.raw_data, arg.num_samples);
            
            // For debug purposes: print every ~50th chunk to avoid terminal spam
            static int counter = 0;
            if (++counter % 50 == 0) {
                std::cout << "[DSP-MODULE] Ingested chunk. Current sample [0]: " 
                          << std::fixed << std::setprecision(4) << arg.raw_data[0] << "\n";
            }
        } 
        else if constexpr (std::is_same_v<T, axl::CommandEvent>) {
            std::cout << "[SYS-MODULE] Command ID: " << arg.action_id 
                      << " | Payload: " << arg.parameters << "\n";
            
            // Trigger a mock MFCC extraction request
            if (arg.action_id == 99) {
                std::vector<float> window(16000); // Request last 1 second
                try {
                    g_audio_buffer.getRecentWindow(window.data(), 16000);
                    std::cout << "[DSP-MODULE] Successfully extracted 1s window for MFCC.\n";
                } catch (const std::exception& e) {
                    std::cout << "[DSP-MODULE] Extration failed: " << e.what() << "\n";
                }
            }
        }
    }, event.payload);
}

/**
 * @brief Raw binary parser for testing. Simulates the HAL passing PCM data.
 */
bool loadWavAndSimulateStream(const std::string& filepath, axl::EventDispatcher& bus) {
    std::ifstream file(filepath, std::ios::binary);
    if (!file) {
        std::cerr << "[ERR] Cannot open file: " << filepath << "\n";
        return false;
    }

    WavHeader header;
    file.read(reinterpret_cast<char*>(&header), sizeof(WavHeader));

    // Basic validation
    if (std::strncmp(header.riff_tag, "RIFF", 4) != 0 || std::strncmp(header.wave_tag, "WAVE", 4) != 0) {
        std::cerr << "[ERR] Invalid WAV format.\n";
        return false;
    }
    if (header.audio_format != 1 || header.bits_per_sample != 16) {
        std::cerr << "[ERR] Only 16-bit PCM supported.\n";
        return false;
    }

    std::cout << "[WAV] Loaded: " << header.sample_rate << "Hz, Channels: " << header.num_channels << "\n";

    std::size_t num_samples = header.data_length / sizeof(int16_t);
    std::vector<int16_t> pcm_data(num_samples);
    file.read(reinterpret_cast<char*>(pcm_data.data()), header.data_length);

    // Convert int16 to float [-1.0f, 1.0f]
    g_mock_audio_memory.assign(num_samples, 0.0f);
    for (std::size_t i = 0; i < num_samples; ++i) {
        g_mock_audio_memory[i] = static_cast<float>(pcm_data[i]) / 32768.0f;
    }

    // Simulate hardware callbacks by dispatching chunks of 512 samples
    const std::size_t CHUNK_SIZE = 512;
    for (std::size_t i = 0; i < g_mock_audio_memory.size(); i += CHUNK_SIZE) {
        std::size_t current_chunk = std::min(CHUNK_SIZE, g_mock_audio_memory.size() - i);
        
        // Pass pointer into persistent memory vector
        bus.enqueue(axl::Event(axl::AudioBufferEvent{
            g_mock_audio_memory.data() + i, 
            current_chunk
        }));
    }

    std::cout << "[SYS] Stream simulation complete. Dispatched " 
              << (num_samples / CHUNK_SIZE) << " frames.\n";
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
        } 
        else if (command == "wav") {
            std::string filepath;
            if (iss >> filepath) {
                loadWavAndSimulateStream(filepath, bus);
            } else {
                std::cout << "[ERR] Usage: wav <path_to_file.wav>\n";
            }
        }
        else if (command == "cmd") {
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