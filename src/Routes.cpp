#include "Routes.h"

#include "Console.h"
#include "GameActions.h"
#include "GameThread.h"
#include "HttpServer.h"
#include "MessageBox.h"
#include "State.h"

#include <charconv>
#include <cmath>
#include <optional>
#include <unordered_set>

using json = nlohmann::json;

#ifndef AGENT_BRIDGE_VERSION
#error "AGENT_BRIDGE_VERSION must come from the CMake project version"
#endif

namespace {
    // "0x14" / "14" / "0X14" -> 0x14. The whole string must be valid.
    std::optional<RE::FormID> ParseFormID(std::string_view s)
    {
        if (s.starts_with("0x") || s.starts_with("0X")) {
            s.remove_prefix(2);
        }
        if (s.empty()) return std::nullopt;

        RE::FormID value = 0;
        const auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), value, 16);
        if (ec != std::errc{} || ptr != s.data() + s.size()) return std::nullopt;
        return value;
    }

    RE::FormID BodyFormID(const json& a_body, std::string_view a_key, bool a_rejectInvalid = false)
    {
        const auto it = a_body.find(a_key);
        if (it == a_body.end() || it->is_null()) return 0;
        if (a_rejectInvalid) {
            std::optional<RE::FormID> value;
            if (it->is_number_unsigned()) {
                const auto raw = it->get<std::uint64_t>();
                if (raw <= UINT32_MAX) value = static_cast<RE::FormID>(raw);
            } else if (it->is_number_integer()) {
                const auto raw = it->get<std::int64_t>();
                if (raw > 0 && raw <= UINT32_MAX) value = static_cast<RE::FormID>(raw);
            } else if (it->is_string()) {
                value = ParseFormID(it->get<std::string>());
            }
            if (!value || *value == 0) {
                throw std::invalid_argument(std::format("{} must be a non-zero hexadecimal form id", a_key));
            }
            return *value;
        }
        if (it->is_number_unsigned()) return it->get<RE::FormID>();
        if (it->is_number_integer()) {
            const auto value = it->get<std::int64_t>();
            return value > 0 && value <= UINT32_MAX ? static_cast<RE::FormID>(value) : 0;
        }
        return it->is_string() ? ParseFormID(it->get<std::string>()).value_or(0) : 0;
    }

    GameActions::ActorSelector ActorSelectorFrom(const json& a_body)
    {
        const auto scope = a_body.value("scope", std::string{ "cell" });
        if (scope != "cell" && scope != "loaded") {
            throw std::invalid_argument("scope must be 'cell' or 'loaded'");
        }
        return {
            .name = a_body.value("name", std::string{}),
            .formID = BodyFormID(a_body, "form_id", true),
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
            { "version", AGENT_BRIDGE_VERSION },
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
        std::unordered_set<std::string_view> includes;
        std::string_view rest = include;
        while (!rest.empty()) {
            const auto comma = rest.find(',');
            includes.insert(rest.substr(0, comma));
            if (comma == std::string_view::npos) break;
            rest.remove_prefix(comma + 1);
        }

        options.nearby = includes.contains("nearby_actors");
        options.cellActors = includes.contains("cell_actors");
        options.loadedActors = includes.contains("loaded_actors");
        options.inventory = includes.contains("inventory");
        options.quests = includes.contains("quests");
        options.plugins = includes.contains("plugins");

        if (const auto radius = req.Get("radius"); !radius.empty()) {
            try {
                const float parsedRadius = std::stof(radius);
                if (!std::isfinite(parsedRadius) || parsedRadius <= 0.0f) {
                    return Http::Response::Error(400, "radius must be a finite positive number");
                }
                options.radius = parsedRadius;
            } catch (...) {
                return Http::Response::Error(400, "radius must be a finite positive number");
            }
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

        const auto parsedRefID = refStr.empty() ? std::optional<RE::FormID>{ 0 } : ParseFormID(refStr);
        if (!parsedRefID || (!refStr.empty() && *parsedRefID == 0)) {
            return Http::Response::Error(400, "ref must be a non-zero hexadecimal form id");
        }
        const RE::FormID refID = *parsedRefID;

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

    Http::Response MessageBoxSelect(const Http::Request& req)
    {
        StructuredMessageBox::Selector selector;
        try {
            const auto body = json::parse(req.body.empty() ? "{}" : req.body);
            selector.text = body.value("text", std::string{});
            if (const auto it = body.find("index"); it != body.end() && it->is_number_unsigned()) {
                selector.index = it->get<std::size_t>();
            } else if (it != body.end() && it->is_number_integer() && it->get<std::int64_t>() >= 0) {
                selector.index = static_cast<std::size_t>(it->get<std::int64_t>());
            }
            if (const auto it = body.find("message"); it != body.end()) {
                if (!it->is_string()) throw std::invalid_argument("message must be a string");
                selector.expectedMessage = it->get<std::string>();
            }
        } catch (const std::exception& e) {
            return Http::Response::Error(400, std::string{ "bad JSON body: " } + e.what());
        }
        auto result = GameThread::Run([selector] { return StructuredMessageBox::Select(selector); });
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
    Http::Route("POST", "/messagebox/select", MessageBoxSelect);
}
