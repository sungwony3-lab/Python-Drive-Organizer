import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = PROJECT_ROOT / "gpt_action_openapi.yaml"
INSTRUCTIONS_PATH = PROJECT_ROOT / "GPTS_INSTRUCTIONS.md"


class GptActionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = SCHEMA_PATH.read_text(encoding="utf-8")
        cls.instructions = INSTRUCTIONS_PATH.read_text(encoding="utf-8")

    def test_expected_21_unique_operation_ids(self) -> None:
        operation_ids = re.findall(r"^\s+operationId:\s+(\S+)\s*$", self.schema, re.MULTILINE)

        self.assertEqual(len(operation_ids), 21)
        self.assertEqual(len(set(operation_ids)), 21)
        self.assertTrue(
            {
                "searchContacts",
                "getContact",
                "getContactsStatus",
                "previewTextEmail",
                "sendTextEmail",
                "getDriveTreePage",
                "exportDriveTree",
                "returnDriveTreeExport",
            }.issubset(
                operation_ids
            )
        )

    def test_tree_page_export_and_download_contract(self) -> None:
        for path in (
            "/folders/tree/page:",
            "/exports/drive-tree:",
            "/exports/{export_id}/openai-file:",
        ):
            self.assertIn(path, self.schema)
        for operation_id in (
            "getDriveTreePage",
            "exportDriveTree",
            "returnDriveTreeExport",
        ):
            self.assertRegex(
                self.schema,
                rf"operationId:\s+{operation_id}[\s\S]{{0,700}}?"
                r"x-openai-isConsequential:\s+false",
            )
        self.assertRegex(
            self.schema,
            r"operationId:\s+returnDriveTreeExport[\s\S]{0,1000}?"
            r"\$ref:\s+[\"']?#/components/schemas/OpenAIFileResponse",
        )
        self.assertNotIn("operationId: downloadDriveTreeExport", self.schema)
        self.assertRegex(
            self.schema,
            r"openaiFileResponse:[\s\S]{0,500}?type:\s+array",
        )
        self.assertRegex(
            self.schema,
            r"OpenAIFileItem:[\s\S]{0,600}?name:[\s\S]*?"
            r"mime_type:[\s\S]*?content:[\s\S]*?format:\s+byte",
        )
        for phrase in (
            "`getDriveTreePage`",
            "exact `next_cursor`",
            "`exportDriveTree`",
            "`returnDriveTreeExport`",
            "raw binary endpoint는 GPT가 호출하지 않는다",
            "페이지를 모두 받아 GPT가 조립하지 않는다",
        ):
            self.assertIn(phrase, self.instructions)

    def test_contacts_paths_are_read_only_actions(self) -> None:
        for operation_id in ("searchContacts", "getContact", "getContactsStatus"):
            pattern = rf"operationId:\s+{operation_id}[\s\S]{{0,500}}?x-openai-isConsequential:\s+false"
            self.assertRegex(self.schema, pattern)

        self.assertIn("/contacts/search:", self.schema)
        self.assertIn("/contacts/status:", self.schema)
        self.assertIn("/contacts/{contact_id}:", self.schema)
        self.assertNotIn("components:\n  parameters:", self.schema)

    def test_server_bearer_and_email_consequential_contract_are_unchanged(self) -> None:
        self.assertIn("url: https://drive-api.sungwony.pe.kr", self.schema)
        self.assertRegex(
            self.schema,
            r"BearerAuth:\s*\n\s+type:\s+http\s*\n\s+scheme:\s+bearer",
        )
        self.assertRegex(
            self.schema,
            r"operationId:\s+previewEmailWithFiles[\s\S]{0,500}?x-openai-isConsequential:\s+false",
        )
        self.assertRegex(
            self.schema,
            r"operationId:\s+sendEmailWithFiles[\s\S]{0,500}?x-openai-isConsequential:\s+true",
        )
        self.assertRegex(
            self.schema,
            r"operationId:\s+previewTextEmail[\s\S]{0,500}?x-openai-isConsequential:\s+false",
        )
        self.assertRegex(
            self.schema,
            r"operationId:\s+sendTextEmail[\s\S]{0,500}?x-openai-isConsequential:\s+true",
        )

    def test_contacts_public_schema_and_limit_are_present(self) -> None:
        for schema_name in (
            "ContactSearchRequest",
            "ContactItem",
            "ContactSearchResponse",
            "ContactsStatusResponse",
            "ContactErrorResponse",
        ):
            self.assertIsNotNone(
                re.search(
                    rf"^    {schema_name}:$",
                    self.schema,
                    re.MULTILINE,
                )
            )
        self.assertRegex(
            self.schema,
            r"ContactSearchRequest:[\s\S]*?limit:[\s\S]*?minimum:\s+1[\s\S]*?maximum:\s+100[\s\S]*?default:\s+20",
        )

    def test_instructions_require_search_disambiguation_and_exact_reread(self) -> None:
        required = (
            "반드시 해당 사람마다 `searchContacts`",
            "첫 번째 후보를 자동 선택하지 않는다",
            "`previewEmailWithFiles` 호출 직전에 각각 `getContact`",
            "`email_usable=false`",
            "`conflict_code`",
            "직접 입력",
            "getContactsStatus",
            "정확한 `preview_id`",
            "링크를 가진 누구나 열람",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.instructions)

    def test_instructions_keep_email_limits_and_send_confirmation(self) -> None:
        self.assertIn("To는 정확히 1명", self.instructions)
        self.assertIn("CC는 최대 5명", self.instructions)
        self.assertIn("파일은 1~5개", self.instructions)
        self.assertIn("사용자의 명확한 승인 후에만", self.instructions)
        self.assertIn("Preview ID", self.instructions)

    def test_instructions_fit_gpt_builder_character_limit(self) -> None:
        self.assertLessEqual(
            len(self.instructions),
            8000,
            "GPTS_INSTRUCTIONS.md must not exceed the GPT Builder 8,000-character limit",
        )

    def test_plain_email_schema_and_instructions_are_present(self) -> None:
        self.assertIn("/email/send-text/preview:", self.schema)
        self.assertIn("/email/send-text:", self.schema)
        for schema_name in (
            "TextEmailPreviewRequest",
            "TextEmailSendRequest",
            "TextEmailPreviewResponse",
            "TextEmailSendResponse",
        ):
            self.assertIsNotNone(
                re.search(rf"^    {schema_name}:$", self.schema, re.MULTILINE)
            )
        for phrase in (
            "`previewTextEmail` → `sendTextEmail`",
            "가짜·빈 `file_id`를 만들지 않는다",
            "첨부 없음",
            "Drive Link 없음",
            "직전 성공한 `previewTextEmail`의 exact `preview_id`",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.instructions)


if __name__ == "__main__":
    unittest.main()
