#include "MessageBox.h"

#include <algorithm>
#include <vector>

using json = nlohmann::json;

namespace {
    struct View
    {
        bool open{ false };
        bool ready{ false };
        std::string message;
        std::vector<std::string> buttons;
    };

    std::string ValueString(const RE::GFxValue& a_value)
    {
        if (a_value.IsString()) return a_value.GetString() ? a_value.GetString() : "";
        if (a_value.IsStringW() && a_value.GetStringW()) {
            return SKSE::stl::utf16_to_utf8(a_value.GetStringW()).value_or("");
        }
        return {};
    }

    std::string StringMember(const RE::GFxValue& a_object, const char* a_name)
    {
        RE::GFxValue value;
        if (!a_object.IsObject() || !a_object.GetMember(a_name, &value)) return {};
        if (value.IsString() || value.IsStringW()) return ValueString(value);

        RE::GFxValue text;
        if (value.IsObject() && value.GetMember("text", &text) &&
            (text.IsString() || text.IsStringW())) {
            return ValueString(text);
        }
        return {};
    }

    bool IsMessageModel(const RE::GFxValue& a_value)
    {
        if (!a_value.IsObject()) return false;
        RE::GFxValue buttons;
        return a_value.GetMember("MessageButtons", &buttons) && buttons.IsArray();
    }

    std::optional<RE::GFxValue> FindMessageModel(RE::GFxMovieView* a_movie)
    {
        RE::GFxValue root;
        if (!a_movie || !a_movie->GetVariable(&root, "_root") || !root.IsObject()) {
            return std::nullopt;
        }
        if (IsMessageModel(root)) return root;

        std::optional<RE::GFxValue> found;
        root.VisitMembers([&](const char*, const RE::GFxValue& a_value) {
            if (!found && IsMessageModel(a_value)) found = a_value;
        });
        return found;
    }

    View ReadView()
    {
        View out;
        auto* ui = RE::UI::GetSingleton();
        out.open = ui && ui->IsMenuOpen(RE::MessageBoxMenu::MENU_NAME);
        if (!out.open) return out;

        auto menu = ui->GetMenu<RE::MessageBoxMenu>();
        auto model = menu && menu->uiMovie ? FindMessageModel(menu->uiMovie.get()) : std::nullopt;
        if (!model) return out;

        out.message = StringMember(*model, "Message");
        if (out.message.empty()) out.message = StringMember(*model, "MessageText");

        RE::GFxValue buttons;
        if (!model->GetMember("MessageButtons", &buttons) || !buttons.IsArray()) return out;
        for (std::uint32_t index = 0; index < buttons.GetArraySize(); ++index) {
            RE::GFxValue button;
            if (!buttons.GetElement(index, &button) || !button.IsObject()) return out;
            auto text = StringMember(button, "ButtonText");
            if (text.empty()) text = StringMember(button, "label");
            out.buttons.push_back(std::move(text));
        }
        out.ready = !out.message.empty() && !out.buttons.empty() &&
            std::ranges::all_of(out.buttons, [](const auto& a_text) { return !a_text.empty(); });
        return out;
    }

    json ToJson(const View& a_view)
    {
        json buttons = json::array();
        for (std::size_t index = 0; index < a_view.buttons.size(); ++index) {
            buttons.push_back({ { "index", index }, { "text", a_view.buttons[index] } });
        }
        return {
            { "open", a_view.open },
            { "ready", a_view.ready },
            { "message", a_view.message },
            { "buttons", std::move(buttons) },
        };
    }
}

json StructuredMessageBox::Snapshot()
{
    return ToJson(ReadView());
}

json StructuredMessageBox::Select(const Selector& a_selector)
{
    const auto selectorCount = (!a_selector.text.empty() ? 1 : 0) +
        (a_selector.index.has_value() ? 1 : 0);
    if (selectorCount != 1) {
        return { { "ok", false }, { "error", "provide exactly one of text or index" } };
    }

    const auto view = ReadView();
    if (!view.open) return { { "ok", false }, { "error", "MessageBoxMenu is not open" } };
    if (!view.ready) {
        return { { "ok", false }, { "error", "MessageBoxMenu structure is not ready" },
                 { "message_box", ToJson(view) } };
    }
    if (a_selector.expectedMessage && view.message != *a_selector.expectedMessage) {
        return { { "ok", false }, { "error", "message box text does not match" },
                 { "expected_message", *a_selector.expectedMessage },
                 { "actual_message", view.message } };
    }

    std::size_t selected = 0;
    if (a_selector.index) {
        selected = *a_selector.index;
        if (selected >= view.buttons.size()) {
            return { { "ok", false }, { "error", "message box button index is out of range" },
                     { "available", view.buttons } };
        }
    } else {
        std::size_t matches = 0;
        for (std::size_t index = 0; index < view.buttons.size(); ++index) {
            if (view.buttons[index] == a_selector.text) {
                selected = index;
                ++matches;
            }
        }
        if (matches != 1) {
            return { { "ok", false },
                     { "error", matches == 0 ? "message box button not found" :
                                               "message box button is ambiguous" },
                     { "match_count", matches }, { "available", view.buttons } };
        }
    }

    auto* ui = RE::UI::GetSingleton();
    auto menu = ui ? ui->GetMenu<RE::MessageBoxMenu>() : nullptr;
    if (!menu || !menu->uiMovie || !menu->fxDelegate) {
        return { { "ok", false }, { "error", "MessageBoxMenu is not ready" } };
    }
    if (!menu->fxDelegate->callbacks.GetAlt("buttonPress")) {
        return { { "ok", false }, { "error", "MessageBoxMenu has no buttonPress callback" } };
    }

    // GameDelegate.call prepends a response ID before the actual parameters.
    // MessageBoxMenu's callback therefore reads the button index from args[0]
    // after FxDelegate consumes this first value as the response ID.
    const RE::GFxValue args[]{ 0.0, static_cast<double>(selected) };
    menu->fxDelegate->Callback(menu->uiMovie.get(), "buttonPress", args, 2);
    return { { "ok", true }, { "message", view.message }, { "index", selected },
             { "text", view.buttons[selected] } };
}
