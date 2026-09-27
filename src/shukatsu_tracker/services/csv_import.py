"""スプレッドシートから書き出した CSV の取り込み。

解析（parse_csv）は DB にも画面にも触れない。入力はファイルの中身と登録済みの
企業名だけで、結果は「何が追加され、どの行がなぜ取り込まれないか」の計画になる。
画面はこの計画を見せ、利用者が確認したあとで CsvImportService.apply が
1つのトランザクションで書く。

CSV は1行 = 選考ステップ1件の縦持ち。同じ企業名の行は1社にまとまる。
取り込みの単位は企業で、ある企業の行に1つでも問題があれば、その企業は丸ごと
取り込まない。半分だけ入れると、CSV を直して取り込み直したときに「登録済みの
企業は上書きしない」という規則に当たり、残りの行を入れる手段がなくなるため。

問題のある行は黙って捨てず、行番号と理由をつけて計画に残す。選択肢にない値を
既定値に丸めることも、読めない締切を空にすることもしない。
"""

from __future__ import annotations

import csv
import io
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass, field

from .. import analytics, constants
from ..db import CompanyRepository, Database, DuplicateKeyError, StepRepository, transaction
from ..models import Company, Step
from .selection import check_single_line

# 見出しの別名。左が取り込み先の項目、右が受け付ける見出し（先頭が標準の名前）。
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("企業名", "会社名", "社名", "企業"),
    "industry": ("業界", "業種"),
    "priority": ("志望度", "優先度"),
    "route": ("応募経路", "経路"),
    "test_type": ("適性検査", "テスト形式"),
    "memo": ("メモ", "備考"),
    "step": ("ステップ", "選考ステップ", "ステップ名"),
    "deadline": ("締切", "締切日", "締め切り", "期限"),
    "result": ("結果", "選考結果"),
    "step_memo": ("ステップメモ",),
}

REQUIRED_FIELDS = ("name",)

_FIELD_NOTES: dict[str, str] = {
    "name": "同じ企業名の行は1社にまとまる",
    "industry": " / ".join(constants.INDUSTRIES),
    "priority": " / ".join(constants.PRIORITIES),
    "route": " / ".join(constants.ROUTES),
    "test_type": " / ".join(constants.TEST_TYPES),
    "memo": "企業のメモ",
    "step": "空欄なら、その行は企業だけを登録する",
    "deadline": "YYYY-MM-DD（例: 2026-10-01）",
    "result": " / ".join(constants.STEP_RESULTS),
    "step_memo": "選考ステップのメモ",
}

# 値が選択肢に限られる企業の項目。空欄なら画面の追加フォームと同じ既定値になる。
_COMPANY_CHOICES: dict[str, list[str]] = {
    "industry": constants.INDUSTRIES,
    "priority": constants.PRIORITIES,
    "route": constants.ROUTES,
    "test_type": constants.TEST_TYPES,
}
_COMPANY_FIELDS = ("industry", "priority", "route", "test_type", "memo")

# 認証情報に当たる見出しの手がかり。当てはまる列は値を読まない。
_CREDENTIAL_HINTS = (
    "マイページ",
    "url",
    "ログイン",
    "login",
    "メール",
    "mail",
    "パスワード",
    "password",
    "pass",
    "アカウント",
    "account",
)
_CREDENTIAL_EXACT = ("id", "pw")

_ENCODINGS = (("utf-8-sig", "UTF-8"), ("cp932", "Shift-JIS"))
_DEFAULT_RESULT = constants.STEP_RESULTS[0]
# 空欄のときの値は、モデルの既定値（企業の追加フォームと同じ）に任せる
_DEFAULTS = Company(name="")


class CsvFormatError(ValueError):
    """ファイル全体として読めない（文字コード・見出し・必須列）。行単位の問題には使わない。"""


@dataclass(frozen=True, slots=True)
class ColumnGuide:
    """画面と文書に出す、列の説明1件。"""

    header: str
    aliases: tuple[str, ...]
    required: bool
    note: str


def column_guide() -> list[ColumnGuide]:
    """受け付ける列の一覧。解析が使う定義からそのまま作る（説明と実装がずれないように）。"""
    return [
        ColumnGuide(aliases[0], aliases[1:], name in REQUIRED_FIELDS, _FIELD_NOTES[name])
        for name, aliases in HEADER_ALIASES.items()
    ]


@dataclass(frozen=True, slots=True)
class PlannedStep:
    """取り込む予定の選考ステップ。"""

    name: str
    deadline: str | None
    result: str
    memo: str
    line: int


@dataclass(frozen=True, slots=True)
class PlannedCompany:
    """取り込む予定の企業と、そのステップ。認証情報の項目は常に空。"""

    company: Company
    steps: tuple[PlannedStep, ...]
    line: int


@dataclass(frozen=True, slots=True)
class SkippedRow:
    """取り込まない行と、その理由。line は表計算ソフト上の行番号（見出しが1行目）。"""

    line: int
    company_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class IgnoredColumn:
    """取り込まない列と、その理由。"""

    header: str
    reason: str


@dataclass(frozen=True, slots=True)
class ImportPlan:
    """取り込む前に画面へ出す要約。この内容がそのまま書き込まれる。"""

    encoding: str
    companies: tuple[PlannedCompany, ...]
    skipped: tuple[SkippedRow, ...]
    ignored_columns: tuple[IgnoredColumn, ...]
    blank_rows: int

    @property
    def step_count(self) -> int:
        return sum(len(company.steps) for company in self.companies)


@dataclass(frozen=True, slots=True)
class ImportResult:
    """実際に書き込んだ件数。"""

    companies: int
    steps: int


@dataclass(slots=True)
class _Row:
    """検証を終えた1行。"""

    line: int
    name: str
    company_values: dict[str, str]
    step: PlannedStep | None


@dataclass(slots=True)
class _Group:
    """同じ企業名の行のまとまり。"""

    first_line: int
    rows: list[_Row] = field(default_factory=list)
    problems: dict[int, str] = field(default_factory=dict)
    all_lines: list[int] = field(default_factory=list)


def _normalise(header: str) -> str:
    """全角・半角、大文字・小文字、空白の違いを吸収する。"""
    return "".join(unicodedata.normalize("NFKC", header).lower().split())


_ALIAS_TO_FIELD = {
    _normalise(alias): field_name for field_name, aliases in HEADER_ALIASES.items() for alias in aliases
}


def _is_credential(normalised: str) -> bool:
    return normalised in _CREDENTIAL_EXACT or any(hint in normalised for hint in _CREDENTIAL_HINTS)


def decode(data: bytes) -> tuple[str, str]:
    """UTF-8（BOM あり・なし）、だめなら Shift-JIS として読む。返り値は（本文, 文字コードの表示名）。"""
    if data.startswith(b"PK\x03\x04"):
        raise CsvFormatError(
            "Excel ブック（.xlsx）のようです。表計算ソフトで CSV として保存し直してください。"
        )
    for codec, label in _ENCODINGS:
        try:
            return data.decode(codec), label
        except UnicodeDecodeError:
            continue
    raise CsvFormatError("文字コードを判別できませんでした。UTF-8 か Shift-JIS の CSV にしてください。")


def _read_records(text: str) -> list[list[str]]:
    try:
        return list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as error:
        raise CsvFormatError(f"CSV として読めませんでした: {error}") from error


def _map_columns(header: list[str], records: list[list[str]]) -> tuple[dict[str, int], list[IgnoredColumn]]:
    """見出しから「項目 → 列番号」を決める。読まない列も理由つきで返す。

    このあと値を読むのは、ここで返した列番号の列だけ。認証情報の列の値には触れない。
    """
    columns: dict[str, int] = {}
    ignored: list[IgnoredColumn] = []
    for index, raw in enumerate(header):
        label = raw.strip()
        normalised = _normalise(raw)
        if not normalised:
            if any(len(record) > index and record[index].strip() for record in records):
                ignored.append(
                    IgnoredColumn(f"（{index + 1}列目・見出しなし）", "見出しが空のため取り込まない")
                )
            continue
        field_name = _ALIAS_TO_FIELD.get(normalised)
        if field_name is None:
            reason = (
                "認証情報に当たるため取り込まない"
                if _is_credential(normalised)
                else "対応する項目がないため取り込まない"
            )
            ignored.append(IgnoredColumn(label, reason))
            continue
        if field_name in columns:
            first = header[columns[field_name]].strip()
            raise CsvFormatError(
                f"見出し「{first}」と「{label}」が同じ項目を指しています。どちらか一方にしてください。"
            )
        columns[field_name] = index
    missing = [HEADER_ALIASES[name][0] for name in REQUIRED_FIELDS if name not in columns]
    if missing:
        found = "、".join(cell.strip() for cell in header if cell.strip()) or "なし"
        raise CsvFormatError(
            f"必須の列「{'」「'.join(missing)}」が見つかりません。1行目を見出しにしてください"
            f"（読み取った見出し: {found}）。"
        )
    return columns, ignored


def _validate(line: int, cells: dict[str, str]) -> tuple[_Row | None, str | None]:
    """1行を検証する。問題があれば（None, 理由）。"""
    # 引用符で囲んだ欄には改行を書けるため、画面から入らない名前もここから入りうる
    for field_name, label in (("name", "企業名"), ("step", "ステップ名")):
        try:
            check_single_line(cells.get(field_name, ""), label)
        except ValueError as error:
            return None, str(error)
    company_values: dict[str, str] = {}
    for field_name in _COMPANY_FIELDS:
        value = cells.get(field_name, "")
        choices = _COMPANY_CHOICES.get(field_name)
        if value and choices is not None and value not in choices:
            label = HEADER_ALIASES[field_name][0]
            return None, f"{label}「{value}」は選択肢にありません（{' / '.join(choices)}）"
        company_values[field_name] = value

    step_name = cells.get("step", "")
    deadline_text = cells.get("deadline", "")
    result = cells.get("result", "")
    step_memo = cells.get("step_memo", "")
    if not step_name:
        if deadline_text or result or step_memo:
            return None, "ステップ名が空なのに、締切・結果・ステップメモのいずれかが入っています"
        return _Row(line, cells["name"], company_values, None), None

    deadline: str | None = None
    if deadline_text:
        parsed = analytics.parse_date(deadline_text)
        if parsed is None:
            return None, f"締切「{deadline_text}」を YYYY-MM-DD の日付として読めません"
        deadline = parsed.isoformat()
    if result and result not in constants.STEP_RESULTS:
        return None, f"結果「{result}」は選択肢にありません（{' / '.join(constants.STEP_RESULTS)}）"
    step = PlannedStep(step_name, deadline, result or _DEFAULT_RESULT, step_memo, line)
    return _Row(line, cells["name"], company_values, step), None


def _merge(group: _Group) -> None:
    """同じ企業の行どうしの食い違いと、ステップ名の重複を問題として記録する。"""
    seen_values: dict[str, tuple[str, int]] = {}
    seen_steps: dict[str, int] = {}
    for row in group.rows:
        for field_name, value in row.company_values.items():
            if not value:
                continue
            known = seen_values.setdefault(field_name, (value, row.line))
            if known[0] != value:
                label = HEADER_ALIASES[field_name][0]
                group.problems.setdefault(
                    row.line, f"{label}「{value}」が {known[1]} 行目の「{known[0]}」と食い違っています"
                )
        if row.step is not None:
            first = seen_steps.setdefault(row.step.name, row.line)
            if first != row.line:
                group.problems.setdefault(
                    row.line, f"ステップ「{row.step.name}」が {first} 行目と重複しています"
                )


def _build(name: str, group: _Group) -> PlannedCompany:
    values: dict[str, str] = {}
    for row in group.rows:
        for field_name, value in row.company_values.items():
            if value:
                values.setdefault(field_name, value)
    return PlannedCompany(
        company=Company(
            name=name,
            industry=values.get("industry", _DEFAULTS.industry),
            priority=values.get("priority", _DEFAULTS.priority),
            route=values.get("route", _DEFAULTS.route),
            test_type=values.get("test_type", _DEFAULTS.test_type),
            memo=values.get("memo", ""),
        ),
        steps=tuple(row.step for row in group.rows if row.step is not None),
        line=group.first_line,
    )


def parse_csv(data: bytes, *, existing_names: Collection[str] = ()) -> ImportPlan:
    """CSV の中身から取り込みの計画を作る。DB には触れない。

    existing_names は登録済みの企業名。同じ名前の企業は上書きせず、取り込まない行として報告する。
    """
    text, encoding = decode(data)
    records = _read_records(text)
    if not records or not any(cell.strip() for cell in records[0]):
        raise CsvFormatError("1行目に見出しがありません。")
    header, body = records[0], records[1:]
    columns, ignored = _map_columns(header, body)

    skipped: list[SkippedRow] = []
    groups: dict[str, _Group] = {}
    blank_rows = 0
    for offset, record in enumerate(body):
        line = offset + 2  # 見出しが1行目
        if not any(cell.strip() for cell in record):
            blank_rows += 1
            continue
        cells = {name: record[index].strip() for name, index in columns.items() if index < len(record)}
        name = cells.get("name", "")
        if not name:
            skipped.append(SkippedRow(line, "", "企業名が空です"))
            continue
        cells["name"] = name
        group = groups.setdefault(name, _Group(first_line=line))
        group.all_lines.append(line)
        if any(cell.strip() for cell in record[len(header) :]):
            group.problems[line] = "見出しより右に値があります（列がずれている可能性があります）"
            continue
        row, problem = _validate(line, cells)
        if row is None:
            group.problems[line] = problem or "読み取れませんでした"
        else:
            group.rows.append(row)

    existing = set(existing_names)
    planned: list[PlannedCompany] = []
    for name, group in groups.items():
        if name in existing:
            skipped.extend(
                SkippedRow(line, name, "同じ名前の企業がすでに登録されています（上書きしません）")
                for line in group.all_lines
            )
            continue
        _merge(group)
        if group.problems:
            culprits = "、".join(str(line) for line in sorted(group.problems))
            for line in group.all_lines:
                reason = group.problems.get(
                    line, f"同じ企業の {culprits} 行目に問題があるため、この企業は取り込みません"
                )
                skipped.append(SkippedRow(line, name, reason))
            continue
        planned.append(_build(name, group))

    return ImportPlan(
        encoding=encoding,
        companies=tuple(planned),
        skipped=tuple(sorted(skipped, key=lambda row: row.line)),
        ignored_columns=tuple(ignored),
        blank_rows=blank_rows,
    )


class CsvImportService:
    """CSV の取り込み。確認用の計画を作る入口と、計画を書き込む入口を分ける。"""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._companies = CompanyRepository(db)
        self._steps = StepRepository(db)

    def preview(self, data: bytes) -> ImportPlan:
        """何が取り込まれるかを返す。書き込まない。"""
        existing = {company.name for company in self._companies.list_all()}
        return parse_csv(data, existing_names=existing)

    def apply(self, plan: ImportPlan) -> ImportResult:
        """計画を1つのトランザクションで書く。途中で失敗したら何も残らない。

        計画を作ってから書くまでの間に同じ名前の企業が登録されていたら、上書きせず
        DuplicateKeyError にする。この確認も書き込みと同じ境界の中で行う。
        """
        with transaction(self._db):
            existing = {company.name for company in self._companies.list_all()}
            clashes = [planned.company.name for planned in plan.companies if planned.company.name in existing]
            if clashes:
                raise DuplicateKeyError(f"すでに登録されている企業があります: {'、'.join(clashes)}")
            for planned in plan.companies:
                company_id = self._companies.add(planned.company)
                self._steps.add_many(
                    [
                        Step(
                            company_id=company_id,
                            name=step.name,
                            deadline=step.deadline,
                            result=step.result,
                            memo=step.memo,
                            sort_order=order,
                        )
                        for order, step in enumerate(planned.steps)
                    ]
                )
        return ImportResult(companies=len(plan.companies), steps=plan.step_count)
