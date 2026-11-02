#include "GameActions.h"
#include "ActorQueries.h"

using json = nlohmann::json;
using ActorQueries::ActorRef;
using ActorQueries::FindActor;
using ActorQueries::LookupError;

json GameActions::ActorDiagnostics(const ActorSelector& a_selector)
{
    if (a_selector.formID == 0 || a_selector.formID == 0x14 || !a_selector.name.empty()) {
        return { { "ok", false }, { "status", "query_failed" }, { "error", "a single NPC reference form_id is required" } };
    }
    const auto found = FindActor(a_selector);
    if (found.matches != 1) {
        auto error = LookupError(a_selector, found);
        error["status"] = "query_failed";
        return error;
    }
    auto* actor = found.actor;
    auto* base = actor->GetActorBase();
    if (!base || base->GetFormID() == 0) {
        return { { "ok", false }, { "status", "query_failed" }, { "error", "actor has no NPC base" } };
    }
    const auto reading = [](RE::TESForm* form) -> json {
        if (!form) return { { "status", "empty" }, { "value", nullptr } };
        if (form->GetFormID() == 0) throw std::runtime_error("diagnostic form has zero form_id");
        const auto signature = RE::FormTypeToString(form->GetFormType());
        if (signature.size() != 4) throw std::runtime_error("diagnostic form has invalid record signature");
        return { { "status", "value" }, { "value", {
            { "name", form->GetName() ? form->GetName() : "" },
            { "editor_id", form->GetFormEditorID() ? form->GetFormEditorID() : "" },
            { "form_id", form->GetFormID() }, { "form_type", std::string(signature) },
        } } };
    };
    const json unavailable{ { "status", "unavailable" }, { "reason", "actor_process_unavailable" } };
    const auto& runtime = actor->GetActorRuntimeData();
    json equipped{ { "right", unavailable }, { "left", unavailable } };
    json spells = equipped;
    if (runtime.currentProcess) {
        equipped["right"] = reading(actor->GetEquippedObject(false));
        equipped["left"] = reading(actor->GetEquippedObject(true));
        spells["right"] = reading(runtime.selectedSpells[RE::Actor::SlotTypes::kRightHand]);
        spells["left"] = reading(runtime.selectedSpells[RE::Actor::SlotTypes::kLeftHand]);
    }
    const auto* extra = actor->extraList.GetByType<RE::ExtraCombatStyle>();
    return { { "ok", true }, { "schema_version", 1 }, { "actor", ActorRef(actor) },
             { "equipped", equipped }, { "selected_spells", spells },
             { "combat_style", { { "base", reading(base->combatStyle) },
                                  { "reference_override", reading(extra ? extra->combatStyle : nullptr) } } } };
}
