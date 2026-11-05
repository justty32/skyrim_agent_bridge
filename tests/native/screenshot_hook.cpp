#include "ScreenshotHookCore.h"
#include <iostream>
#include <stdexcept>
#include <vector>
namespace {
std::vector<int> order;
std::int64_t Game(std::int64_t argument) { order.push_back(1); return argument ^ 0x123456789abcdefLL; }
void Capture() noexcept { order.push_back(2); }
void OtherCapture() noexcept { order.push_back(3); }
std::int64_t Other(std::int64_t argument) { return Screenshot::Hook::Invoke(Game, argument, OtherCapture); }
std::int64_t Ours(std::int64_t argument) { return Screenshot::Hook::Invoke(Game, argument, Capture); }
void Check(bool condition) { if (!condition) throw std::runtime_error("hook chain check failed"); }
}
int main(int argc, char**) {
    try {
        const auto expected = std::int64_t(77) ^ 0x123456789abcdefLL;
        Check(Screenshot::Hook::Invoke(Other,77,Capture)==expected);
        if (argc > 1) order.push_back(1);
        Check(order == std::vector<int>({1,3,2}));
        order.clear(); Check(Screenshot::Hook::Invoke(Ours,77,OtherCapture)==expected);
        Check(order == std::vector<int>({1,2,3}));
        Check(Screenshot::Hook::CallTarget(100,std::vector<std::uint8_t>{0xe8,5,0,0,0}) == 110);
        Check(Screenshot::Hook::CallTarget(100,std::vector<std::uint8_t>{0xe8,0xfb,0xff,0xff,0xff}) == 100);
        Check(!Screenshot::Hook::CallTarget(100,std::vector<std::uint8_t>{0xff,0x15,0,0,0}));
        Check(!Screenshot::Hook::CallTarget(100,std::vector<std::uint8_t>{0xe8,0}));
        std::cout << "screenshot hook predecessor exactly once/result/order/E8 guards: PASS\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
