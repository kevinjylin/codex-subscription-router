"""Keep Chrome's native bridge compatible with the router's signing team."""

import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tarfile
import tempfile
import plistlib
import subprocess


def patch_runtime(source: str) -> str:
    """Patch the upstream lifecycle, including refresh, registry and uninstall.

    Anchors describe build 13536's functions; any drift blocks the build.
    """
    helper = "require(require(`node:path`).join(__dirname,`chrome-bridge.cjs`))"
    selector = re.compile(
        r"async function (?P<name>[\w$]+)\(e\)\{let t=[\w$]+\.Dt\(\),"
        r"n=\(0,[\w$]+\.join\)\(e.codexHome,`plugins`,`cache`\),r=e.pluginRoot;"
    )
    matches = list(selector.finditer(source))
    if len(matches) != 1:
        raise RuntimeError("could not find Chrome native-host path selector")
    match = matches[0]
    head = f"async function {match.group('name')}(e){{"
    source = source[:match.start()] + head + f"if(process.platform===`darwin`)return {helper}.selectHost(e);" + source[match.start()+len(head):]
    registration = re.compile(
        r"async function [\w$]+\(e\)\{let t=\{allowed_origins:.*?"
        r"(?P<tail>await Promise\.all\(r\.map\(async e=>\{await [\w$]+\(e,Buffer\.from\(n\)\)\}\)\),await [\w$]+\(\{manifestPath:r\[0\],nativeHostName:e.nativeHostName\}\))\}"
    )
    matches = list(registration.finditer(source))
    if len(matches) != 1:
        raise RuntimeError("could not find Chrome native-host manifest writer")
    match = matches[0]
    # The real writer is exercised by staged boot, without modifying global
    # Chrome registrations while another desktop instance is running.
    tail = match.group('tail')
    replacement = (
        "if(process.env.CODEX_MUX_LAUNCH_CHECK===`1`){"
        f"if(e.extensionHostPath!=={helper}.selectHost({{}}))throw Error(`Router Chrome host mismatch`);"
        "console.log(`codex-router-chrome-registration-ready`);return;}"
        + tail + f";{helper}.guard(r,e)"
    )
    source = source[:match.start('tail')] + replacement + source[match.end('tail'):]
    uninstall = re.compile(r"(async function [\w$]+\(e\)\{let t=[\w$]+\(e.pluginName\);if\(t==null\)return;let n=[\w$]+\.parse\(e.marketplaceName\))")
    if len(uninstall.findall(source)) != 1:
        raise RuntimeError("could not find Chrome native-host uninstall lifecycle")
    source = uninstall.sub(lambda m: m[0].replace("let n=", f"if(e.pluginName===`chrome`){helper}.stop(true);let n=", 1), source)
    # Upstream removes registry entries by plugin-cache path. Our bridge is
    # outside that cache: additionally match its exact path and owning home.
    removal = re.compile(r"(return n!=null&&n\.nativeHostNames\.includes\(t.nativeHostName\)&&)(\(0,[\w$]+\.isAbsolute\)\(n.paths.extensionHostPath\)&&[\w$]+\(n.paths.extensionHostPath,t.pluginCacheRoot\))")
    if len(removal.findall(source)) != 1:
        raise RuntimeError("could not find Chrome native-host registry removal")
    source = removal.sub(lambda m: m[1] + "((n.paths.codexHome===t.codexHome&&n.paths.extensionHostPath===" + helper + ".selectHost({}))||(" + m[2] + "))", source)
    registry = re.compile(r"(async function [\w$]+\(e\)\{)(await Promise\.all\([\w$]+\(\{codexHome:e.codexHome\}\)\.map\(async t=>\{let\{contents:n,resources:r\}=)")
    if len(registry.findall(source)) != 1:
        raise RuntimeError("could not find Chrome native-host registry writer")
    source = registry.sub(lambda m: m[1] + "if(process.env.CODEX_MUX_LAUNCH_CHECK===`1`)return;" + m[2], source)
    return source


def validate_bridge(app: Path) -> bool:
    """Verify the packaged compatibility bridge before a staged boot can pass."""
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    team = info.get("CodexMuxSigningTeamIdentifier")
    archive = app / "Contents/Resources/plugin-signatures/openai-bundled/chrome/plugin.tar.gz"
    if team in (None, "adhoc") or not archive.is_file():
        return False
    bridge = app / BRIDGE_RELATIVE
    data = bridge.read_bytes()
    shim = team_comparison_shim(team)
    if data[SHIM_OFFSET:SHIM_OFFSET + len(shim)] != shim or any(
        data[call:call + 4] != branch(call, SHIM_OFFSET, link=True) for call in TEAM_COMPARISONS
    ):
        raise RuntimeError("packaged Chrome bridge does not match the router signing team")
    subprocess.run(["codesign", "--verify", "--strict", str(bridge)], check=True, capture_output=True)
    return True


# ChatGPT 26.1002.52244 arm64. Refuse changed code instead of bypassing checks.
HOST_SHA256 = "ff06f508870eef77fc2575cd0706dc277ab12cdfc8ee0662ffc25d52f94a6efc"
SHIM_OFFSET = 0xAFD00
MEMCMP_OFFSET = 0x85560
TEAM_COMPARISONS = (0x2A104, 0x2A16C)
BRIDGE_RELATIVE = Path("Contents/Resources/router-chrome-bridge/ChatGPT for Chrome")
_SHIM = bytes.fromhex(
    "5f2800f1e1020054090040f90a1040794b8688d26b88a6f26b46c6f2eb88e9f2"
    "3f010beb8c49865240014c7aa1010054290040f92a1040796ba886d28bc6a6f2"
    "eba6c9f2cb2ae7f23f010bebcc888a5240014c7a6100005400008052c0035fd600000014"
)


def branch(source: int, target: int, *, link: bool = False) -> bytes:
    delta = target - source
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise RuntimeError("Chrome bridge branch target is out of range")
    return struct.pack("<I", (0x94000000 if link else 0x14000000) | ((delta // 4) & 0x3FFFFFF))


def team_comparison_shim(team: str) -> bytes:
    """Accept the exact router team when comparing an OpenAI team tuple.

    All other comparisons still call memcmp. Existing code-identifier and
    parent-process checks remain in the original native authorizer.
    """
    if re.fullmatch(r"[A-Z0-9]{10}", team) is None:
        raise RuntimeError("Chrome bridge requires a ten-character Apple signing team")
    code = bytearray(_SHIM)
    words = [int.from_bytes(team[i:i + 2].encode("ascii"), "little") for i in range(0, 10, 2)]
    for offset, opcode, word in zip(
        (56, 60, 64, 68, 76),
        (0xD280000B, 0xF2A0000B, 0xF2C0000B, 0xF2E0000B, 0x5280000C),
        words,
    ):
        struct.pack_into("<I", code, offset, opcode | word << 5)
    code[-4:] = branch(SHIM_OFFSET + len(code) - 4, MEMCMP_OFFSET)
    return bytes(code)


def patch_bridge(data: bytes, team: str) -> bytes:
    shim = team_comparison_shim(team)
    if hashlib.sha256(data).hexdigest() != HOST_SHA256:
        raise RuntimeError("unsupported Chrome native bridge; its signing checks need review")
    if data[SHIM_OFFSET:SHIM_OFFSET + len(shim)] != bytes(len(shim)):
        raise RuntimeError("Chrome bridge padding changed")
    patched = bytearray(data)
    for call in TEAM_COMPARISONS:
        if data[call:call + 4] != branch(call, MEMCMP_OFFSET, link=True):
            raise RuntimeError("Chrome bridge team comparison changed")
        patched[call:call + 4] = branch(call, SHIM_OFFSET, link=True)
    patched[SHIM_OFFSET:SHIM_OFFSET + len(shim)] = shim
    return bytes(patched)


def build_bridge(app: Path, team: str) -> Path | None:
    archive = app / "Contents/Resources/plugin-signatures/openai-bundled/chrome/plugin.tar.gz"
    if not archive.is_file():
        return None
    with tarfile.open(archive) as package:
        member = package.extractfile("extension-host/macos/arm64/ChatGPT for Chrome")
        if member is None:
            raise RuntimeError("bundled Chrome native bridge is missing")
        patched = patch_bridge(member.read(), team)
    target = app / BRIDGE_RELATIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(patched)
    target.chmod(0o755)
    return target


def register_bridge(app: Path, home: Path) -> None:
    bridge = app / BRIDGE_RELATIVE
    manifest = home / "Library/Application Support/Google/Chrome/NativeMessagingHosts/com.openai.codexextension.json"
    if not bridge.is_file() or not manifest.is_file():
        return
    data = json.loads(manifest.read_text())
    if data.get("name") != "com.openai.codexextension" or data.get("type") != "stdio":
        raise RuntimeError("unexpected Chrome native-host registration")
    data["path"] = str(bridge)
    # Preserve every existing extension origin and replace only after writing.
    with tempfile.NamedTemporaryFile(mode="w", dir=manifest.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            json.dump(data, temporary, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fchmod(temporary.fileno(), manifest.stat().st_mode & 0o777)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary_path, manifest)
    finally:
        temporary_path.unlink(missing_ok=True)
