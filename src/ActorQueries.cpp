#include "ActorQueries.h"

#include <algorithm>
#include <cctype>
#include <format>
#include <unordered_set>

using json = nlohmann::json;

namespace ActorQueries {
    std::string Fold(std::string_view a_text)
    {
        std::string out(a_text);
        std::transform(out.begin(), out.end(), out.begin(), [](unsigned char c) {
            return static_cast<char>(std::tolower(c));
        });
        return out;
    }

    json ActorRef(RE::Actor* a_actor)
    {
        const auto pos = a_actor->GetPosition();
        auto* cell = a_actor->GetParentCell();
        auto* world = a_actor->GetWorldspace();
        return {
            { "name", a_actor->GetDisplayFullName() ? a_actor->GetDisplayFullName() : "" },
            { "form_id", a_actor->GetFormID() },
            { "base_form_id", a_actor->GetBaseObject() ? a_actor->GetBaseObject()->GetFormID() : 0 },
            { "position", { { "x", pos.x }, { "y", pos.y }, { "z", pos.z } } },
            { "cell", cell && cell->GetFormEditorID() ? cell->GetFormEditorID() : "" },
            { "cell_form_id", cell ? cell->GetFormID() : 0 },
            { "worldspace", world && world->GetFormEditorID() ? world->GetFormEditorID() : "" },
            { "worldspace_form_id", world ? world->GetFormID() : 0 },
            { "loaded_3d", a_actor->Is3DLoaded() },
            { "disabled", a_actor->IsDisabled() },
        };
    }

    void ConsiderActor(ActorLookup& a_found, RE::Actor* a_actor, RE::PlayerCharacter* a_player,
                       const GameActions::ActorSelector& a_selector)
    {
        if (!a_actor || a_actor == a_player || a_actor->IsMarkedForDeletion()) return;
        if (a_selector.formID != 0) {
            if (a_actor->GetFormID() == a_selector.formID) {
                a_found.actor = a_actor;
                ++a_found.matches;
            }
            return;
        }
        const char* displayName = a_actor->GetDisplayFullName();
        if (displayName && Fold(displayName) == Fold(a_selector.name)) {
            a_found.actor = a_actor;
            ++a_found.matches;
        }
    }

    ActorLookup FindActor(const GameActions::ActorSelector& a_selector)
    {
        ActorLookup found;
        auto* player = RE::PlayerCharacter::GetSingleton();
        auto* cell = player ? player->GetParentCell() : nullptr;
        if (!player || !cell) return found;

        if (a_selector.formID != 0) {
            auto* actor = RE::TESForm::LookupByID<RE::Actor>(a_selector.formID);
            if (actor && (a_selector.loadedScope || actor->GetParentCell() == cell)) {
                ConsiderActor(found, actor, player, a_selector);
            }
            return found;
        }

        if (a_selector.loadedScope) {
            auto* lists = RE::ProcessLists::GetSingleton();
            if (!lists) return found;
            std::unordered_set<RE::FormID> seen;
            for (auto* process : lists->allProcesses) {
                if (!process) continue;
                for (const auto& handle : *process) {
                    auto actor = handle.get();
                    if (!actor || !seen.insert(actor->GetFormID()).second) continue;
                    ConsiderActor(found, actor.get(), player, a_selector);
                }
            }
            return found;
        }

        cell->ForEachReference([&](RE::TESObjectREFR* a_ref) {
            auto* actor = a_ref ? a_ref->As<RE::Actor>() : nullptr;
            ConsiderActor(found, actor, player, a_selector);
            return RE::BSContainer::ForEachResult::kContinue;
        });
        return found;
    }

    std::string Describe(const GameActions::ActorSelector& a_selector)
    {
        return a_selector.formID != 0
            ? std::format("form id 0x{:08X}", a_selector.formID)
            : std::format("name '{}'", a_selector.name);
    }

    json LookupError(const GameActions::ActorSelector& a_selector, const ActorLookup& a_lookup)
    {
        const auto scope = a_selector.loadedScope ? "loaded actor set" : "player's current cell";
        if (a_lookup.matches == 0) {
            return { { "ok", false },
                     { "error", std::format("no actor with {} in the {}", Describe(a_selector), scope) } };
        }
        return { { "ok", false },
                 { "error", std::format("actor {} is ambiguous ({} matches in {})",
                                        Describe(a_selector), a_lookup.matches, scope) } };
    }

}
