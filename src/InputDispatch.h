#pragma once
#include <cstdint>

// Production cache operation, also instantiated by the native host. The engine
// owns these preconstructed events; no Create/delete or event-source bypass.
namespace Input::Dispatch {
template <class Queue, class Device>
bool Append(Queue* queue, Device keyboard, std::uint32_t scan, float value, float duration) {
    if (!queue || queue->buttonEventCount >= Queue::MAX_BUTTON_EVENTS) return false;
    auto& event = queue->GetRuntimeData().buttonEvents[queue->buttonEventCount];
    event.GetRuntimeData().value = value;
    event.GetRuntimeData().heldDownSecs = duration;
    event.device = keyboard;
    event.idCode = scan;
    event.userEvent = {};
    // Matching CommonLib fork's AddButtonEvent wrongly dereferences the
    // VR-only extension on flat runtimes. Preserve its cache path without it.
    queue->PushOntoInputQueue(&event);
    ++queue->buttonEventCount;
    return true;
}
template <class Process, class Device, class Tick>
void AfterKeyboard(Process previous, Device* device, float elapsed, Tick tick) {
    previous(device, elapsed);
    tick();
}
}
