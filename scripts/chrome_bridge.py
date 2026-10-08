"""Keep Chrome's native bridge compatible with the router's signing team."""

import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tarfile
import tempfile


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
