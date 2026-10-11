#!/usr/bin/env python3
"""Boot a staged router app until its window talks to Codex, then quit it.

    python3 scripts/launch_check.py --app ~/.codex-mux/port-stage/"Codex (router).app"

Starts the app's Electron binary with a throwaway desktop profile and passes
once the renderer is running and the multiplexed app-server has answered.
It catches failures no static check sees: signing, Electron fuses and
integrity seals, and main-process crashes. It uses the real ~/.codex-mux
accounts, so it needs Henry's Mac, not CI.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from patch_app import codex_entrypoint, stop_lingering_helpers  # noqa: E402
from desktop_home import prepare_desktop_home  # noqa: E402
from chrome_bridge import validate_bridge  # noqa: E402

READY = ("codex-router-renderer-ready", "[AppServerConnection] response_routed")


def stop(process: subprocess.Popen[str], app: Path) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    stop_lingering_helpers(app)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()

    app = args.app.expanduser().resolve()
    required = set(READY)
    if validate_bridge(app):
        required.add("codex-router-chrome-registration-ready")
    primary_home = Path.home() / ".codex"
    runtime_home = Path.home() / ".codex-mux" / "desktop-home"
    prepare_desktop_home(primary_home, runtime_home)
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    executable = app / "Contents" / "MacOS" / info["CFBundleExecutable"]
    with tempfile.TemporaryDirectory(prefix="codex-router-launch-") as profile:
        log_path = Path(profile) / "launch.log"
        with log_path.open("w") as log:
            process = subprocess.Popen(
                [str(executable), f"--user-data-dir={profile}/user-data"],
                env={
                    **os.environ,
                    "ELECTRON_ENABLE_LOGGING": "1",
                    "CODEX_CLI_PATH": str(codex_entrypoint(app / "Contents" / "Resources")),
                    "CODEX_MUX_UI_TESTS": "1",
                    "CODEX_MUX_LAUNCH_CHECK": "1",
                    "CODEX_HOME": str(runtime_home),
                    "CODEX_MUX_PRIMARY_SQLITE_HOME": str(primary_home),
                },
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=True,
            )
        deadline = time.monotonic() + args.timeout
        seen: set[str] = set()
        try:
            while time.monotonic() < deadline and process.poll() is None:
                output = log_path.read_text(errors="replace")
                seen = {marker for marker in required if marker in output}
                if any("initialize_handshake_result" in line and "outcome=success" in line
                       for line in output.splitlines()):
                    seen.add(READY[1])
                if seen == required or "FATAL:" in output:
                    break
                time.sleep(1)
        finally:
            stop(process, app)
        output = log_path.read_text(errors="replace")

    if seen == required and "FATAL:" not in output:
        print("launched: renderer and app-server ready; " +
              ("Chrome registration lifecycle verified (live browser connection still requires a separate check)"
               if len(required) > len(READY) else "Chrome compatibility not checked"))
        return 0
    print(f"launch failed (exit {process.returncode}, saw {sorted(seen) or 'nothing'}):")
    for line in output.splitlines()[-15:]:
        print(f"  {line[:300]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
