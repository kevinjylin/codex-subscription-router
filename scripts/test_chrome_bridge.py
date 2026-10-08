"""Exercise the bridge's actual arm64 comparator and preserved registration."""

import json
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

import chrome_bridge


class ChromeBridgeTests(unittest.TestCase):
    def test_unknown_binary_or_invalid_team_is_rejected(self):
        for data, team in ((b"changed native host", "C5467MV9FT"), (b"", "invalid")):
            with self.subTest(team=team):
                with self.assertRaises(RuntimeError):
                    chrome_bridge.patch_bridge(data, team)

    def test_registration_preserves_origins_and_other_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app, home = root / "Router.app", root / "home"
            bridge = app / chrome_bridge.BRIDGE_RELATIVE
            bridge.parent.mkdir(parents=True)
            bridge.touch()
            manifest = home / "Library/Application Support/Google/Chrome/NativeMessagingHosts/com.openai.codexextension.json"
            manifest.parent.mkdir(parents=True)
            original = {"name": "com.openai.codexextension", "type": "stdio", "path": "original", "allowed_origins": ["chrome-extension://existing/"], "description": "existing description"}
            manifest.write_text(json.dumps(original))
            chrome_bridge.register_bridge(app, home)
            expected = dict(original, path=str(bridge))
            self.assertEqual(json.loads(manifest.read_text()), expected)
            chrome_bridge.register_bridge(app, home)
            self.assertEqual(json.loads(manifest.read_text()), expected)
            original["name"] = "different-host"
            manifest.write_text(json.dumps(original))
            with self.assertRaises(RuntimeError):
                chrome_bridge.register_bridge(app, home)
            self.assertEqual(json.loads(manifest.read_text()), original)

    @unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "arm64", "native arm64 macOS test")
    def test_actual_instructions_only_accept_the_selected_team(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            code = ".text\n"
            for name, team in (("router_compare", "C5467MV9FT"), ("rotated_compare", "A111111111")):
                shim = chrome_bridge.team_comparison_shim(team)
                code += f".globl _{name}\n_{name}:\n.byte " + ",".join(str(b) for b in shim[:-4]) + "\nb _memcmp\n"
            assembly = root / "compare.s"
            assembly.write_text(code)
            source = root / "compare.c"
            source.write_text('''#include <stddef.h>
#include <stdio.h>
#include <string.h>
int router_compare(const void*, const void*, size_t);
int rotated_compare(const void*, const void*, size_t);
int main(void) {
  const char *teams[] = {"2DC432GLL2", "C5467MV9FT", "C5467MV9FU", "A111111111", "EQHXZ8M8AV"};
  for (size_t n=0; n<=11; ++n) {
    for (size_t i=0; i<5; ++i) {
      for (size_t j=0; j<5; ++j) {
        int same = memcmp(teams[i],teams[j],n)==0;
        int router = same || (n==10 && i==0 && j==1);
        int rotated = same || (n==10 && i==0 && j==3);
        if ((router_compare(teams[i],teams[j],n)==0)!=router ||
            (rotated_compare(teams[i],teams[j],n)==0)!=rotated) {
          fprintf(stderr,"unexpected acceptance: n=%zu i=%zu j=%zu\\n",n,i,j); return 1;
        }
      }
    }
  }
  return 0;
}
''')
            executable = root / "compare"
            subprocess.run(["xcrun", "clang", str(source), str(assembly), "-o", str(executable)], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
