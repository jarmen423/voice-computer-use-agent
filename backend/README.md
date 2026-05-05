# VoiceUse License Server

FastAPI backend for VoiceUse license management.

## Deploy

```bash
cd backend
pip install -r requirements.txt
uvicorn license_server:app --host 0.0.0.0 --port 8000
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `sqlite:///./license_server.db` |
| `SECRET_KEY` | Signing secret | `dev-secret-change-me` |
| `ADMIN_API_KEY` | Key for admin endpoints | `admin-dev-secret` |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/license/activate` | Activate key on machine |
| POST | `/v1/license/validate` | Validate token |
| POST | `/v1/license/deactivate` | Deactivate machine |
| POST | `/admin/generate-keys` | Generate new keys (admin) |
| GET | `/admin/stats` | View stats (admin) |
| GET | `/health` | Health check |

## Generate Keys

```bash
curl -X POST https://license.voiceuse.ai/admin/generate-keys \
  -H "X-Admin-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"count": 10, "tier": "pro", "max_machines": 3}'
```
