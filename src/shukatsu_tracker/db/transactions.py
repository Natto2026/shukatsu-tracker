"""トランザクション境界。

BEGIN と COMMIT を発行するのはこのモジュールだけ。

境界の間は接続のロックを握り続ける。握らずに「いまトランザクション中か」を
接続の状態から判定すると、別スレッドが開けたトランザクションを自分のものと
誤認し、相手の ROLLBACK で自分の書き込みが消える。入れ子の判定は接続の状態
ではなく、この層が数える深さで行う。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

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
            db.commit()
        else:
            db.leave()
    finally:
        db.lock.release()
