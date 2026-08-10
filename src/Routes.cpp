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

    RE::FormID BodyFormID(const json& a_body, std::string_view a_key)
    {
        const auto it = a_body.find(a_key);
        if (it == a_body.end() || it->is_null()) return 0;
        if (it->is_number_unsigned()) return it->get<RE::FormID>();
        if (it->is_number_integer()) {
            const auto value = it->get<std::int64_t>();
            return value > 0 && value <= UINT32_MAX ? static_cast<RE::FormID>(value) : 0;
        }
        return it->is_string() ? ParseFormID(it->get<std::string>()) : 0;
    }

    GameActions::ActorSelector ActorSelectorFrom(const json& a_body)
    {
        const auto scope = a_body.value("scope", std::string{ "cell" });
        if (scope != "cell" && scope != "loaded") {
            throw std::invalid_argument("scope must be 'cell' or 'loaded'");
        }
        return {
            .name = a_body.value("name", std::string{}),
            .formID = BodyFormID(a_body, "form_id"),
            .loadedScope = scope == "loaded",
        };
    }

    // GET /ping — the Phase 0.1 deliverable. Answers WITHOUT touching the game
    // thread on purpose: it must stay reachable during a load screen or a hang,
    // so the runner can tell "process alive, game busy" from "process dead".
    Http::Response Ping(const Http::Request&)
    {
        return Http::Response::Ok({
            { "ok", true },
            { "plugin", "AgentBridge" },
            { "version", "0.6.0" },
        });
    }

    // GET /state[?include=nearby_actors,cell_actors,loaded_actors,inventory,quests,plugins]
    //
    // Player and game blocks always come back. The rest is opt-in — see
    // State::Options for why.
    Http::Response StateRoute(const Http::Request& req)
    {
        State::Options options;

        const std::string include = req.Get("include");
        options.nearby = include.find("nearby") != std::string::npos;
        options.cellActors = include.find("cell_actors") != std::string::npos;
        options.loadedActors = include.find("loaded_actors") != std::string::npos;
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
        GameActions::ActorSelector selector;
        float distance = 128.0f;
        try {
            const auto body = json::parse(req.body.empty() ? "{}" : req.body);
            selector = ActorSelectorFrom(body);
            distance = body.value("distance", distance);
        } catch (const std::exception& e) {
            return Http::Response::Error(400, std::string{ "bad JSON body: " } + e.what());
        }
        auto result = GameThread::Run(
            [selector, distance] { return GameActions::MoveToActor(selector, distance); },
            std::chrono::milliseconds{ 10000 });
        if (!result) return Http::Response::Error(503, "game thread did not respond in time");
        return result->value("ok", false) ? Http::Response::Ok(*result) : Http::Response{ 400, *result };
    }

    Http::Response ActorActivate(const Http::Request& req)
    {
        GameActions::ActorSelector selector;
        try {
            const auto body = json::parse(req.body.empty() ? "{}" : req.body);
            selector = ActorSelectorFrom(body);
        } catch (const std::exception& e) {
            return Http::Response::Error(400, std::string{ "bad JSON body: " } + e.what());
        }
        auto result = GameThread::Run([selector] { return GameActions::ActivateActor(selector); });
        if (!result) return Http::Response::Error(503, "game thread did not respond in time");
        return result->value("ok", false) ? Http::Response::Ok(*result) : Http::Response{ 400, *result };
    }

    Http::Response DialogueSelect(const Http::Request& req)
    {
        GameActions::DialogueSelector selector;
        try {
            const auto body = json::parse(req.body.empty() ? "{}" : req.body);
            selector.text = body.value("text", std::string{});
            selector.contains = body.value("contains", false);
            if (const auto it = body.find("index"); it != body.end() && it->is_number_unsigned()) {
                selector.index = it->get<std::size_t>();
            } else if (it != body.end() && it->is_number_integer() && it->get<std::int64_t>() >= 0) {
                selector.index = static_cast<std::size_t>(it->get<std::int64_t>());
            }
            selector.infoFormID = BodyFormID(body, "info_form_id");
        } catch (const std::exception& e) {
            return Http::Response::Error(400, std::string{ "bad JSON body: " } + e.what());
        }
        auto result = GameThread::Run([selector] {
            return GameActions::SelectDialogue(selector);
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
