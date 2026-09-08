#pragma once

#include <variant>
#include <string>
#include <cstdint>

//dedicated namespace with the whole code inside
namespace axl {

    //payload data structures
    struct AudioBufferEvent {
        const float* raw_data;
        std::size_t num_samples;
        // Non alloca memoria dinamicamente, punta solo al Ring Buffer pre-allocato
    };

    struct CommandEvent {
        uint32_t action_id;
        std::string parameters; // Es: payload JSON dal web server
    };

    struct SystemTickEvent {
        uint64_t timestamp_ms; // Per timer e watchdog
    };

    // 2. La nostra Type-Safe Union
    // EventPayload occuperà in memoria solo lo spazio della struct più grande,
    // più un byte per ricordare quale tipo contiene attualmente.
    using EventPayload = std::variant<
        AudioBufferEvent,
        CommandEvent,
        SystemTickEvent
    >;

    // 3. L'involucro dell'Evento
    struct Event {
        EventPayload payload;
        
        // Costruttori impliciti per facilitare la creazione
        template<typename T>
        Event(T&& data) : payload(std::forward<T>(data)) {}
    };

}//namespace axl