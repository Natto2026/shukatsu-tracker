-- 初版スキーマ。既存DBに対しても安全に流せるよう IF NOT EXISTS で書く。
-- companies に password 列は意図的に持たない（平文保存は漏洩リスクのため）。
-- {{PK}} {{TODAY}} は接続先の方言に置き換わる。

CREATE TABLE IF NOT EXISTS companies (
    id {{PK}},
    name TEXT NOT NULL UNIQUE,
    industry TEXT NOT NULL DEFAULT 'その他',
    priority TEXT NOT NULL DEFAULT 'B',
    route TEXT NOT NULL DEFAULT '一般公募',
    test_type TEXT NOT NULL DEFAULT '不明',
    mypage_url TEXT NOT NULL DEFAULT '',
    login_email TEXT NOT NULL DEFAULT '',
    memo TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ({{TODAY}})
);

CREATE TABLE IF NOT EXISTS steps (
    id {{PK}},
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    deadline TEXT,
    result TEXT NOT NULL DEFAULT '選考中',
    memo TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);

-- 企業を削除しても回答は残す（書いた文章は資産のため SET NULL）。
CREATE TABLE IF NOT EXISTS es_answers (
    id {{PK}},
    company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    category TEXT NOT NULL DEFAULT 'その他',
    question TEXT NOT NULL,
    char_limit INTEGER,
    answer TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ({{TODAY}})
);
