"""评分负例和进程边界回归，避免只测试一组“满分答案”。"""
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from pathlib import Path

from evaluations.run import compare_baseline, load_tasks, observe, run_case, summarize, write_reports
from evaluations.scoring import rag_score, search_score, sql_score
from evaluations.scoring import score


class EvaluationTests(unittest.TestCase):
    def test_published_suites_and_reference_queries_are_consistent(self):
        from app.tools.sql_safety import SQLPolicy, validate_sql
        root = Path(__file__).resolve().parents[1] / 'evaluations'
        tasks = load_tasks(root / 'tasks.json')
        indexed = {task['id']: task for task in tasks}
        policy = SQLPolicy('deepsearch_db', frozenset({'drugs', 'inventory', 'sales_records'}))
        # 防止分类任务集与默认集的题目/答案漂移，并检查参考 SQL 仍满足安全策略。
        for category in ('search', 'sql', 'rag', 'file'):
            suite = load_tasks(root / 'suites' / f'{category}.json')
            self.assertEqual(suite, [task for task in tasks if task['category'] == category])
        for task in load_tasks(root / 'suites/smoke.json'):
            self.assertEqual(task, indexed[task['id']])
        for task in tasks:
            with self.subTest(task=task['id']):
                if task['category'] == 'sql':
                    validate_sql(task['reference_sql'], policy)
                if task['category'] != 'file':
                    value, _ = score(task, task['fixture'])
                    self.assertGreaterEqual(value, task['threshold'])

    def test_live_sql_calls_model_without_reference_then_executes_both(self):
        tool = MagicMock()
        tool.invoke.side_effect = ['total\n50', 'total\n50']
        model = MagicMock()
        model.invoke.return_value.content = 'SELECT COUNT(*) AS total FROM drugs'
        db = SimpleNamespace(begin_sql_retry_scope=MagicMock(return_value='token'),
                             end_sql_retry_scope=MagicMock(), execute_sql_query=tool)
        task = {'category': 'sql', 'question': '统计药品总数',
                'reference_sql': 'SELECT COUNT(drug_id) AS total FROM drugs'}
        with patch.dict('sys.modules', {'app.tools.db_tools': db, 'app.agent.llm': SimpleNamespace(model=model)}):
            result = observe(task, 'live', Path('.'))
        self.assertNotIn(task['reference_sql'], str(model.invoke.call_args))
        self.assertEqual(tool.invoke.call_args_list[0].args[0]['query'], model.invoke.return_value.content)
        self.assertEqual(tool.invoke.call_args_list[1].args[0]['query'], task['reference_sql'])
        self.assertEqual(db.begin_sql_retry_scope.call_count, 2)
        self.assertEqual(db.end_sql_retry_scope.call_count, 2)
        self.assertEqual(sql_score({}, result)[0], 1)

    def test_live_search_and_rag_dispatch_to_project_tools(self):
        search, rag = MagicMock(), MagicMock()
        search.invoke.return_value = {'results': []}
        rag.invoke.return_value = 'answer'
        with patch.dict('sys.modules', {'app.tools.tavily_tool': SimpleNamespace(internet_search=search),
                                        'app.tools.ragflow_tools': SimpleNamespace(create_ask_delete=rag)}), \
                patch.dict('os.environ', {'EVAL_RAGFLOW_CHAT_NAME': 'benchmark'}):
            self.assertEqual(observe({'category': 'search', 'question': 'query', 'k': 3}, 'live', Path('.')), {'results': []})
            self.assertEqual(observe({'category': 'rag', 'question': 'question'}, 'live', Path('.')), {'answer': 'answer'})
        search.invoke.assert_called_once_with({'query': 'query', 'max_results': 3})
        rag.invoke.assert_called_once_with({'chat_name': 'benchmark', 'question': 'question'})

    def test_search_ranking_duplicate_and_unrelated_pages(self):
        task = {'relevant_urls': ['https://example.org/docs'], 'k': 3}
        result = {'results': [{'url': 'https://other.org/docs'},
                              {'url': 'https://example.org/docs/?tracking=1#part'},
                              {'url': 'https://example.org/docs'}]}
        value, metrics = search_score(task, result)
        self.assertAlmostEqual(metrics['precision_at_k'], 1 / 3)
        self.assertEqual(metrics['recall_at_k'], 1)
        self.assertEqual(metrics['mrr'], 0.5)
        self.assertLess(value, 1)
        self.assertEqual(search_score(task, {'results': []})[0], 0)
        self.assertEqual(search_score(task, {'results': [{'url': 'https://example.org/other'}]})[0], 0)

    def test_sql_values_order_duplicates_and_errors(self):
        result = {'reference': 'id,amount\n1,20.00\n2,30.00', 'candidate': 'id,amount\n2,30\n1,20'}
        self.assertEqual(sql_score({'ordered': False}, result)[0], 1)
        self.assertEqual(sql_score({'ordered': True}, result)[0], 0)
        self.assertEqual(sql_score({}, {'reference': 'id\n1\n1', 'candidate': 'id\n1'})[0], 0)
        self.assertEqual(sql_score({}, {'reference': 'id\n1', 'candidate': 'wrong\n1'})[0], 0)
        for value in ('[SQL_ERROR] unavailable', '', 'id,name\n1'):
            with self.assertRaises(ValueError):
                sql_score({}, {'reference': value, 'candidate': value})

    def test_rag_missing_facts_contradiction_and_error(self):
        task = {'required_facts': [['3次', '三次'], ['4次']], 'forbidden_phrases': ['无限重试']}
        self.assertEqual(rag_score(task, {'answer': '三次重试，4 次执行'})[0], 1)
        self.assertEqual(rag_score(task, {'answer': '三次'})[0], 0.5)
        self.assertEqual(rag_score(task, {'answer': '3次，4次，无限重试'})[0], 0)
        with self.assertRaises(ValueError):
            rag_score(task, {'answer': 'RAGFlow 提问失败：不可用'})

    def test_task_validation_blocks_duplicate_and_path_traversal_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'tasks.json'
            task = {'id': 'safe', 'category': 'file', 'format': 'md', 'content': 'x', 'required_text': ['x']}
            for tasks in ([task, task], [{**task, 'id': '../escape'}], [{**task, 'threshold': 0}]):
                path.write_text(json.dumps({'version': 1, 'tasks': tasks}), encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_tasks(path)

    def test_real_file_tool_and_content_failure(self):
        task = {'id': 'file', 'category': 'file', 'format': 'md', 'content': '# Report', 'required_text': ['missing']}
        with tempfile.TemporaryDirectory() as folder:
            result = run_case(task, 'offline', Path(folder), 30)
            self.assertFalse(result['passed'])
            self.assertEqual(result['score'], 0)
            self.assertTrue((Path(folder) / 'report.md').exists())

    def test_hard_timeout_and_missing_service_count_as_failed(self):
        task = {'id': 'timeout', 'category': 'search'}
        with tempfile.TemporaryDirectory() as folder:
            result = run_case(task, 'offline', Path(folder), 0.001)
            self.assertEqual(result['error'], 'Timeout')
            self.assertFalse(result['passed'])
            result = run_case(task, 'offline', Path(folder), 30)
            self.assertEqual(result['error'], 'KeyError')
            self.assertEqual(summarize([result])['search']['pass_rate'], 0)

    def test_report_empty_categories_not_scored_as_success(self):
        result = {'id': 'failed', 'category': 'sql', 'passed': False, 'score': 0,
                  'metrics': {}, 'error': 'Timeout', 'duration_seconds': 1}
        summary = summarize([result])
        self.assertEqual(summary['sql']['errors'], 1)
        self.assertIsNone(summary['rag']['pass_rate'])
        with tempfile.TemporaryDirectory() as folder:
            write_reports({'mode': 'offline', 'created_at': 'test', 'summary': summary, 'results': [result]}, Path(folder))
            self.assertIn('Timeout', (Path(folder) / 'report.md').read_text(encoding='utf-8'))
            self.assertEqual(json.loads((Path(folder) / 'report.json').read_text(encoding='utf-8'))['mode'], 'offline')

    def test_baseline_rejects_different_modes_or_tasks(self):
        report = {'mode': 'offline', 'task_set_sha256': 'same', 'results': [{'id': 'one'}],
                  'summary': {'sql': {'pass_rate': 0.5}}}
        baseline = {**report, 'summary': {'sql': {'pass_rate': 1}}}
        self.assertEqual(compare_baseline(report, baseline), {'sql': -0.5})
        for other in ({**baseline, 'mode': 'live'}, {**baseline, 'task_set_sha256': 'different'},
                      {**baseline, 'results': [{'id': 'two'}]}):
            with self.assertRaises(ValueError):
                compare_baseline(report, other)


if __name__ == '__main__':
    unittest.main()
