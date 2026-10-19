"""SQL policy/retry tests; mock MySQL so rejected statements cannot reach a DB."""
import os
import unittest
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from mysql.connector import Error
from mysql.connector.errors import ReadTimeoutError

from app.tools import db_tools
from app.tools.sql_safety import SQLPolicy, UnsafeSQL, validate_sql


POLICY = SQLPolicy("deepsearch_db", frozenset({"drugs", "inventory", "sales_records"}))


class SQLValidationTests(unittest.TestCase):
    def test_select_join_cte_subqueries_union_and_safe_functions(self):
        queries = [
            "SELECT * FROM drugs",
            "SELECT d.drug_id, SUM(s.quantity_sold) FROM drugs d JOIN sales_records s ON d.drug_id=s.drug_id GROUP BY d.drug_id",
            "WITH inventory_summary AS (SELECT drug_id, SUM(quantity_on_hand) AS qty FROM inventory GROUP BY drug_id) SELECT * FROM inventory_summary",
            "SELECT * FROM drugs WHERE drug_id IN (SELECT drug_id FROM sales_records)",
            "SELECT drug_id FROM drugs UNION SELECT drug_id FROM inventory",
            "SELECT * FROM deepsearch_db.drugs",
            "WITH drugs AS (SELECT * FROM inventory) SELECT * FROM drugs",
            "SELECT COUNT(*), IFNULL(SUM(quantity_on_hand),0),DATE_FORMAT(expiry_date,'%Y'),DATEDIFF(expiry_date,CURDATE()) FROM inventory",
            "SELECT 'DELETE FROM secret; -- literal' AS label FROM drugs LIMIT 2",
            "SELECT 1",
        ]
        for query in queries:
            with self.subTest(query=query):
                sql, tables = validate_sql(query, POLICY)
                self.assertTrue(sql)
                self.assertTrue(tables <= POLICY.tables)

    def test_deny_writes_exports_locks_vars_comments_and_functions(self):
        queries = [
            "DELETE FROM drugs", "UPDATE drugs SET generic_name='x'",
            "INSERT INTO drugs(drug_id) VALUES(99)", "DROP TABLE drugs",
            "ALTER TABLE drugs ADD secret INT", "TRUNCATE TABLE drugs",
            "SHOW TABLES", "DESCRIBE drugs", "CALL mutate()", "SET @x=1",
            "SELECT * FROM drugs; DELETE FROM drugs", "SELECT 1; SELECT 2",
            "SELECT * INTO OUTFILE '/tmp/result' FROM drugs",
            "SELECT * FROM drugs INTO DUMPFILE '/tmp/result'",
            "SELECT * FROM drugs FOR UPDATE", "SELECT * FROM drugs FOR SHARE",
            "SELECT * FROM drugs LOCK IN SHARE MODE",
            "SELECT @x := 1 FROM drugs", "SELECT @@version", "SELECT @x",
            "SELECT SLEEP(10)", "SELECT BENCHMARK(1000000,1)",
            "SELECT LOAD_FILE('/etc/passwd')", "SELECT GET_LOCK('x',10)",
            "SELECT RELEASE_LOCK('x')", "SELECT custom_function() FROM drugs",
            "SELECT deepsearch_db.custom_function() FROM drugs",
            "SELECT /*+ MAX_EXECUTION_TIME(0) */ * FROM drugs",
            "SELECT /*!50000 SQL_NO_CACHE */ * FROM drugs",
            "SELECT * FROM drugs -- comment", "SELECT * FROM drugs #comment",
            "SELECT * FROM drugs WHERE", "SELECT * FROM drugs LIMIT -1", "",
        ]
        for query in queries:
            with self.subTest(query=query), self.assertRaises(UnsafeSQL):
                validate_sql(query, POLICY)

    def test_whitelist_in_all_scopes_and_cte_shadowing(self):
        queries = [
            "SELECT * FROM secret", "SELECT * FROM mysql.user",
            "SELECT * FROM information_schema.tables",
            "SELECT * FROM drugs JOIN secret USING(drug_id)",
            "SELECT * FROM drugs WHERE EXISTS(SELECT 1 FROM secret)",
            "SELECT drug_id FROM drugs UNION SELECT drug_id FROM secret",
            "WITH drugs AS (SELECT * FROM secret) SELECT * FROM drugs",
            "WITH x AS (SELECT * FROM secret) SELECT * FROM drugs",
            "SELECT * FROM (SELECT * FROM secret) AS drugs",
            "SELECT * FROM other_db.drugs",
        ]
        for query in queries:
            with self.subTest(query=query), self.assertRaises(UnsafeSQL):
                validate_sql(query, POLICY)

    def test_limits_and_trailing_semicolon(self):
        self.assertIn("LIMIT 1001", validate_sql("SELECT * FROM drugs LIMIT 50000", POLICY)[0])
        self.assertIn("LIMIT 1001", validate_sql("SELECT * FROM drugs;", POLICY)[0])
        self.assertIn("LIMIT 5", validate_sql("SELECT * FROM drugs LIMIT 5", POLICY)[0])

    def test_invalid_configuration_fails_closed(self):
        for value in ("", "*", "drugs;DROP TABLE drugs", "mysql.user"):
            with patch.dict(os.environ, {"MYSQL_DATABASE": "deepsearch_db", "MYSQL_ALLOWED_TABLES": value}), self.assertRaises(ValueError):
                SQLPolicy.from_env()
        with patch.dict(os.environ, {"MYSQL_QUERY_TIMEOUT_SECONDS": "0"}), self.assertRaises(ValueError):
            SQLPolicy.from_env()


class SQLExecutionTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(db_tools.SQLPolicy, "from_env", return_value=POLICY),
                        patch.object(db_tools, "get_db_config", return_value={}),
                        patch.object(db_tools.time, "sleep"),
                        patch.object(db_tools.monitor, "report_tool")]
        for patcher in self.patches: patcher.start()
    def tearDown(self):
        for patcher in reversed(self.patches): patcher.stop()

    def test_rejected_query_and_injected_preview_never_connect(self):
        with patch.object(db_tools, "connect") as connect:
            for query in ("DELETE FROM drugs", "SELECT * FROM secret", "SELECT * FROM drugs WHERE"):
                self.assertIn("SQL_REJECTED", db_tools.execute_sql_query.invoke({"query": query}))
            self.assertIn("SQL_REJECTED", db_tools.get_table_data.invoke({"table_name": "drugs; DROP TABLE drugs"}))
            connect.assert_not_called()

    def test_only_three_retries_then_task_budget_stops_other_tools(self):
        token = db_tools.begin_sql_retry_scope()
        try:
            with patch.object(db_tools, "_execute_once", side_effect=Error(errno=3024)) as execute:
                result = db_tools.execute_sql_query.invoke({"query": "SELECT * FROM drugs"})
                self.assertIn("SQL_RETRY_EXHAUSTED", result)
                self.assertEqual(execute.call_count, 4)
                self.assertEqual(db_tools.time.sleep.call_count, 3)
                self.assertIn("SQL_RETRY_EXHAUSTED", db_tools.list_sql_tables.invoke({}))
                self.assertIn("SQL_RETRY_EXHAUSTED", db_tools.execute_sql_query.invoke({"query": "SELECT COUNT(*) FROM inventory"}))
                self.assertEqual(execute.call_count, 4)
        finally:
            db_tools.end_sql_retry_scope(token)

    def test_success_after_timeout_and_no_retry_on_permanent_errors(self):
        with patch.object(db_tools, "_execute_once", side_effect=[ReadTimeoutError(), "count\n1"]) as execute:
            self.assertEqual(db_tools.execute_sql_query.invoke({"query": "SELECT COUNT(*) FROM drugs"}), "count\n1")
            self.assertEqual(execute.call_count, 2)
        for errno in (1045, 1064, 1142, 1146):
            with patch.object(db_tools, "_execute_once", side_effect=Error(errno=errno)) as execute:
                self.assertIn("SQL_ERROR", db_tools.execute_sql_query.invoke({"query": "SELECT * FROM drugs"}))
                self.assertEqual(execute.call_count, 1)

    def test_transient_connection_failures_retry_but_new_scope_resets(self):
        for _ in range(2):
            token = db_tools.begin_sql_retry_scope()
            try:
                with patch.object(db_tools, "_execute_once", side_effect=Error(errno=2003)) as execute:
                    self.assertIn("SQL_RETRY_EXHAUSTED", db_tools.list_sql_tables.invoke({}))
                    self.assertEqual(execute.call_count, 4)
            finally: db_tools.end_sql_retry_scope(token)

    def test_retry_budget_shared_with_worker_thread(self):
        token = db_tools.begin_sql_retry_scope()
        try:
            with ThreadPoolExecutor(1) as pool:
                self.assertEqual(pool.submit(copy_context().run, db_tools._retry_budget.get().claim_retry).result(), 1)
                self.assertEqual(db_tools._retry_budget.get().retries, 1)
        finally: db_tools.end_sql_retry_scope(token)

    def test_connection_is_read_only_deadlined_and_bounds_csv(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.side_effect = [[("drugs",), ("secret",)], [(1, "a,b"), (2, "x"), (3, "y")]]
        cursor.description = [("id",), ("name",)]
        policy = SQLPolicy(POLICY.database, POLICY.tables, timeout_seconds=2, max_rows=2)
        with patch.object(db_tools, "connect", return_value=conn):
            result = db_tools._execute_once({}, policy, "SELECT * FROM drugs LIMIT 3", frozenset({"drugs"}))
        conn.start_transaction.assert_called_once_with(readonly=True)
        commands = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertIn("SET SESSION max_execution_time = 2000", commands)
        self.assertIn("SET SESSION innodb_lock_wait_timeout = 2", commands)
        self.assertIn('1,"a,b"', result)
        self.assertIn("最多返回 2 行", result)
        self.assertNotIn("3,y", result)
        conn.__exit__.assert_called_once()

    def test_views_are_not_treated_as_safe_base_tables(self):
        conn, cursor = MagicMock(), MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = []
        with patch.object(db_tools, "connect", return_value=conn), self.assertRaises(UnsafeSQL):
            db_tools._execute_once({}, POLICY, "SELECT * FROM drugs", frozenset({"drugs"}))
        self.assertNotEqual(cursor.execute.call_args.args[0], "SELECT * FROM drugs")

    def test_listing_only_returns_whitelisted_base_tables(self):
        conn, cursor = MagicMock(), MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [("drugs",), ("secret",)]
        with patch.object(db_tools, "connect", return_value=conn):
            self.assertEqual(db_tools._execute_once({}, POLICY), "可用的表有：drugs")

    def test_readonly_setup_failure_prevents_model_query(self):
        conn, cursor = MagicMock(), MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value = cursor
        conn.start_transaction.side_effect = Error(errno=1142)
        with patch.object(db_tools, "connect", return_value=conn):
            self.assertIn("SQL_ERROR", db_tools.execute_sql_query.invoke({"query": "SELECT * FROM drugs"}))
        self.assertEqual(cursor.execute.call_count, 3)  # Only deadline setup, no query.
        conn.__exit__.assert_called_once()


class SQLConnectionConfigTests(unittest.TestCase):
    def test_driver_deadlines_and_security_options(self):
        with patch.dict(os.environ, {"MYSQL_USER": "reader", "MYSQL_PASSWORD": "fake", "MYSQL_SQL_MODE": "NO_BACKSLASH_ESCAPES"}):
            config = db_tools.get_db_config(SQLPolicy(POLICY.database, POLICY.tables, timeout_seconds=3))
        self.assertEqual(config["connection_timeout"], 3)
        self.assertEqual(config["read_timeout"], 5)
        self.assertEqual(config["write_timeout"], 5)
        self.assertEqual(config["sql_mode"], "TRADITIONAL")
        self.assertFalse(config["autocommit"])
        self.assertFalse(config["allow_local_infile"])
        self.assertIn(-db_tools.ClientFlag.MULTI_STATEMENTS, config["client_flags"])


if __name__ == "__main__": unittest.main()
