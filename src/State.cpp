#include "State.h"
#include "StateActors.h"

#include <algorithm>
#include <string>
#include <vector>

using json = nlohmann::json;

namespace {
    // Every name in here can be null — GetName() on a form with no FULL record
    // returns "" or nullptr depending on the type, and a null into nlohmann is
    // a JSON null, which then breaks a runner doing a string compare. Always ""
    // instead.
    std::string Str(const char* s) { return s ? s : ""; }

    json FormRef(RE::TESForm* form)
    {
        if (!form) return nullptr;
        return json{
            { "name", Str(form->GetName()) },
            { "form_id", form->GetFormID() },
            { "editor_id", Str(form->GetFormEditorID()) },
        };
    }

    json PlayerBlock(RE::PlayerCharacter* player)
    {
        const auto pos = player->GetPosition();
        auto* cell = player->GetParentCell();
        auto* world = player->GetWorldspace();
        auto* avo = player->AsActorValueOwner();

        json av = json::object();
        if (avo) {
            const auto pair = [&](const char* key, RE::ActorValue v) {
                av[key] = json{
                    { "current", avo->GetActorValue(v) },
                    { "max", avo->GetPermanentActorValue(v) },
                };
            };
            pair("health", RE::ActorValue::kHealth);
            pair("magicka", RE::ActorValue::kMagicka);
            pair("stamina", RE::ActorValue::kStamina);
            av["carry_weight"] = avo->GetActorValue(RE::ActorValue::kCarryWeight);
        }

        return json{
            { "name", Str(player->GetName()) },
            { "level", player->GetLevel() },
            { "position", { { "x", pos.x }, { "y", pos.y }, { "z", pos.z } } },
            { "angle_z", player->GetAngleZ() },
            { "cell", Str(cell ? cell->GetFormEditorID() : nullptr) },
            { "cell_form_id", cell ? cell->GetFormID() : 0 },
            { "worldspace", Str(world ? world->GetFormEditorID() : nullptr) },
            { "worldspace_form_id", world ? world->GetFormID() : 0 },
            { "interior", cell ? cell->IsInteriorCell() : false },
            { "actor_values", av },
            { "flags", {
                { "in_combat", player->IsInCombat() },
                { "sneaking", player->IsSneaking() },
                { "weapon_drawn", player->AsActorState()->IsWeaponDrawn() },
                { "dead", player->IsDead() },
            } },
            { "equipped", {
                { "right", FormRef(player->GetEquippedObject(false)) },
                { "left", FormRef(player->GetEquippedObject(true)) },
            } },
        };
    }

    json GameBlock()
    {
        json block = json::object();

        if (auto* calendar = RE::Calendar::GetSingleton()) {
            block["time"] = {
                { "hour", calendar->GetHour() },
                { "day", calendar->GetDay() },
                { "month", calendar->GetMonth() },
                { "year", calendar->GetYear() },
                { "days_passed", calendar->GetDaysPassed() },
            };
        }

        // Which menus are up matters for two things the runner needs to know:
        // whether a `handoff_user` step actually landed on the right screen, and
        // whether the game is in a state where input would go somewhere useful.
        json menus = json::array();
        if (auto* ui = RE::UI::GetSingleton()) {
            for (const auto& [name, entry] : ui->menuMap) {
                if (entry.menu && entry.menu->OnStack()) {
                    menus.push_back(std::string{ name });
                }
            }
        }
        std::sort(menus.begin(), menus.end());   // stable output — diffable between calls
        block["menus_open"] = menus;

        json dialogue = json::object();
        dialogue["active"] = false;
        dialogue["menu_open"] = false;
        dialogue["options"] = json::array();
        if (auto* ui = RE::UI::GetSingleton()) {
            dialogue["menu_open"] = ui->IsMenuOpen(RE::DialogueMenu::MENU_NAME);
        }
        if (auto* mtm = RE::MenuTopicManager::GetSingleton()) {
            if (auto* d = mtm->lastSelectedDialogue) {
                dialogue["active"] = true;
                dialogue["topic"] = Str(d->topicText.c_str());
                dialogue["quest"] = FormRef(d->parentQuest);
            }
            if (auto speaker = mtm->speaker.get()) {
                dialogue["speaker"] = Str(speaker->GetDisplayFullName());
            }
            if (mtm->dialogueList) {
                std::size_t index = 0;
                for (auto* option : *mtm->dialogueList) {
                    if (!option) continue;
                    dialogue["options"].push_back({
                        { "index", index++ },
                        { "topic_index", option->unk14 },
                        { "text", Str(option->topicText.c_str()) },
                        { "topic_form_id", option->parentTopic ? option->parentTopic->GetFormID() : 0 },
                        { "info_form_id", option->parentTopicInfo ? option->parentTopicInfo->GetFormID() : 0 },
                    });
                }
            }
        }
        block["dialogue"] = dialogue;

        return block;
    }

    json InventoryBlock(RE::PlayerCharacter* player, std::size_t limit)
    {
        json out = json::array();
        for (auto& [object, data] : player->GetInventory()) {
            if (!object || data.first <= 0) continue;
            if (out.size() >= limit) break;
            out.push_back(json{
                { "name", Str(data.second ? data.second->GetDisplayName() : object->GetName()) },
                { "form_id", object->GetFormID() },
                { "count", data.first },
                { "worn", data.second && data.second->IsWorn() },
            });
        }
        return out;
    }

    json QuestsBlock(std::size_t limit)
    {
        json out = json::array();
        auto* handler = RE::TESDataHandler::GetSingleton();
        if (!handler) return out;

        for (auto* quest : handler->GetFormArray<RE::TESQuest>()) {
            if (!quest || !quest->IsActive()) continue;
            if (out.size() >= limit) break;
            out.push_back(json{
                { "name", Str(quest->GetName()) },
                { "editor_id", Str(quest->GetFormEditorID()) },
                { "form_id", quest->GetFormID() },
                { "stage", quest->currentStage },
                { "completed", quest->IsCompleted() },
            });
        }
        return out;
    }

    // The load order the engine ended up with, which is not necessarily the one
    // written in plugins.txt — MO2's VFS, missing masters and .esl slotting can
    // all change it. `index` is the byte a FormID actually carries: 0x00-0xFD for
    // full plugins, 0xFE000-0xFEFFF for light ones.
    json PluginsBlock()
    {
        json out = json::array();
        auto* handler = RE::TESDataHandler::GetSingleton();
        if (!handler) return out;

        const auto append = [&out](const RE::TESFile* file) {
            if (!file) return;
            out.push_back(json{
                { "name", std::string{ file->GetFilename() } },
                { "index", file->GetPartialIndex() },
                { "light", file->IsLight() },
            });
        };

        // Not `compiledFileCollection` directly: on the multi-runtime (NG) build
        // that member only exists behind a runtime-specific layout, and naming it
        // doesn't compile. The accessors relocate for you.
        if (const auto* mods = handler->GetLoadedMods()) {
            for (std::size_t i = 0; i < handler->GetLoadedModCount(); ++i) append(mods[i]);
        }
        if (const auto* light = handler->GetLoadedLightMods()) {
            for (std::size_t i = 0; i < handler->GetLoadedLightModCount(); ++i) append(light[i]);
        }
        return out;
    }
}

json State::Snapshot(const Options& a_options)
{
    auto* player = RE::PlayerCharacter::GetSingleton();
    if (!player) {
        return json{ { "ok", false }, { "error", "no player" } };
    }

    json out{
        { "ok", true },
        { "player", PlayerBlock(player) },
        { "game", GameBlock() },
    };

    if (a_options.nearby) {
        out["nearby_actors"] = StateActors::Nearby(player, a_options.radius, a_options.limit);
    }
    if (a_options.cellActors) {
        out["cell_actors"] = StateActors::CurrentCell(player, a_options.limit);
    }
    if (a_options.inventory) {
        out["inventory"] = InventoryBlock(player, a_options.limit);
    }
    if (a_options.quests) {
        out["quests"] = QuestsBlock(a_options.limit);
    }
    if (a_options.plugins) {
        out["plugins"] = PluginsBlock();
    }

    return out;
}
