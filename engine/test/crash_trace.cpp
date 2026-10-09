// Prints the faulting stack when a native test dies from an access violation.
// GoogleTest only guards the test thread; a fault on a library or engine
// thread otherwise ends the process with no record of where it happened.
#include <windows.h>
#include <dbghelp.h>
#include <cstdio>

namespace {

LONG CALLBACK TraceAccessViolation(EXCEPTION_POINTERS* info) {
    const EXCEPTION_RECORD* record = info->ExceptionRecord;
    if (record->ExceptionCode != EXCEPTION_ACCESS_VIOLATION) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    static LONG reported = 0;
    if (InterlockedExchange(&reported, 1) != 0) return EXCEPTION_CONTINUE_SEARCH;

    const HANDLE process = GetCurrentProcess();
    SymSetOptions(SYMOPT_UNDNAME | SYMOPT_DEFERRED_LOADS | SYMOPT_LOAD_LINES);
    SymInitialize(process, nullptr, TRUE);

    const ULONG_PTR operation = record->NumberParameters > 0 ? record->ExceptionInformation[0] : 0;
    const ULONG_PTR target = record->NumberParameters > 1 ? record->ExceptionInformation[1] : 0;
    std::fprintf(stderr, "[crash] access violation: thread=%lu %s address=0x%llx\n",
                 GetCurrentThreadId(),
                 operation == 0 ? "read" : operation == 1 ? "write" : "execute",
                 static_cast<unsigned long long>(target));

    CONTEXT context = *info->ContextRecord;
    STACKFRAME64 frame{};
    frame.AddrPC.Offset = context.Rip;
    frame.AddrPC.Mode = AddrModeFlat;
    frame.AddrFrame.Offset = context.Rbp;
    frame.AddrFrame.Mode = AddrModeFlat;
    frame.AddrStack.Offset = context.Rsp;
    frame.AddrStack.Mode = AddrModeFlat;

    for (int depth = 0; depth < 64; ++depth) {
        if (!StackWalk64(IMAGE_FILE_MACHINE_AMD64, process, GetCurrentThread(), &frame, &context,
                         nullptr, SymFunctionTableAccess64, SymGetModuleBase64, nullptr)) break;
        const DWORD64 pc = frame.AddrPC.Offset;
        if (pc == 0) break;

        char module[MAX_PATH] = "?";
        const DWORD64 base = SymGetModuleBase64(process, pc);
        if (base != 0) GetModuleFileNameA(reinterpret_cast<HMODULE>(base), module, MAX_PATH);

        char buffer[sizeof(SYMBOL_INFO) + 512] = {};
        auto* symbol = reinterpret_cast<SYMBOL_INFO*>(buffer);
        symbol->SizeOfStruct = sizeof(SYMBOL_INFO);
        symbol->MaxNameLen = 511;
        DWORD64 displacement = 0;
        const bool named = SymFromAddr(process, pc, &displacement, symbol) != FALSE;
        std::fprintf(stderr, "[crash] #%02d %s+0x%llx %s+0x%llx\n", depth, module,
                     static_cast<unsigned long long>(pc - base), named ? symbol->Name : "?",
                     static_cast<unsigned long long>(displacement));
    }
    std::fflush(stderr);
    return EXCEPTION_CONTINUE_SEARCH;
}

const PVOID installed = AddVectoredExceptionHandler(1, TraceAccessViolation);

} // namespace
