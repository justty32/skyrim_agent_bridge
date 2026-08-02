#pragma once

// Route registration. Split from HttpServer so the transport stays free of any
// RE:: dependency — the socket code is testable/portable, the game reads live
// here.
namespace Routes {
    void Register();
}
