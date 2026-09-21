"""永続化層が投げる例外。

呼び出し側が sqlite3 や psycopg の例外型に依存しないようにするため、
ドライバ固有の例外はこの層で共通の型に翻訳する。DB を差し替えても
サービス層と画面のエラー処理を書き換えずに済む。
"""

from __future__ import annotations


class DatabaseError(Exception):
    """永続化層で発生した、呼び出し側が扱うべきエラー。"""


class DuplicateKeyError(DatabaseError):
    """一意制約の違反（同じ企業名の二重登録など）。"""


class ForeignKeyError(DatabaseError):
    """存在しない行を参照した、または参照されている行を消そうとした。"""


class ConnectionFailedError(DatabaseError):
    """接続できなかった（未対応の接続先・依存の不足・ファイルやサーバーに届かない）。

    文面にドライバのメッセージを含めない。接続先のホスト名や利用者名が
    そのまま画面に出るため。元の例外は原因として連鎖させておく。
    """


class MigrationError(DatabaseError):
    """スキーマの適用に失敗した。どのファイルで失敗したかを文面に含める。"""
