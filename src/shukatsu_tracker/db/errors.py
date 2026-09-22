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


class BusyError(DatabaseError):
    """別のセッションが書き込み中で、待ち時間内に書けなかった（ロック待ちの超過・競合）。"""


class ConnectionLostError(DatabaseError):
    """開いていた接続が使えなくなった（サーバーの再起動・切断・閉じた接続への操作）。

    接続を作り直さないと以後の操作がすべて失敗する。画面はこの型を見て張り直す。
    """


class ConnectionFailedError(DatabaseError):
    """接続できなかった（未対応の接続先・依存の不足・ファイルやサーバーに届かない）。

    文面にドライバのメッセージを含めない。接続先のホスト名や利用者名が
    そのまま画面に出るため。元の例外は原因として連鎖させておく。
    """


class MigrationError(DatabaseError):
    """スキーマの適用に失敗した。どのファイルで失敗したかを文面に含める。"""
