#include <iostream>
#include <string>
#include <sstream>
#include <fstream>
#include <vector>
#include <cstdint>
#include <cstring>
#include "axl/AxlCore.hpp"

#pragma pack(push, 1)
struct WavHeader {
    char riff_tag[4];        uint32_t riff_length;
    char wave_tag[4];        char fmt_tag[4];
    uint32_t fmt_length;     uint16_t audio_format;
    uint16_t num_channels;   uint32_t sample_rate;
    uint32_t byte_rate;      uint16_t block_align;
    uint16_t bits_per_sample; char data_tag[4];
    uint32_t data_length;
};
#pragma pack(pop)

std::vector<float> g_mock_audio_memory;

bool loadWavAndSimulateStream(const std::string& filepath) {
    std::ifstream file(filepath, std::ios::binary);
    if (!file) return false;

    WavHeader header;
    file.read(reinterpret_cast<char*>(&header), sizeof(WavHeader));
    if (std::strncmp(header.riff_tag, "RIFF", 4) != 0) return false;

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
        // Clean Façade call
        axl::AxlCore::getInstance().pushAudioChunk(g_mock_audio_memory.data() + i, current_chunk);
    }
    return true;
}

int main() {
    std::cout << "=========================================\n";
    std::cout << " A-X-L Core Native - Integration Testing \n";
    std::cout << "=========================================\n\n";

    // 1. Boot the core
    axl::AxlCore::getInstance().init("host_mock_model.tflite");

    std::cout << "[SYS] Commands: 'wav <filepath>', 'cmd <id> <payload>', 'exit'\n";

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
            if (iss >> filepath) {
                if (!loadWavAndSimulateStream(filepath)) {
                    std::cout << "[ERR] Invalid WAV file.\n";
                }
            }
        } else if (command == "cmd") {
            uint32_t id;
            std::string data;
            if (iss >> id) {
                std::getline(iss, data);
                axl::AxlCore::getInstance().enqueueCommand(id, data);
            }
        }
    }

    // 2. Teardown
    axl::AxlCore::getInstance().shutdown();
    return 0;
}