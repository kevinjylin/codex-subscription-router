"""Regressions for certificate selection and actual Apple signing teams."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import patch_app


class SigningTests(unittest.TestCase):
    def test_explicit_adhoc_never_looks_up_a_certificate(self):
        with patch.dict(os.environ, {"CODEX_MUX_SIGNING_IDENTITY": "-"}), patch.object(
            patch_app, "output"
        ) as lookup:
            identity = patch_app.resolve_signing_identity(True)
            self.assertEqual(identity, "-")
            self.assertIsNone(patch_app.signing_team_identifier(identity))
            lookup.assert_not_called()

    def test_allow_adhoc_is_a_fallback_not_a_certificate_override(self):
        identity = "Apple Development: person@example.com (8X3B6MM3HZ)"
        with patch.dict(os.environ, {"CODEX_MUX_SIGNING_IDENTITY": ""}), patch.object(
            patch_app, "output", return_value=f'  1) ABCDEF "{identity}"'
        ):
            self.assertEqual(patch_app.resolve_signing_identity(True), identity)

    def test_team_comes_from_the_signed_probe_not_the_display_name(self):
        identity = "Apple Development: person@example.com (8X3B6MM3HZ)"
        result = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(patch_app.subprocess, "run", return_value=result) as sign, patch.object(
            patch_app, "signed_code_metadata", return_value=("probe", "C5467MV9FT")
        ) as metadata:
            self.assertEqual(patch_app.signing_team_identifier(identity), "C5467MV9FT")
            self.assertIn(identity, sign.call_args.args[0])
            self.assertEqual(sign.call_args.args[0][-1], str(metadata.call_args.args[0]))

    def test_identity_hash_does_not_need_a_parenthesized_team(self):
        result = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(patch_app.subprocess, "run", return_value=result), patch.object(
            patch_app, "signed_code_metadata", return_value=("probe", "C5467MV9FT")
        ):
            self.assertEqual(patch_app.signing_team_identifier("A" * 40), "C5467MV9FT")

    def test_failed_signing_keeps_the_certificate_error(self):
        result = subprocess.CompletedProcess([], 1, stdout="", stderr="certificate unavailable")
        with patch.object(patch_app.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "certificate unavailable"):
                patch_app.signing_team_identifier("certificate")

    def test_missing_team_fails_closed(self):
        result = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(patch_app.subprocess, "run", return_value=result), patch.object(
            patch_app, "signed_code_metadata", return_value=("probe", None)
        ):
            with self.assertRaisesRegex(RuntimeError, "did not produce an Apple team"):
                patch_app.signing_team_identifier("certificate")


class NativePipeSigningTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.app = Path(scratch.name) / "Router.app"
        self.module = self.app / "Contents/Resources/native/browser-use-peer-authorization.node"
        self.module.parent.mkdir(parents=True)

    def test_only_the_team_changes_and_the_module_is_resigned(self):
        original_code = bytes.fromhex("4d8688d26d88a6f26d46c6f2ed88e9f28e498652")
        replacement_code = bytes.fromhex("6da886d28dc6a6f2eda6c9f2cd2ae7f2ce888a52")
        data = b"ancestry-check\0" + b"2DC432GLL2" + original_code + b"\0identifier-check"
        self.module.write_bytes(data)
        with patch.object(patch_app, "run") as run, patch.object(
            patch_app, "sign_runtime_executable"
        ) as sign:
            patch_app.patch_native_pipe_signing_team(self.app, "certificate", "C5467MV9FT")
            self.assertEqual(self.module.read_bytes(), data.replace(b"2DC432GLL2", b"C5467MV9FT").replace(original_code, replacement_code))
            sign.assert_called_once_with(self.module, "certificate", entitlements=None)
            self.assertEqual(run.call_args_list[0].args[0][1], "--remove-signature")
            self.assertEqual(run.call_args_list[-1].args[0][1:3], ["--verify", "--strict"])

    def test_changed_binary_layout_fails_before_patching_or_signing(self):
        for data in (b"no-team", b"2DC432GLL2", b"2DC432GLL2\0duplicate\0" + b"2DC432GLL2"):
            with self.subTest(data=data):
                self.module.write_bytes(data)
                with patch.object(patch_app, "run"), patch.object(
                    patch_app, "sign_runtime_executable"
                ) as sign:
                    with self.assertRaisesRegex(RuntimeError, "exactly one"):
                        patch_app.patch_native_pipe_signing_team(self.app, "certificate", "C5467MV9FT")
                    self.assertEqual(self.module.read_bytes(), data)
                    sign.assert_not_called()

    def test_invalid_team_leaves_the_module_untouched(self):
        with patch.object(patch_app, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "ten-character"):
                patch_app.patch_native_pipe_signing_team(self.app, "certificate", "invalid")
            run.assert_not_called()


class StagedSigningTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.stage = root / "stage"
        self.destination = root / "installed" / "Router.app"
        self.destination.mkdir(parents=True)
        (self.stage / self.destination.name).mkdir(parents=True)
        (self.stage / patch_app.COMPUTER_USE_APP_NAME).mkdir()

    def install(self, installed_team, staged_team, allow=False):
        with patch.object(
            patch_app, "existing_signing_team", side_effect=[installed_team, staged_team]
        ), patch.object(patch_app, "ensure_components_are_stopped") as stopped, patch.object(
            patch_app, "install_built"
        ) as swap, patch.object(Path, "rmdir"):
            patch_app.install_staged(self.stage, self.destination, allow)
            stopped.assert_called_once()
            swap.assert_called_once()

    def test_same_team_update_does_not_need_override(self):
        self.install("C5467MV9FT", "C5467MV9FT")

    def test_adhoc_to_certificate_is_rejected_before_stopping_apps(self):
        with patch.object(
            patch_app, "existing_signing_team", side_effect=[None, "C5467MV9FT"]
        ), patch.object(patch_app, "ensure_components_are_stopped") as stopped, patch.object(
            patch_app, "install_built"
        ) as swap:
            with self.assertRaisesRegex(RuntimeError, "allow-signing-team-change"):
                patch_app.install_staged(self.stage, self.destination)
            stopped.assert_not_called()
            swap.assert_not_called()

    def test_explicit_team_change_installs_the_prepared_pair(self):
        self.install(None, "C5467MV9FT", allow=True)

    def test_cli_forwards_explicit_team_change_to_staged_install(self):
        with patch("sys.argv", ["patch_app.py", "--install-staged", str(self.stage),
                                "--destination", str(self.destination),
                                "--allow-signing-team-change"]), patch.object(
            patch_app, "install_staged"
        ) as install:
            self.assertEqual(patch_app.main(), 0)
            install.assert_called_once_with(self.stage, self.destination, True)

if __name__ == "__main__":
    unittest.main()
