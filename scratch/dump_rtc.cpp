#include <rtc/rtc.hpp>
#include <iostream>
int main() {
    rtc::Configuration c;
    rtc::IceServer is("stun:stun.l.google.com:19302");
    rtc::IceServer is2("turn:host", "user", "pass");
}
