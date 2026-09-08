#include "axl/RingBuffer.hpp"
#include <algorithm>
#include <stdexcept>

//using namespace std; //better not to use std in low level system

namespace axl {

RingBuffer::RingBuffer(std::size_t capacity)
    : buffer_(capacity, 0.0f), capacity_(capacity), write_index_(0), available_samples_(0) {
    if (capacity == 0) {
        throw std::invalid_argument("[AXL-DSP] RingBuffer capacity must be strictly positive.");
    }
}

void RingBuffer::push(const float* data, std::size_t size) {
    std::lock_guard<std::mutex> lock(buffer_mutex_);

    // Edge case: if the incoming chunk is larger than the entire buffer,
    // we only care about the most recent 'capacity_' samples.
    if (size >= capacity_) {
        data += (size - capacity_);
        size = capacity_;
    }

    std::size_t space_until_end = capacity_ - write_index_;

    if (size <= space_until_end) {
        // Fast path: contiguous write without wrapping
        std::copy(data, data + size, buffer_.begin() + write_index_);
        write_index_ += size;
    } else {
        // Wrap-around path: split the write into two chunks
        std::copy(data, data + space_until_end, buffer_.begin() + write_index_);
        std::copy(data + space_until_end, data + size, buffer_.begin());
        write_index_ = size - space_until_end;
    }

    // Keep write index strict
    if (write_index_ == capacity_) {
        write_index_ = 0;
    }

    available_samples_ = std::min(capacity_, available_samples_ + size);
}

void RingBuffer::getRecentWindow(float* out_buffer, std::size_t window_size) const {
    std::lock_guard<std::mutex> lock(buffer_mutex_);

    if (window_size > available_samples_) {
        throw std::out_of_range("[AXL-DSP] Requested window exceeds available samples.");
    }

    // Calculate logical start point based on current write_index_
    std::size_t read_index = (write_index_ >= window_size)
                                 ? (write_index_ - window_size)
                                 : (capacity_ - (window_size - write_index_));

    std::size_t space_until_end = capacity_ - read_index;

    if (window_size <= space_until_end) {
        // Fast path: contiguous read
        std::copy(buffer_.begin() + read_index, buffer_.begin() + read_index + window_size, out_buffer);
    } else {
        // Wrap-around path: assemble the linear buffer from two disjoint segments
        std::copy(buffer_.begin() + read_index, buffer_.end(), out_buffer);
        std::copy(buffer_.begin(), buffer_.begin() + (window_size - space_until_end), out_buffer + space_until_end);
    }
}

void RingBuffer::reset() {
    std::lock_guard<std::mutex> lock(buffer_mutex_);
    write_index_ = 0;
    available_samples_ = 0;
    // Overwriting memory is sufficient; no need to waste CPU cycles zeroing it out.
}

} // namespace axl