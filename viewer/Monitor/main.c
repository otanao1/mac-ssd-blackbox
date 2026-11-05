/* Dedicated embedded interpreter: no generic Python execution or user imports. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <mach-o/dyld.h>
#include <Security/Security.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    /* Validate sealed Python/resources before importing any application code. */
    SecCodeRef self = NULL;
    SecStaticCodeRef code = NULL;
    OSStatus valid = SecCodeCopySelf(kSecCSDefaultFlags, &self);
    if (valid == errSecSuccess) valid = SecCodeCopyStaticCode(self, kSecCSDefaultFlags, &code);
    if (valid == errSecSuccess) valid = SecStaticCodeCheckValidity(code,
        kSecCSStrictValidate | kSecCSCheckNestedCode | kSecCSCheckAllArchitectures, NULL);
    if (code) CFRelease(code);
    if (self) CFRelease(self);
    if (valid != errSecSuccess) { fputs("Monitor signature validation failed\n", stderr); return 78; }
    char raw[PATH_MAX], executable[PATH_MAX], contents[PATH_MAX];
    uint32_t size = sizeof(raw);
    if (_NSGetExecutablePath(raw, &size) || !realpath(raw, executable)) return 70;
    snprintf(contents, sizeof(contents), "%s", executable);
    for (int i = 0; i < 2; i++) {
        char *slash = strrchr(contents, '/');
        if (!slash) return 70;
        *slash = 0;
    }
    /* Existing subprocess worker argv is Python-compatible; nothing else is. */
    if (argc > 1 && !strcmp(argv[1], "-m")) {
        if (argc < 3 || strcmp(argv[2], "blackbox")) return 64;
        argc -= 2; argv += 2; argv[0] = executable;
    }
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.parse_argv = 0;
    config.site_import = 0;
    config.write_bytecode = 0;
    config.module_search_paths_set = 1;
    PyStatus status = PyConfig_SetBytesArgv(&config, argc, argv);
    if (PyStatus_Exception(status)) goto fail;
    char home[PATH_MAX];
    if (snprintf(home, sizeof(home), "%s/Resources/Python", contents) >= sizeof(home)) return 70;
    status = PyConfig_SetBytesString(&config, &config.home, home);
    if (PyStatus_Exception(status)) goto fail;
    status = PyConfig_SetBytesString(&config, &config.executable, executable);
    if (PyStatus_Exception(status)) goto fail;
    status = PyConfig_SetString(&config, &config.run_module, L"blackbox");
    if (PyStatus_Exception(status)) goto fail;
    const char *suffixes[] = {"/Resources/Python/lib/python3.11",
        "/Frameworks/PythonModules", "/Resources/runtime", NULL};
    for (int i = 0; suffixes[i]; i++) {
        char path[PATH_MAX];
        if (snprintf(path, sizeof(path), "%s%s", contents, suffixes[i]) >= sizeof(path)) return 70;
        wchar_t *wide = Py_DecodeLocale(path, NULL);
        if (!wide) return 70;
        status = PyWideStringList_Append(&config.module_search_paths, wide);
        PyMem_RawFree(wide);
        if (PyStatus_Exception(status)) goto fail;
    }
    status = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(status)) goto fail;
    PyConfig_Clear(&config);
    return Py_RunMain();
fail:
    PyConfig_Clear(&config);
    Py_ExitStatusException(status);
    return 70;
}
