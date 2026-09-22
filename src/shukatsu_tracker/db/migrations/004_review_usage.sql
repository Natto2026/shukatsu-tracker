-- 所見の実行にかかったトークン数。あとからコストを確認できるようにする。
-- 通信しない実行先の所見と、この版より前に取った所見は NULL のまま。
ALTER TABLE reviews ADD COLUMN input_tokens INTEGER;
ALTER TABLE reviews ADD COLUMN output_tokens INTEGER;
