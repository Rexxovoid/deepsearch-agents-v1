"""Bounded RAGFlow HTTP calls and explicit HTTP/business-error handling."""
import json
import logging
import os

import requests
from langchain_core.tools import tool

from app.api.monitor import monitor
from app.ragflow.rag_config import _load_ragflow_env

logger = logging.getLogger(__name__)


def _request(method: str, path: str, **kwargs):
    key, base = _load_ragflow_env()
    try:
        timeout = float(os.getenv("RAGFLOW_TIMEOUT_SECONDS", "60"))
        if not 1 <= timeout <= 600:
            raise ValueError
    except ValueError:
        raise ValueError("RAGFLOW_TIMEOUT_SECONDS 必须介于 1 和 600 秒之间") from None
    return requests.request(method, base + "/api/v1" + path,
                            headers={"Authorization": "Bearer " + key}, timeout=(5, timeout), **kwargs)


def _check_payload(payload: dict):
    code = payload.get("code", 0)
    if code not in (0, "0"):
        detail = str(payload.get("message") or "").strip()
        if not detail:
            detail = "服务端未提供错误详情，请检查 RAGFlow 服务日志及聊天模型、Embedding 模型配置"
        raise RuntimeError(f"RAGFlow 业务错误（code={code}）：{detail}")


def _json_request(method: str, path: str, **kwargs):
    with _request(method, path, **kwargs) as response:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("RAGFlow 返回了无法识别的响应格式")
        _check_payload(payload)
        return payload.get("data")


def _list_chats(name: str | None = None):
    chats = []
    page = 1
    while True:
        data = _json_request("GET", "/chats", params={"page": page, "page_size": 100, "name": name})
        batch = data.get("chats", []) if isinstance(data, dict) else data
        if not isinstance(batch, list):
            raise RuntimeError("RAGFlow 助手列表格式不正确")
        chats.extend(batch)
        if len(batch) < 100 or (isinstance(data, dict) and len(chats) >= data.get("total", len(chats))):
            return chats
        page += 1


def _read_answer(response) -> str:
    response.raise_for_status()
    if "text/event-stream" not in response.headers.get("content-type", ""):
        payload = response.json()
        _check_payload(payload)
        data = payload.get("data")
        answer = data.get("answer", "") if isinstance(data, dict) else ""
    else:
        answer = ""
        for line in response.iter_lines(decode_unicode=True):
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            if not line or not line.startswith("data:"):
                continue  # SSE comments, event names and heartbeat lines.
            content = line[5:].strip()
            if content == "[DONE]":
                break
            payload = json.loads(content)
            _check_payload(payload)
            data = payload.get("data")
            if data is True:
                break
            if isinstance(data, dict) and isinstance(data.get("answer"), str):
                # legacy=True explicitly requests cumulative answers.
                answer = data["answer"] or answer
    if not answer.strip():
        raise RuntimeError("RAGFlow 未返回回答，请检查助手绑定知识库、文档解析状态及模型配置")
    if answer.lstrip().startswith("**ERROR**"):
        raise RuntimeError("RAGFlow 返回了错误答案，请检查 RAGFlow 服务日志及模型配置")
    return answer


def _error_message(exc: Exception) -> str:
    if isinstance(exc, requests.Timeout):
        return "连接或读取 RAGFlow 超时，请检查服务及模型响应速度"
    if isinstance(exc, requests.ConnectionError):
        return "无法连接 RAGFlow，请检查服务地址、端口和网络"
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else "未知"
        return f"RAGFlow HTTP 错误（{status}），请检查服务地址、API Key 和接口版本"
    message = str(exc)
    key = (os.getenv("RAGFLOW_API_KEY") or "").strip()
    return message.replace(key, "<已隐藏>") if key else message


@tool
def get_assistant_list() -> str:
    """查询 RAGFlow 聊天助手名称、描述及关联知识库；提问之前先调用此工具。"""
    monitor.report_tool(tool_name="ragflow聊天助手列表查询工具：get_assistant_list")
    try:
        chats = _list_chats()
        if not chats:
            return "RAGFlow 中没有可用聊天助手，请先创建助手并绑定已解析的知识库"
        return "\n".join(
            f"助手名称:{chat['name']};功能介绍：{chat.get('description') or '未填写'};"
            f"关联的知识库：{'、'.join(chat.get('kb_names') or []) or '未返回知识库名称'}"
            for chat in chats
        )
    except Exception as exc:
        return "查询 RAGFlow 助手失败：" + _error_message(exc)


@tool
def create_ask_delete(chat_name: str, question: str) -> str:
    """向指定 RAGFlow 助手提问并清理临时会话。chat_name 必须来自 get_assistant_list，question 为用户问题。"""
    monitor.report_tool(tool_name="ragflow提问助手工具：create_ask_delete", args={"chat_name": chat_name, "question": question})
    chat_id = session_id = None
    try:
        if not chat_name.strip() or not question.strip():
            raise ValueError("助手名称和问题不能为空")
        if not any(character.isalnum() for character in question):
            raise ValueError("问题未包含有效文字，可能发生了编码丢失；请重新输入中文或英文问题")
        chats = [chat for chat in _list_chats(chat_name) if chat.get("name") == chat_name]
        if not chats:
            raise ValueError("没有找到该聊天助手，请先调用 get_assistant_list 确认名称")
        if len(chats) > 1:
            raise ValueError("存在同名聊天助手，请在 RAGFlow 中设置不同名称后重试")
        chat_id = chats[0]["id"]
        session = _json_request("POST", f"/chats/{chat_id}/sessions", json={"name": "deepsearch_temp_query"})
        session_id = session["id"]
        payload = {"chat_id": chat_id, "messages": [{"role": "user", "content": question}],
                   "question": question, "stream": True, "session_id": session_id, "legacy": True}
        response = _request("POST", "/chat/completions", json=payload, stream=True)
        # Older servers expose only the per-chat endpoint. Never retry a model/business error.
        if response.status_code in {404, 405}:
            response.close()
            response = _request("POST", f"/chats/{chat_id}/completions", json=payload, stream=True)
        with response:
            return _read_answer(response)
    except Exception as exc:
        return "RAGFlow 提问失败：" + _error_message(exc)
    finally:
        if chat_id and session_id:
            try:
                _json_request("DELETE", f"/chats/{chat_id}/sessions", json={"ids": [session_id]})
            except Exception as exc:
                logger.warning("RAGFlow 临时会话清理失败：%s", _error_message(exc))
