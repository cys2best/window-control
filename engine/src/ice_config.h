#pragma once
#include <rtc/rtc.hpp>
#include <string>
#include <vector>

struct IceServerSpec {
    std::vector<std::string> urls;
    std::string username;
    std::string credential;
};

rtc::Configuration BuildIceConfiguration(const std::vector<IceServerSpec>& specs);
