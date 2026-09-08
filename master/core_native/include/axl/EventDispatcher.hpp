#pragma once

#include <queue>
#include <mutex>
#include <condition_variable>
#include <thread>
#include <atomic>
#include <vector>
#include <functional>
#include "axl/Event.hpp"

namespace axl {

    /**
     * @brief Callback alias for modules listening to the event bus.
     */
    using EventCallback = std::function<void(const Event&)>;

    /**
     * @brief Thread-safe message bus for asynchronous event routing.
     * 
     * Operates a dedicated background thread to prevent blocking the main
     * execution loop or the audio sampling routines. Designed to remain
     * suspended in idle state using condition variables, ensuring minimal
     * CPU footprint (crucial for battery-constrained environments)
     */
    class EventDispatcher {
    public:
        EventDispatcher();
        ~EventDispatcher();

        // Delete copy and move semantics to enforce unique ownership
        // The dispatcher must remain a single source of truth (Singleton-like context)
        EventDispatcher(const EventDispatcher&) = delete;
        EventDispatcher& operator=(const EventDispatcher&) = delete;

        /**
         * @brief Spawns the background worker thread.
         */
        void start();

        /**
         * @brief Safely shuts down the worker thread, flushing pending events.
         */
        void stop();

        /**
         * @brief Pushes a new event into the thread-safe queue.
         * @param event The polymorphic event payload, passed by rvalue reference.
         */
        void enqueue(Event&& event);

        /**
         * @brief Registers a module to receive dispatched events.
         * @param callback The function to invoke when an event is processed.
         */
        void subscribe(EventCallback callback);

    private:
        /**
         * @brief The core execution loop running on the worker thread.
         */
        void dispatchLoop();

        std::queue<Event> event_queue_;
        std::mutex queue_mutex_;
        std::condition_variable queue_cv_;
        
        std::vector<EventCallback> subscribers_;
        
        std::thread worker_thread_;
        std::atomic<bool> is_running_;
    };

} // namespace axl