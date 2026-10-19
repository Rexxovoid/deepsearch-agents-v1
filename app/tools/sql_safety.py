"""Fail-closed MySQL SELECT validation; never execute the original model text."""
import os
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError
from sqlglot.optimizer.scope import traverse_scope


class UnsafeSQL(ValueError):
    pass


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} 必须为整数") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须介于 {minimum} 和 {maximum} 之间")
    return value


@dataclass(frozen=True)
class SQLPolicy:
    database: str
    tables: frozenset[str]
    timeout_seconds: int = 10
    max_rows: int = 1000

    @classmethod
    def from_env(cls):
        database = (os.getenv("MYSQL_DATABASE") or "").strip()
        tables = frozenset(name.strip() for name in os.getenv(
            "MYSQL_ALLOWED_TABLES", "drugs,inventory,sales_records"
        ).split(",") if name.strip())
        if not database or not tables:
            raise ValueError("MYSQL_DATABASE 和 MYSQL_ALLOWED_TABLES 不能为空")
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) for name in tables):
            raise ValueError("MYSQL_ALLOWED_TABLES 只接受逗号分隔的简单表名，不接受通配符")
        return cls(database, tables, _integer("MYSQL_QUERY_TIMEOUT_SECONDS", 10, 1, 300),
                   _integer("MYSQL_MAX_ROWS", 1000, 1, 10000))


# Only these known, side-effect-free function AST classes are accepted. Unknown
# functions (including UDFs), SLEEP/BENCHMARK, file and named-lock functions fail.
SAFE_FUNCTIONS = frozenset("""
COUNT SUM AVG MIN MAX ROUND ABS CEIL FLOOR COALESCE NULLIF IF CASE
CONCAT CONCAT_WS LOWER UPPER LENGTH CHARACTER_LENGTH SUBSTRING LEFT RIGHT
REPLACE TRIM LTRIM RTRIM DATE YEAR MONTH DAY DAY_OF_MONTH DAY_OF_WEEK
TIME_TO_STR STR_TO_TIME DATEDIFF TIMESTAMPDIFF CURRENT_DATE CURRENT_TIMESTAMP
CURRENT_TIME TS_OR_DS_TO_TIMESTAMP TS_OR_DS_TO_DATE CAST EXTRACT LAST_DAY
DATE_ADD DATE_SUB TIMESTAMP_ADD TIMESTAMP_SUB DATE_TRUNC
ROW_NUMBER RANK DENSE_RANK LAG LEAD FIRST_VALUE LAST_VALUE NTH_VALUE
STDDEV STDDEV_POP STDDEV_SAMP VARIANCE VARIANCE_POP VARIANCE_SAMP
GROUP_CONCAT ARRAY_AGG MOD POW POWER SQRT SIGN GREATEST LEAST
""".split())

FORBIDDEN_NODES = frozenset("""
into lock hint parameter sessionparameter propertyeq command insert update
delete merge create drop alter grant revoke transaction commit rollback set
use execute show describe load_data copy export attach detach pragma
""".split())


def validate_sql(query: str, policy: SQLPolicy) -> tuple[str, frozenset[str]]:
    if not isinstance(query, str) or not query.strip() or len(query) > 20000:
        raise UnsafeSQL("SQL 不能为空且不得超过 20000 个字符")
    try:
        tokens = sqlglot.Dialect.get_or_raise("mysql").tokenize(query)
        if any(token.comments for token in tokens):
            raise UnsafeSQL("不允许 SQL 注释、可执行注释或优化器提示")
        statements = sqlglot.parse(query, read="mysql", error_level=ErrorLevel.RAISE)
        if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.SetOperation, exp.Subquery)):
            raise UnsafeSQL("只允许单条 SELECT 查询（可含只读 CTE、子查询和 UNION）")
        tree = statements[0]
        for node in tree.walk():
            if node.key in FORBIDDEN_NODES or isinstance(node, (exp.DDL, exp.DML)):
                raise UnsafeSQL("禁止写操作、导出文件、变量赋值、锁定及会话设置")
            if isinstance(node, exp.Dot) and node.find(exp.Func):
                raise UnsafeSQL("不允许带数据库限定的函数")
            if isinstance(node, exp.Func) and node.sql_name() not in SAFE_FUNCTIONS:
                raise UnsafeSQL(f"不允许使用函数：{node.sql_name()}")

        physical_tables = set()
        # Scope resolution distinguishes physical tables from CTE/derived aliases,
        # so hiding an unauthorized table inside a CTE or UNION cannot bypass it.
        for scope in traverse_scope(tree):
            for source in scope.sources.values():
                if not isinstance(source, exp.Table):
                    continue
                if not isinstance(source.this, exp.Identifier):
                    raise UnsafeSQL("不允许表函数或动态表名")
                if source.catalog or (source.db and source.db != policy.database):
                    raise UnsafeSQL("禁止跨数据库查询")
                if source.name not in policy.tables:
                    raise UnsafeSQL(f"表不在白名单中：{source.name}")
                physical_tables.add(source.name)
        # Also reject function-based table nodes not represented as a table source.
        if any(not isinstance(table.this, exp.Identifier) for table in tree.find_all(exp.Table)):
            raise UnsafeSQL("不允许动态表或表函数")
        limit = tree.args.get("limit")
        if limit is not None:
            count = limit.expression
            if not isinstance(count, exp.Literal) or not count.is_int or int(count.this) < 0:
                raise UnsafeSQL("LIMIT 必须使用非负整数")
            if int(count.this) > policy.max_rows:
                tree = tree.limit(policy.max_rows + 1)
        else:
            tree = tree.limit(policy.max_rows + 1)
        # Regenerate validated SQL: original text, comments and optimizer hints
        # are never forwarded to MySQL.
        return tree.sql(dialect="mysql", comments=False), frozenset(physical_tables)
    except UnsafeSQL:
        raise
    except (SqlglotError, RecursionError, ValueError, TypeError) as exc:
        raise UnsafeSQL("SQL 无法安全解析，请使用标准 MySQL SELECT 语句") from exc
