1. RAGFLOW遇到无响应无法正确访问知识库 - 原始提问请求返回 HTTP 200，但 SSE 内含业务码 500、空错误信息，以及 `**ERROR**: `。
- 独立调用两个知识库的 `/api/v1/retrieval`，均返回业务码 100、`AssertionError()`。
- 当前服务要求 `messages`；只传 `question` 会返回业务码 101。