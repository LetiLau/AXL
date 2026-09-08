#pragma once

#include <vector>
#include <cstddef>
#include <stdexcept>
#include <mutex>

namespace axl {

    /**
     * @brief A pre-allocated, thread-safe circular buffer optimized for continuous audio streaming.
     * 
     * Designed specifically for DSP sliding-window extraction (e.g., MFCC).
     * Guarantees zero dynamic memory allocation during normal push/read operations.
     */
    class RingBuffer {
    public:
        /**
         * @brief Constructs the ring buffer.
         * @param capacity The maximum number of samples the buffer can hold (e.g., 3 seconds at 16kHz).
         */
        explicit RingBuffer(std::size_t capacity);

        // Prevent copying to maintain strict memory ownership
        RingBuffer(const RingBuffer&) = delete;
        RingBuffer& operator=(const RingBuffer&) = delete;

        /**
         * @brief Pushes a new chunk of audio data into the buffer.
         * Overwrites the oldest data if capacity is exceeded.
         * 
         * @param data Pointer to the raw float audio samples.
         * @param size Number of samples to write.
         */
        void push(const float* data, std::size_t size);

        /**
         * @brief Extracts a linear sequence of the most recent samples.
         * 
         * Useful for grabbing the "last N milliseconds" of audio to feed into the neural net.
         * 
         * @param out_buffer Pre-allocated destination array.
         * @param window_size Number of recent samples to extract.
         * @throws std::out_of_range if window_size > capacity.
         */
        void getRecentWindow(float* out_buffer, std::size_t window_size) const;

        /**
         * @brief Clears the internal state (pointers), essentially emptying the buffer.
         */
        void reset();

    private:
        std::vector<float> buffer_;
        std::size_t capacity_;
        std::size_t write_index_;
        std::size_t available_samples_;
        
        // Mutex to protect concurrent access (e.g., Audio thread writing, DSP thread reading)
        mutable std::mutex buffer_mutex_;
    };

}//namespace axl