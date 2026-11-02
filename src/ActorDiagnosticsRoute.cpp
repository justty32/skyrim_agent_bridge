#include "ActorDiagnosticsRoute.h"

#include "GameActions.h"
#include "GameThread.h"
#include "RouteParsing.h"

using json = nlohmann::json;
using RouteParsing::ParseFormID;

Http::Response ActorDiagnosticsRoute::Handle(const Http::Request& req)
{
    if (req.Get("schema_version") != "1") {
        return Http::Response{ 400, { { "ok", false }, { "status", "unsupported" },
                                     { "error", "schema_version must be 1" } } };
    }
    const auto id = ParseFormID(req.Get("form_id"));
    const auto scope = req.Get("scope").empty() ? std::string{ "cell" } : req.Get("scope");
    if (!id || *id == 0 || *id == 0x14 || (scope != "cell" && scope != "loaded") || !req.Get("name").empty()) {
        return Http::Response::Error(400, "a single NPC reference form_id and cell/loaded scope are required");
    }
    const GameActions::ActorSelector selector{ .name = {}, .formID = *id, .loadedScope = scope == "loaded" };
    try {
        auto result = GameThread::Run([selector] { return GameActions::ActorDiagnostics(selector); });
        if (!result) return Http::Response::Error(503, "game thread did not respond in time");
        return result->value("ok", false) ? Http::Response::Ok(*result) : Http::Response{ 400, *result };
    } catch (const std::exception& e) {
        return Http::Response::Error(503, std::string{ "actor diagnostics failed: " } + e.what());
    }
}
