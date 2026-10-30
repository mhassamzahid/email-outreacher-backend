# Coldbrew API

FastAPI backend for Coldbrew, the outreach dashboard (`../email-outreacher-frontend`). Deployed on Railway.

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

## Sign-in

Supabase Auth, email and password. There's no sign-up: accounts are made, and passwords reset, in the
Supabase dashboard (Authentication > Users). Signed in, anyone can change their own password from the
sidebar. It's the shared Supabase account, so the change applies to Lead Intel too.

Every route except health needs `Authorization: Bearer <Supabase access token>`, checked against the
project's public keys (`SUPABASE_JWKS_URL`, or the standard address under `SUPABASE_URL`). Supabase Auth
is shared with Lead Intel, so any account there can sign in; what it can do here comes from
`eo_users`: a first sign-in is added as Sales with no workspaces until an admin gives them some.
Admins open every workspace and manage users and workspaces; Management and admins can change ICPs.

## Endpoints (v1)

| Method | Path | |
|---|---|---|
| GET/POST | `/api/v1/exclusions` | The do-not-contact list (every workspace): companies by domain, phone, Google place id or name and city; people by email or its domain. Excluded ones are never found, made leads, or written to. Management and admins change it |
| DELETE | `/api/v1/exclusions/{id}` | Take an entry off the list |
| POST | `/api/v1/exclusions/company/{companyId}` | Put a pipeline company on the list by every key it has, and mark it not a fit |
| GET | `/api/v1/mailboxes` | The sending mailboxes from CheapSequencer (limits, warmup, today's sends and placements), plan usage, campaigns, and each domain's DNS health from CheapInboxes. Read-only |
| GET, PUT | `/api/v1/sending/settings` | Sender company and physical address (CAN-SPAM), the generic text of emails 2 and 3, and how many hours before it's due a follow-up is written. Saving (managers) writes the signature into every "Coldbrew: " campaign that's draft or paused |
| GET | `/api/v1/sending/campaigns` | The "Coldbrew: " campaigns in CheapSequencer with how many leads were queued into each |
| POST | `/api/v1/sending/queue` | Put approved first emails into their ICP's campaign (`{emailIds}`): a new list, a CSV import with `email_1_subject`/`email_1_body` and the generic follow-ups, then enrollment. Never activates a campaign or sends. 409 until the sender address is set |
| POST | `/api/v1/sending/follow-ups/run` | Read each campaign's enrollments, record sends, replies and stops, and have Claude write the follow-ups due within the lead time for leads who haven't replied. Also runs on its own every 30 minutes |
| GET | `/api/v1/me` | The signed-in person: role, status and workspaces |
| GET/POST | `/api/v1/users` | Admins: everyone; add someone by email with a role and workspaces, ready for their first sign-in |
| PATCH/DELETE | `/api/v1/users/{id}` | Admins: change role, status or workspaces; remove from this app (their Supabase account stays). The last active admin can't be lost |
| GET | `/api/v1/workspaces` | The workspaces the signed-in person can open |
| PUT | `/api/v1/workspaces/{id}/members` | Admins: exactly who can open it |
| POST | `/api/v1/workspaces` | Create one (`{name, description}`); 409 if the name is taken (ignoring case) |
| PATCH | `/api/v1/workspaces/{id}` | Rename or change the description |
| DELETE | `/api/v1/workspaces/{id}` | Delete it and everything it owns in our `eo_` tables; refuses the last one |
| GET | `/api/v1/icps` | Lead Intel ICPs with company counts and the outreach profile |
| POST | `/api/v1/icps` | Add an ICP to Lead Intel's `icps` table (Lead Intel's fields; insert-only, recorded as `LEAD_INTEL_ICP_CREATED_BY`) |
| GET | `/api/v1/icps/{id}` | One ICP |
| PUT | `/api/v1/icps/{id}/profile` | Save outreach settings for an ICP, including its product and its verify, hook and email prompt choices (writes `outreach.eo_icp_profiles` only) |
| GET | `/api/v1/icps/{id}/companies` | Contactable companies for an ICP (`q`, `limit`, `offset`) |
| GET | `/api/v1/companies` | Contactable Lead Intel companies (`icpId`, `noIcp`, `inWorkspace`, `q`, `limit`, `offset`); with `X-Workspace-Id` each has `importedAt` |
| POST | `/api/v1/companies/import` | Pull Lead Intel companies into a workspace: `{companyIds}` or `{icpId}` (writes `eo_workspace_companies` only) |
| POST | `/api/v1/companies/remove` | Take companies out of a workspace (`{companyIds}`); Lead Intel keeps them |
| GET | `/api/v1/companies/{id}` | One company with every stored field (website text, Instagram profile…) |
| GET | `/api/v1/stakeholders` | People found at the workspace's companies (`companyId`) |
| GET | `/api/v1/stakeholders/template.csv` | People CSV template: the columns of Apollo's people export that we use |
| POST | `/api/v1/stakeholders` | Add one person by hand (`icpId` optional); with an email or LinkedIn URL they become a lead |
| POST | `/api/v1/stakeholders/import` | Import people from a CSV body in Apollo's export format (`icpId`, `dryRun`); matched to pipeline companies by domain or name; repeats update, not duplicate |
| POST | `/api/v1/stakeholders/search` | Apollo People Search by company domain, `{companyIds, titles, seniorities}`; 0 credits, paid Apollo plans only |
| POST | `/api/v1/stakeholders/search-icp` | Apollo People Search for a Lead Intel ICP, anywhere: titles, seniorities, company locations, keywords, size, verified-email only, `page`/`perPage` (max 100); 0 credits, paid Apollo plans only |
| POST | `/api/v1/stakeholders/reveal` | Apollo enrichment for `{stakeholderIds}` (max 25): email + LinkedIn, adds them as leads; ~1 credit each, never twice |
| POST | `/api/v1/stakeholders/search-linkedin` | People at companies on LinkedIn via Apify (`harvestapi~linkedin-company-employees`), full names + LinkedIn, no emails; ~$0.004/person |
| POST | `/api/v1/stakeholders/add-as-leads` | Turn found people with a LinkedIn URL or email into leads; free |
| GET | `/api/v1/posts` | Fetched posts (`leadId`) |
| POST | `/api/v1/posts/fetch` | Recent LinkedIn (`harvestapi~linkedin-profile-posts`) and Instagram (`apify~instagram-scraper`) posts for up to 25 leads; `sources` limits it to `linkedin`, `instagram` or both; Instagram falls back to the company's account from Lead Intel |
| GET | `/api/v1/pipeline/companies` | The company pipeline (`icpId`, `status`: found, qualified, not_fit, error) |
| POST | `/api/v1/pipeline/find` | Find companies for an ICP in Lead Intel (qualified straight away), Apollo (`mixed_companies/search`, 1 credit a page) and/or Google Maps (Places API Text Search over a grid around each place, as Lead Intel does; $0.035 a request, capped by `googleMaps.maxSearches`; chains skipped); deduplicated by domain, Lead Intel id, Apollo id and Google place id |
| POST | `/api/v1/pipeline/research` | Research up to 10 Apollo or Google Maps companies (also takes the emails, phone and owner their site publishes, as Lead Intel did): website (own crawl, Jina fallback), Instagram profile (Apify), the ICP's rule, then a Claude Haiku score |
| GET | `/api/v1/pipeline/companies/{id}/content` | What research read: each website page's full text (`eo_company_pages`) and the company's own Instagram posts (`eo_posts`) |
| PATCH | `/api/v1/pipeline/companies/{id}` | Keep (`qualified`) or drop (`not_fit`) by hand; research won't undo it |
| POST | `/api/v1/pipeline/stakeholders` | People at qualified companies: Apollo by domain, or LinkedIn (Apify) by name |
| GET | `/api/v1/prompts` | The workspace's prompts and all their versions; creates the built-in defaults (company verification, hook selection, email generation, email scoring) the first time |
| POST | `/api/v1/prompts` | New prompt with its v1 (`{name, kind, version, note, isDefault}`) |
| POST | `/api/v1/prompts/{id}/versions` | Save the next version, which becomes the one in use |
| PATCH | `/api/v1/prompts/{id}` | Rename, or make it the default of its kind |
| DELETE | `/api/v1/prompts/{id}` | Delete it; 409 for the default or one an ICP uses |
| POST | `/api/v1/emails/generate` | For up to 25 leads: hook selection (ranked hooks from their posts, their company's posts and website, and their profile), email generation from the top hook with the ICP's rated examples (counts in settings), then email scoring against those examples and the sample email; replaces unsent drafts. Never sends |
| GET | `/api/v1/emails` | Drafted emails with their ranked hooks, hook post, score and rating (`status`, `leadId`) |
| PATCH | `/api/v1/emails/{id}` | Edit subject or body, approve, skip, or back to draft (never `sent`) |
| PUT | `/api/v1/emails/{id}/feedback` | Rate an email `good` or `bad` with tags and a note; replaces an earlier rating |
| DELETE | `/api/v1/emails/{id}/feedback` | Remove a rating |
| GET | `/api/v1/emails/feedback/stats` | Good and bad counts per ICP |
| POST | `/api/v1/improve/email-prompt` | Claude (Sonnet 5.5) suggests a revised Email Generation prompt for an ICP from its ratings (at least 3); nothing is saved |
| GET/PUT | `/api/v1/settings/feedback` | How many examples Claude learns from: library examples, good and bad rated emails per email (0-10 each, default 3) and rated emails per prompt suggestion (3-50, default 20) |
| GET/PUT | `/api/v1/settings/research` | How much of a website research reads while Claude picks each next page: most pages per company (1-15, default 6) and most seconds (30-600, default 120) |
| GET/POST | `/api/v1/products` | What the workspace sells (name, description, value, proof, offer, call to action, link), with the ICPs selling each |
| PUT/DELETE | `/api/v1/products/{id}` | Edit a product; delete one no ICP sells (409 otherwise) |
| GET/POST | `/api/v1/examples` | The example email library: emails in our voice, each linked to ICPs or to all; the newest for a lead's ICP fill {{examples.samples}} |
| PUT/DELETE | `/api/v1/examples/{id}` | Edit or delete an example |
| POST | `/api/v1/examples/from-email/{emailId}` | Save a generated email (with edits) as an example for its lead's ICP |
| GET | `/api/v1/usage` | Spend over `days` (default 30): Claude tokens and estimated dollars, Apollo credits, Apify dollars; per provider, per action, and the latest 100 calls |
| GET | `/api/v1/leads/template.csv` | CSV template with the columns the import reads |
| GET | `/api/v1/leads` | Leads in a workspace (`icpId`, `status`, `q`, `limit`, `offset`), each with `postCount` |
| POST | `/api/v1/leads` | Add one lead by hand; 409 if it's a duplicate |
| POST | `/api/v1/leads/import` | Import a CSV sent as the body (`Content-Type: text/csv`); `icpId`, `dryRun` |
| POST | `/api/v1/leads/move` | Move `{leadIds}` to `{icpId}`; drafts and posts stay |
| POST | `/api/v1/leads/delete` | Delete `{leadIds}` with their posts, hooks, drafts and ratings; the people they came from stay |

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
