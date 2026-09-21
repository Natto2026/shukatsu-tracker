"""CSV の取り込みの検証。

解析は DB なしで、書き込みは実際の DB で確かめる。見るのは
「黙って捨てない・黙って丸めない」ことと「途中で失敗したら何も残らない」こと。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shukatsu_tracker.db import DuplicateKeyError
from shukatsu_tracker.models import Company
from shukatsu_tracker.services import CsvFormatError, CsvImportService
from shukatsu_tracker.services.csv_import import (
    ImportPlan,
    PlannedCompany,
    PlannedStep,
    column_guide,
    parse_csv,
)

SAMPLE = Path(__file__).parent.parent / "docs" / "sample_import.csv"

BASIC = (
    "企業名,業界,志望度,ステップ,締切,結果\n"
    "アオゾラ電機,メーカー,S,ES,2026-10-01,通過\n"
    "アオゾラ電機,,,1次面接,,\n"
    "ミカヅキ銀行,金融,A,ES,2026-10-05,\n"
)


def plan_of(text: str, **kwargs) -> ImportPlan:
    return parse_csv(text.encode("utf-8"), **kwargs)


def reasons(plan: ImportPlan) -> dict[int, str]:
    return {row.line: row.reason for row in plan.skipped}


@pytest.fixture
def importer(conn) -> CsvImportService:
    return CsvImportService(conn)


class TestEncoding:
    @pytest.mark.parametrize(
        ("codec", "label"),
        [("utf-8", "UTF-8"), ("utf-8-sig", "UTF-8"), ("cp932", "Shift-JIS")],
    )
    def test_utf8_with_and_without_bom_and_shift_jis_are_read(self, codec, label):
        plan = parse_csv(BASIC.encode(codec))
        assert plan.encoding == label
        assert [c.company.name for c in plan.companies] == ["アオゾラ電機", "ミカヅキ銀行"]

    def test_undecodable_bytes_are_reported(self):
        with pytest.raises(CsvFormatError, match="文字コード"):
            parse_csv(b"\xff\xfe\x00\x81")

    def test_an_excel_workbook_is_reported_as_such(self):
        with pytest.raises(CsvFormatError, match="xlsx"):
            parse_csv(b"PK\x03\x04" + b"\x00" * 16)


class TestHeaders:
    def test_header_aliases_are_accepted(self):
        plan = plan_of(
            "会社名,業種,優先度,経路,選考ステップ,締切日,選考結果\nアオゾラ電機,メーカー,S,学校推薦,ES,2026-10-01,通過\n"
        )
        company = plan.companies[0]
        assert (company.company.industry, company.company.priority, company.company.route) == (
            "メーカー",
            "S",
            "学校推薦",
        )
        assert company.steps == (PlannedStep("ES", "2026-10-01", "通過", "", 2),)

    def test_full_width_and_spaces_in_headers_are_tolerated(self):
        plan = plan_of(" 企業名 ,ステップ　\nアオゾラ電機,ES\n")
        assert plan.companies[0].steps[0].name == "ES"

    def test_missing_required_column_is_reported_with_the_headers_found(self):
        with pytest.raises(CsvFormatError, match="企業名") as error:
            plan_of("名前,ステップ\nアオゾラ電機,ES\n")
        assert "名前" in str(error.value)

    def test_two_headers_for_the_same_field_are_rejected(self):
        with pytest.raises(CsvFormatError, match="同じ項目"):
            plan_of("企業名,会社名\nアオゾラ電機,アオゾラ電機\n")

    def test_empty_file_is_reported(self):
        with pytest.raises(CsvFormatError, match="見出し"):
            parse_csv(b"")

    def test_every_guide_header_is_understood_by_the_parser(self):
        """画面に出す列の説明と、解析が受け付ける見出しがずれていないこと。"""
        for guide in column_guide():
            for header in (guide.header, *guide.aliases):
                columns = header if guide.required else f"企業名,{header}"
                assert plan_of(columns + "\n").ignored_columns == (), header


class TestCredentialsAreNotImported:
    CSV = (
        "企業名,マイページURL,ログインID,パスワード,登録メール,ステップ\n"
        "アオゾラ電機,https://example.com/mypage,user01,hunter2,someone@example.com,ES\n"
    )

    def test_credential_columns_never_reach_the_plan(self):
        plan = plan_of(self.CSV)
        assert "hunter2" not in repr(plan)
        assert "user01" not in repr(plan)
        assert "example.com" not in repr(plan)
        assert plan.companies[0].company.mypage_url == ""
        assert plan.companies[0].company.login_email == ""

    def test_credential_columns_are_listed_as_not_imported(self):
        plan = plan_of(self.CSV)
        assert {c.header for c in plan.ignored_columns} == {
            "マイページURL",
            "ログインID",
            "パスワード",
            "登録メール",
        }
        assert all("認証情報" in c.reason for c in plan.ignored_columns)

    def test_unknown_columns_are_listed_not_silently_dropped(self):
        plan = plan_of("企業名,年収,ステップ\nアオゾラ電機,非公開,ES\n")
        assert [(c.header, c.reason) for c in plan.ignored_columns] == [
            ("年収", "対応する項目がないため取り込まない")
        ]

    def test_credentials_are_not_stored(self, importer, selection):
        importer.apply(importer.preview(self.CSV.encode("cp932")))
        company = selection.companies()[0]
        assert (company.mypage_url, company.login_email) == ("", "")


class TestRows:
    def test_rows_with_the_same_name_become_one_company(self):
        plan = plan_of(BASIC)
        first = plan.companies[0]
        assert first.company == Company(name="アオゾラ電機", industry="メーカー", priority="S")
        assert [s.name for s in first.steps] == ["ES", "1次面接"]
        assert first.steps[1].result == "選考中"
        assert plan.step_count == 3
        assert plan.skipped == ()

    def test_blank_cells_take_the_same_defaults_as_the_form(self):
        company = plan_of("企業名\nアオゾラ電機\n").companies[0]
        assert company.company == Company(name="アオゾラ電機")
        assert company.steps == ()

    def test_blank_rows_are_counted_and_skipped(self):
        plan = plan_of("企業名,ステップ\n\nアオゾラ電機,ES\n,\n  ,  \n")
        assert plan.blank_rows == 3
        assert plan.skipped == ()
        assert len(plan.companies) == 1

    def test_line_numbers_match_the_spreadsheet_rows(self):
        plan = plan_of('企業名,メモ,ステップ\nアオゾラ電機,"改行を\n含むメモ",ES\n,,ES\n')
        assert reasons(plan) == {3: "企業名が空です"}

    def test_blank_company_name_is_reported_with_its_line(self):
        plan = plan_of("企業名,ステップ\n,ES\nアオゾラ電機,ES\n")
        assert reasons(plan) == {2: "企業名が空です"}
        assert [c.company.name for c in plan.companies] == ["アオゾラ電機"]

    @pytest.mark.parametrize("value", ["2026/10/01", "10月1日", "2026-13-01", "2026-02-30", "未定"])
    def test_unreadable_deadline_is_reported_not_nulled(self, value):
        plan = plan_of(f"企業名,ステップ,締切\nアオゾラ電機,ES,{value}\n")
        assert plan.companies == ()
        assert value in reasons(plan)[2]
        assert "YYYY-MM-DD" in reasons(plan)[2]

    def test_deadline_is_normalised_to_iso(self):
        plan = plan_of("企業名,ステップ,締切\nアオゾラ電機,ES,2026-1-5\n")
        assert plan.companies[0].steps[0].deadline == "2026-01-05"

    @pytest.mark.parametrize(
        ("column", "value"),
        [
            ("業界", "IT"),
            ("志望度", "高"),
            ("応募経路", "ナビサイト"),
            ("適性検査", "spi3"),
            ("結果", "合格"),
        ],
    )
    def test_value_outside_the_choices_is_reported_not_defaulted(self, column, value):
        plan = plan_of(f"企業名,ステップ,{column}\nアオゾラ電機,ES,{value}\n")
        assert plan.companies == ()
        assert f"{column}「{value}」は選択肢にありません" in reasons(plan)[2]

    def test_step_details_without_a_step_name_are_reported(self):
        plan = plan_of("企業名,ステップ,締切\nアオゾラ電機,,2026-10-01\n")
        assert plan.companies == ()
        assert "ステップ名が空" in reasons(plan)[2]

    def test_cells_beyond_the_header_are_reported(self):
        plan = plan_of("企業名,ステップ\nアオゾラ電機,ES,2026-10-01\n")
        assert plan.companies == ()
        assert "見出しより右" in reasons(plan)[2]

    def test_one_bad_row_holds_back_the_whole_company(self):
        """半分だけ入れない。直して取り込み直すと「登録済み」に当たり、残りを入れられなくなるため。"""
        plan = plan_of(
            "企業名,ステップ,締切\n"
            "アオゾラ電機,ES,2026-10-01\n"
            "アオゾラ電機,1次面接,来週\n"
            "ミカヅキ銀行,ES,2026-10-05\n"
        )
        assert [c.company.name for c in plan.companies] == ["ミカヅキ銀行"]
        assert "来週" in reasons(plan)[3]
        assert "3 行目に問題があるため" in reasons(plan)[2]


class TestDuplicates:
    def test_existing_company_is_reported_not_overwritten(self):
        plan = plan_of(BASIC, existing_names={"アオゾラ電機"})
        assert [c.company.name for c in plan.companies] == ["ミカヅキ銀行"]
        assert set(reasons(plan)) == {2, 3}
        assert all("すでに登録されています" in reason for reason in reasons(plan).values())

    def test_repeated_step_within_a_company_is_reported(self):
        plan = plan_of("企業名,ステップ\nアオゾラ電機,ES\nアオゾラ電機,ES\n")
        assert plan.companies == ()
        assert "2 行目と重複" in reasons(plan)[3]

    def test_conflicting_company_attributes_are_reported(self):
        plan = plan_of("企業名,業界,ステップ\nアオゾラ電機,メーカー,ES\nアオゾラ電機,金融,Webテスト\n")
        assert plan.companies == ()
        assert "食い違っています" in reasons(plan)[3]

    def test_repeating_the_same_attribute_is_not_a_conflict(self):
        plan = plan_of("企業名,業界,ステップ\nアオゾラ電機,メーカー,ES\nアオゾラ電機,メーカー,Webテスト\n")
        assert len(plan.companies) == 1
        assert plan.skipped == ()


class TestSampleFile:
    def test_the_bundled_sample_imports_without_any_skip(self):
        plan = parse_csv(SAMPLE.read_bytes())
        assert plan.companies
        assert plan.skipped == ()
        assert plan.ignored_columns == ()

    def test_the_bundled_sample_uses_only_the_demo_company_names(self):
        """サンプルは架空の企業名だけで作る（scripts/demo_data.py と同じ名前）。"""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "demo_data", Path(__file__).parent.parent / "scripts" / "demo_data.py"
        )
        assert spec and spec.loader
        demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)
        demo_names = {row[0] for row in demo.DEMO_COMPANIES}
        plan = parse_csv(SAMPLE.read_bytes())
        assert {c.company.name for c in plan.companies} <= demo_names


class TestApply:
    def test_preview_writes_nothing(self, importer, selection):
        importer.preview(BASIC.encode("utf-8"))
        assert selection.companies() == []

    def test_apply_writes_companies_and_steps_in_order(self, importer, selection):
        result = importer.apply(importer.preview(BASIC.encode("utf-8")))
        assert (result.companies, result.steps) == (2, 3)
        companies = {c.name: c for c in selection.companies()}
        assert set(companies) == {"アオゾラ電機", "ミカヅキ銀行"}
        steps = selection.steps_of(companies["アオゾラ電機"].id or -1)
        assert [(s.name, s.deadline, s.result, s.sort_order) for s in steps] == [
            ("ES", "2026-10-01", "通過", 0),
            ("1次面接", None, "選考中", 1),
        ]

    def test_default_steps_are_not_added_on_top_of_the_csv(self, importer, selection):
        importer.apply(importer.preview("企業名\nアオゾラ電機\n".encode()))
        company = selection.companies()[0]
        assert selection.steps_of(company.id or -1) == []

    def test_preview_reports_companies_already_in_the_database(self, importer, selection):
        selection.add_company(Company(name="アオゾラ電機"), with_default_steps=False)
        plan = importer.preview(BASIC.encode("utf-8"))
        assert [c.company.name for c in plan.companies] == ["ミカヅキ銀行"]

    def test_a_failure_midway_leaves_nothing_behind(self, importer, selection, monkeypatch):
        """2社目の途中で失敗したら、1社目も残らないこと。"""
        plan = importer.preview(BASIC.encode("utf-8"))
        original = importer._steps.add_many
        calls = []

        def fail_on_second(steps):
            calls.append(steps)
            if len(calls) == 2:
                raise RuntimeError("途中で失敗")
            return original(steps)

        monkeypatch.setattr(importer._steps, "add_many", fail_on_second)
        with pytest.raises(RuntimeError):
            importer.apply(plan)
        assert selection.companies() == []
        assert selection.all_step_views() == []

    def test_a_duplicate_inside_the_database_rolls_back_the_whole_import(self, importer, selection):
        """一意制約の違反が DB で起きても、先に入れた企業が残らないこと。"""
        twice = PlannedCompany(Company(name="ミカヅキ銀行"), (), 3)
        plan = ImportPlan(
            encoding="UTF-8",
            companies=(PlannedCompany(Company(name="アオゾラ電機"), (), 2), twice, twice),
            skipped=(),
            ignored_columns=(),
            blank_rows=0,
        )
        with pytest.raises(DuplicateKeyError):
            importer.apply(plan)
        assert selection.companies() == []

    def test_company_registered_after_the_preview_is_not_overwritten(self, importer, selection):
        plan = importer.preview(BASIC.encode("utf-8"))
        selection.add_company(Company(name="ミカヅキ銀行", memo="先に登録"), with_default_steps=False)
        with pytest.raises(DuplicateKeyError, match="ミカヅキ銀行"):
            importer.apply(plan)
        assert [(c.name, c.memo) for c in selection.companies()] == [("ミカヅキ銀行", "先に登録")]
