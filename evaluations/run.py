"""CLI 评测入口：离线规则回归或真实服务评测，输出可比较的 JSON/Markdown。"""
import argparse
import hashlib
import json
import multiprocessing
import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from evaluations.scoring import score

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ('search', 'sql', 'rag', 'file')


def load_tasks(path):
    payload = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if payload.get('version') != 1 or not isinstance(payload.get('tasks'), list) or not payload['tasks']:
        raise ValueError('Expected nonempty version 1 task set')
    seen = set()
    for task in payload['tasks']:
        task_id = task.get('id', '')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', task_id) or task_id in seen:
            raise ValueError('Task IDs must be unique safe identifiers')
        seen.add(task_id)
        category = task.get('category')
        if category not in CATEGORIES or not 0 < task.get('threshold', 1) <= 1:
            raise ValueError(f'Invalid category/threshold: {task_id}')
        required = {'search': ('question', 'relevant_urls'), 'sql': ('question', 'reference_sql'),
                    'rag': ('question', 'required_facts'), 'file': ('format', 'content', 'required_text')}[category]
        if any(not task.get(key) for key in required):
            raise ValueError(f'Missing task fields: {task_id}')
        if category == 'search' and (not isinstance(task.get('k', 5), int) or not 1 <= task.get('k', 5) <= 20):
            raise ValueError('Search k must be 1..20')
        if category == 'search' and (not isinstance(task['relevant_urls'], list) or any(not isinstance(url, str) or not url.startswith(('https://', 'http://')) for url in task['relevant_urls'])):
            raise ValueError('Relevant URLs must be HTTP(S) page URLs')
        if category == 'file' and task['format'] not in ('md', 'pdf'):
            raise ValueError('File format must be md/pdf')
        if category == 'rag' and any(not isinstance(fact, list) or not fact or any(not isinstance(x, str) or not x.strip() for x in fact) for fact in task['required_facts']):
            raise ValueError('Each fact needs nonempty alias strings')
    return payload['tasks']


def file_result(task, workspace):
    from app.api.context import set_session_context, reset_session_context
    from app.tools.markdown_tools import generate_markdown
    from app.tools.pdf_tools import convert_md_to_pdf
    workspace.mkdir(parents=True, exist_ok=True)
    token = set_session_context(str(workspace.resolve()))
    try:
        generate_markdown.invoke({'content': task['content'], 'filename': 'report.md'})
        md = workspace / 'report.md'
        if not md.exists() or md.read_text(encoding='utf-8') != task['content']:
            return {'valid': False}
        if task['format'] == 'pdf':
            convert_md_to_pdf.invoke({'md_filename': 'report.md'})
            pdf = workspace / 'report.pdf'
            if not pdf.exists() or not pdf.read_bytes().startswith(b'%PDF-'):
                return {'valid': False}
            from pypdf import PdfReader
            reader = PdfReader(pdf)
            text = '\n'.join(page.extract_text() or '' for page in reader.pages)
            valid = len(reader.pages) > 0
        else:
            text, valid = md.read_text(encoding='utf-8'), True
        # 验证文件可读且包含要求的内容，不以工具返回“成功”字符串作为成功依据。
        return {'valid': valid and all(value in text for value in task['required_text'])}
    finally:
        reset_session_context(token)


def observe(task, mode, workspace):
    if task['category'] == 'file':
        return file_result(task, workspace)
    if mode == 'offline':
        # 固定样本仅验证评分/报告流程；绝不将它包装成真实服务准确率。
        return task['fixture']
    if task['category'] == 'search':
        from app.tools.tavily_tool import internet_search
        return internet_search.invoke({'query': task['question'], 'max_results': task.get('k', 5)})
    if task['category'] == 'rag':
        from app.tools.ragflow_tools import create_ask_delete
        name = os.getenv('EVAL_RAGFLOW_CHAT_NAME', '').strip()
        if not name:
            raise ValueError('Missing EVAL_RAGFLOW_CHAT_NAME')
        return {'answer': create_ask_delete.invoke({'chat_name': name, 'question': task['question']})}
    from app.tools.db_tools import begin_sql_retry_scope, end_sql_retry_scope, execute_sql_query
    from app.agent.llm import model
    # SQL 评测覆盖“自然语言 → SQL → 只读执行”，不把参考答案传给被测模型。
    schema = ('drugs(drug_id,generic_name,brand_name,approval_number,specifications,dosage_form,manufacturer,therapeutic_area,description,created_at); '
              'inventory(inventory_id,drug_id,batch_number,quantity_on_hand,warehouse_location,production_date,expiry_date,last_updated); '
              'sales_records(sale_id,drug_id,sale_date,quantity_sold,unit_price,total_amount,customer_name,region,sales_rep)')
    response = model.invoke([('system', '根据表结构生成 MySQL 单条只读 SELECT，直接输出 SQL，不要解释或代码围栏。表结构：' + schema),
                             ('human', task['question'])])
    sql = response.content
    if not isinstance(sql, str):
        raise ValueError('Model did not return SQL text')
    sql = re.sub(r'^```(?:sql)?\s*|\s*```$', '', sql.strip(), flags=re.I)
    token = begin_sql_retry_scope()
    try:
        candidate = execute_sql_query.invoke({'query': sql})
    finally:
        end_sql_retry_scope(token)
    # 参考查询用独立预算；错误不能与另一条相同错误误判为“结果一致”。
    token = begin_sql_retry_scope()
    try:
        reference = execute_sql_query.invoke({'query': task['reference_sql']})
    finally:
        end_sql_retry_scope(token)
    return {'candidate': candidate, 'reference': reference}


def child_run(task, mode, workspace, sender):
    try:
        result = observe(task, mode, Path(workspace))
        value, metrics = score(task, result)
        sender.send({'score': value, 'metrics': metrics, 'error': None})
    except Exception as exc:
        # 报告不写原始异常、SQL 结果或知识库回答，防止密钥和业务数据泄露。
        sender.send({'score': 0.0, 'metrics': {}, 'error': type(exc).__name__})
    finally:
        sender.close()


def run_case(task, mode, workspace, timeout):
    context = multiprocessing.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=child_run, args=(task, mode, str(workspace), sender))
    started = time.monotonic()
    process.start()
    sender.close()
    try:
        # 独立进程提供硬超时；不能只停止等待，让后台请求继续运行。
        if receiver.poll(timeout):
            try:
                result = receiver.recv()
            except EOFError:
                result = {'score': 0.0, 'metrics': {}, 'error': 'ProcessExited'}
        else:
            result = {'score': 0.0, 'metrics': {}, 'error': 'Timeout'}
    finally:
        process.join(0.5)
        if process.is_alive():
            process.terminate()
            process.join(2)
        if process.is_alive():
            process.kill()
            process.join()
        receiver.close()
    return {'id': task['id'], 'category': task['category'], **result,
            'passed': result['error'] is None and result['score'] >= task.get('threshold', 1),
            'duration_seconds': round(time.monotonic() - started, 3)}


def summarize(results):
    summary = {}
    for category in CATEGORIES:
        rows = [row for row in results if row['category'] == category]
        summary[category] = {'total': len(rows), 'passed': sum(row['passed'] for row in rows),
                             'errors': sum(row['error'] is not None for row in rows),
                             'pass_rate': sum(row['passed'] for row in rows) / len(rows) if rows else None,
                             'mean_score': sum(row['score'] for row in rows) / len(rows) if rows else None}
        # 失败仍计入通过率分母；细项只聚合实际产生该指标的任务，单独标明样本量。
        keys = {key for row in rows for key in row['metrics']}
        summary[category]['metrics'] = {}
        for key in sorted(keys):
            values = [row['metrics'][key] for row in rows if key in row['metrics']]
            summary[category]['metrics'][key] = {'mean': sum(values) / len(values), 'samples': len(values)}
    return summary


def compare_baseline(report, baseline):
    # 不同任务集、模式或筛选范围的分数不可直接比较。
    if any(report.get(key) != baseline.get(key) for key in ('mode', 'task_set_sha256')) or {row['id'] for row in report['results']} != {row['id'] for row in baseline['results']}:
        raise ValueError('Baseline must use the same mode, task set and task selection')
    return {category: (item['pass_rate'] - baseline['summary'][category]['pass_rate'])
            if item['pass_rate'] is not None else None for category, item in report['summary'].items()}


def write_reports(report, output):
    output.mkdir(parents=True, exist_ok=True)
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 研搜评测报告', '', f"模式：{report['mode']}；北京时间：{report['created_at']}", '',
             '离线：搜索/SQL/RAG 使用固定样本，文件真实生成。在线：调用真实服务。分数不是完整 Agent 端到端质量。', '',
             '| 类别 | 任务数 | 通过 | 错误 | 通过率 | 平均分 |', '| --- | --- | --- | --- | --- | --- |']
    for category, item in report['summary'].items():
        rate = f"{item['pass_rate']:.1%}" if item['pass_rate'] is not None else '未评测'
        mean = f"{item['mean_score']:.3f}" if item['mean_score'] is not None else '未评测'
        lines.append(f"| {category} | {item['total']} | {item['passed']} | {item['errors']} | {rate} | {mean} |")
    lines += ['', '## 细项指标', '']
    for category, item in report['summary'].items():
        for key, metric in item['metrics'].items():
            lines.append(f"- {category}.{key}: {metric['mean']:.3f}（有效样本 {metric['samples']}）")
    if 'baseline_delta' in report:
        lines += ['', '## 相比基线的通过率变化', '']
        for category, delta in report['baseline_delta'].items():
            if delta is not None:
                lines.append(f'- {category}: {delta:+.1%}')
    lines += ['', '| 任务 | 分数 | 通过 | 错误 | 秒 |', '| --- | --- | --- | --- | --- |']
    for row in report['results']:
        lines.append(f"| {row['id']} | {row['score']:.3f} | {row['passed']} | {row['error'] or ''} | {row['duration_seconds']} |")
    (output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('offline', 'live'), default='offline')
    parser.add_argument('--tasks', type=Path, default=ROOT / 'evaluations/tasks.json')
    parser.add_argument('--category', choices=CATEGORIES)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--baseline', type=Path, help='同模式、同任务集的 report.json')
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 3600:
        parser.error('--timeout must be 0..3600 seconds (exclusive of zero)')
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    tasks = load_tasks(args.tasks)
    tasks = [task for task in tasks if not args.category or task['category'] == args.category]
    if not tasks:
        parser.error('No matching tasks')
    digest = hashlib.sha256(args.tasks.read_bytes()).hexdigest()
    baseline = None
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
        # 运行前检查，避免花费外部调用后才发现基线不兼容。
        compare_baseline({'mode': args.mode, 'task_set_sha256': digest, 'results': tasks, 'summary': {}}, baseline)
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    # 每次运行独立目录，避免旧产物导致文件生成失败时仍被误判成功。
    output = (args.output or ROOT / 'evaluations/reports').resolve() / now.strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True)
    results = []
    for task in tasks:
        row = run_case(task, args.mode, output / 'artifacts' / task['id'], args.timeout)
        results.append(row)
        print(f"{row['id']}: {'PASS' if row['passed'] else 'FAIL'} score={row['score']:.3f} error={row['error']}", flush=True)
    report = {'version': 1, 'mode': args.mode, 'created_at': now.isoformat(),
              'task_set_sha256': digest, 'case_timeout_seconds': args.timeout,
              'summary': summarize(results), 'results': results}
    if baseline is not None:
        report['baseline_delta'] = compare_baseline(report, baseline)
    write_reports(report, output)
    print(f'Report: {output / "report.md"}')
    return 0 if all(row['passed'] for row in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
