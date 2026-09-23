-- ============================================================
-- 探渊 — 数据库只读账号初始化脚本（TDD §14 主防线）
-- ============================================================
--
-- 用法（以管理员身份连接 MySQL 后执行）：
--     mysql -u root -p < sql/create_readonly_user.sql
--
-- ── 为什么必须有这个账号 ──────────────────────────────────
--
-- tools/mysql_tools.py 里的关键字拦截是**兜底**，不是防线。
-- 应用层的字符串匹配总能被绕过（注释拼接、大小写、编码、MySQL 方言
-- 特性、以及未来某次改动不小心放宽了正则……）。
-- 只有数据库侧的权限才是不可绕过的：即使一条 DROP TABLE 混过了应用层，
-- 数据库也会因为该账号没有 DROP 权限而直接报错拒绝。
--
-- 两者必须同时具备，缺一不可：
--     只读账号   → 保证"即使漏过也改不动"
--     关键字拦截 → 保证"根本送不到数据库"，并在日志里留下痕迹
--
-- ── 权限为何是 SELECT 而不是 SELECT,SHOW VIEW ─────────────
--
-- 本项目只用 SHOW TABLES / SELECT / 聚合查询，不需要视图与存储过程。
-- 权限给得越少，可被滥用的面就越小。
-- ============================================================

-- 1) 创建只读账号
--    请把 CHANGE_ME_STRONG_PASSWORD 换成强密码，并同步写入 .env 的 MYSQL_PASSWORD。
--    密码中避免使用 $ 与反引号（shell 与 SQL 都要转义，容易出错）。
CREATE USER IF NOT EXISTS 'tanyuan_ro'@'localhost'
    IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';

-- 2) 只授 SELECT，且只授给本项目使用的那一个库
--    注意：不要写 ON *.* —— 那会让这个账号能读到整台 MySQL 上的所有库。
GRANT SELECT ON pharma_db.* TO 'tanyuan_ro'@'localhost';

-- 3) 立即生效
FLUSH PRIVILEGES;

-- 4) 自检：应当只看到一行 USAGE（表示无任何实际权限之外）——
--    正确结果应包含 "GRANT SELECT ON `pharma_db`.* TO ..."
SHOW GRANTS FOR 'tanyuan_ro'@'localhost';

-- ── 验证（可选，推荐做一次） ─────────────────────────────
-- 用只读账号连接后执行下面三条，前两条应成功、第三条必须被拒绝：
--
--     mysql -u tanyuan_ro -p pharma_db
--     mysql> SELECT COUNT(*) FROM drugs;            -- ✅ 应成功
--     mysql> SHOW TABLES;                           -- ✅ 应成功
--     mysql> DROP TABLE drugs;                      -- ❌ 应报 ERROR 1142
--
-- 若第三条没有报错，说明授权写错了，请回到第 2 步检查。

-- ── 附：已有环境的最小改动方案 ───────────────────────────
-- 若不方便新建账号，至少要把现有账号收权：
--
--     REVOKE ALL PRIVILEGES ON pharma_db.* FROM 'root'@'localhost';  -- 视环境而定
--     GRANT SELECT ON pharma_db.* TO 'tanyuan_ro'@'localhost';
--
-- 但更推荐新建专用账号——root 权限过宽，且与本项目其他用途混用难以审计。
