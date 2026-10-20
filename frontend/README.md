# DeepSearch Agents Frontend

React + Vite + Tailwind CSS + Ant Design frontend for the DeepSearch Agents FastAPI backend.

## Run

```bash
pnpm install
pnpm dev
```

By default the app talks to `http://localhost:8000` and `ws://localhost:8000`.
Override with `.env.local`:

```bash
VITE_API_BASE_URL=http://localhost:8000
VITE_WS_BASE_URL=ws://localhost:8000
```

## Backend Contract

- `POST /api/task`
- `POST /api/upload`
- `GET /api/files`
- `GET /api/download`
- `WebSocket /ws/{thread_id}`


## Accounts and conversation history

Register an account on the sign-in page. The sidebar lists only that account's
conversations, named after the first submitted question. Switching conversations
or signing out does not cancel a running backend task. Refreshing restores history,
current task status, attachments and generated files.

Account/password hashes and expiring login sessions are stored in
`app/data/accounts.sqlite3`; conversation ownership and event history are stored in
`app/data/events.sqlite3`, which also stores durable queued jobs. Back up both databases together. Passwords use salted
scrypt hashes. Login tokens are carried in an HttpOnly cookie; ownership is checked
on the backend for task submission/cancellation, history, WebSocket connections,
uploads, file listing and downloads.

Use the same hostname for the frontend and API (for example `localhost` for both,
not `localhost` and `127.0.0.1`). For deployment, proxy `/api` and `/ws` on the same
site. Set `VITE_API_BASE_URL` / `VITE_WS_BASE_URL` to the public API addresses when
building the frontend. Backend environment variables:

```text
FRONTEND_ORIGINS=https://research.example.com
COOKIE_SECURE=1
```

Local development defaults allow `http://localhost:5173` and
`http://127.0.0.1:5173`. `COOKIE_SECURE=1` requires HTTPS. Restart the backend after
changing these settings.

Pre-account legacy history/files have no verified owner and are not automatically
assigned or exposed to new accounts. An administrator must verify ownership before
performing a migration. Queued jobs survive backend restarts. Interrupted attempts
are retried from the beginning after lease expiry within their retry budget;
Agent checkpoints are still held in memory. See [task queue](../docs/task-queue.md).

Validation:

```powershell
npm.cmd run build
# From the repository root:
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
node tests/test_history.cjs
```
