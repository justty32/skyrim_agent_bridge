// Host-only RE doubles. The test runner inserts the actual producer body below.
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <iostream>
#include <charconv>
#include <optional>
#include <map>
#include <string>
#include <string_view>
using json = nlohmann::json;
namespace RE {
    using FormID = unsigned;
    struct TESForm {
        unsigned id = 1;
        std::string type = "WEAP";
        unsigned GetFormID() const { return id; }
        const char* GetName() const { return "synthetic"; }
        const char* GetFormEditorID() const { return ""; }
        std::string_view GetFormType() const { return type; }
    };
    std::string_view FormTypeToString(std::string_view type) { return type; }
    struct TESNPC : TESForm { TESForm* combatStyle = nullptr; };
    struct ExtraCombatStyle { TESForm* combatStyle = nullptr; };
    struct Actor {
        struct SlotTypes { enum { kLeftHand, kRightHand }; };
        struct Runtime { void* currentProcess = nullptr; TESForm* selectedSpells[2]{}; } runtime;
        struct Extras {
            ExtraCombatStyle* value = nullptr;
            template<class T> T* GetByType() { return value; }
        } extraList;
        TESNPC* base = nullptr;
        TESForm* equipped[2]{};
        TESNPC* GetActorBase() { return base; }
        const Runtime& GetActorRuntimeData() { return runtime; }
        TESForm* GetEquippedObject(bool left) { return equipped[left ? 0 : 1]; }
    };
}
namespace GameActions {
    struct ActorSelector { std::string name; unsigned formID = 0; bool loadedScope = false; };
    json ActorDiagnostics(const ActorSelector&);
}
struct ActorLookup { RE::Actor* actor = nullptr; unsigned matches = 0; } lookup;
ActorLookup FindActor(const GameActions::ActorSelector&) { return lookup; }
json LookupError(const GameActions::ActorSelector&, const ActorLookup&) {
    return {{"ok", false}, {"error", "synthetic lookup failure"}};
}
json ActorRef(RE::Actor* actor) {
    return {{"name", "synthetic"}, {"form_id", 0x01001234}, {"base_form_id", actor->base->id}};
}
namespace Http {
    struct Request {
        std::map<std::string, std::string> query;
        std::string Get(const std::string& key) const {
            const auto it = query.find(key);
            return it == query.end() ? "" : it->second;
        }
    };
    struct Response {
        int status;
        json body;
        static Response Ok(json value) { return { 200, value }; }
        static Response Error(int code, std::string error) {
            return { code, {{"ok", false}, {"error", error}} };
        }
    };
}
namespace GameThread {
    bool timeout = false;
    bool throws = false;
    template<class F> std::optional<json> Run(F fn) {
        if (timeout) return std::nullopt;
        if (throws) throw std::runtime_error("synthetic unexpected exception");
        return fn();
    }
}
namespace RouteHarness {
    // PARSER_BODY
    // ROUTE_BODY
}
// PRODUCER_BODY
int main() {
    const GameActions::ActorSelector request{ "", 0x01001234, false };
    RE::Actor actor;
    RE::TESNPC base;
    actor.base = &base;
    lookup = { &actor, 1 };
    json cases = json::array();
    cases.push_back(GameActions::ActorDiagnostics(request)); // unavailable process, empty CSTY
    actor.runtime.currentProcess = &actor;
    cases.push_back(GameActions::ActorDiagnostics(request)); // all empty
    RE::TESForm weapon, spell, style;
    spell.type = "SPEL";
    style.type = "CSTY";
    actor.equipped[1] = &weapon;
    actor.runtime.selectedSpells[0] = &spell;
    base.combatStyle = &style;
    RE::ExtraCombatStyle extra{ &style };
    actor.extraList.value = &extra;
    cases.push_back(GameActions::ActorDiagnostics(request)); // independent hands/spells/CSTY
    lookup.matches = 0;
    cases.push_back(GameActions::ActorDiagnostics(request));
    lookup.matches = 2;
    cases.push_back(GameActions::ActorDiagnostics(request));
    lookup.matches = 1;
    actor.base = nullptr;
    cases.push_back(GameActions::ActorDiagnostics(request));
    actor.base = &base;
    cases.push_back(GameActions::ActorDiagnostics({ "", 0x14, false }));
    weapon.id = 0;
    bool threw = false;
    try { GameActions::ActorDiagnostics(request); } catch (const std::runtime_error&) { threw = true; }
    if (!threw) return 2;
    weapon.id = 1;
    weapon.type = "invalid";
    threw = false;
    try { GameActions::ActorDiagnostics(request); } catch (const std::runtime_error&) { threw = true; }
    if (!threw) return 3;
    weapon.type = "WEAP";
    Http::Request http{ {{"schema_version", "1"}, {"form_id", "0x01001234"}} };
    if (RouteHarness::ActorDiagnostics(http).status != 200) return 4;
    for (const auto* id : { "", "0", "0x14", "100000000", "123junk", " 123" }) {
        http.query["form_id"] = id;
        if (RouteHarness::ActorDiagnostics(http).status != 400) return 5;
    }
    http.query["form_id"] = "0x01001234";
    http.query["scope"] = "all";
    if (RouteHarness::ActorDiagnostics(http).status != 400) return 6;
    http.query["scope"] = "loaded";
    http.query["schema_version"] = "2";
    if (RouteHarness::ActorDiagnostics(http).body["status"] != "unsupported") return 7;
    http.query["schema_version"] = "1";
    GameThread::timeout = true;
    if (RouteHarness::ActorDiagnostics(http).status != 503) return 8;
    GameThread::timeout = false;
    GameThread::throws = true;
    if (RouteHarness::ActorDiagnostics(http).status != 503) return 9;
    std::cout << cases.dump();
}
