import json
from pathlib import Path
import tempfile
import tomllib
import unittest

from desktop_home import clean_runtime_config, prepare_desktop_home


class DesktopHomeTests(unittest.TestCase):
    def test_cleanup_preserves_custom_hook_and_other_settings(self):
        wrapped = ["/old/SkyComputerUseClient", "turn-ended", "--previous-notify",
                   json.dumps(["/custom/notify", "argument"])]
        source = 'model = "test"\nnotify = ' + json.dumps(wrapped) + '''
[mcp_servers.node_repl]
command = "/wrong/node_repl"
[mcp_servers.node_repl.env]
CODEX_HOME = "/wrong"
[mcp_servers.keep]
command = "/custom/server"
[projects."/repo"]
trust_level = "trusted"
'''
        result = tomllib.loads(clean_runtime_config(source))
        self.assertEqual(result['notify'], ["/custom/notify", "argument"])
        self.assertEqual(result['mcp_servers'], {'keep': {'command': '/custom/server'}})
        self.assertEqual(result['projects'], {'/repo': {'trust_level': 'trusted'}})

    def test_runtime_cache_is_independent_and_history_stays_shared(self):
        with tempfile.TemporaryDirectory() as scratch:
            source, target = Path(scratch) / 'official', Path(scratch) / 'router'
            source.mkdir()
            (source / 'config.toml').write_text('model = "test"\n')
            cache = source / 'plugins/cache/package'
            cache.mkdir(parents=True)
            original_mcp = json.dumps({'command': 'official', 'env': {'CODEX_HOME': str(source.resolve())}})
            (cache / '.mcp.json').write_text(original_mcp)
            (source / 'state_5.sqlite').write_bytes(b'history')
            (source / 'sessions').mkdir()
            prepare_desktop_home(source, target)
            private = target / 'plugins/cache/package/.mcp.json'
            self.assertEqual(json.loads(private.read_text())['env']['CODEX_HOME'], str(target.resolve()))
            private.write_text('{"command":"router"}')
            self.assertEqual((cache / '.mcp.json').read_text(), original_mcp)
            self.assertTrue((target / 'state_5.sqlite').is_symlink())
            self.assertEqual((target / 'sessions').resolve(), (source / 'sessions').resolve())
            (target / 'config.toml').write_text('model = "custom"\n')
            prepare_desktop_home(source, target)
            self.assertEqual((target / 'config.toml').read_text(), 'model = "custom"\n')
            self.assertEqual(private.read_text(), '{"command":"router"}')

    def test_shared_runtime_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as scratch:
            source, target = Path(scratch) / 'official', Path(scratch) / 'router'
            source.mkdir()
            target.mkdir()
            (target / 'plugins').symlink_to(source)
            with self.assertRaisesRegex(RuntimeError, "must not be shared"):
                prepare_desktop_home(source, target)
