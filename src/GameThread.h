#pragma once

#include <chrono>
#include <functional>
#include <future>
#include <optional>
#include <type_traits>

// Marshalling HTTP handlers onto the game thread.
//
// The HTTP server runs on its own thread. Nearly every RE:: read is only safe
// on the game's main thread — touching PlayerCharacter, cell refs or the
// console from the socket thread is how you get a crash that reproduces once
// every fifty runs. So a route that needs game state does NOT read it directly;
// it hands a callable to SKSE's task interface and blocks until the game thread
// has run it.
//
// The timeout matters: during a load screen, a full menu pause, or a hang, the
// task queue may not drain at all. A blocked-forever handler would wedge the
// socket thread and make the whole bridge look dead, so a route that times out
// answers 503 instead and the runner can retry.
namespace GameThread {
    inline constexpr auto kDefaultTimeout = std::chrono::milliseconds{ 3000 };

    // Runs `fn` on the game thread, returns its result, or nullopt on timeout.
    template <class F>
    auto Run(F&& fn, std::chrono::milliseconds timeout = kDefaultTimeout)
        -> std::optional<std::invoke_result_t<F>>
    {
        using R = std::invoke_result_t<F>;

        auto* task = SKSE::GetTaskInterface();
        if (!task) {
            return std::nullopt;
        }

        // shared_ptr because the task interface owns the callable until the game
        // thread runs it, which may be after Run() has already given up.
        auto promise = std::make_shared<std::promise<R>>();
        auto future = promise->get_future();

        task->AddTask([promise, fn = std::forward<F>(fn)]() mutable {
            try {
                promise->set_value(fn());
            } catch (...) {
                // A route handler that throws must not take the game thread with
                // it. The waiter sees the exception via the future.
                try { promise->set_exception(std::current_exception()); } catch (...) {}
            }
        });

        if (future.wait_for(timeout) != std::future_status::ready) {
            return std::nullopt;
        }
        return future.get();
    }
}
