"""确定性评分：公开评分口径，避免将模型自评当作事实准确率。"""
import csv
import io
import math
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit


def canonical_url(value):
    parts = urlsplit(value)
    # 忽略锚点、跟踪参数和尾斜杠；仍按具体页面匹配，不能只命中域名。
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip('/'), '', ''))


def search_score(task, result):
    relevant = {canonical_url(url) for url in task['relevant_urls']}
    urls = [canonical_url(item['url']) for item in result['results'][:task.get('k', 5)]]
    seen = set()
    hits = []
    for url in urls:
        hits.append(int(url in relevant and url not in seen))
        seen.add(url)
    count = sum(hits)
    k = task.get('k', 5)
    dcg = sum(hit / math.log2(rank + 2) for rank, hit in enumerate(hits))
    ideal = sum(1 / math.log2(rank + 2) for rank in range(min(k, len(relevant))))
    metrics = {'precision_at_k': count / k, 'recall_at_k': count / len(relevant),
               'mrr': next((1 / (i + 1) for i, hit in enumerate(hits) if hit), 0),
               'ndcg_at_k': dcg / ideal}
    return metrics['ndcg_at_k'], metrics


def csv_rows(value):
    if value.startswith('[SQL_'):
        raise ValueError('SQL tool returned an error')
    rows = list(csv.reader(io.StringIO(value)))
    if not rows:
        raise ValueError('Missing CSV header')
    if any(len(row) != len(rows[0]) for row in rows[1:]):
        raise ValueError('Malformed/truncated CSV')
    # 数值按十进制比较，20 与 20.00 等价；保留字符串和重复记录。
    def cell(text):
        try:
            number = Decimal(text)
            if number.is_finite():
                return ('number', number)
        except InvalidOperation:
            pass
        return ('text', text)
    return rows[0], [tuple(cell(text) for text in row) for row in rows[1:]]


def sql_score(task, result):
    expected_columns, expected = csv_rows(result['reference'])
    columns, actual = csv_rows(result['candidate'])
    equal = actual == expected if task.get('ordered', False) else Counter(actual) == Counter(expected)
    exact = columns == expected_columns and equal
    return float(exact), {'execution_match': float(exact), 'row_count': len(actual)}


def rag_score(task, result):
    answer = result['answer']
    if answer.startswith(('RAGFlow 提问失败', '**ERROR**')):
        raise ValueError('RAGFlow returned an error')
    # 每组事实允许多种表述；规则覆盖率只是代理指标，不能替代人工忠实度审核。
    normalize = lambda text: re.sub(r'\s+', '', text).casefold()
    text = normalize(answer)
    hits = sum(any(normalize(alias) in text for alias in fact) for fact in task['required_facts'])
    forbidden = sum(normalize(phrase) in text for phrase in task.get('forbidden_phrases', []))
    coverage = hits / len(task['required_facts'])
    return coverage if not forbidden else 0.0, {'fact_coverage': coverage, 'forbidden_hits': forbidden}


def score(task, result):
    if task['category'] == 'file':
        success = bool(result['valid'])
        return float(success), {'generation_success': float(success)}
    return {'search': search_score, 'sql': sql_score, 'rag': rag_score}[task['category']](task, result)
