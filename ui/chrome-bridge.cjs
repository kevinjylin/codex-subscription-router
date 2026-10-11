// The shared Chrome host must accept both the vendor and this router's team.
// Keep the signed bridge outside the vendor's immutable plugin cache.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const guards = new Map();
const HOST = 'com.openai.codexextension';

function selectHost({ resourcesPath = process.resourcesPath }) {
  const bridge = path.join(resourcesPath, 'router-chrome-bridge', 'ChatGPT for Chrome');
  if (!fs.statSync(bridge).isFile()) throw new Error(`Router Chrome bridge missing: ${bridge}`);
  return bridge;
}

function reconcile(file, expected) {
  let data;
  try { data = JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return; throw error; }
  if (data.name !== HOST || data.type !== 'stdio') throw new Error('Unexpected Chrome native-host registration');
  if (data.path === expected) return;
  const temporary = `${file}.router-${crypto.randomUUID()}`;
  try {
    fs.writeFileSync(temporary, JSON.stringify({ ...data, path: expected }, null, 2) + '\n', { mode: fs.statSync(file).mode & 0o777 });
    fs.renameSync(temporary, file);
  } finally { fs.rmSync(temporary, { force: true }); }
}

function guard(files, registration) {
  if (registration.nativeHostName !== HOST || process.env.CODEX_MUX_LAUNCH_CHECK === '1') return;
  for (const file of files) {
    if (guards.has(file)) continue;
    const expected = registration.extensionHostPath;
    // Stat polling survives atomic replacements and exhausted OS watch handles.
    const listener = () => {
      try { reconcile(file, expected); }
      catch (error) { console.error('[router-chrome-bridge] registration repair failed:', error.message); }
    };
    fs.watchFile(file, { persistent: false, interval: 1000 }, listener);
    guards.set(file, { expected, listener });
    reconcile(file, expected);
  }
}

function stop(remove = false) {
  for (const [file, state] of guards) {
    fs.unwatchFile(file, state.listener);
    if (remove) {
      try {
        const data = JSON.parse(fs.readFileSync(file, 'utf8'));
        if (data.name === HOST && data.type === 'stdio' && data.path === state.expected) fs.unlinkSync(file);
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
  }
  guards.clear();
}

module.exports = { selectHost, reconcile, guard, stop };
