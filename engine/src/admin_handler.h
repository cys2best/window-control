#pragma once
#include "scrcpy_source.h"
#include <httplib.h>

class AdminHandler {
public:
    explicit AdminHandler(ScrcpySource& source);
    void RegisterRoutes(httplib::Server& server);

private:
    ScrcpySource& source_;
};
