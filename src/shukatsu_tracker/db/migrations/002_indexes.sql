-- 一覧・集計で必ず使う結合キーと並び順に索引を張る。
CREATE INDEX IF NOT EXISTS idx_steps_company ON steps(company_id, sort_order, id);
CREATE INDEX IF NOT EXISTS idx_steps_deadline ON steps(deadline) WHERE deadline IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_es_answers_company ON es_answers(company_id);
