NEXORIAN Proxy Gateway

Environment
- Copy `webapp/backend/.env.example` to `webapp/backend/.env` and fill in keys (Flutterwave, SMTP, API keys).

Build and run with Docker

```bash
cd webapp
docker-compose build
docker-compose up -d
```

Or run directly for development:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r webapp/backend/requirements.txt
python3 webapp/backend/sync_proxies.py
uvicorn webapp.backend.main:app --reload --host 0.0.0.0 --port 8000
```
