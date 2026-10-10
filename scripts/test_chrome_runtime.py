"""Execute patched upstream lifecycle code, including the staged boot path."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import chrome_bridge

FIXTURE = Path(__file__).parent / 'fixtures/chrome-lifecycle-13536.js'
HELPERS = Path(__file__).resolve().parent.parent / 'ui'


class ChromeRuntimeTests(unittest.TestCase):
    def test_upstream_registration_refresh_registry_and_uninstall(self):
        patched = chrome_bridge.patch_runtime(FIXTURE.read_text())
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            bridge = root / 'router-chrome-bridge/ChatGPT for Chrome'
            bridge.parent.mkdir(); bridge.touch()
            script = '''
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const vm = require('node:vm');
const root = ROOT, file = path.join(root, 'manifest.json');
process.resourcesPath = root;
let writes = 0, ready = false;
const context = { require, __dirname: HELPERS, Buffer,
 process: { platform: 'darwin', resourcesPath: root, env: {} },
 console: { log: s => { ready = s === 'codex-router-chrome-registration-ready'; } },
 YP: 'test', XF: () => [file], yI: async () => {},
 VF: async (p,b) => { writes++; fs.writeFileSync(p,b); },
 WF: () => 'com.openai.codexextension', iF: { parse: x => x },
 g: path, $F: async () => [], CF: async () => false,
 kF: x => x, _I: (p,r) => p.startsWith(r + '/'),
};
vm.createContext(context); vm.runInContext(PATCHED, context);
(async () => {
 const host = await context.zF({ resourcesPath: root });
 assert.equal(host, path.join(root, 'router-chrome-bridge/ChatGPT for Chrome'));
 const registration = { extensionHostPath: host, extensionIds: ['existing'], nativeHostName: 'com.openai.codexextension' };
 await context.RF(registration);
 assert.equal(JSON.parse(fs.readFileSync(file)).path, host);
 await context.RF(registration);
 assert.equal(JSON.parse(fs.readFileSync(file)).path, host);
 const record = {nativeHostNames: [registration.nativeHostName], paths: {codexHome: '/router-home', extensionHostPath: host}};
 assert.equal(context.EF(record, {codexHome: '/router-home', nativeHostName: registration.nativeHostName, pluginCacheRoot: '/cache'}), true);
 assert.equal(context.EF(record, {codexHome: '/official-home', nativeHostName: registration.nativeHostName, pluginCacheRoot: '/cache'}), false);
 context.process.env.CODEX_MUX_LAUNCH_CHECK = '1';
 const before = writes;
 await context.RF(registration); await context.bF({codexHome: '/test'});
 assert.equal(writes, before); assert.equal(ready, true);
 await assert.rejects(context.RF({...registration, extensionHostPath: '/vendor/host'}), /mismatch/);
 context.process.env.CODEX_MUX_LAUNCH_CHECK = '';
 await context.yF({pluginName: 'chrome', marketplaceName: 'openai-bundled', codexHome: '/router-home'});
 assert.equal(fs.existsSync(file), false);
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => require(path.join(HELPERS, 'chrome-bridge.cjs')).stop());
'''.replace('ROOT', json.dumps(str(root))).replace('HELPERS', json.dumps(str(HELPERS))).replace('PATCHED', json.dumps(patched))
            result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_ambiguous_and_changed_anchors_fail_closed(self):
        original = FIXTURE.read_text()
        for source in ('', original + original, original.replace('e.pluginRoot;', 'e.otherRoot;')):
            with self.assertRaises(RuntimeError):
                chrome_bridge.patch_runtime(source)
