/* Certificate-free identity adapter, loaded only by the copied CUA service
 * and its Apple Event client. A local marker satisfies their existing team
 * allowlists only after kernel identity, executable path, and signature checks.
 * This does not create an Apple team or change TCC / application approvals. */
#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <bsm/libbsm.h>
#include <dlfcn.h>
#include <libproc.h>
#include <limits.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef ROUTER_APP_NAME
#error ROUTER_APP_NAME must name the sibling desktop bundle
#endif
#ifndef ROUTER_OWNER_UID
#error ROUTER_OWNER_UID must identify the installing user
#endif
#define LOCAL_TEAM "CDXMUX0000"
#define LIBRARY_SUFFIX "/Contents/Frameworks/codex-mux-local-auth.dylib"

/* Retained keys prevent pointer reuse. Bounded eviction can deny an in-flight
 * request, but can never authorize a different code object. */
static struct { CFTypeRef code; pid_t pid; CFDataRef hash; } peers[256];
static size_t next_peer;
static pthread_mutex_t peer_lock = PTHREAD_MUTEX_INITIALIZER;

static bool owner_process(pid_t pid) {
    struct proc_bsdinfo info;
    return getuid() == ROUTER_OWNER_UID && geteuid() == ROUTER_OWNER_UID &&
        proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, sizeof(info)) == sizeof(info) &&
        info.pbi_uid == ROUTER_OWNER_UID && info.pbi_ruid == ROUTER_OWNER_UID;
}

static bool executable_allowed(CFDictionaryRef info, pid_t pid) {
    CFURLRef url = CFDictionaryGetValue(info, kSecCodeInfoMainExecutable);
    char path[PATH_MAX], executable[PATH_MAX], running[PATH_MAX], library[PATH_MAX];
    if (!url || CFGetTypeID(url) != CFURLGetTypeID() ||
        !CFURLGetFileSystemRepresentation(url, true, (UInt8 *)path, sizeof(path)) ||
        !realpath(path, executable) ||
        proc_pidpath(pid, path, sizeof(path)) <= 0 ||
        !realpath(path, running) || strcmp(executable, running) != 0) return false;
    Dl_info image;
    if (!dladdr((void *)&executable_allowed, &image) ||
        !realpath(image.dli_fname, library)) return false;
    size_t length = strlen(library), suffix = strlen(LIBRARY_SUFFIX);
    if (length <= suffix || strcmp(library + length - suffix, LIBRARY_SUFFIX)) return false;
    library[length - suffix] = '\0';
    /* The helper can be beside the desktop app or embedded inside it. */
    char desktop[PATH_MAX];
    if (snprintf(desktop, sizeof(desktop), "%s", library) >= (int)sizeof(desktop)) return false;
    char *embedded = strstr(desktop, "/Contents/Resources/cua_node/");
    if (embedded) *embedded = '\0';
    else {
        char *slash = strrchr(desktop, '/');
        if (!slash) return false;
        *slash = '\0';
        size_t used = strlen(desktop);
        if (snprintf(desktop + used, sizeof(desktop) - used, "/%s", ROUTER_APP_NAME) >=
            (int)(sizeof(desktop) - used)) return false;
    }
    const char *desktop_paths[] = {
        "Contents/MacOS/ChatGPT",
        "Contents/Resources/cua_node/bin/node",
        "Contents/Resources/cua_node/bin/node_repl",
        "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
        "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex.real",
    };
    const char *helper_paths[] = {
        "Contents/MacOS/SkyComputerUseService",
        "Contents/SharedSupport/SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient",
    };
    for (size_t group = 0; group < 2; group++) {
        const char *root = group ? library : desktop;
        struct stat attributes;
        if (stat(root, &attributes) || attributes.st_uid != ROUTER_OWNER_UID ||
            (attributes.st_mode & 0022)) continue;
        const char **paths = group ? helper_paths : desktop_paths;
        size_t count = group ? sizeof(helper_paths) / sizeof(*helper_paths) :
            sizeof(desktop_paths) / sizeof(*desktop_paths);
        for (size_t i = 0; i < count; i++) {
            char expected[PATH_MAX], canonical[PATH_MAX];
            if (snprintf(expected, sizeof(expected), "%s/%s", root, paths[i]) >= (int)sizeof(expected)) continue;
            if (realpath(expected, canonical) && !strcmp(executable, canonical) &&
                !stat(executable, &attributes) && attributes.st_uid == ROUTER_OWNER_UID &&
                !(attributes.st_mode & 0022)) return true;
        }
    }
    return false;
}

static void remember(CFTypeRef code, pid_t pid, CFDataRef hash) {
    pthread_mutex_lock(&peer_lock);
    size_t index = next_peer++ % (sizeof(peers) / sizeof(*peers));
    if (peers[index].code) CFRelease(peers[index].code);
    if (peers[index].hash) CFRelease(peers[index].hash);
    peers[index].code = CFRetain(code);
    peers[index].pid = pid;
    peers[index].hash = CFRetain(hash);
    pthread_mutex_unlock(&peer_lock);
}

static CFDataRef lookup(CFTypeRef code, pid_t *pid) {
    CFDataRef hash = NULL;
    pthread_mutex_lock(&peer_lock);
    for (size_t i = 0; i < sizeof(peers) / sizeof(*peers); i++) {
        if (peers[i].code == code) {
            *pid = peers[i].pid;
            hash = CFRetain(peers[i].hash);
            break;
        }
    }
    pthread_mutex_unlock(&peer_lock);
    return hash;
}

static void identify(SecCodeRef code, pid_t pid) {
    if (!owner_process(pid) || SecCodeCheckValidity(code, kSecCSDefaultFlags, NULL)) return;
    CFDictionaryRef info = NULL;
    if (SecCodeCopySigningInformation(code, kSecCSSigningInformation, &info) || !info) return;
    CFDataRef hash = CFDictionaryGetValue(info, kSecCodeInfoUnique);
    if (hash && CFGetTypeID(hash) == CFDataGetTypeID() && executable_allowed(info, pid))
        remember(code, pid, hash);
    CFRelease(info);
}

static OSStatus local_guest(SecCodeRef host, CFDictionaryRef attrs,
                            SecCSFlags flags, SecCodeRef *code) {
    OSStatus status = SecCodeCopyGuestWithAttributes(host, attrs, flags, code);
    if (status || !code || !*code || !attrs) return status;
    pid_t pid = 0;
    CFDataRef audit = CFDictionaryGetValue(attrs, kSecGuestAttributeAudit);
    if (audit && CFGetTypeID(audit) == CFDataGetTypeID() && CFDataGetLength(audit) == sizeof(audit_token_t)) {
        audit_token_t token;
        memcpy(&token, CFDataGetBytePtr(audit), sizeof(token));
        if (audit_token_to_euid(token) != ROUTER_OWNER_UID ||
            audit_token_to_ruid(token) != ROUTER_OWNER_UID) return status;
        pid = audit_token_to_pid(token);
    } else if (!audit) {
        CFNumberRef number = CFDictionaryGetValue(attrs, kSecGuestAttributePid);
        if (number && CFGetTypeID(number) == CFNumberGetTypeID())
            CFNumberGetValue(number, kCFNumberIntType, &pid);
    }
    if (pid > 0) identify(*code, pid);
    return status;
}

static OSStatus local_self(SecCSFlags flags, SecCodeRef *code) {
    OSStatus status = SecCodeCopySelf(flags, code);
    if (!status && code && *code) identify(*code, getpid());
    return status;
}

static OSStatus local_static(SecCodeRef code, SecCSFlags flags, SecStaticCodeRef *out) {
    OSStatus status = SecCodeCopyStaticCode(code, flags, out);
    pid_t pid = 0;
    CFDataRef hash = lookup(code, &pid);
    if (!status && out && *out && hash && owner_process(pid)) remember(*out, pid, hash);
    if (hash) CFRelease(hash);
    return status;
}

static OSStatus local_information(SecStaticCodeRef code, SecCSFlags flags, CFDictionaryRef *out) {
    OSStatus status = SecCodeCopySigningInformation(code, flags, out);
    if (status || !out || !*out || CFDictionaryContainsKey(*out, kSecCodeInfoTeamIdentifier)) return status;
    pid_t pid = 0;
    CFDataRef known = lookup(code, &pid);
    CFDataRef actual = CFDictionaryGetValue(*out, kSecCodeInfoUnique);
    if (known && actual && CFEqual(known, actual) && owner_process(pid) && executable_allowed(*out, pid)) {
        CFMutableDictionaryRef local = CFDictionaryCreateMutableCopy(NULL, 0, *out);
        CFDictionarySetValue(local, kSecCodeInfoTeamIdentifier, CFSTR(LOCAL_TEAM));
        /* Electron keeps the upstream executable signing identifier while
         * its copied bundle and the native production allowlist use ours. */
        CFURLRef url = CFDictionaryGetValue(local, kSecCodeInfoMainExecutable);
        char path[PATH_MAX];
        const char *desktop_suffix = "/Contents/MacOS/ChatGPT";
        if (CFURLGetFileSystemRepresentation(url, true, (UInt8 *)path, sizeof(path)) &&
            strlen(path) >= strlen(desktop_suffix) &&
            !strcmp(path + strlen(path) - strlen(desktop_suffix), desktop_suffix))
            CFDictionarySetValue(local, kSecCodeInfoIdentifier, CFSTR("app.cdxmux.multi"));
        CFRelease(*out);
        *out = local;
    }
    if (known) CFRelease(known);
    return status;
}

#define INTERPOSE(replacement, original) \
    __attribute__((used, section("__DATA,__interpose"))) static const struct { \
        const void *replace; const void *original; \
    } interpose_##replacement = { (const void *)&replacement, (const void *)&original }
INTERPOSE(local_guest, SecCodeCopyGuestWithAttributes);
INTERPOSE(local_self, SecCodeCopySelf);
INTERPOSE(local_static, SecCodeCopyStaticCode);
INTERPOSE(local_information, SecCodeCopySigningInformation);
