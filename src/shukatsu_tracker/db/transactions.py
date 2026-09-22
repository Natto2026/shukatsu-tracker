"""トランザクション境界。

BEGIN と COMMIT を発行するのはこのモジュールだけ。

境界の間は接続のロックを握り続ける。握らずに「いまトランザクション中か」を
接続の状態から判定すると、別スレッドが開けたトランザクションを自分のものと
誤認し、相手の ROLLBACK で自分の書き込みが消える。入れ子の判定は接続の状態
ではなく、この層が数える深さで行う。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress

from .database import Database


@contextmanager
def transaction(db: Database) -> Iterator[Database]:
    """まとめて成功するか、まとめて失敗するかのどちらかにする。

    同じスレッドからの入れ子は、いちばん外側の境界にまとめる。
    別スレッドはロックの解放を待ってから自分の境界を開く。
    """
    db.lock.acquire()
    outermost = db.depth == 0
    try:
        if outermost:
            db.begin()
        else:
            db.enter()
        try:
            yield db
        except BaseException:
            if outermost:
                db.rollback()
            else:
                db.leave()
            raise
        if outermost:
            _commit_or_roll_back(db)
        else:
            db.leave()
    finally:
        db.lock.release()


def _commit_or_roll_back(db: Database) -> None:
    """COMMIT を発行し、失敗したら ROLLBACK を試みてから元の例外を上げる。

    COMMIT の失敗（ディスクの I/O エラー、サーバーとの切断など）をそのまま
    上げると、開いたままのトランザクションが書き込みロックを握り続け、
    別のセッションの起動が「database is locked」で止まる。ROLLBACK 自体が
    失敗する場合（ドライバがすでに巻き戻している）は、元の例外を優先する。
    """
    try:
        db.commit()
    except BaseException:
        with suppress(Exception):
            db.rollback()
        raise
