# Chrome availability audit — October 10, 2026

The evidence points to the router's native Chrome integration, rather than an incorrectly installed Chrome extension. The browser gate correctly reports the absence of a usable Chrome provider; its generic error hides the integration failure underneath.

## Live observations

- `cua.getState()` lists Google Chrome as a running native app, but lists only the in-app browser and MCP Apps as browser providers. `cua.getBrowser({id:"chrome"})` reproduces `Browser is not available: chrome`.
- The `chrome`, `browser`, and `unified-computer-use` plugins are enabled in the router's runtime config. Current desktop logs recognize the Chrome extension as installed and its plugin as current.
- Installed router: 0.14.0, ChatGPT build 13536 / 26.1002.52244, signing team `C5467MV9FT`. This checkout's `main` is 0.13.0 at `e72fb6d`; the updater follows `codex/preserve-upstream-customizations` at `9f0f204`.
- The router now runs its bundled `codex` and `codex.real` processes. Port 48123 responds with HTTP 401 to an unauthenticated `/v1/accounts` request. The earlier wrong-backend/no-listener diagnosis is not the present state. `/health` returns 404 and is not an appropriate health probe.

## 1. The active Chrome bridge is the unmodified vendor binary

Chrome's native messaging registration points to:

`~/.codex-mux/desktop-home/plugins/cache/openai-bundled/chrome/latest/extension-host/macos/arm64/ChatGPT for Chrome`

Process inspection and `lsof` confirm Chrome launched that executable (PID 57208 during this audit), listening on `/tmp/codex-browser-use/5d132b11-fb03-44b0-855a-dcb0aa8dfea7.sock`. Its SHA-256 is `ff06f508870eef77fc2575cd0706dc277ab12cdfc8ee0662ffc25d52f94a6efc`, exactly the original binary hash recorded in the newer `scripts/chrome_bridge.py`. Its signing team is OpenAI's `2DC432GLL2`; the router's executable uses `C5467MV9FT`.

The custom bridge at `~/.codex-mux/chrome-bridge/ChatGPT for Chrome` has the modified team-comparison branches and router signature, but the registration and live process do not use it. The newer intended bundle location, `Contents/Resources/router-chrome-bridge/ChatGPT for Chrome`, is absent from the installed app.

The original binary's peer checks and the newer patch's explicit team-comparison changes make this a concrete compatibility mismatch and the leading explanation for the missing provider. This audit did not obtain a native-host rejection message naming the failed check, nor perform a repair-and-retest; it does not claim that this is the only remaining handshake issue.

## 2. Confirmed updater import bug prevents installation of the newer fix

`scripts/update.py:90-98` imports a release's `patch_app.py` by absolute path with `spec_from_file_location`, but does not make that release's sibling modules importable. The newer patcher imports `desktop_home` and `chrome_bridge` by bare module name.

Loading the selected merged release through the installed updater reproduces:

`ModuleNotFoundError: No module named 'desktop_home'`

`~/.codex-mux/logs/update.log` records the same exception repeatedly for `v0.14.0-9f0f2043eedb47cfcd9cfdbaa0d5368c6936ac40`. The updater settings still record installed customization revision `80135ee`, predating the Chrome bridge commit. Later log entries also include disk-space and DNS failures; those are separate historical obstacles, not a substitute for the reproducible import defect.

Repair should resolve sibling imports from the selected release in isolation, avoiding accidental imports from another checkout or previously cached modules. Test the copied standalone updater, not only invocation from the repository's scripts directory.

## 3. The newer bridge registration fix is not durable

In the selected merged release, `scripts/chrome_bridge.py:84-107` changes the native messaging manifest once; `scripts/patch_app.py:2052` calls it during installation. It does not update the desktop's native-host path selection or the v2 runtime registry. It also silently skips registration if no existing manifest is present.

The installed desktop's bootstrap contains its own native-host lifecycle:

- `_F` chooses the original extension host from the Chrome plugin and passes it to `RF`.
- `RF` writes `path: e.extensionHostPath` to the native messaging manifests.
- The main bundle has a Chrome cache watcher that invokes this reconciliation when plugin cache state changes.

An isolated execution of the actual installed `RF` function, with only filesystem sinks mocked, replaced a custom bridge registration with the plugin-cache original. Thus installing the pending fix alone is insufficient: a later plugin reconciliation can undo it. Both official and router apps also use the same native host name, so registration ownership needs an explicit coexistence design.

Repair should make runtime reconciliation select the compatible host consistently, align v2 registry paths, and verify the result after plugin refresh and application restart. Preserve the intended peer authorization checks.

## 4. Runtime-home isolation omits the Chrome runtime registry

`scripts/desktop_home.py:14` omits `chrome-native-hosts-v2.json` from private runtime entries. Lines 128-131 symlink other existing primary-home entries into the router home. A temporary-directory reproduction confirms this registry becomes a symlink to the official home.

The present on-disk registry is a regular file, so a currently shared symlink is not the immediate cause. However, its entries include both official and router executable paths, consistent with incomplete historical isolation. Some entries are intentionally shared through the upstream global registry; their mere coexistence does not prove a routing failure. The reproducible defect is treating the per-home executable-path registry as shared history during home preparation.

## 5. Boot verification cannot certify Chrome availability

`scripts/launch_check.py:28,79-93` succeeds after a renderer-ready marker and an app-server response/handshake. It does not require Chrome discovery, a native-host handshake, or a browser operation. It directly launches the Electron executable with injected environment variables, rather than exercising the installed launcher's complete path.

The newer bridge tests cover binary comparator behavior and one-shot manifest writes. They omit the subsequent runtime reconciliation that overwrites the registration.

Acceptance checks should cover: standalone updater imports; installed-launcher startup; active native-host executable and trust compatibility; registration readback after plugin refresh; Chrome provider discovery in a fresh chat; and a harmless browser tab/screenshot interaction. Boot checks remain useful, but should be reported as boot checks.

## Scope

This was an analysis task. No source implementation, app bundle, registration, permission, or running process was changed. Reproductions used temporary files or mocked filesystem sinks. The only repository addition is this report. Chrome remains unavailable at the end of the audit.

## Resolution (same day)

- **Bridge selection is durable.** `chrome_bridge.patch_runtime` patches the desktop's own native-host path selector, manifest writer, v2 registry removal and uninstall lifecycle so every refresh picks the router-signed bridge packaged at `Contents/Resources/router-chrome-bridge/`. `ui/chrome-bridge.cjs` also watches the manifest and restores the router bridge path if the manifest is replaced. Anchor drift fails the build.
- **Updater imports fixed.** `update.load_module` resolves a release's sibling modules from that release only, then restores `sys.path`/`sys.modules`. A copied standalone updater is tested.
- **Registry isolation.** `chrome-native-hosts-v2.json` is private to the router home; a legacy symlink to the official registry is detached.
- **Verification.** `launch_check.py` validates the packaged bridge (team shim, branches, `codesign --verify --strict`) and requires the patched registration path to run during staged boot.
- **Live confirmation.** After Chrome's registration pointed to a router-team bridge (16:04), the running 0.14.0 router attached a real Chrome tab (`backend=chrome browserID=3`). This confirms the bridge mismatch as the cause. The installed 0.14.0 app still has the unpatched reconciliation, so that manual registration lasts only until the patched build is installed.
