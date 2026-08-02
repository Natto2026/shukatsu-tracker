from shukatsu_tracker import ai_export, research


class TestResearchLinks:
    def test_links_are_https_and_encoded(self):
        links = research.research_links("テスト株式会社")
        assert len(links) >= 5
        for link in links:
            assert link["url"].startswith("https://")
            assert " " not in link["url"]
            assert "テスト株式会社" not in link["url"]  # URLエンコードされている

    def test_covers_key_research_pages(self):
        labels = "".join(link["label"] for link in research.research_links("A社"))
        for keyword in ["公式", "採用", "事業内容", "クチコミ", "選考体験記"]:
            assert keyword in labels


class TestBuildAnalysisMarkdown:
    COMPANY = {
        "id": 1, "name": "テスト株式会社", "industry": "SIer・IT", "priority": "A",
        "route": "一般公募", "test_type": "SPI", "memo": "夏インターン参加済み",
    }
    STEP = {
        "name": "ES", "result": "落選", "deadline": "2026-07-01", "memo": "",
        "company_name": "テスト株式会社", "route": "一般公募", "test_type": "SPI",
    }

    def build(self, es_answers=None):
        return ai_export.build_analysis_markdown(
            [self.COMPANY], {1: [self.STEP]}, [self.STEP], es_answers=es_answers
        )

    def test_contains_prompt_and_records(self):
        md = self.build()
        assert "キャリアメンター" in md  # 分析依頼プロンプト
        assert "テスト株式会社" in md
        assert "落選" in md
        assert "夏インターン参加済み" in md

    def test_contains_aggregates(self):
        md = self.build()
        assert "応募経路別の通過率" in md
        assert "| 一般公募 | 0 | 1 | 0% |" in md

    def test_export_never_contains_credentials(self):
        """マイページURL・ログイン用メールはAIへの書き出しに絶対に含めない。"""
        company = {
            **self.COMPANY,
            "mypage_url": "https://mypage.example.com/secret-entry-123",
            "login_email": "watashi@example.com",
        }
        md = ai_export.build_analysis_markdown([company], {1: [self.STEP]}, [self.STEP])
        assert "mypage.example.com" not in md
        assert "watashi@example.com" not in md

    def test_es_answers_only_when_requested(self):
        answers = [{"category": "ガクチカ", "question": "学生時代に力を入れたこと",
                    "answer": "体育会の活動", "company_name": None}]
        assert "体育会の活動" not in self.build()
        assert "体育会の活動" in self.build(es_answers=answers)
