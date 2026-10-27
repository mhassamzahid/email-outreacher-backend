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

## Database

This service shares Lead Intel's Supabase database. **Lead Intel's tables are read-only here.**

- Lead Intel tables (`public.icps`, `businesses`, `field_metadata`, `identity_keys`) are read
  through `app/repositories/lead_intel.py`, inside `SET TRANSACTION READ ONLY`, so Postgres rejects
  any write.
- This service's own tables live in the `outreach` schema and every one starts with `eo_`. Alembic only ever sees that schema,
  and `tests/test_lead_intel_safety.py` fails if a migration or the read repo reaches outside it.
- No foreign keys point at Lead Intel tables, so its deletes and merges behave exactly as before.

Set `DATABASE_URL` in `.env` (Supabase → Connect → connection string), then:

```bash
alembic upgrade head --sql   # preview the SQL first, without connecting
alembic upgrade head         # create the outreach schema and tables
```

Check the connection and that Lead Intel still has every column we read:
`GET /api/v1/health/db` (`missingColumns` should be empty).

## Endpoints (v1)

| Method | Path | |
|---|---|---|
| GET | `/api/v1/icps` | Lead Intel ICPs with company counts and the outreach profile |
| GET | `/api/v1/icps/{id}` | One ICP |
| PUT | `/api/v1/icps/{id}/profile` | Save outreach settings for an ICP (writes `outreach.eo_icp_profiles` only) |
| GET | `/api/v1/icps/{id}/companies` | Contactable companies for an ICP (`q`, `limit`, `offset`) |
| GET | `/api/v1/companies` | All contactable companies (`icpId`, `q`, `limit`, `offset`) |
| GET | `/api/v1/companies/{id}` | One company with every stored field (website text, Instagram profile…) |

"Contactable" means `status = active` and not excluded. Merged and do-not-contact businesses are
never returned.

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
  db/lead_intel.py    Lead Intel tables (read-only descriptions)
  db/models.py        our tables, `outreach` schema
  repositories/       queries
  schemas/            request/response models (camelCase on the wire)
migrations/           Alembic, fenced to the `outreach` schema
tests/
```

## Deploy (Railway)

`railway.json` sets the start command and the `/health` healthcheck. Set the variables from
`.env.example` on the Railway service, including `CORS_ORIGINS` with the Vercel URL.
