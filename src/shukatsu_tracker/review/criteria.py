"""点検の観点を TOML から読み込む。

観点はコードではなくデータとして持つ。書き方の基準は見直しが入るものなので、
文面を直すたびにコードを変更しなくて済むようにしている。

`criteria/base.toml` が業界を問わない共通の観点、
`criteria/industry/*.toml` が業界ごとの上乗せ。上乗せ側は観点を追加するほか、
`emphasis` に共通観点の id を並べることで、その観点の重みを引き上げる。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

CRITERIA_DIR = Path(__file__).parent / "criteria"
BASE_FILE = CRITERIA_DIR / "base.toml"
INDUSTRY_DIR = CRITERIA_DIR / "industry"

_EMPHASIS_BONUS = 1
_MAX_WEIGHT = 4


@dataclass(frozen=True, slots=True)
class Criterion:
    """1つの観点。"""

    id: str
    title: str
    check: str
    weak: str
    weight: int

    @property
    def emphasis_label(self) -> str:
        return "特に重視" if self.weight >= 3 else "確認"


@dataclass(frozen=True, slots=True)
class CriteriaSet:
    """ある業界向けに組み上がった観点の一式。"""

    industry: str
    reader: str
    criteria: tuple[Criterion, ...]

    def __iter__(self):
        return iter(self.criteria)

    def __len__(self) -> int:
        return len(self.criteria)


def _load(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _to_criteria(raw: dict) -> list[Criterion]:
    found: list[Criterion] = []
    for entry in raw.get("criteria", []):
        missing = {"id", "title", "check", "weak"} - set(entry)
        if missing:
            raise ValueError(f"観点の項目が足りません: {sorted(missing)}")
        found.append(
            Criterion(
                id=entry["id"],
                title=entry["title"],
                check=entry["check"].strip(),
                weak=entry["weak"].strip(),
                weight=int(entry.get("weight", 1)),
            )
        )
    return found


@lru_cache(maxsize=1)
def base_criteria() -> tuple[Criterion, ...]:
    return tuple(_to_criteria(_load(BASE_FILE)))


@lru_cache(maxsize=1)
def _industry_files() -> dict[str, Path]:
    """業界名 → 定義ファイルの対応表。対応は各ファイルの meta.matches が持つ。"""
    mapping: dict[str, Path] = {}
    if not INDUSTRY_DIR.is_dir():
        return mapping
    for path in sorted(INDUSTRY_DIR.glob("*.toml")):
        meta = _load(path).get("meta", {})
        matches = meta.get("matches")
        if not matches:
            raise ValueError(f"meta.matches がありません: {path.name}")
        if matches in mapping:
            raise ValueError(f"業界 {matches} の定義が重複しています: {path.name}")
        mapping[matches] = path
    return mapping


def available_industries() -> list[str]:
    """上乗せ観点が用意されている業界の一覧。"""
    return sorted(_industry_files())


def for_industry(industry: str | None) -> CriteriaSet:
    """業界に応じた観点一式を組み立てる。

    対応する定義がない業界（「その他」など）は共通の観点だけを返す。
    """
    merged = {c.id: c for c in base_criteria()}
    reader = "応募書類を読み慣れた第三者として、書き方の観点から読む。"

    path = _industry_files().get(industry or "")
    if path is not None:
        raw = _load(path)
        meta = raw.get("meta", {})
        reader = meta.get("reader", reader).strip()
        for criterion in _to_criteria(raw):
            merged[criterion.id] = criterion
        for emphasized in meta.get("emphasis", []):
            current = merged.get(emphasized)
            if current is None:
                raise ValueError(f"emphasis に未定義の観点があります: {emphasized}（{path.name}）")
            merged[emphasized] = replace(current, weight=min(current.weight + _EMPHASIS_BONUS, _MAX_WEIGHT))

    ordered = sorted(merged.values(), key=lambda c: (-c.weight, c.id))
    return CriteriaSet(industry=industry or "指定なし", reader=reader, criteria=tuple(ordered))
