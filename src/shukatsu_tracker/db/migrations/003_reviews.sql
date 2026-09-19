-- 回答への所見の履歴。回答は後から書き換わるため、評価した時点の本文を
-- answer_snapshot に控えて、どの文面への所見かを後から辿れるようにする。
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    es_answer_id INTEGER NOT NULL REFERENCES es_answers(id) ON DELETE CASCADE,
    industry TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL,
    model TEXT,
    prompt TEXT NOT NULL,
    result TEXT NOT NULL,
    answer_snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_reviews_answer ON reviews(es_answer_id, id DESC);
