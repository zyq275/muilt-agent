"""
数据库三工具 — TDD §7.2

工具：
- list_sql_tables()  -> str   列出所有可用表
- get_table_data(table_name: str) -> str  读取指定表前 100 行
- execute_sql_query(sql: str) -> str  执行自定义 SQL

连接配置：从环境变量读取（TDD §12）
charset=utf8mb4，autocommit=True
异常统一返回"连接数据库失败：{错误}"
"""

import os
import re
import sys
from io import StringIO
from pathlib import Path

# 确保项目根目录在 sys.path 中（支持直接运行本文件）
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import mysql.connector
from dotenv import load_dotenv

from utils.logger_utils import get_logger

load_dotenv()
logger = get_logger(__name__)


def _get_connection() -> mysql.connector.connection.MySQLConnection:
    """从环境变量读取配置，创建数据库连接。"""
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DATABASE", "pharma_db"),
        charset=os.getenv("MYSQL_CHARSET", "utf8mb4"),
        autocommit=True,
    )


def _format_rows(cursor) -> str:
    """
    将 cursor 结果格式化为 CSV 文本：
    字段名1,字段名2,...
    值1,值2,...
    """
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    buf = StringIO()
    buf.write(",".join(columns) + "\n")
    for row in rows:
        buf.write(",".join(str(v) for v in row) + "\n")
    return buf.getvalue().rstrip("\n")


def list_sql_tables() -> str:
    """
    列出所有可用表。

    Returns:
        "可用的表：a,b,c"  或  "没有可用的表"  或  "连接数据库失败：{错误}"
    """
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()

        if not tables:
            return "没有可用的表"

        result = "可用的表：" + ",".join(tables)
        logger.info(result)
        return result

    except Exception as e:
        logger.error(f"list_sql_tables 失败：{e}")
        return f"连接数据库失败：{e}"


def get_table_data(table_name: str) -> str:
    """
    读取指定表前 100 行数据。

    Args:
        table_name: 表名

    Returns:
        CSV 格式文本（字段名+数据行），或 "查询表{table_name},结果没有数据"，或 "连接数据库失败：{错误}"
    """
    try:
        conn = _get_connection()
        cursor = conn.cursor()

        # 使用参数化表名防止注入——表名不能直接参数化，故做白名单校验
        cursor.execute("SHOW TABLES")
        valid_tables = {row[0] for row in cursor.fetchall()}

        if table_name not in valid_tables:
            cursor.close()
            conn.close()
            return f"查询表{table_name},结果没有数据"

        # 查询前 100 行
        query = f"SELECT * FROM `{table_name}` LIMIT 100"
        cursor.execute(query)

        if cursor.description is None:
            cursor.close()
            conn.close()
            return f"查询表{table_name},结果没有数据"

        result = _format_rows(cursor)
        cursor.close()
        conn.close()

        logger.info(f"get_table_data({table_name}) 返回 {len(result.splitlines())} 行")
        return result

    except Exception as e:
        logger.error(f"get_table_data 失败：{e}")
        return f"连接数据库失败：{e}"


# ── SQL 只读拦截（TDD §14：主防线是只读账号，这里是兜底） ────────
#
# 明确约定：本函数是**辅助防线**，不是唯一防线。
# 正则/关键字拦截总能被绕过（注释、大小写、编码、多语句、MySQL 方言特性……），
# 真正的主防线是数据库侧的只读账号（见 sql/create_readonly_user.sql）。
# 二者必须同时具备。

# 允许的语句起始关键字（白名单）
_ALLOWED_SQL_PREFIXES = ("select", "show", "desc", "describe", "explain", "with")

# 危险关键字（黑名单，词边界匹配）
_FORBIDDEN_SQL_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "replace", "rename", "grant", "revoke", "load", "outfile", "dumpfile",
    "shutdown", "call", "execute", "prepare", "handler", "lock", "unlock",
    "set", "use", "reset", "purge", "kill", "flush", "install", "uninstall",
    "into",  # 堵 `SELECT ... INTO OUTFILE/DUMPFILE`
)

_SQL_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_SQL_LINE_COMMENT_RE = re.compile(r"--[^\n]*|#[^\n]*")
_SQL_STRING_RE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
_SQL_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(_FORBIDDEN_SQL_KEYWORDS) + r")\b", re.IGNORECASE
)


def _normalize_sql(sql: str) -> str:
    """
    归一化 SQL 以便安全判定：去注释、去字符串字面量、压空白。

    必须先去掉字符串与注释，否则 `SELECT 'DROP TABLE'` 这类正常查询会被
    误伤，而 `SELECT/**/1;DROP` 这类拼接又会被漏放。

    Args:
        sql: 原始 SQL。

    Returns:
        归一化后的 SQL（小写、单空格分隔）。
    """
    s = _SQL_BLOCK_COMMENT_RE.sub(" ", sql)
    # 行注释里的引号不该参与字符串匹配，先摘掉行注释
    s = _SQL_LINE_COMMENT_RE.sub(" ", s)
    # 字符串字面量整体替换为占位符，使其中的关键字不参与判定
    s = _SQL_STRING_RE.sub("''", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _reject_reason(sql: str) -> str | None:
    """
    检查 SQL 是否为只读查询；不是则返回拒绝原因。

    Args:
        sql: 原始 SQL。

    Returns:
        None 表示放行；否则为人类可读的拒绝原因。
    """
    normalized = _normalize_sql(sql)
    if not normalized:
        return "SQL 为空"

    lowered = normalized.lower()

    # 1. 多语句拦截（; 后还有内容）
    stripped = lowered.rstrip(";").strip()
    if ";" in stripped:
        return "检测到多条语句（只允许单条只读查询）"

    # 2. 首关键字白名单
    first_word = stripped.split(" ", 1)[0]
    if first_word not in _ALLOWED_SQL_PREFIXES:
        return f"以 `{first_word.upper()}` 开头的语句不在只读白名单内"

    # 3. 危险关键字黑名单（兜底，防 `WITH ... DELETE` 之类）
    hit = _SQL_KEYWORD_RE.search(stripped)
    if hit:
        return f"检测到写操作关键字 `{hit.group(1).upper()}`"

    return None


def execute_sql_query(sql: str) -> str:
    """
    执行自定义 SQL 查询。

    Args:
        sql: SQL 语句

    Returns:
        CSV 格式文本（字段名+数据行），或 "执行语句{sql},结果没有数据"，或 "连接数据库失败：{错误}"
    """
    # TDD §14：只读拦截。命中即拒，绝不把写语句送到数据库。
    reason = _reject_reason(sql)
    if reason is not None:
        logger.warning(f"SQL 只读拦截：{reason} | 原始语句：{sql[:200]!r}")
        return (
            f"查询被拒绝：{reason}。"
            "本系统只允许执行只读查询（SELECT/SHOW/DESC/EXPLAIN/WITH），"
            "不允许任何修改数据的操作。"
        )

    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)

        if cursor.description is None:
            # 非 SELECT 语句（如 INSERT/UPDATE/DELETE）
            cursor.close()
            conn.close()
            return f"执行语句{sql},结果没有数据"

        result = _format_rows(cursor)
        if not result or "\n" not in result:
            # 只有表头没有数据行
            cursor.close()
            conn.close()
            return f"执行语句{sql},结果没有数据"
        cursor.close()
        conn.close()

        logger.info(f"execute_sql_query 返回 {len(result.splitlines())} 行")
        return result

    except Exception as e:
        logger.error(f"execute_sql_query 失败：{e}")
        return f"连接数据库失败：{e}"


# ── __main__ 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    print("=== list_sql_tables ===")
    print(list_sql_tables())
    print()

    print("=== get_table_data('drugs') ===")
    data = get_table_data("drugs")
    # 只打印前 5 行，避免刷屏
    for line in data.splitlines()[:6]:
        print(line)
    print("...")
    print()

    print("=== execute_sql_query('SELECT COUNT(*) AS total FROM drugs') ===")
    print(execute_sql_query("SELECT COUNT(*) AS total FROM drugs"))
    print()

    # ── 只读拦截测试（TDD §14）─────────────────────────
    # 不依赖数据库连接：_reject_reason 是纯函数，单独验证判定逻辑。
    print("=== SQL 只读拦截 ===")
    _cases: list[tuple[str, bool]] = [
        # (SQL, 是否应当被拒绝)
        ("SELECT * FROM drugs LIMIT 5", False),
        ("SHOW TABLES", False),
        ("DESC drugs", False),
        ("EXPLAIN SELECT * FROM drugs", False),
        ("WITH t AS (SELECT 1) SELECT * FROM t", False),
        ("SELECT * FROM drugs WHERE name = 'DROP TABLE drugs'", False),  # 字符串里的关键字不应误伤
        ("DROP TABLE drugs", True),
        ("DELETE FROM drugs", True),
        ("UPDATE drugs SET price = 0", True),
        ("TRUNCATE TABLE drugs", True),
        ("INSERT INTO drugs VALUES (1)", True),
        ("SELECT * FROM drugs; DROP TABLE drugs", True),          # 多语句
        ("SELECT/**/1; DROP TABLE drugs", True),                  # 注释绕过
        ("-- 注释\nDELETE FROM drugs", True),                      # 行注释开头
        ("SELECT * FROM drugs INTO OUTFILE '/tmp/x'", True),      # 落盘
        ("WITH t AS (SELECT 1) DELETE FROM drugs", True),         # CTE 掩盖
    ]
    _failed = 0
    for _sql, _should_reject in _cases:
        _reason = _reject_reason(_sql)
        _rejected = _reason is not None
        _ok = _rejected == _should_reject
        _failed += 0 if _ok else 1
        _verdict = "✅ 已拒绝" if _rejected else "▶️  放行"
        _expect = "拒绝" if _should_reject else "放行"
        _mark = "" if _ok else f"  ❌ 预期{_expect}，实际{'拒绝' if _rejected else '放行'}"
        print(f"  {_verdict} [{_expect}] {_sql[:52]!r}{_mark}")
        if _reason:
            print(f"        原因：{_reason}")
    print(f"\n拦截用例：{len(_cases) - _failed}/{len(_cases)} 符合预期")
