# Support Ticket API

Django/DRF + PostgreSQL backend for tickets, messages, assignment and status history.
Interactive API documentation: **[Swagger UI](http://127.0.0.1:8000/api/docs/)**.

## Run locally

Use Python 3.12 and Docker Compose. PostgreSQL is mandatory for development/tests.

```bash
git clone --branch develop https://github.com/Rtapy/support-ticket-api.git
cd support-ticket-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

Edit `.env`: set `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD`, and keep the other
`POSTGRES_*` values from the example or replace them for your server. Single-quote
secrets containing shell metacharacters. Local database port: `55439`; change it
if occupied. `.env` is ignored by Git. Export it in each terminal:

```bash
set -a
source .env
set +a
docker compose up -d --wait db
python manage.py migrate
python manage.py createsuperuser --username admin
python manage.py runserver
```

Compose runs only PostgreSQL; skip its commands when using an existing server.
Credentials initialize a new volume only: editing `.env` does not update existing
PostgreSQL users/passwords. Stop the local database with `docker compose stop db`.

## Users, roles and tokens

At [Django Admin](http://127.0.0.1:8000/admin/), create active users `employee` and
`support`; add `support` to the migration-created `Support` group.

| Ticket API role | Access |
| --- | --- |
| Employee | Own tickets/messages/history; create, reply and close own resolved ticket |
| Support group | All tickets; reply, assign, change status and reopen |
| Superuser | Same ticket operations as Support |

`is_staff` alone does not grant Support access. User/group management uses Django
Admin's built-in permissions. Ticket/message/history Admin screens are read-only.

In another activated terminal with `.env` exported, provision tokens:

```bash
python manage.py drf_create_token employee
python manage.py drf_create_token support
python manage.py drf_create_token admin
```

Swagger and [OpenAPI schema](http://127.0.0.1:8000/api/schema/) are public. In
Swagger, click **Authorize** and enter the complete value **`Token <key>`**.
Django Admin login does not authenticate ticket API requests. Swagger UI assets
are served locally, without a CDN. Each operation documents its input, response,
role restrictions, examples and errors.

## Endpoints

Ticket paths require `Authorization: Token <key>`. Base: `/api/`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET, POST | `tickets/` | List visible tickets (`?status=OPEN`) / create for self |
| GET | `tickets/{id}/` | Detail |
| GET, POST | `tickets/{id}/messages/` | Messages / reply with optional `new_status` |
| GET | `tickets/{id}/history/` | Chronological status history |
| POST | `tickets/{id}/assign/` | Active Support `assignee_id`, or null to unassign |
| POST | `tickets/{id}/status/` | Normal status transition |
| POST | `tickets/{id}/close/` | Close a resolved ticket |
| POST | `tickets/{id}/reopen/` | Reopen to OPEN |

All three lists return `{next, previous, results}` with fixed 20-item cursor pages.
Follow returned links unchanged. Tickets are newest first; messages/history are
chronological. Detail and POST responses are single objects. There is no generic
PUT/PATCH/DELETE, token-login or user-management API.

Normal workflow: `OPEN → IN_PROGRESS | RESOLVED`, `IN_PROGRESS → RESOLVED`,
`RESOLVED → CLOSED`. Only Support/Admin can Reopen (`RESOLVED | CLOSED → OPEN`).
Closed tickets reject messages until reopened. Owners can read logically deleted
tickets/messages; Support/Admin can read all, including `is_deleted`/`deleted_at`.
Mutations use atomic transactions and ticket row locks; reply/status/history commit
together. Server-controlled creation fields are ignored if submitted. See
[DECISIONS.md](docs/DECISIONS.md) for defaults, assumptions and protection limits.

Example: create as Employee, then reply/resolve as Support:

```bash
EMPLOYEE_TOKEN='<employee-token>'
SUPPORT_TOKEN='<support-token>'
curl -X POST http://127.0.0.1:8000/api/tickets/ \
  -H "Authorization: Token $EMPLOYEE_TOKEN" -H 'Content-Type: application/json' \
  -d '{"title":"Account access","description":"Cannot sign in","priority":"HIGH"}'
TICKET_ID=1 # Replace with the returned ID.
curl -X POST "http://127.0.0.1:8000/api/tickets/$TICKET_ID/messages/" \
  -H "Authorization: Token $SUPPORT_TOKEN" -H 'Content-Type: application/json' \
  -d '{"content":"Access restored","new_status":"RESOLVED"}'
```

To check roles, an Employee requesting another owner's ticket gets `404`; using
status/assignment/Reopen on their own ticket gets `403`; a missing token gets `401`.
Use the corresponding role's token in Swagger to try these cases.

## Verify

With PostgreSQL running and the environment exported:

```bash
python manage.py check --database default
python manage.py makemigrations --check --dry-run
python manage.py test tickets
python manage.py spectacular --file /tmp/support-ticket-openapi.yaml --validate --fail-on-warn
```

The database role needs permission to create `test_<POSTGRES_DB>`; the local
Compose role has it. Tests cover access, workflow, history, rollback, preservation,
cursor pagination and real PostgreSQL concurrency. Schema tests check all ten
operations, token security, request fields and paginated/single-object responses.
Historical evidence: [required delivery](docs/DELIVERY_VERIFICATION.md) and
[write/concurrency audit](docs/WRITE_CONCURRENCY_AUDIT.md).
Current Swagger/schema and browser checks: [verification](docs/SWAGGER_VERIFICATION.md).

Search and advanced filters are not implemented. Frontend, deployment,
notifications, attachments, SLA and advanced reporting remain outside scope.
