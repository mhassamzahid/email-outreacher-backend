# Outreacher API

FastAPI backend for the outreach dashboard (`../email-outreacher-frontend`). Deployed on Railway.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # fill in what you need
uvicorn app.main:app --reload --port 8000
```

- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health and http://localhost:8000/api/v1/health

## Test

```bash
pytest
```

## Layout

```
app/
  main.py             app factory, CORS, router mounting
  core/config.py      settings from environment variables
  api/router.py       /api/v1 router; add feature routers here
  api/routes/         one module per feature
tests/
```

## Deploy (Railway)

`railway.json` sets the start command and the `/health` healthcheck. Set the variables from
`.env.example` on the Railway service, including `CORS_ORIGINS` with the Vercel URL.
