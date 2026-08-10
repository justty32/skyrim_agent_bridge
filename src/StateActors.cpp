#include "StateActors.h"

#include <algorithm>
#include <unordered_set>
#include <vector>

using json = nlohmann::json;

namespace {
    struct Entry
    {
        float distance;
        json value;
    };

    json ActorValue(RE::Actor* a_actor, RE::PlayerCharacter* a_player, float a_distance)
    {
        const auto pos = a_actor->GetPosition();
        return {
            { "name", a_actor->GetDisplayFullName() ? a_actor->GetDisplayFullName() : "" },
            { "form_id", a_actor->GetFormID() },
            { "base_form_id", a_actor->GetBaseObject() ? a_actor->GetBaseObject()->GetFormID() : 0 },
            { "distance", a_distance },
            { "position", { { "x", pos.x }, { "y", pos.y }, { "z", pos.z } } },
            { "level", a_actor->GetLevel() },
            { "dead", a_actor->IsDead() },
            { "in_combat", a_actor->IsInCombat() },
            { "hostile_to_player", a_actor->IsHostileToActor(a_player) },
            { "player_teammate", a_actor->IsPlayerTeammate() },
            { "cell", a_actor->GetParentCell() && a_actor->GetParentCell()->GetFormEditorID()
                ? a_actor->GetParentCell()->GetFormEditorID() : "" },
            { "cell_form_id", a_actor->GetParentCell() ? a_actor->GetParentCell()->GetFormID() : 0 },
            { "same_cell", a_actor->GetParentCell() == a_player->GetParentCell() },
            { "loaded_3d", a_actor->Is3DLoaded() },
            { "disabled", a_actor->IsDisabled() },
        };
    }

    json Finish(std::vector<Entry>& a_found, std::size_t a_limit)
    {
        std::sort(a_found.begin(), a_found.end(),
            [](const Entry& a, const Entry& b) { return a.distance < b.distance; });
        if (a_found.size() > a_limit) a_found.resize(a_limit);

        json out = json::array();
        for (auto& entry : a_found) out.push_back(std::move(entry.value));
        return out;
    }
}

json StateActors::Nearby(RE::PlayerCharacter* a_player, float a_radius, std::size_t a_limit)
{
    auto* lists = RE::ProcessLists::GetSingleton();
    if (!lists) return json::array();

    const auto origin = a_player->GetPosition();
    std::vector<Entry> found;
    for (const auto& handle : lists->highActorHandles) {
        auto actor = handle.get();
        if (!actor || actor.get() == a_player) continue;
        const float distance = origin.GetDistance(actor->GetPosition());
        if (distance <= a_radius) {
            found.push_back({ distance, ActorValue(actor.get(), a_player, distance) });
        }
    }
    return Finish(found, a_limit);
}

json StateActors::CurrentCell(RE::PlayerCharacter* a_player, std::size_t a_limit)
{
    auto* cell = a_player->GetParentCell();
    if (!cell) return json::array();

    const auto origin = a_player->GetPosition();
    std::vector<Entry> found;
    cell->ForEachReference([&](RE::TESObjectREFR* a_ref) {
        auto* actor = a_ref ? a_ref->As<RE::Actor>() : nullptr;
        if (actor && actor != a_player) {
            const float distance = origin.GetDistance(actor->GetPosition());
            found.push_back({ distance, ActorValue(actor, a_player, distance) });
        }
        return RE::BSContainer::ForEachResult::kContinue;
    });
    return Finish(found, a_limit);
}

json StateActors::Loaded(RE::PlayerCharacter* a_player, std::size_t a_limit)
{
    auto* lists = RE::ProcessLists::GetSingleton();
    if (!lists) return json::array();

    const auto origin = a_player->GetPosition();
    std::unordered_set<RE::FormID> seen;
    std::vector<Entry> found;
    for (auto* process : lists->allProcesses) {
        if (!process) continue;
        for (const auto& handle : *process) {
            auto actor = handle.get();
            if (!actor || actor.get() == a_player || !seen.insert(actor->GetFormID()).second) continue;
            const float distance = origin.GetDistance(actor->GetPosition());
            found.push_back({ distance, ActorValue(actor.get(), a_player, distance) });
        }
    }
    return Finish(found, a_limit);
}
