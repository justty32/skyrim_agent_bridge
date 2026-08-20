#include "State.h"
#include "StateActors.h"
#include "MessageBox.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <string>
#include <vector>

using json = nlohmann::json;

namespace {
    std::atomic<std::uint64_t> g_loadEpoch{ 0 };

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

    json Point3(const RE::NiPoint3& point)
    {
        return { { "x", point.x }, { "y", point.y }, { "z", point.z } };
    }

    json Vector4(const RE::hkVector4& vector)
    {
        alignas(16) float values[4];
        _mm_store_ps(values, vector.quad);
        return {
            { "x", values[0] }, { "y", values[1] },
            { "z", values[2] }, { "w", values[3] },
        };
    }

    json Bound(const RE::BSBound& bound)
    {
        return {
            { "center", Point3(bound.center) },
            { "extents", Point3(bound.extents) },
        };
    }

    json ShapeBlock(const RE::hkpShape* shape, const RE::hkTransform& transform,
        std::size_t depth = 0)
    {
        if (!shape) return nullptr;
        RE::hkAabb aabb;
        shape->GetAabbImpl(transform, 0.0F, aabb);
        json block{
            { "type", static_cast<std::underlying_type_t<RE::hkpShapeType>>(shape->type) },
            { "world_aabb_havok_metres", {
                { "min", Vector4(aabb.min) },
                { "max", Vector4(aabb.max) },
            } },
        };
        if (depth >= 4) {
            block["truncated"] = true;
            return block;
        }
        if (shape->type == RE::hkpShapeType::kCapsule) {
            const auto* capsule = static_cast<const RE::hkpCapsuleShape*>(shape);
            const auto a = Vector4(capsule->vertexA);
            const auto b = Vector4(capsule->vertexB);
            const auto dx = b["x"].get<float>() - a["x"].get<float>();
            const auto dy = b["y"].get<float>() - a["y"].get<float>();
            const auto dz = b["z"].get<float>() - a["z"].get<float>();
            const auto axisLength = std::sqrt(dx * dx + dy * dy + dz * dz);
            block.update({
                { "kind", "capsule" },
                { "radius_havok_metres", capsule->radius },
                { "vertex_a_havok_metres", a },
                { "vertex_b_havok_metres", b },
                { "axis_length_havok_metres", axisLength },
                { "total_length_havok_metres", axisLength + 2.0F * capsule->radius },
            });
        } else if (shape->type == RE::hkpShapeType::kList) {
            const auto* list = static_cast<const RE::hkpListShape*>(shape);
            block["kind"] = "list";
            block["children"] = json::array();
            for (const auto& child : list->childInfo) {
                block["children"].push_back(ShapeBlock(child.shape, transform, depth + 1));
            }
        }
        return block;
    }

    json PlayerCollisionBlock(RE::PlayerCharacter* player)
    {
        auto* controller = player->GetCharController();
        if (!controller) return nullptr;

        json block{
            { "controller_type", "unknown" },
            { "collision_bound_skyrim_units", Bound(controller->collisionBound) },
            { "bumper_bound_skyrim_units", Bound(controller->bumperCollisionBound) },
            { "center", controller->center },
            { "scale", controller->scale },
            { "actor_height", controller->actorHeight },
            { "active_shape", nullptr },
            { "proxy", nullptr },
        };
        RE::hkVector4 rawPosition;
        RE::hkVector4 centeredPosition;
        controller->GetPosition(rawPosition, false);
        controller->GetPosition(centeredPosition, true);
        block["position_havok_metres"] = {
            { "raw", Vector4(rawPosition) },
            { "with_center_offset", Vector4(centeredPosition) },
        };

        auto* proxyController = skyrim_cast<RE::bhkCharProxyController*>(controller);
        if (!proxyController) return block;
        block["controller_type"] = "bhkCharProxyController";

        auto* proxy = proxyController->GetCharacterProxy();
        if (!proxy) return block;
        block["proxy"] = {
            { "keep_distance_havok_metres", proxy->keepDistance },
            { "keep_contact_tolerance_havok_metres", proxy->keepContactTolerance },
        };

        auto* phantom = proxy->shapePhantom;
        const auto* shape = phantom ? phantom->GetShape() : nullptr;
        if (!shape) return block;
        block["proxy"]["phantom_translation_havok_metres"] =
            Vector4(phantom->motionState.transform.translation);

        block["active_shape"] = ShapeBlock(shape, phantom->motionState.transform);
        return block;
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
            { "collision", PlayerCollisionBlock(player) },
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
        block["load_epoch"] = g_loadEpoch.load(std::memory_order_relaxed);

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
        block["message_box"] = StructuredMessageBox::Snapshot();

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

std::uint64_t State::NotifySaveLoaded() noexcept
{
    return g_loadEpoch.fetch_add(1, std::memory_order_relaxed) + 1;
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
    if (a_options.loadedActors) {
        out["loaded_actors"] = StateActors::Loaded(player, a_options.limit);
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
