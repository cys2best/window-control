#include "engine_config.h"
#include <sstream>

std::vector<std::string> ParseCommaSeparatedList(const std::string& csv) {
    std::vector<std::string> result;
    std::stringstream ss(csv);
    std::string item;
    while (std::getline(ss, item, ',')) {
        if (!item.empty()) result.push_back(item);
    }
    return result;
}
