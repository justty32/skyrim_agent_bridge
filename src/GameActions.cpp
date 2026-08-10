#include "GameActions.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <format>
#include <unordered_set>
#include <vector>

using json = nlohmann::json;

namespace {
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

    struct ActorLookup
    {
        RE::Actor* actor{ nullptr };
        std::size_t matches{ 0 };
    };

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

    json MovePlayerNear(RE::Actor* a_actor, float a_distance)
    {
        auto* player = RE::PlayerCharacter::GetSingleton();
        if (!player) return { { "ok", false }, { "error", "no player" } };

        const bool crossedCell = player->GetParentCell() != a_actor->GetParentCell();
        if (crossedCell) player->MoveTo(a_actor);

        const auto actorPos = a_actor->GetPosition();
        const float angle = a_actor->GetAngleZ();
        const RE::NiPoint3 playerPos{
            actorPos.x - std::sin(angle) * a_distance,
            actorPos.y - std::cos(angle) * a_distance,
            actorPos.z,
        };
        player->SetPosition(playerPos, true);

        const float faceActor = std::atan2(actorPos.x - playerPos.x, actorPos.y - playerPos.y);
        player->SetAngle({ 0.0f, 0.0f, faceActor });
        return { { "ok", true }, { "actor", ActorRef(a_actor) }, { "distance", a_distance },
                 { "crossed_cell", crossedCell } };
    }

    struct DialogueLookup
    {
        RE::MenuTopicManager::Dialogue* dialogue{ nullptr };
        std::size_t optionIndex{ 0 };
        std::size_t matches{ 0 };
        std::vector<std::string> available;
    };

    DialogueLookup FindDialogue(const GameActions::DialogueSelector& a_selector)
    {
        DialogueLookup found;
        auto* manager = RE::MenuTopicManager::GetSingleton();
        if (!manager || !manager->dialogueList) return found;

        const std::string wanted = Fold(a_selector.text);
        std::size_t optionIndex = 0;
        for (auto* dialogue : *manager->dialogueList) {
            if (!dialogue) continue;
            const char* topicText = dialogue->topicText.c_str();
            const std::string text = topicText ? topicText : "";
            found.available.push_back(text);
            const std::string folded = Fold(text);
            const bool match = a_selector.index
                ? optionIndex == *a_selector.index
                : a_selector.infoFormID != 0
                    ? dialogue->parentTopicInfo && dialogue->parentTopicInfo->GetFormID() == a_selector.infoFormID
                    : (a_selector.contains && folded.contains(wanted)) ||
                      (!a_selector.contains && folded == wanted);
            if (match) {
                found.dialogue = dialogue;
                found.optionIndex = optionIndex;
                ++found.matches;
            }
            ++optionIndex;
        }
        return found;
    }
}

json GameActions::MoveToActor(const ActorSelector& a_selector, float a_distance)
{
    if (a_selector.name.empty() == (a_selector.formID == 0)) {
        return { { "ok", false }, { "error", "provide exactly one of actor name or form_id" } };
    }
    if (a_distance < 32.0f || a_distance > 2048.0f) {
        return { { "ok", false }, { "error", "distance must be between 32 and 2048" } };
    }
    const auto found = FindActor(a_selector);
    if (found.matches != 1) return LookupError(a_selector, found);
    if (found.actor->IsDisabled()) {
        return { { "ok", false }, { "error", "target actor is disabled" }, { "actor", ActorRef(found.actor) } };
    }
    return MovePlayerNear(found.actor, a_distance);
}

json GameActions::ActivateActor(const ActorSelector& a_selector)
{
    if (a_selector.name.empty() == (a_selector.formID == 0)) {
        return { { "ok", false }, { "error", "provide exactly one of actor name or form_id" } };
    }
    const auto found = FindActor(a_selector);
    if (found.matches != 1) return LookupError(a_selector, found);
    auto* player = RE::PlayerCharacter::GetSingleton();
    if (!player || found.actor->GetParentCell() != player->GetParentCell() || !found.actor->Is3DLoaded()) {
        return { { "ok", false }, { "error", "target actor must be loaded in the player's current cell; move_to it first" },
                 { "actor", ActorRef(found.actor) } };
    }

    const bool dialogueStarted = found.actor->SetDialogueWithPlayer(true, false, nullptr);
    return {
        { "ok", true },
        { "actor", ActorRef(found.actor) },
        { "dialogue_started", dialogueStarted },
    };
}

json GameActions::SelectDialogue(const DialogueSelector& a_selector)
{
    auto* ui = RE::UI::GetSingleton();
    auto* manager = RE::MenuTopicManager::GetSingleton();
    if (!ui || !ui->IsMenuOpen(RE::DialogueMenu::MENU_NAME) || !manager) {
        return { { "ok", false }, { "error", "Dialogue Menu is not open" } };
    }

    const auto selectorCount = (!a_selector.text.empty() ? 1 : 0) +
        (a_selector.index.has_value() ? 1 : 0) + (a_selector.infoFormID != 0 ? 1 : 0);
    if (selectorCount != 1) {
        return { { "ok", false }, { "error", "provide exactly one of text, index, or info_form_id" } };
    }

    const auto found = FindDialogue(a_selector);
    if (found.matches != 1 || !found.dialogue) {
        return {
            { "ok", false },
            { "error", found.matches == 0 ? "dialogue option not found" : "dialogue option is ambiguous" },
            { "match_count", found.matches },
            { "available", found.available },
        };
    }
    if (!found.dialogue->parentTopicInfo) {
        return { { "ok", false }, { "error", "selected dialogue has no topic info" } };
    }
    auto menu = ui->GetMenu<RE::DialogueMenu>();
    if (!menu || !menu->uiMovie || !menu->fxDelegate) {
        return { { "ok", false }, { "error", "Dialogue Menu is not ready" } };
    }

    const std::string selectedText = found.dialogue->topicText.c_str()
        ? found.dialogue->topicText.c_str()
        : "";
    const auto topicFormID = found.dialogue->parentTopic
        ? found.dialogue->parentTopic->GetFormID()
        : 0;
    const auto infoFormID = found.dialogue->parentTopicInfo->GetFormID();
    const auto topicIndex = found.dialogue->unk14;
    const RE::GFxValue selectArgs[]{ found.optionIndex, 1, false };
    const bool positioned = menu->uiMovie->Invoke(
        "_root.DialogueMenu_mc.TopicList.doSetSelectedIndex",
        nullptr,
        selectArgs,
        3);
    const RE::GFxValue clickArgs[]{ false };
    const bool clicked = menu->uiMovie->Invoke(
        "_root.DialogueMenu_mc.onSelectionClick",
        nullptr,
        clickArgs,
        1);
    if (!positioned || !clicked) {
        return { { "ok", false }, { "error", "Dialogue Menu rejected structured selection" } };
    }
    return {
        { "ok", true },
        { "index", found.optionIndex },
        { "text", selectedText },
        { "topic_index", topicIndex },
        { "topic_form_id", topicFormID },
        { "info_form_id", infoFormID },
    };
}

json GameActions::ReadGlobal(std::string_view a_editorID)
{
    if (a_editorID.empty()) return { { "ok", false }, { "error", "editor_id is required" } };
    auto* global = RE::TESForm::LookupByEditorID<RE::TESGlobal>(a_editorID);
    if (!global) {
        return { { "ok", false },
                 { "error", std::format("no global with editor id '{}'", a_editorID) } };
    }
    return {
        { "ok", true },
        { "editor_id", global->GetFormEditorID() ? global->GetFormEditorID() : "" },
        { "form_id", global->GetFormID() },
        { "value", global->value },
    };
}

json GameActions::CloseDialogue()
{
    auto* manager = RE::MenuTopicManager::GetSingleton();
    auto speaker = manager ? manager->speaker.get() : nullptr;
    if (!speaker) return { { "ok", false }, { "error", "dialogue has no active speaker" } };
    const bool closed = speaker->SetDialogueWithPlayer(false, false, nullptr);
    return { { "ok", closed }, { "error", closed ? "" : "Skyrim rejected dialogue close" } };
}
