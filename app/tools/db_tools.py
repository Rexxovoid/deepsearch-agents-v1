"""Whitelisted, read-only MySQL tools with bounded retries and server deadlines."""
import csv
import io
import os
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from dotenv import load_dotenv
from langchain_core.tools import tool
from mysql.connector import Error, connect
from mysql.connector.constants import ClientFlag
from mysql.connector.errors import ConnectionTimeoutError, ReadTimeoutError, WriteTimeoutError

from app.api.monitor import monitor
from app.tools.sql_safety import SQLPolicy, UnsafeSQL, validate_sql

load_dotenv()
MAX_RETRIES = 3
TRANSIENT_ERRORS = {1205, 1213, 2002, 2003, 2006, 2013, 2055, 3024}
TIMEOUT_ERRORS = {1205, 3024}


@dataclass
class SQLRetryBudget:
    retries: int = 0
    stopped: bool = False
    last_failure: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)

    def claim_retry(self):
        with self.lock:
            if self.stopped or self.retries >= MAX_RETRIES:
                self.stopped = True
                return None
            self.retries += 1
            return self.retries


_retry_budget = ContextVar("sql_retry_budget", default=None)


def begin_sql_retry_scope():
    """Each Agent task shares one budget, including tools run in worker threads."""
    return _retry_budget.set(SQLRetryBudget())


def end_sql_retry_scope(token):
    _retry_budget.reset(token)


def get_db_config(policy: SQLPolicy | None = None):
    policy = policy or SQLPolicy.from_env()
    config = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": policy.database,
        "charset": "utf8mb4",
        "collation": os.getenv("MYSQL_COLLATION", "utf8mb4_unicode_ci"),
        # Deterministic quoting rules must match the MySQL AST serializer.
        "sql_mode": "TRADITIONAL",
        "autocommit": False,
        "connection_timeout": min(5, policy.timeout_seconds),
        "read_timeout": policy.timeout_seconds + 2,
        "write_timeout": policy.timeout_seconds + 2,
        "use_pure": True,
        "allow_local_infile": False,
        "client_flags": [-ClientFlag.MULTI_STATEMENTS, -ClientFlag.LOCAL_FILES],
    }
    missing = [key for key in ("user", "password", "database") if config[key] is None]
    if missing:
        raise ValueError(f"缺失数据库配置：{', '.join(missing)}")
    return config


def _execute_once(config, policy, sql=None, tables=frozenset()):
    with connect(**config) as conn:
        with conn.cursor() as cursor:
            # Values are validated integers, not model-supplied SQL.
            cursor.execute(f"SET SESSION max_execution_time = {policy.timeout_seconds * 1000}")
            cursor.execute(f"SET SESSION innodb_lock_wait_timeout = {policy.timeout_seconds}")
            cursor.execute(f"SET SESSION lock_wait_timeout = {policy.timeout_seconds}")
            conn.start_transaction(readonly=True)
            names = sorted(policy.tables)
            placeholders = ",".join(["%s"] * len(names))
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
                f"AND TABLE_NAME IN ({placeholders})",
                (policy.database, *names),
            )
            available = {row[0] for row in cursor.fetchall()} & policy.tables
            if sql is None:
                return "可用的表有：" + ", ".join(sorted(available)) if available else "白名单中没有可用的基础表"
            if not tables <= available:
                raise UnsafeSQL("查询包含不存在的表或视图；只允许白名单中的基础表")
            cursor.execute(sql)
            if cursor.description is None:
                raise UnsafeSQL("数据库未返回只读查询结果")
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchall()  # SQL already has a bounded outer LIMIT.
            output = io.StringIO(newline="")
            writer = csv.writer(output, lineterminator="\n")
            writer.writerow(columns)
            writer.writerows(rows[:policy.max_rows])
            if len(rows) > policy.max_rows:
                output.write(f"\n[结果已截断，最多返回 {policy.max_rows} 行]\n")
            return output.getvalue().rstrip("\n")


def _run_query(policy, sql=None, tables=frozenset()):
    config = get_db_config(policy)
    budget = _retry_budget.get() or SQLRetryBudget()
    while True:
        if budget.stopped:
            return f"[SQL_RETRY_EXHAUSTED] {budget.last_failure}；已达到 3 次重试上限，本任务停止数据库访问，请勿再次调用 SQL 工具。"
        try:
            return _execute_once(config, policy, sql, tables)
        except Error as exc:
            timeout = isinstance(exc, (ConnectionTimeoutError, ReadTimeoutError, WriteTimeoutError)) or exc.errno in TIMEOUT_ERRORS
            # Use controlled diagnostics: driver messages can contain SQL/data.
            if exc.errno in {2002, 2003}:
                failure = f"无法连接 MySQL（错误码 {exc.errno}），请核对 MYSQL_HOST、MYSQL_PORT、数据库服务状态及容器端口映射"
            elif timeout:
                failure = f"数据库连接或查询超时（错误码 {exc.errno}）"
            else:
                failure = f"数据库连接或执行故障（错误码 {exc.errno}）"
            budget.last_failure = failure
            transient = timeout or exc.errno in TRANSIENT_ERRORS
            if not transient:
                # Do not repeat syntax, authentication, permission or schema errors.
                return f"[SQL_ERROR] 数据库查询失败（错误码 {exc.errno}），不会自动重试；请检查语法、数据库权限和配置。"
            retry = budget.claim_retry()
            if retry is None:
                return f"[SQL_RETRY_EXHAUSTED] {failure}；首次执行及 3 次自动重试均失败，本任务停止数据库访问。"
            monitor.report_tool("SQL 查询自动重试", {"retry": retry, "max_retries": MAX_RETRIES, "timeout": timeout, "mysql_errno": exc.errno, "reason": failure})
            time.sleep(0.25 * (2 ** (retry - 1)))


def _invoke(query=None):
    try:
        policy = SQLPolicy.from_env()
        if query is None:
            return _run_query(policy)
        sql, tables = validate_sql(query, policy)
        return _run_query(policy, sql, tables)
    except UnsafeSQL as exc:
        return f"[SQL_REJECTED] {exc}；请求未执行或已停止，不会重试。"
    except (ValueError, TypeError) as exc:
        return f"[SQL_CONFIG_ERROR] {exc}"


@tool
def list_sql_tables() -> str:
    """列出当前库中白名单允许且真实存在的基础表，不展示其他表或视图。"""
    monitor.report_tool("数据库表名查询工具：list_sql_tables", {})
    return _invoke()


@tool
def get_table_data(table_name: str) -> str:
    """预览白名单基础表前100行并返回CSV。先调用list_sql_tables确认表名。"""
    monitor.report_tool("数据库表数据查询工具：get_table_data", {"table_name": table_name})
    try:
        policy = SQLPolicy.from_env()
        if table_name not in policy.tables:
            raise UnsafeSQL("预览表名必须与白名单中的表名完全一致")
        # Names are configuration-validated simple identifiers; never interpolate
        # untrusted table text even when the user supplies backticks or semicolons.
        sql, tables = validate_sql(f"SELECT * FROM `{table_name}` LIMIT 100", policy)
        return _run_query(policy, sql, tables)
    except UnsafeSQL as exc:
        return f"[SQL_REJECTED] {exc}；不会重试。"
    except (ValueError, TypeError) as exc:
        return f"[SQL_CONFIG_ERROR] {exc}"


@tool
def execute_sql_query(query: str) -> str:
    """执行单条只读SELECT（允许CTE/JOIN/UNION），所有基础表必须在白名单。禁止写操作、注释、锁、变量和非批准函数。超时/临时故障最多重试3次，收到SQL_RETRY_EXHAUSTED后停止SQL调用。"""
    monitor.report_tool("数据库表数据查询工具：execute_sql_query", {"query": query})
    return _invoke(query)
