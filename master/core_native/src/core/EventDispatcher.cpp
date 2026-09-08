#include "axl/EventDispatcher.hpp"

namespace axl {

EventDispatcher::EventDispatcher() : is_running_(false) {
    // Initialization only. Thread is NOT spawned here to allow controlled startup.
}

EventDispatcher::~EventDispatcher() {
    stop();
}

void EventDispatcher::start() {
    bool expected = false;
    if (is_running_.compare_exchange_strong(expected, true)) {
        worker_thread_ = std::thread(&EventDispatcher::dispatchLoop, this);
    }
}

void EventDispatcher::stop() {
    bool expected = true;
    if (is_running_.compare_exchange_strong(expected, false)) {
        // Wake up the worker thread if it's currently sleeping on an empty queue
        queue_cv_.notify_one();
        
        if (worker_thread_.joinable()) {
            worker_thread_.join();
        }
    }
}

void EventDispatcher::enqueue(Event&& event) {
    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        // std::move enforces transfer of ownership without copying the underlying variant
        event_queue_.push(std::move(event));
    }
    // Signal the worker thread that a new payload is available
    queue_cv_.notify_one();
}

void EventDispatcher::subscribe(EventCallback callback) {
    // Note: Assuming subscriptions happen during setup phase (single-threaded).
    // If dynamic subscriptions are needed later, this vector requires a read-write lock.
    subscribers_.push_back(std::move(callback));
}

void EventDispatcher::dispatchLoop() {
    while (is_running_) {
        Event current_event(SystemTickEvent{0}); // Dummy initialization

        {
            std::unique_lock<std::mutex> lock(queue_mutex_);
            
            // Suspend thread execution safely. The OS will wake this thread ONLY when:
            // 1. queue_cv_.notify_one() is called AND
            // 2. The lambda condition returns true (queue not empty OR shutting down)
            queue_cv_.wait(lock, [this] {
                return !event_queue_.empty() || !is_running_;
            });

            if (!is_running_ && event_queue_.empty()) {
                break; // Clean exit on shutdown
            }

            // Extract the event
            current_event = std::move(event_queue_.front());
            event_queue_.pop();
        } // queue_mutex_ is released here

        // Fan-out the event to all registered modules
        for (const auto& callback : subscribers_) {
            callback(current_event);
        }
    }
}

}//namespace axl