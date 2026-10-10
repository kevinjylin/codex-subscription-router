const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const bridge = require('./chrome-bridge.cjs');

test('registration guard survives a vendor refresh and preserves origin permissions', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'router-chrome-test-'));
  t.after(() => { bridge.stop(); fs.rmSync(root, { recursive: true, force: true }); });
  const file = path.join(root, 'host.json');
  const original = { name: 'com.openai.codexextension', type: 'stdio', path: '/vendor/host', allowed_origins: ['chrome-extension://existing/'], description: 'keep' };
  fs.writeFileSync(file, JSON.stringify(original));
  bridge.guard([file], { nativeHostName: original.name, extensionHostPath: '/router/compatible-host' });
  assert.equal(JSON.parse(fs.readFileSync(file)).path, '/router/compatible-host');
  fs.writeFileSync(file + '.tmp', JSON.stringify(original));
  fs.renameSync(file + '.tmp', file);
  const deadline = Date.now() + 2000;
  while (JSON.parse(fs.readFileSync(file)).path !== '/router/compatible-host' && Date.now() < deadline) await new Promise(r => setTimeout(r, 20));
  assert.deepEqual(JSON.parse(fs.readFileSync(file)), { ...original, path: '/router/compatible-host' });
  bridge.stop(true);
  assert.equal(fs.existsSync(file), false);
});

test('missing bridge and unexpected registrations fail closed', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'router-chrome-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  assert.throws(() => bridge.selectHost({ resourcesPath: root }), /ENOENT/);
  const file = path.join(root, 'host.json');
  const original = JSON.stringify({ name: 'another.host', type: 'stdio', path: '/vendor/host' });
  fs.writeFileSync(file, original);
  assert.throws(() => bridge.reconcile(file, '/router/host'), /Unexpected/);
  assert.equal(fs.readFileSync(file, 'utf8'), original);
});
