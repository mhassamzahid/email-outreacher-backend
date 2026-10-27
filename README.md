# Outreacher API

FastAPI backend for the outreach dashboard (`../email-outreacher-frontend`). Deployed on Railway.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # fill in what you need
uvicorn main:app --reload
```

- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health and http://localhost:8000/api/v1/health

## Database

This service shares Lead Intel's Supabase database. **Lead Intel's tables are read-only here, with one agreed exception:** adding a new ICP inserts a row into `public.icps` (`lead_intel_writes.py`). Nothing in Lead Intel is ever updated or deleted, and no other Lead Intel table is written; `tests/test_lead_intel_safety.py` enforces this.

- Lead Intel tables (`public.icps`, `businesses`, `field_metadata`, `identity_keys`) are read
  through `lead_intel.py`, inside `SET TRANSACTION READ ONLY`, so Postgres rejects
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
| GET | `/api/v1/workspaces` | All workspaces |
| POST | `/api/v1/workspaces` | Create one (`{name, description}`); 409 if the name is taken (ignoring case) |
| PATCH | `/api/v1/workspaces/{id}` | Rename or change the description |
| DELETE | `/api/v1/workspaces/{id}` | Delete it and everything it owns in our `eo_` tables; refuses the last one |
| GET | `/api/v1/icps` | Lead Intel ICPs with company counts and the outreach profile |
| POST | `/api/v1/icps` | Add an ICP to Lead Intel's `icps` table (Lead Intel's fields; insert-only, recorded as `LEAD_INTEL_ICP_CREATED_BY`) |
| GET | `/api/v1/icps/{id}` | One ICP |
| PUT | `/api/v1/icps/{id}/profile` | Save outreach settings for an ICP (writes `outreach.eo_icp_profiles` only) |
| GET | `/api/v1/icps/{id}/companies` | Contactable companies for an ICP (`q`, `limit`, `offset`) |
| GET | `/api/v1/companies` | Contactable Lead Intel companies (`icpId`, `noIcp`, `inWorkspace`, `q`, `limit`, `offset`); with `X-Workspace-Id` each has `importedAt` |
| POST | `/api/v1/companies/import` | Pull Lead Intel companies into a workspace: `{companyIds}` or `{icpId}` (writes `eo_workspace_companies` only) |
| POST | `/api/v1/companies/remove` | Take companies out of a workspace (`{companyIds}`); Lead Intel keeps them |
| GET | `/api/v1/companies/{id}` | One company with every stored field (website text, Instagram profile…) |
| GET | `/api/v1/stakeholders` | People found at the workspace's companies (`companyId`) |
| POST | `/api/v1/stakeholders/search` | Apollo People Search by company domain, `{companyIds, titles, seniorities}`; 0 credits |
| POST | `/api/v1/stakeholders/search-icp` | Apollo People Search for a Lead Intel ICP, anywhere: titles, seniorities, company locations, keywords, size, verified-email only, `page`/`perPage` (max 100); 0 credits |
| POST | `/api/v1/stakeholders/reveal` | Apollo enrichment for `{stakeholderIds}` (max 25): email + LinkedIn, adds them as leads; ~1 credit each, never twice |
| POST | `/api/v1/stakeholders/search-linkedin` | People at companies on LinkedIn via Apify (`harvestapi~linkedin-company-employees`), full names + LinkedIn, no emails; ~$0.004/person |
| POST | `/api/v1/stakeholders/add-as-leads` | Turn found people with a LinkedIn URL or email into leads; free |
| GET | `/api/v1/posts` | Fetched posts (`leadId`) |
| POST | `/api/v1/posts/fetch` | Recent LinkedIn (`harvestapi~linkedin-profile-posts`) and Instagram (`apify~instagram-scraper`) posts for up to 25 leads; Instagram falls back to the company's account from Lead Intel |
| GET | `/api/v1/leads/template.csv` | CSV template with the columns the import reads |
| GET | `/api/v1/leads` | Leads in a workspace (`icpId`, `status`, `q`, `limit`, `offset`) |
| POST | `/api/v1/leads` | Add one lead by hand; 409 if it's a duplicate |
| POST | `/api/v1/leads/import` | Import a CSV sent as the body (`Content-Type: text/csv`); `icpId`, `dryRun` |

Lead endpoints need an `X-Workspace-Id` header until sign-in exists. A lead needs an email or a
LinkedIn URL. Duplicates within a workspace are caught by email, then LinkedIn URL, then company
domain plus full name; the database's unique indexes on email and LinkedIn URL are the final check.

"Contactable" means `status = active` and not excluded. Merged and do-not-contact businesses are
never returned.

## Test

```bash
pytest
```

## Layout

```
main.py           the app: CORS, /api/v1 router list
config.py         settings from environment variables
database.py       engine, read-only connection, sessions
models.py         our tables (`outreach.eo_*`)
lead_intel.py     everything that reads Lead Intel; nothing in it writes
crud.py           reads and writes for our tables
schemas.py        request/response models (camelCase on the wire)
dependencies.py   FastAPI dependencies (repos, workspace header)
routers/          one module per feature
services/         logic with no database (CSV parsing, dedupe)
migrations/       Alembic, fenced to the `outreach` schema
tests/
```

## Deploy (Railway)

`railway.json` sets the start command and the `/health` healthcheck. Set the variables from
`.env.example` on the Railway service, including `CORS_ORIGINS` with the Vercel URL.
