"""RAGFlow connection configuration, validated when a tool is invoked."""
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv


def _load_ragflow_env() -> tuple[str, str]:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    key = (os.getenv("RAGFLOW_API_KEY") or "").strip()
    base = (os.getenv("RAGFLOW_API_URL") or "").strip().rstrip("/")
    if not key or key in {"111", "your-api-key", "ragflow-your-api-key"}:
        raise ValueError("请在 .env 中配置有效的 RAGFLOW_API_KEY")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("RAGFLOW_API_URL 必须是 http(s) 服务地址，不含凭据、查询参数或页面锚点")
    # The SDK/API layer appends /api/v1 itself.
    path = parsed.path.rstrip("/")
    if path.endswith("/api/v1"):
        path = path[:-7]
    return key, urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
