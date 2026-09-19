"""永続化層。接続・スキーマ適用・テーブルごとの読み書き。"""

from .connection import connect, transaction
from .database import Database
from .dialects import describe
from .errors import DatabaseError, DuplicateKeyError, ForeignKeyError, MigrationError
from .repositories import (
    CompanyRepository,
    EsAnswerRepository,
    ReviewRepository,
    StepRepository,
)

__all__ = [
    "CompanyRepository",
    "Database",
    "DatabaseError",
    "DuplicateKeyError",
    "EsAnswerRepository",
    "ForeignKeyError",
    "MigrationError",
    "ReviewRepository",
    "StepRepository",
    "connect",
    "describe",
    "transaction",
]
