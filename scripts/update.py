#!/usr/bin/env python3
"""Keep the installed router app on the newest published release.

    python3 scripts/update.py enable [--app PATH]   # once: install the updater agent
    python3 scripts/update.py check                  # what the agent runs hourly
    python3 scripts/update.py apply [--relaunch] [--after-pid PID]
    python3 scripts/update.py status | disable

Releases are source-only, so every update is built on this Mac: the newest
published GitHub release patches the newest official ChatGPT build it
supports into a staged app, which the app offers as an update only after
launch_check.py boots it. Installing waits for the app to quit; its Update
button quits it, and with automatic updates on, quitting installs. Kept on
disk: the installed and staged release sources, one staged build, the
patcher's one backup, and the official builds a supported release can use.
"""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY = "braindead-dev/codex-subscription-router"
LABEL = "app.cdxmux.updater"
STATE_ROOT = Path.home() / ".codex-mux"
ROOT = STATE_ROOT / "update"
STAGE = ROOT / "staged"
SOURCES = STATE_ROOT / "sources"
LOG = STATE_ROOT / "logs" / "update.log"
AGENT = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
DEFAULT_APP = Path.home() / "Applications" / "Codex Subscription Router.app"
HEADERS = {"User-Agent": "codex-subscription-router/update"}
MERGED = ROOT / "merged"


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def set_state(status: str, message: str = "", **fields: object) -> None:
    """Record what the app shows: ready, building, up-to-date, installing, failed."""
    state = read_json(ROOT / "state.json") or {}
    state.update(fields, status=status, message=message)
    state["updatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_json(ROOT / "state.json", state)
    print(f"{status}: {message}" if message else status, flush=True)


def version_key(version: str, build: str) -> tuple[int, ...]:
    return (*(int(part) for part in version.split(".")), int(build))


def describe(version: str, build: str) -> str:
    return f"{version} (ChatGPT build {build})"


def installed(app: Path) -> dict:
    with (app / "Contents" / "Info.plist").open("rb") as handle:
        return plistlib.load(handle)


def run(command: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    print("$ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def load_module(source: Path, name: str):
    """Load sibling imports from this release, without leaking across releases."""
    scripts = (source / "scripts").resolve()
    siblings = {path.stem for path in scripts.glob("*.py")}
    saved = {key: sys.modules[key] for key in siblings if key in sys.modules}
    search_path = sys.path[:]
    key = hashlib.sha256(str(scripts).encode()).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(
        f"release_{key}_{name}", scripts / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    try:
        for sibling in siblings:
            sys.modules.pop(sibling, None)
        sys.path.insert(0, str(scripts))
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    finally:
        sys.path[:] = search_path
        for sibling in siblings:
            sys.modules.pop(sibling, None)
        sys.modules.update(saved)
    return module


def latest_release() -> tuple[str, str]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/releases/latest", headers=HEADERS
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.load(response)
    return release["tag_name"].removeprefix("v"), release["tarball_url"]


def fetch_release(version: str, tarball: str) -> Path:
    target = ROOT / "src" / f"v{version}"
    if (target / "VERSION").is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as scratch:
        archive = Path(scratch) / "source.tar.gz"
        request = urllib.request.Request(tarball, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=300) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
        with tarfile.open(archive) as bundle:
            bundle.extractall(scratch, filter="data")
        (top,) = [path for path in Path(scratch).iterdir() if path.is_dir()]
        if (top / "VERSION").read_text(encoding="utf-8").strip() != version:
            raise RuntimeError(f"release v{version} does not contain version {version}")
        top.rename(target)
    return target


def git_output(source: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(source), *arguments],
        text=True,
    ).strip()


def customization_revision(source: Path, ref: str = "HEAD") -> str:
    if git_output(source, "status", "--porcelain"):
        raise RuntimeError("Customizations checkout has uncommitted changes; commit them before updating")
    return git_output(source, "rev-parse", "--verify", f"{ref}^{{commit}}")


def merge_release(source: Path, revision: str, version: str, repository: str) -> Path:
    """Merge in an isolated clone; conflicts never change the user's checkout or app."""
    target = MERGED / f"v{version}-{revision}"
    if (target / ".customizations-ready.json").is_file():
        return target
    MERGED.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=MERGED) as scratch:
        combined = Path(scratch) / "source"
        run(["git", "clone", "--no-hardlinks", "--no-checkout", str(source), str(combined)])
        run(["git", "-C", str(combined), "checkout", "--detach", revision])
        run(["git", "-C", str(combined), "fetch", "--no-tags", repository, f"refs/tags/v{version}"])
        try:
            run([
                "git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
                "-c", "merge.conflictStyle=merge",
                "-c", "user.name=Codex local updater", "-c", "user.email=codex-local@localhost",
                "-C", str(combined), "merge", "--no-edit", "--no-ff", "FETCH_HEAD",
            ])
        except subprocess.CalledProcessError as error:
            conflicts = git_output(combined, "diff", "--name-only", "--diff-filter=U")
            if conflicts == "CHANGELOG.md":
                # Both projects prepend release notes. Keep both sides of that
                # documentation-only conflict, without resolving code conflicts.
                changelog = combined / "CHANGELOG.md"
                text, count = re.subn(
                    r"(?m)^<<<<<<<[^\n]*\n(.*?)^=======\n(.*?)^>>>>>>>[^\n]*\n",
                    lambda match: match[1].rstrip() + "\n\n" + match[2],
                    changelog.read_text(), flags=re.DOTALL,
                )
                if count == 0 or any(marker in text for marker in ["<<<<<<<", "=======", ">>>>>>>"]):
                    raise RuntimeError("Unrecognized changelog merge; installed app unchanged") from error
                changelog.write_text(text)
                run(["git", "-C", str(combined), "add", "CHANGELOG.md"])
                run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
                     "-c", "user.name=Codex local updater", "-c", "user.email=codex-local@localhost",
                     "-C", str(combined), "commit", "--no-edit"])
            else:
                raise RuntimeError(
                    f"Upstream v{version} could not merge with your customizations; "
                    f"installed app unchanged. Conflicts: {conflicts or 'see update log'}"
                ) from error
        if (combined / "VERSION").read_text().strip() != version:
            raise RuntimeError("Merged source does not match the upstream release version")
        write_json(combined / ".customizations-ready.json", {
            "customizationsRevision": revision,
            "upstreamRevision": git_output(combined, "rev-parse", "FETCH_HEAD"),
            "mergedRevision": git_output(combined, "rev-parse", "HEAD"),
        })
        combined.rename(target)
    return target


def choose_build(source: Path) -> tuple[str, str | None]:
    """The newest official build the release supports that is here or offered."""
    supported = {build for _, build in load_module(source, "patch_app").SUPPORTED_BUILDS}
    offered = {build: url for _, build, url in load_module(source, "appcast").builds()}
    for build in sorted(supported, key=int, reverse=True):
        if (SOURCES / f"ChatGPT-{build}.app").is_dir() or build in offered:
            return build, offered.get(build)
    raise RuntimeError("no official build this release supports can be downloaded")


def discard_stage() -> None:
    if STAGE.is_dir():
        shutil.rmtree(STAGE)
    (ROOT / "stage.json").unlink(missing_ok=True)


def build_stage(source: Path, app: Path, version: str, build: str, url: str | None,
                customizations_revision: str | None = None) -> None:
    official = SOURCES / f"ChatGPT-{build}.app"
    if not official.is_dir():
        load_module(source, "appcast").fetch(url, build, SOURCES)
    if not (source / "node_modules" / "@electron" / "asar").is_dir():
        run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=source)
    if customizations_revision is not None:
        run(["npm", "run", "check"], cwd=source)
        run([sys.executable, "scripts/verify_build.py", "--source", str(official)], cwd=source)
    info = installed(app)
    env = {**os.environ, "CODEX_MUX_DISPLAY_NAME": info["CFBundleName"]}
    command = [
        sys.executable, "scripts/patch_app.py",
        "--source", str(official), "--destination", str(app), "--stage", str(STAGE),
    ]
    if info.get("CodexMuxSigningTeamIdentifier") == "adhoc":
        env["CODEX_MUX_SIGNING_IDENTITY"] = "-"
        command.append("--allow-adhoc-signing")
    run(command, cwd=source, env=env)
    run([sys.executable, "scripts/launch_check.py", "--app", str(STAGE / app.name)], cwd=source)
    write_json(
        ROOT / "stage.json",
        {"version": version, "build": build, "source": str(source),
         "customizationsRevision": customizations_revision},
    )


def check(settings: dict) -> int:
    app = Path(settings["app"])
    info = installed(app)
    current = (info.get("CodexMuxVersion", "0.0.0"), info["CFBundleVersion"])
    version, tarball = latest_release()
    custom_source = settings.get("customizations_source")
    revision = customization_revision(Path(custom_source), settings.get("customizations_ref", "HEAD")) if custom_source else None
    custom_changed = revision is not None and revision != settings.get("installed_customizations_revision")
    newer = version_key(version, "0") >= version_key(current[0], "0")
    if newer:
        source = (
            merge_release(Path(custom_source), revision, version,
                          f"https://github.com/{REPOSITORY}.git")
            if revision is not None else fetch_release(version, tarball)
        )
        build, url = choose_build(source)
        newer = version_key(version, build) > version_key(*current) or custom_changed
    if not newer:
        discard_stage()
        set_state("up-to-date", installed=describe(*current), available=None)
        collect_garbage(app)
        return 0
    target = describe(version, build)
    fields = {"installed": describe(*current), "available": target}
    stage = read_json(ROOT / "stage.json") or {}
    if (stage.get("version"), stage.get("build"), stage.get("customizationsRevision")) != (version, build, revision) or not (
        STAGE / app.name
    ).is_dir():
        discard_stage()
        set_state("building", f"Preparing {target}", **fields)
        try:
            build_stage(source, app, version, build, url, revision)
        except (subprocess.CalledProcessError, OSError, RuntimeError) as error:
            discard_stage()
            set_state("failed", f"Preparing {target} failed: {error}", **fields)
            return 1
    set_state("ready", f"{target} is ready to install", **fields)
    if settings.get("auto") and not load_module(source, "patch_app").running_components(app):
        return apply(settings, relaunch=False, after_pid=None)
    return 0


def wait_for_exit(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.5)
    return False


def apply(settings: dict, relaunch: bool, after_pid: int | None) -> int:
    """Install the staged build once the app has quit; with RELAUNCH the app
    reopens whether or not the install went through."""
    app = Path(settings["app"])
    try:
        if after_pid is not None and not wait_for_exit(after_pid, 600):
            print("the app did not quit; the update stays ready", flush=True)
            return 1
        return install_stage(app)
    finally:
        if relaunch:
            run(["open", str(app)])


def install_stage(app: Path) -> int:
    stage = read_json(ROOT / "stage.json")
    if stage is None or not (STAGE / app.name).is_dir():
        print("no staged update", flush=True)
        return 1
    source = Path(stage["source"])
    running = load_module(source, "patch_app").running_components
    deadline = time.monotonic() + 120
    while running(app) and time.monotonic() < deadline:
        time.sleep(1)
    target = describe(stage["version"], stage["build"])
    set_state("installing", f"Installing {target}")
    try:
        run(
            [
                sys.executable, str(source / "scripts" / "patch_app.py"),
                "--install-staged", str(STAGE), "--destination", str(app),
            ]
        )
    except subprocess.CalledProcessError:
        set_state("ready", f"Installing {target} failed; see {LOG}")
        return 1
    shutil.copy2(source / "scripts" / "update.py", ROOT / "update.py")
    settings = read_json(ROOT / "settings.json") or {}
    settings["installed_customizations_revision"] = stage.get("customizationsRevision")
    write_json(ROOT / "settings.json", settings)
    (ROOT / "stage.json").unlink()
    set_state("up-to-date", installed=target, available=None)
    collect_garbage(app)
    return 0


def collect_garbage(app: Path) -> None:
    """Keep only the installed and staged release sources, and the official
    builds at least as new as the oldest one the installed release supports."""
    version = installed(app).get("CodexMuxVersion")
    stage = read_json(ROOT / "stage.json") or {}
    keep = {ROOT / "src" / f"v{version}", Path(stage.get("source", "/"))}
    if (ROOT / "src").is_dir():
        for directory in (ROOT / "src").iterdir():
            if directory.is_dir() and directory not in keep:
                shutil.rmtree(directory)
                print(f"removed {directory}", flush=True)
    source = ROOT / "src" / f"v{version}"
    if (source / "scripts" / "patch_app.py").is_file():
        oldest = min(int(build) for _, build in load_module(source, "patch_app").SUPPORTED_BUILDS)
        for official in SOURCES.glob("ChatGPT-*.app"):
            build = official.name.removeprefix("ChatGPT-").removesuffix(".app")
            if build.isdigit() and int(build) < oldest:
                shutil.rmtree(official)
                print(f"removed {official}", flush=True)
    if LOG.is_file() and LOG.stat().st_size > 1_000_000:
        LOG.write_bytes(LOG.read_bytes()[-200_000:])


def launchctl(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *arguments], capture_output=True, text=True)


def enable(app: Path, customizations_source: Path | None = None) -> int:
    app = app.expanduser().resolve()
    installed(app)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    # The PATH entry survives interpreter upgrades; sys.executable names a version.
    python = shutil.which("python3") or sys.executable
    settings = read_json(ROOT / "settings.json") or {"auto": False}
    if customizations_source is not None:
        customizations_source = customizations_source.expanduser().resolve()
        customization_revision(customizations_source)
        settings["customizations_source"] = str(customizations_source)
        settings["customizations_ref"] = git_output(customizations_source, "symbolic-ref", "--short", "HEAD")
    settings.update(app=str(app), python=python)
    write_json(ROOT / "settings.json", settings)
    if Path(__file__).resolve() != (ROOT / "update.py").resolve():
        shutil.copy2(Path(__file__).resolve(), ROOT / "update.py")
    agent = {
        "Label": LABEL,
        "ProgramArguments": [python, str(ROOT / "update.py"), "check"],
        "RunAtLoad": True,
        "StartInterval": 3600,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    }
    AGENT.parent.mkdir(parents=True, exist_ok=True)
    with AGENT.open("wb") as handle:
        plistlib.dump(agent, handle)
    domain = f"gui/{os.getuid()}"
    launchctl("bootout", f"{domain}/{LABEL}")
    result = launchctl("bootstrap", domain, str(AGENT))
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        return 1
    print(f"updates enabled for {app}; automatic install on quit is "
          f"{'on' if settings['auto'] else 'off'}")
    return 0


def disable() -> int:
    launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    AGENT.unlink(missing_ok=True)
    (ROOT / "settings.json").unlink(missing_ok=True)
    discard_stage()
    print("updates disabled")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    enable_parser = commands.add_parser("enable")
    enable_parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    enable_parser.add_argument("--customizations-source", type=Path,
                               help="Merge upstream releases with this clean, committed local Git checkout")
    commands.add_parser("check")
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("--relaunch", action="store_true")
    apply_parser.add_argument("--after-pid", type=int)
    commands.add_parser("status")
    commands.add_parser("disable")
    args = parser.parse_args()

    if args.command == "enable":
        return enable(args.app, args.customizations_source)
    if args.command == "disable":
        return disable()
    if args.command == "status":
        print(json.dumps(read_json(ROOT / "state.json") or {}, indent=2))
        return 0
    settings = read_json(ROOT / "settings.json")
    if settings is None:
        print("updates are not enabled; run `python3 scripts/update.py enable`", file=sys.stderr)
        return 1
    with (ROOT / "lock").open("w") as lock:
        if args.command == "check":
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            try:
                return check(settings)
            except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
                # A staged build stays ready through a failed check.
                staged = (ROOT / "stage.json").is_file()
                set_state("ready" if staged else "failed", f"Update check failed: {error}")
                return 1
        fcntl.flock(lock, fcntl.LOCK_EX)
        return apply(settings, args.relaunch, args.after_pid)


if __name__ == "__main__":
    raise SystemExit(main())
