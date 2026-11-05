#include "InputRoute.h"
#include "Input.h"
#include <limits>
namespace InputRoute {
Http::Response Handle(const Http::Request& request) {
    std::uint32_t scan{};
    try {
        if (!request.query.empty()) return Http::Response::Error(400, "input takes no query parameters");
        const auto body = nlohmann::json::parse(request.body);
        if (!body.is_object() || body.size() != 3 || !body.contains("schema") || !body.contains("scan_code") ||
            !body.contains("action") || !body["schema"].is_number_integer() || body["schema"] != 1 ||
            !body["action"].is_string() || body["action"] != "tap" || !body["scan_code"].is_number_integer()) {
            return Http::Response::Error(400, "expected schema 1 keyboard tap with integer scan_code");
        }
        const auto value = body["scan_code"].get<std::int64_t>();
        if (value < 1 || value > 254) return Http::Response::Error(400, "scan_code must be 1..254; 255 is invalid");
        scan = static_cast<std::uint32_t>(value);
    } catch (const std::exception&) {
        return Http::Response::Error(400, "invalid input JSON body");
    }
    const auto result = Input::Tap(scan);
    const int status = result.status == "completed" ? 200 :
        (result.reason == "busy" ? 409 : (result.status == "rejected" && result.reason != "unavailable" ? 400 : 503));
    return Http::Response{status, {{"schema", 1}, {"request_id", result.requestId}, {"state", result.status},
        {"reason", result.reason}, {"down_dispatched", result.downQueued}, {"up_dispatched", result.upQueued}}};
}
}
