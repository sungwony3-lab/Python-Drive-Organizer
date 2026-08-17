import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


class MigrationSetupContractTests(unittest.TestCase):
    def read(self, name: str) -> str:
        return (PROJECT_ROOT / name).read_text(encoding="utf-8-sig")

    def test_scripts_are_path_independent(self) -> None:
        for name in (
            "setup_windows.ps1",
            "verify_install.ps1",
            "prepare_migration.ps1",
            "uninstall_tasks.ps1",
        ):
            with self.subTest(name=name):
                source = self.read(name)
                self.assertIn("$PSScriptRoot", source)
                self.assertNotIn(r"C:\Users\HLB", source)
                self.assertNotIn("C:\\Users\\", source)

    def test_setup_rebuilds_venv_and_installs_requirements(self) -> None:
        source = self.read("setup_windows.ps1")
        self.assertIn("-m venv", source)
        self.assertIn("-m pip install -r", source)
        self.assertIn("-m pip check", source)
        self.assertIn("Python 3.10 or newer", source)
        self.assertIn("Existing .venv appears copied or broken", source)

    def test_task_contracts_match_operating_configuration(self) -> None:
        source = self.read("setup_windows.ps1")
        self.assertIn("Python Drive Organizer API", source)
        self.assertIn("Python Drive Organizer Daily Refresh", source)
        self.assertIn(
            "-m uvicorn api_server:app --host 127.0.0.1 --port 8000",
            source,
        )
        self.assertIn("daily_refresh.py", source)
        self.assertIn("PT20S", source)
        self.assertIn("-Daily -At '08:00'", source)
        self.assertIn("-MultipleInstances IgnoreNew", source)
        self.assertIn("-StartWhenAvailable", source)

    def test_setup_never_embeds_or_prints_secret_values(self) -> None:
        source = self.read("setup_windows.ps1")
        self.assertNotRegex(source, r"ya29\.[A-Za-z0-9_-]+")
        self.assertNotRegex(source, r"eyJ[A-Za-z0-9_-]{20,}\.")
        self.assertNotRegex(source, r"PDO_API_KEY\s*=\s*[A-Za-z0-9_-]{32,}")
        self.assertIn("[REDACTED]", source)
        self.assertNotIn("Get-Content -Raw -LiteralPath $secret", source)

    def test_verify_is_non_destructive(self) -> None:
        source = self.read("verify_install.ps1")
        forbidden = (
            "send_enhanced_email(",
            "send_prepared_email(",
            "create_anyone_reader_permission(",
            "/email/send-file",
            "/email/send-files",
            "permissions().create",
            "files().update",
            "files().delete",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, source)
        self.assertIn("mode=ro", source)
        self.assertIn("No email was sent", source)

    def test_prepare_migration_does_not_build_private_archive(self) -> None:
        source = self.read("prepare_migration.ps1")
        self.assertNotIn("Compress-Archive", source)
        self.assertIn("No PRIVATE_MIGRATION_BUNDLE", source)
        for name in (
            ".env",
            "credentials.json",
            "token.json",
            "drive_download_token.json",
            "gmail_send_token.json",
            "drive_share_token.json",
            "data\\drive_index.db",
            "data\\email_send_state.db",
            "data\\enhanced_email_state.db",
        ):
            self.assertIn(name, source)

    def test_uninstall_requires_explicit_switch_and_preserves_data(self) -> None:
        source = self.read("uninstall_tasks.ps1")
        self.assertIn("$ConfirmRemoval", source)
        self.assertIn("Unregister-ScheduledTask", source)
        self.assertNotIn("Remove-Item", source)
        self.assertIn("SQLite databases", source)

    def test_requirements_include_direct_runtime_imports(self) -> None:
        requirements = {
            line.strip().lower()
            for line in self.read("requirements.txt").splitlines()
            if line.strip() and not line.startswith("#")
        }
        expected = {
            "google-api-python-client",
            "google-auth",
            "google-auth-httplib2",
            "google-auth-oauthlib",
            "fastapi",
            "pydantic",
            "uvicorn",
            "httpx",
            "python-dotenv",
            "python-docx",
            "openpyxl",
        }
        self.assertEqual(requirements, expected)

    def test_docs_separate_manual_online_work(self) -> None:
        manual = self.read("MANUAL_ONLINE_SETUP.md")
        guide = self.read("MIGRATION_GUIDE.md")
        for term in (
            "Google OAuth",
            "Cloudflare",
            "GPT Builder",
            "drive_share_token.json",
        ):
            self.assertIn(term, manual)
        self.assertIn("enhanced_email_state.db", guide)
        self.assertIn("idempotency", guide)
        self.assertIn(".venv", guide)

    def test_no_pasted_secret_like_values_in_migration_artifacts(self) -> None:
        names = (
            "setup_windows.ps1",
            "verify_install.ps1",
            "prepare_migration.ps1",
            "uninstall_tasks.ps1",
            "MANUAL_ONLINE_SETUP.md",
            "MIGRATION_GUIDE.md",
        )
        secret_patterns = (
            re.compile(r"ya29\.[A-Za-z0-9_-]+"),
            re.compile(r'"refresh_token"\s*:\s*".+"'),
            re.compile(r'"client_secret"\s*:\s*".+"'),
            re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        )
        for name in names:
            source = self.read(name)
            for pattern in secret_patterns:
                with self.subTest(name=name, pattern=pattern.pattern):
                    self.assertIsNone(pattern.search(source))


if __name__ == "__main__":
    unittest.main()
