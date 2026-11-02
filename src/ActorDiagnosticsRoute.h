#pragma once

#include "HttpServer.h"

// Single-NPC v1 read route. Validation stays on the socket thread; the snapshot
// runs entirely inside one GameThread::Run task with the existing timeout.
namespace ActorDiagnosticsRoute {
    Http::Response Handle(const Http::Request& req);
}
