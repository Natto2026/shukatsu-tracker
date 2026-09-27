from __future__ import annotations

from factories import make_step

from shukatsu_tracker import ai_export, analytics, research
from shukatsu_tracker.models import Company, EsAnswer


class TestResearchLinks:
    def test_links_are_https_and_encoded(self):
        links = research.research_links("テスト株式会社")
        assert len(links) >= 5
        for link in links:
            assert link.url.startswith("https://")
            assert " " not in link.url
            assert "テスト株式会社" not in link.url  # URLエンコードされている

    def test_covers_key_research_pages(self):
        labels = "".join(link.label for link in research.research_links("A社"))
        for keyword in ["公式", "採用", "事業内容", "クチコミ", "選考体験記"]:
            assert keyword in labels


class TestBuildAnalysisMarkdown:
    COMPANY = Company(
        id=1,
        name="テスト株式会社",
        industry="SIer・IT",
        priority="A",
        route="一般公募",
        test_type="SPI",
        memo="夏インターン参加済み",
    )
    STEP = make_step(company_id=1, company_name="テスト株式会社", result="落選", deadline="2026-07-01")

    def build(self, es_answers=None) -> str:
        return ai_export.build_analysis_markdown([self.COMPANY], {1: [self.STEP]}, es_answers=es_answers)

    def test_contains_request_and_records(self):
        markdown = self.build()
        assert "# 依頼: 選考データの分析" in markdown
        assert "テスト株式会社" in markdown
        assert "落選" in markdown
        assert "夏インターン参加済み" in markdown

    def test_contains_aggregates(self):
        markdown = self.build()
        assert "応募経路別のステップ通過率" in markdown
        assert "| 一般公募 | 0 | 1 | 0% |" in markdown

    def test_pass_rate_says_how_it_is_counted(self):
        """書き出しを読む側が企業単位の通過率と取り違えないよう、数え方を添える。"""
        assert analytics.PASS_RATE_UNIT in self.build()

    def test_export_never_contains_credentials(self):
        """マイページURL・ログイン用メールは書き出しに絶対に含めない。"""
        from dataclasses import replace

        company = replace(
            self.COMPANY,
            mypage_url="https://mypage.example.com/secret-entry-123",
            login_email="watashi@example.com",
        )
        markdown = ai_export.build_analysis_markdown([company], {1: [self.STEP]})
        assert "mypage.example.com" not in markdown
        assert "watashi@example.com" not in markdown

    def test_answers_only_when_requested(self):
        answers = [
            EsAnswer(
                question="学生時代に力を入れたこと",
                category="ガクチカ",
                answer="体育会の活動",
            )
        ]
        assert "体育会の活動" not in self.build()
        assert "体育会の活動" in self.build(es_answers=answers)

    def test_empty_answers_are_skipped(self):
        answers = [EsAnswer(question="未記入の設問", category="その他", answer="")]
        assert "未記入の設問" not in self.build(es_answers=answers)
