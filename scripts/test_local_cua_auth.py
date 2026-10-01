"""Native authentication boundaries and fail-closed Mach-O installation."""
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import tempfile
import unittest

import patch_app


class LibraryInstallTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.executable = Path(self.scratch.name) / "service"
        data = bytearray(8192)
        struct.pack_into("<8I", data, 0, 0xFEEDFACF, 0x100000C, 0, 2, 1, 152, 0, 0)
        struct.pack_into("<II", data, 32, 0x19, 152)
        struct.pack_into("<I", data, 32 + 64, 1)
        struct.pack_into("<I", data, 32 + 72 + 48, 4096)
        data[4096:] = b"x" * 4096
        self.executable.write_bytes(data)

    def test_adds_required_library_without_moving_sections(self):
        original = self.executable.read_bytes()
        patch_app.add_local_auth_library(self.executable, "@executable_path/auth.dylib")
        data = self.executable.read_bytes()
        self.assertEqual(len(data), len(original))
        self.assertEqual(data[4096:], original[4096:])
        self.assertEqual(struct.unpack_from("<I", data, 16)[0], 2)
        self.assertEqual(struct.unpack_from("<I", data, 184)[0], 0xC)
        with self.assertRaisesRegex(RuntimeError, "already patched"):
            patch_app.add_local_auth_library(self.executable, "@executable_path/auth.dylib")

    def test_nonzero_padding_fails_without_writing(self):
        data = bytearray(self.executable.read_bytes())
        data[184] = 1
        self.executable.write_bytes(data)
        with self.assertRaisesRegex(RuntimeError, "header padding"):
            patch_app.add_local_auth_library(self.executable, "@executable_path/auth.dylib")
        self.assertEqual(self.executable.read_bytes(), data)

    def test_truncated_commands_fail_without_writing(self):
        data = bytearray(self.executable.read_bytes())
        struct.pack_into("<I", data, 36, 144)
        self.executable.write_bytes(data)
        with self.assertRaisesRegex(RuntimeError, "segment size"):
            patch_app.add_local_auth_library(self.executable, "@executable_path/auth.dylib")
        self.assertEqual(self.executable.read_bytes(), data)


PROBE = r'''
#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <bsm/libbsm.h>
#include <mach/mach.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    SecCodeRef guest = NULL; SecStaticCodeRef code = NULL;
    CFDictionaryRef info = NULL; OSStatus status = 0;
    if (argc > 2 && !strcmp(argv[1], "disk")) {
        CFURLRef url = CFURLCreateFromFileSystemRepresentation(NULL,
            (const UInt8 *)argv[2], strlen(argv[2]), false);
        status = SecStaticCodeCreateWithPath(url, 0, &code); CFRelease(url);
    } else if (argc > 2 && !strcmp(argv[1], "pid")) {
        int pid = atoi(argv[2]);
        CFNumberRef value = CFNumberCreate(NULL, kCFNumberIntType, &pid);
        const void *keys[] = {kSecGuestAttributePid}, *values[] = {value};
        CFDictionaryRef attrs = CFDictionaryCreate(NULL, keys, values, 1,
            &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
        status = SecCodeCopyGuestWithAttributes(NULL, attrs, 0, &guest);
        CFRelease(attrs); CFRelease(value);
    } else if (argc > 1 && !strcmp(argv[1], "audit")) {
        audit_token_t token;
        mach_msg_type_number_t count = TASK_AUDIT_TOKEN_COUNT;
        if (task_info(mach_task_self(), TASK_AUDIT_TOKEN, (task_info_t)&token, &count)) return 2;
        CFDataRef value = CFDataCreate(NULL, (const UInt8 *)&token, sizeof(token));
        const void *keys[] = {kSecGuestAttributeAudit}, *values[] = {value};
        CFDictionaryRef attrs = CFDictionaryCreate(NULL, keys, values, 1,
            &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
        status = SecCodeCopyGuestWithAttributes(NULL, attrs, 0, &guest);
        CFRelease(attrs); CFRelease(value);
    } else status = SecCodeCopySelf(0, &guest);
    if (!status && !code) status = SecCodeCopyStaticCode(guest, 0, &code);
    if (!status) status = SecCodeCopySigningInformation(code, kSecCSSigningInformation, &info);
    if (status) { printf("error:%d\n", status); return 1; }
    CFStringRef team = CFDictionaryGetValue(info, argc > 3 && !strcmp(argv[3], "identifier")
        ? kSecCodeInfoIdentifier : kSecCodeInfoTeamIdentifier);
    char text[128] = "none";
    if (team) CFStringGetCString(team, text, sizeof(text), kCFStringEncodingUTF8);
    puts(text);
    CFRelease(info); CFRelease(code); if (guest) CFRelease(guest);
    return 0;
}
'''


@unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "arm64", "native macOS arm64 check")
class NativeAuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="router-cua-auth-test-")
        cls.root = Path(cls.scratch.name)
        cls.desktop = cls.root / "Router.app"
        cls.helper = cls.root / "CUA.app"
        cls.library = cls.helper / "Contents/Frameworks/codex-mux-local-auth.dylib"
        cls.library.parent.mkdir(parents=True)
        cls.probe = cls.helper / "Contents/MacOS/SkyComputerUseService"
        cls.peer = cls.desktop / "Contents/MacOS/ChatGPT"
        cls.probe.parent.mkdir(parents=True)
        cls.peer.parent.mkdir(parents=True)
        source = cls.root / "probe.c"
        source.write_text(PROBE)
        cls.compile_library(os.getuid())
        subprocess.run(["xcrun", "clang", str(source), "-framework", "Security",
                        "-framework", "CoreFoundation", "-Wl,-needed_library," + str(cls.library),
                        "-o", str(cls.probe)], check=True, capture_output=True)
        source.write_text("#include <unistd.h>\nint main(void){char c;return read(0,&c,1)<0;}\n")
        subprocess.run(["xcrun", "clang", str(source), "-o", str(cls.peer)], check=True, capture_output=True)
        subprocess.run(["codesign", "-s", "-", "-f", "-i", "com.openai.codex", str(cls.peer)],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    @classmethod
    def compile_library(cls, uid):
        subprocess.run([
            "xcrun", "clang", "-dynamiclib", "-arch", "arm64", "-O2", "-Wall", "-Wextra", "-Werror",
            f"-DROUTER_OWNER_UID={uid}", '-DROUTER_APP_NAME="Router.app"',
            str(patch_app.PROJECT_ROOT / "native/local_cua_auth.c"),
            "-framework", "Security", "-framework", "CoreFoundation", "-lbsm", "-o", str(cls.library),
        ], check=True, capture_output=True)

    def query(self, *args):
        return subprocess.check_output([str(self.probe), *map(str, args)], text=True).strip()

    def running_peer(self, path=None):
        peer = subprocess.Popen([str(path or self.peer)], stdin=subprocess.PIPE)
        self.addCleanup(peer.wait)
        self.addCleanup(peer.stdin.close)
        return peer

    def test_exact_same_user_peer_gets_local_marker(self):
        peer = self.running_peer()
        self.assertEqual(self.query("pid", peer.pid), patch_app.LOCAL_CUA_TEAM_IDENTIFIER)

    def test_desktop_identity_matches_independent_native_allowlist(self):
        peer = self.running_peer()
        self.assertEqual(self.query("pid", peer.pid, "identifier"), patch_app.DESKTOP_BUNDLE_IDENTIFIER)

    def test_kernel_audit_token_and_self_identity_work(self):
        self.assertEqual(self.query("audit"), patch_app.LOCAL_CUA_TEAM_IDENTIFIER)
        self.assertEqual(self.query(), patch_app.LOCAL_CUA_TEAM_IDENTIFIER)

    def test_same_identifier_at_unapproved_path_is_denied(self):
        other = self.root / "same-identifier"
        shutil.copyfile(self.peer, other)
        other.chmod(0o755)
        peer = self.running_peer(other)
        self.assertEqual(self.query("pid", peer.pid), "none")

    def test_static_file_without_kernel_peer_context_is_denied(self):
        self.assertEqual(self.query("disk", self.peer), "none")

    def test_writable_bundle_is_denied(self):
        peer = self.running_peer()
        self.desktop.chmod(0o777)
        try:
            self.assertEqual(self.query("pid", peer.pid), "none")
        finally:
            self.desktop.chmod(0o755)

    def test_different_install_owner_is_denied(self):
        self.compile_library(os.getuid() + 1)
        try:
            self.assertEqual(self.query(), "none")
        finally:
            self.compile_library(os.getuid())


if __name__ == "__main__":
    unittest.main()
