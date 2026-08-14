import unittest

from name_parser import PARSER_VERSION, parse_filename


class NameParserTests(unittest.TestCase):
    def assert_revision(self, name: str, number: int) -> None:
        result = parse_filename(name)
        self.assertEqual(result["revision_type"], "REVISION")
        self.assertEqual(result["revision_number"], number)
        self.assertEqual(result["base_name"], "ABC")

    def test_plain_file(self) -> None:
        result = parse_filename("ABC.pdf")
        self.assertEqual(result["base_name"], "ABC")
        self.assertEqual(result["revision_type"], "NONE")
        self.assertIsNone(result["revision_number"])
        self.assertEqual(result["copy_type"], "NONE")
        self.assertIsNone(result["copy_number"])
        self.assertEqual(result["auto_action"], "NONE")
        self.assertEqual(result["parser_version"], PARSER_VERSION)

    def test_revision_suffixes(self) -> None:
        cases = {
            "ABC R1.pdf": 1,
            "ABC R.2.pdf": 2,
            "ABC REV3.pdf": 3,
            "ABC REV.04.pdf": 4,
            "ABC r1.pdf": 1,
            "ABC rev.02.pdf": 2,
            "ABC REV 06.pdf": 6,
            "ABC (R.3).pdf": 3,
            "ABC_R4.pdf": 4,
            "ABC-REV05.pdf": 5,
        }
        for name, number in cases.items():
            with self.subTest(name=name):
                self.assert_revision(name, number)

    def test_revision_is_suffix_only(self) -> None:
        result = parse_filename("R2 변압기 설치도면.pdf")
        self.assertEqual(result["revision_type"], "NONE")
        self.assertEqual(result["base_name"], "R2 변압기 설치도면")

    def test_copy_words(self) -> None:
        korean = parse_filename("ABC - 복사본.pdf")
        english = parse_filename("ABC copy.pdf")
        self.assertEqual(korean["copy_type"], "KOREAN_COPY")
        self.assertEqual(korean["base_name"], "ABC")
        self.assertEqual(korean["auto_action"], "NONE")
        self.assertEqual(english["copy_type"], "ENGLISH_COPY")
        self.assertEqual(english["base_name"], "ABC")
        self.assertEqual(english["auto_action"], "NONE")

    def test_single_parenthesized_copy(self) -> None:
        for name, number in (
            ("ABC (1).pdf", 1),
            ("ABC (2).pdf", 2),
            ("ABC (12).pdf", 12),
        ):
            with self.subTest(name=name):
                result = parse_filename(name)
                self.assertEqual(result["copy_type"], "SINGLE_PAREN_COPY")
                self.assertEqual(result["copy_number"], number)
                self.assertEqual(result["base_name"], "ABC")
                self.assertEqual(result["auto_action"], "DELETE")

    def test_multiple_parenthesized_suffix_is_not_auto_delete(self) -> None:
        for name in (
            "ABC (1)(2).pdf",
            "ABC (1) (2).pdf",
            "ABC (1)(1).pdf",
            "ABC (1) (1).pdf",
        ):
            with self.subTest(name=name):
                result = parse_filename(name)
                self.assertEqual(result["copy_type"], "NONE")
                self.assertIsNone(result["copy_number"])
                self.assertEqual(result["auto_action"], "NONE")

    def test_revision_and_single_copy(self) -> None:
        result = parse_filename("ABC R2 (1).pdf")
        self.assertEqual(result["revision_type"], "REVISION")
        self.assertEqual(result["revision_number"], 2)
        self.assertEqual(result["copy_type"], "SINGLE_PAREN_COPY")
        self.assertEqual(result["copy_number"], 1)
        self.assertEqual(result["base_name"], "ABC")
        self.assertEqual(result["auto_action"], "DELETE")

    def test_trailing_digits_are_not_revision(self) -> None:
        for name, expected_base in (
            ("테스트1.pdf", "테스트1"),
            ("테스트2.pdf", "테스트2"),
            ("테스트3.pdf", "테스트3"),
            ("분전반1.pdf", "분전반1"),
            ("분전반2.pdf", "분전반2"),
            ("2026 보고서.pdf", "2026 보고서"),
            ("260402_그린동 배치협의안.pdf", "260402_그린동 배치협의안"),
        ):
            with self.subTest(name=name):
                result = parse_filename(name)
                self.assertEqual(result["revision_type"], "NONE")
                self.assertIsNone(result["revision_number"])
                self.assertEqual(result["base_name"], expected_base)

    def test_normalized_name(self) -> None:
        result = parse_filename("  CDC   모듈샾장  900KVA 변대설치 (R.3).dwg  ")
        self.assertEqual(
            result["normalized_name"],
            "cdc 모듈샾장 900kva 변대설치 (r.3).dwg",
        )
        self.assertEqual(result["base_name"], "CDC 모듈샾장 900KVA 변대설치")

    def test_extensionless_and_korean_names(self) -> None:
        self.assertEqual(
            parse_filename("확장자 없는 파일명")["base_name"],
            "확장자 없는 파일명",
        )
        self.assertEqual(
            parse_filename("한글 파일명")["normalized_name"],
            "한글 파일명",
        )

    def test_explicit_extension_argument(self) -> None:
        result = parse_filename("ABC REV.04.PDF", "pdf")
        self.assertEqual(result["base_name"], "ABC")
        self.assertEqual(result["revision_number"], 4)

    def test_deterministic_result(self) -> None:
        name = "ABC R2 (1).pdf"
        self.assertEqual(parse_filename(name), parse_filename(name))


if __name__ == "__main__":
    unittest.main()
