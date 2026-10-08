"""Keep desktop runtime configuration private while sharing primary history."""

import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib


PRIVATE_ENTRIES = {"config.toml", "plugins", ".tmp", "tmp", "node_repl", "computer-use", "browser", "visualizations"}
RUNTIME_SERVERS = {"node_repl", "computer-use", "cua_repl"}


def clean_runtime_config(contents: str) -> str:
    """Remove desktop-owned runtime definitions, preserving other TOML values."""
    parsed = tomllib.loads(contents)
    expected = copy.deepcopy(parsed)
    for name in RUNTIME_SERVERS:
        expected.get("mcp_servers", {}).pop(name, None)
    hook = parsed.get("notify")
    while isinstance(hook, list) and len(hook) >= 2 and Path(hook[0]).name == "SkyComputerUseClient" and hook[1] == "turn-ended":
        hook = json.loads(hook[hook.index("--previous-notify") + 1]) if "--previous-notify" in hook else None
    if hook is None:
        expected.pop("notify", None)
    elif "notify" in parsed:
        expected["notify"] = hook

    result = []
    section = ""
    skipping_notify = False
    notify_lines = []
    for line in contents.splitlines(keepends=True):
        if skipping_notify:
            notify_lines.append(line)
            try:
                tomllib.loads("".join(notify_lines))
            except tomllib.TOMLDecodeError:
                continue
            skipping_notify = False
            continue
        match = re.match(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$", line.strip())
        if match:
            section = match.group(1).replace('"', '').replace("'", '')
        if any(section == f"mcp_servers.{name}" or section.startswith(f"mcp_servers.{name}.") for name in RUNTIME_SERVERS):
            continue
        if section == "" and re.match(r"^\s*notify\s*=", line):
            if hook is not None:
                result.append("notify = " + json.dumps(hook) + "\n")
            try:
                tomllib.loads(line)
            except tomllib.TOMLDecodeError:
                skipping_notify = True
                notify_lines = [line]
            continue
        result.append(line)
    repaired = "".join(result)
    actual = tomllib.loads(repaired)
    # Empty implicit tables disappear when their last child is removed.
    if not expected.get("mcp_servers") and "mcp_servers" not in actual:
        expected.pop("mcp_servers", None)
    if actual != expected:
        raise RuntimeError("runtime config cleanup would change unrelated settings")
    return repaired


def clone_directory(source: Path, target: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["/bin/cp", "-cR", str(source), str(target)], check=True)
    else:
        shutil.copytree(source, target, symlinks=True)
    # An absolute cache symlink must not lead mutable runtime files back into
    # the official app's cache. Relative links already remain inside the copy.
    for path in target.rglob("*"):
        if path.is_symlink():
            old = Path(os.readlink(path))
            if old.is_absolute() and old.is_relative_to(source):
                path.unlink()
                path.symlink_to(target / old.relative_to(source))


def prepare_desktop_home(source: Path, target: Path) -> None:
    source, target = source.resolve(), target.resolve()
    if target == source or target.is_relative_to(source):
        raise RuntimeError("desktop runtime home must be separate from the official home")
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.chmod(0o700)
    # The browser sandbox requires writable roots without symlink components.
    visualizations = target / "visualizations"
    if visualizations.is_symlink():
        with tempfile.TemporaryDirectory(prefix=".visualizations-", dir=target) as scratch:
            replacement = Path(scratch) / "visualizations"
            shutil.copytree(visualizations.resolve(strict=True), replacement)
            visualizations.unlink()
            replacement.rename(visualizations)
    visualizations.mkdir(mode=0o700, exist_ok=True)
    for name in PRIVATE_ENTRIES:
        if (target / name).is_symlink():
            raise RuntimeError(f"desktop runtime entry must not be shared: {name}")
    config = target / "config.toml"
    if not config.exists() and (source / "config.toml").is_file():
        contents = clean_runtime_config((source / "config.toml").read_text())
        contents = contents.replace(str(source / ".tmp"), str(target / ".tmp"))
        tomllib.loads(contents)
        config.write_text(contents)
        config.chmod(0o600)
    for name in ["plugins", "browser"]:
        original, private = source / name, target / name
        if original.is_dir() and not private.exists():
            clone_directory(original, private)
    marketplaces = source / ".tmp" / "bundled-marketplaces"
    private_marketplaces = target / ".tmp" / "bundled-marketplaces"
    if marketplaces.is_dir() and not private_marketplaces.exists():
        private_marketplaces.parent.mkdir(mode=0o700, exist_ok=True)
        clone_directory(marketplaces, private_marketplaces)
    # Registry metadata records absolute plugin/cache paths. Relocate only
    # those paths; external marketplace locations remain unchanged.
    for path in (target / "plugins").glob("*.json"):
        text = path.read_text()
        changed = text.replace(str(source / "plugins"), str(target / "plugins"))
        changed = changed.replace(str(source / ".tmp"), str(target / ".tmp"))
        if changed != text:
            json.loads(changed)
            path.write_text(changed)
    # Seeded MCP environments can still embed the old CODEX_HOME even when
    # their package files are independent. Rebase those paths before the first
    # authenticated desktop setup refreshes the definitions.
    for path in (target / "plugins" / "cache").rglob(".mcp.json"):
        text = path.read_text()
        # Match a complete home path, not the .codex prefix of .codex-mux.
        changed = re.sub(re.escape(str(source)) + r'(?=[/":])', lambda _: str(target), text)
        if changed != text:
            json.loads(changed)
            path.write_text(changed)
    for path in source.iterdir():
        destination = target / path.name
        if path.name not in PRIVATE_ENTRIES and not destination.exists() and not destination.is_symlink():
            destination.symlink_to(path, target_is_directory=path.is_dir())


if __name__ == "__main__":
    prepare_desktop_home(Path.home() / ".codex", Path.home() / ".codex-mux" / "desktop-home")
