#include "Routes.h"

#include "Console.h"
#include "GameActions.h"
#include "GameThread.h"
#include "HttpServer.h"
#include "State.h"

using json = nlohmann::json;

namespace {
    // "0x14" / "14" / "0X14" -> 0x14. Returns 0 on anything unparseable, which
    // the caller treats as "no target" rather than an error — a bad ref should
    // not silently retarget the command at something else.
    RE::FormID ParseFormID(const std::string& s)
    {
        if (s.empty()) return 0;
        try {
            return static_cast<RE::FormID>(std::stoul(s, nullptr, 16));
        } catch (...) {
            return 0;
        }
    }

    // GET /ping — the Phase 0.1 deliverable. Answers WITHOUT touching the game
    // thread on purpose: it must stay reachable during a load screen or a hang,
    // so the runner can tell "process alive, game busy" from "process dead".
    Http::Response Ping(const Http::Request&)
    {
        return Http::Response::Ok({
            { "ok", true },
            { "plugin", "AgentBridge" },
            { "version", "0.5.0" },
        });
    }

    // GET /state[?include=nearby,inventory,quests,plugins][&radius=4096][&limit=32]
    //
    // Player and game blocks always come back. The rest is opt-in — see
    // State::Options for why.
    Http::Response StateRoute(const Http::Request& req)
    {
        State::Options options;

        const std::string include = req.Get("include");
        options.nearby = include.find("nearby") != std::string::npos;
        options.cellActors = include.find("cell_actors") != std::string::npos;
        options.inventory = include.find("inventory") != std::string::npos;
        options.quests = include.find("quests") != std::string::npos;
        options.plugins = include.find("plugins") != std::string::npos;

        if (const auto radius = req.Get("radius"); !radius.empty()) {
            try { options.radius = std::stof(radius); } catch (...) {}
        }
        if (const auto limit = req.Get("limit"); !limit.empty()) {
            try { options.limit = static_cast<std::size_t>(std::stoul(limit)); } catch (...) {}
        }

        auto snapshot = GameThread::Run([options]() -> json { return State::Snapshot(options); });

        if (!snapshot) {
            // Task queue didn't drain in time — loading screen, pause, or hang.
            return Http::Response::Error(503, "game thread did not respond in time");
        }
        return Http::Response::Ok(*snapshot);
    }

    // POST /console  {"cmd": "coc WhiterunBanneredMare", "ref": "0x14"}
    //
    // `ref` is optional and is the console's "selected reference" — the thing
    // `player.additem`-style dotted commands act on. Omit it for global commands.
    //
    // Timeout is longer than the default: a command runs synchronously on the
    // game thread, and some of them (`coc` into an unloaded cell) are not quick.
    Http::Response ConsoleCmd(const Http::Request& req)
    {
        std::string cmd;
        std::string refStr;
        try {
            const auto body = json::parse(req.body.empty() ? "{}" : req.body);
            cmd = body.value("cmd", std::string{});
            refStr = body.value("ref", std::string{});
        } catch (const std::exception& e) {
            return Http::Response::Error(400, std::string{ "bad JSON body: " } + e.what());
        }

        if (cmd.empty()) {
            return Http::Response::Error(400, "missing \"cmd\"");
        }

        const RE::FormID refID = ParseFormID(refStr);

        auto ran = GameThread::Run(
            [cmd, refID]() -> json {
                RE::TESObjectREFR* target = nullptr;
                if (refID != 0) {
                    target = RE::TESForm::LookupByID<RE::TESObjectREFR>(refID);
                    if (!target) {
                        return json{ { "ok", false },
                                     { "error", std::format("no reference with form id 0x{:08X}", refID) } };
                    }
                }

                const auto result = Console::Execute(cmd, target);
                return json{
                    { "ok", result.ran },
                    { "cmd", cmd },
                    { "output", result.output },
                    { "output_captured", !result.output.empty() },
                };
            },
            std::chrono::milliseconds{ 10000 });

        if (!ran) {
            return Http::Response::Error(503, "game thread did not respond in time");
        }
        if (!ran->value("ok", false)) {
            return Http::Response{ 400, *ran };
        }
        return Http::Response::Ok(*ran);
    }

    Http::Response ActorMove(const Http::Request& req)
    {
        std::string name;
        float distance = 128.0f;
        try {
            const auto body = json::parse(req.body.empty() ? "{}" : req.body);
            name = body.value("name", std::string{});
            distance = body.value("distance", distance);
        } catch (const std::exception& e) {
            return Http::Response::Error(400, std::string{ "bad JSON body: " } + e.what());
        }
        auto result = GameThread::Run([name, distance] { return GameActions::MoveToActor(name, distance); });
        if (!result) return Http::Response::Error(503, "game thread did not respond in time");
        return result->value("ok", false) ? Http::Response::Ok(*result) : Http::Response{ 400, *result };
    }

    Http::Response ActorActivate(const Http::Request& req)
    {
        std::string name;
        try {
            const auto body = json::parse(req.body.empty() ? "{}" : req.body);
            name = body.value("name", std::string{});
        } catch (const std::exception& e) {
            return Http::Response::Error(400, std::string{ "bad JSON body: " } + e.what());
        }
        auto result = GameThread::Run([name] { return GameActions::ActivateActor(name); });
        if (!result) return Http::Response::Error(503, "game thread did not respond in time");
        return result->value("ok", false) ? Http::Response::Ok(*result) : Http::Response{ 400, *result };
    }

    Http::Response DialogueSelect(const Http::Request& req)
    {
        std::string text;
        bool contains = false;
        try {
            const auto body = json::parse(req.body.empty() ? "{}" : req.body);
            text = body.value("text", std::string{});
            contains = body.value("contains", false);
        } catch (const std::exception& e) {
            return Http::Response::Error(400, std::string{ "bad JSON body: " } + e.what());
        }
        auto result = GameThread::Run([text, contains] {
            return GameActions::SelectDialogue(text, contains);
        });
        if (!result) return Http::Response::Error(503, "game thread did not respond in time");
        return result->value("ok", false) ? Http::Response::Ok(*result) : Http::Response{ 400, *result };
    }

    Http::Response DialogueClose(const Http::Request&)
    {
        auto result = GameThread::Run([] { return GameActions::CloseDialogue(); });
        if (!result) return Http::Response::Error(503, "game thread did not respond in time");
        return result->value("ok", false) ? Http::Response::Ok(*result) : Http::Response{ 400, *result };
    }

    Http::Response GlobalRead(const Http::Request& req)
    {
        const std::string editorID = req.Get("editor_id");
        auto result = GameThread::Run([editorID] { return GameActions::ReadGlobal(editorID); });
        if (!result) return Http::Response::Error(503, "game thread did not respond in time");
        return result->value("ok", false) ? Http::Response::Ok(*result) : Http::Response{ 400, *result };
    }
}

void Routes::Register()
{
    Http::Route("GET", "/ping", Ping);
    Http::Route("GET", "/state", StateRoute);
    Http::Route("GET", "/global", GlobalRead);
    Http::Route("POST", "/console", ConsoleCmd);
    Http::Route("POST", "/actor/move-to", ActorMove);
    Http::Route("POST", "/actor/activate", ActorActivate);
    Http::Route("POST", "/dialogue/select", DialogueSelect);
    Http::Route("POST", "/dialogue/close", DialogueClose);
}
