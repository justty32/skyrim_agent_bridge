#include "GameActions.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <format>
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
        return {
            { "name", a_actor->GetDisplayFullName() ? a_actor->GetDisplayFullName() : "" },
            { "form_id", a_actor->GetFormID() },
            { "base_form_id", a_actor->GetBaseObject() ? a_actor->GetBaseObject()->GetFormID() : 0 },
            { "position", { { "x", pos.x }, { "y", pos.y }, { "z", pos.z } } },
        };
    }

    struct ActorLookup
    {
        RE::Actor* actor{ nullptr };
        std::size_t matches{ 0 };
    };

    ActorLookup FindActorInPlayerCell(std::string_view a_name)
    {
        ActorLookup found;
        auto* player = RE::PlayerCharacter::GetSingleton();
        auto* cell = player ? player->GetParentCell() : nullptr;
        if (!cell) return found;

        const std::string wanted = Fold(a_name);
        cell->ForEachReference([&](RE::TESObjectREFR* a_ref) {
            auto* actor = a_ref ? a_ref->As<RE::Actor>() : nullptr;
            if (!actor || actor == player) return RE::BSContainer::ForEachResult::kContinue;
            const char* displayName = actor->GetDisplayFullName();
            if (displayName && Fold(displayName) == wanted) {
                found.actor = actor;
                ++found.matches;
            }
            return RE::BSContainer::ForEachResult::kContinue;
        });
        return found;
    }

    json LookupError(std::string_view a_name, const ActorLookup& a_lookup)
    {
        if (a_lookup.matches == 0) {
            return { { "ok", false },
                     { "error", std::format("no actor named '{}' in the player's current cell", a_name) } };
        }
        return { { "ok", false },
                 { "error", std::format("actor name '{}' is ambiguous ({} matches in current cell)",
                                        a_name, a_lookup.matches) } };
    }

    json MovePlayerNear(RE::Actor* a_actor, float a_distance)
    {
        auto* player = RE::PlayerCharacter::GetSingleton();
        if (!player) return { { "ok", false }, { "error", "no player" } };

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
        return { { "ok", true }, { "actor", ActorRef(a_actor) }, { "distance", a_distance } };
    }

    struct DialogueLookup
    {
        RE::MenuTopicManager::Dialogue* dialogue{ nullptr };
        std::size_t optionIndex{ 0 };
        std::size_t matches{ 0 };
        std::vector<std::string> available;
    };

    DialogueLookup FindDialogue(std::string_view a_text, bool a_contains)
    {
        DialogueLookup found;
        auto* manager = RE::MenuTopicManager::GetSingleton();
        if (!manager || !manager->dialogueList) return found;

        const std::string wanted = Fold(a_text);
        std::size_t optionIndex = 0;
        for (auto* dialogue : *manager->dialogueList) {
            if (!dialogue) continue;
            const char* topicText = dialogue->topicText.c_str();
            const std::string text = topicText ? topicText : "";
            found.available.push_back(text);
            const std::string folded = Fold(text);
            if ((a_contains && folded.contains(wanted)) || (!a_contains && folded == wanted)) {
                found.dialogue = dialogue;
                found.optionIndex = optionIndex;
                ++found.matches;
            }
            ++optionIndex;
        }
        return found;
    }
}

json GameActions::MoveToActor(std::string_view a_name, float a_distance)
{
    if (a_name.empty()) return { { "ok", false }, { "error", "actor name is required" } };
    if (a_distance < 32.0f || a_distance > 2048.0f) {
        return { { "ok", false }, { "error", "distance must be between 32 and 2048" } };
    }
    const auto found = FindActorInPlayerCell(a_name);
    if (found.matches != 1) return LookupError(a_name, found);
    return MovePlayerNear(found.actor, a_distance);
}

json GameActions::ActivateActor(std::string_view a_name)
{
    if (a_name.empty()) return { { "ok", false }, { "error", "actor name is required" } };
    const auto found = FindActorInPlayerCell(a_name);
    if (found.matches != 1) return LookupError(a_name, found);

    const bool dialogueStarted = found.actor->SetDialogueWithPlayer(true, false, nullptr);
    return {
        { "ok", true },
        { "actor", ActorRef(found.actor) },
        { "dialogue_started", dialogueStarted },
    };
}

json GameActions::SelectDialogue(std::string_view a_text, bool a_contains)
{
    auto* ui = RE::UI::GetSingleton();
    auto* manager = RE::MenuTopicManager::GetSingleton();
    if (!ui || !ui->IsMenuOpen(RE::DialogueMenu::MENU_NAME) || !manager) {
        return { { "ok", false }, { "error", "Dialogue Menu is not open" } };
    }

    const auto found = FindDialogue(a_text, a_contains);
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
