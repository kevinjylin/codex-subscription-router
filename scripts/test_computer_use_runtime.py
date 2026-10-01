"""Regressions for production Computer Use socket configuration."""

import json
from pathlib import Path
import subprocess
import unittest

import patch_app


class ComputerUseRuntimeTests(unittest.TestCase):
    def test_production_and_development_get_router_socket_on_mac(self):
        # Upstream p is false for the packaged production runtime, which used
        # to silently drop the override even though the service used it.
        source = (
            "({serviceAppPath:m.platform===`darwin`?u.serviceAppPath:null,"
            "serviceNativePipePath:m.platform===`darwin`&&p?P.default.env[pa]:null})"
        )
        pipe = Path('/tmp/router "test"/computer-use.sock')
        patched = patch_app.patch_computer_use_runtime_pipe(source, pipe)
        script = (
            "const P={default:{env:{}}},pa='SKY_CUA_SERVICE_NATIVE_PIPE_PATH';"
            "const u={serviceAppPath:'/tmp/helper.app'};"
            "const result=[];"
            "for(const platform of ['darwin','win32'])for(const p of [false,true]){"
            "const m={platform};result.push(" + patched + ");}"
            "console.log(JSON.stringify(result));"
        )
        result = json.loads(subprocess.check_output(["node", "-e", script], text=True))
        for entry in result[:2]:
            self.assertEqual(entry["serviceNativePipePath"], str(pipe))
            self.assertEqual(entry["serviceAppPath"], "/tmp/helper.app")
        for entry in result[2:]:
            self.assertIsNone(entry["serviceNativePipePath"])
            self.assertIsNone(entry["serviceAppPath"])

    def test_missing_or_ambiguous_anchor_fails_closed(self):
        source = "serviceNativePipePath:m.platform===`darwin`&&p?P.default.env[pa]:null"
        for invalid in ["", source + "," + source, source.replace("darwin", "linux")]:
            with self.assertRaisesRegex(RuntimeError, "runtime socket setting"):
                patch_app.patch_computer_use_runtime_pipe(invalid, Path("/tmp/cua.sock"))


if __name__ == "__main__":
    unittest.main()
