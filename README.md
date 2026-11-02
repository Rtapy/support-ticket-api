# Support Ticket API

Django/DRF backend for a support ticket system. The API supports ticket creation,
role-scoped reads, history, messages, assignment and status workflow. User/group
management uses Django Admin; ticket Admin screens are read-only.
Requirements and business decisions live in
[the definition](docs/SUPPORT_TICKET_DISCOVERY_DEFINE.md) and
[DECISIONS.md](docs/DECISIONS.md).

## Local setup

Requires Python 3.12+, Docker with Compose, and an available local port (default
`55439`). PostgreSQL is required for both development and tests; there is no
SQLite fallback. Compose runs only the local database. An existing PostgreSQL
16+ instance can also be used by setting the `POSTGRES_*` variables accordingly.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set a unique `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD`.
Generate a secret key with:

```bash
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

Quote values containing shell metacharacters with single quotes. `.env` is
ignored by Git. Compose reads it automatically; Django reads the process
environment, so export it in each new shell before running Django commands:

```bash
set -a
source .env
set +a
docker compose up -d --wait db
python manage.py check --database default
python manage.py migrate
python manage.py runserver
```

`DJANGO_DEBUG` defaults to `False`; the example enables it for local development.
`DJANGO_ALLOWED_HOSTS` is a comma-separated list. Database name, user, password,
host, port and Django secret key must be provided explicitly.

## Checks and tests

With the environment exported and PostgreSQL healthy:

```bash
python manage.py check --database default
python manage.py makemigrations --check --dry-run
python manage.py test tickets
```

The test runner uses the same PostgreSQL server and creates
`test_<POSTGRES_DB>`. The database role must be allowed to create databases.
The local Compose database role has that permission. Focused tests cover model
preservation, roles, token authentication, read-only ticket Admin, ticket API access
and atomic ticket creation, status workflow, assignment, replies with status changes
and PostgreSQL row-lock concurrency.
To run only the tests for a specific API task:

```bash
python manage.py test tickets.test_api
python manage.py test tickets.test_workflow
python manage.py test tickets.test_assignment
python manage.py test tickets.test_messages
python manage.py test tickets.test_concurrency
```

The five concurrency tests use `TransactionTestCase` and independent PostgreSQL
connections. They observe the waiting `SELECT ... FOR UPDATE` in PostgreSQL before
allowing the first writer to commit; no lock behavior is mocked. See the
[write/concurrency audit](docs/WRITE_CONCURRENCY_AUDIT.md) for scenarios and scope.

## Data preservation

`Ticket`, `TicketMessage` and `TicketStatusHistory` reject instance and queryset
deletion with `ProtectedError`. Ticket/message logical-delete fields are stored
without filtering records out of the default manager; no delete/restore operation
is exposed. User and ticket foreign keys use `PROTECT` to preserve references.

Status history rejects updates through `save()`, queryset `update()`,
`bulk_update()` and conflict-updating `bulk_create()`. New events can be inserted;
an existing primary key cannot be overwritten by `save()`. These are application
ORM protections, not database triggers or restrictions on direct SQL access.

The creation service and ticket POST endpoint atomically save the ticket and its
initial `NULL → OPEN` history event. Creating a `Ticket` directly through the ORM
does not automatically create history. Status, close and reopen services lock the
ticket row inside a transaction and check current ownership, role and workflow
after acquiring the lock. Status, update timestamp and history commit together;
history-write failures roll back all changes.

Assignment also locks the ticket row before checking access and the target user.
It changes only the assignee and update timestamp, without changing status or
adding status history. A failure rolls back the assignment and timestamp.

Posting a message locks the ticket and validates access, closed state and any
requested status transition before saving. Message, optional status/history and
ticket update timestamp share one transaction. A rejected request has no side
effects; a write failure rolls back the whole reply operation.

Logically deleted tickets and their history remain visible under the same access
rules: owners see their own records and Support/Admin see all records. API responses
include `is_deleted` and `deleted_at` so clients can control presentation.

## Authentication

DRF defaults to `TokenAuthentication` and `IsAuthenticated`. API requests
use `Authorization: Token <token>`. Missing/invalid tokens and tokens belonging
to inactive users are rejected. No token-login or custom user-management API is exposed.

After exporting the environment and running migrations, create an administrator:

```bash
python manage.py createsuperuser
```

Log in at `/admin/` to create users and manage group memberships. Migrations create
the `Support` group without granting permissions or creating users. Its creation
is idempotent; reversing the migration preserves the group and memberships.

Roles are determined as follows:

- An active superuser is Admin.
- An active member of the `Support` group is Support.
- Other active users are Employees; `is_staff` alone does not grant Support access.
- Inactive and anonymous users do not receive Support/Admin privileges.

For example, create users named `employee` and `support` in Admin, and add only
`support` to the `Support` group. Generate their tokens using DRF's built-in command:

```bash
python manage.py drf_create_token employee
python manage.py drf_create_token support
```

The command prints each user's token. User/group management follows the built-in
Django Admin permissions, with no additional custom role or superuser-only restriction.
The ticket API still identifies Admin by `is_superuser`, independently of Admin-site
permissions. Authentication tests also use a test-only probe route that is absent
from the application URL configuration.

## Implemented ticket API

All routes require a token. User references are represented as numeric IDs.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/api/tickets/` | Own tickets for Employee; all tickets for Support/Admin |
| POST | `/api/tickets/` | Create for the authenticated user, with initial history |
| GET | `/api/tickets/{id}/` | Detail under the same ownership/role rules |
| GET | `/api/tickets/{id}/history/` | History ordered by `changed_at`, then ID |
| POST | `/api/tickets/{id}/status/` | Support/Admin: normal status transition |
| POST | `/api/tickets/{id}/close/` | Owner or Support/Admin: close a resolved ticket |
| POST | `/api/tickets/{id}/reopen/` | Support/Admin: reopen a resolved/closed ticket |
| POST | `/api/tickets/{id}/assign/` | Support/Admin: assign to active Support or unassign |
| GET, POST | `/api/tickets/{id}/messages/` | List messages / reply, optionally with a Support/Admin status change |

List responses are unpaginated arrays ordered by creation time descending, then ID
descending. `?status=OPEN` (or `IN_PROGRESS`, `RESOLVED`, `CLOSED`) filters within
the visible records. An invalid status returns `400`. An Employee requesting
another user's detail or history receives `404`, including for logically deleted
tickets. Generic `PUT/PATCH/DELETE` are unavailable.

Creation accepts `title` (required, up to 255 characters), `description` (required)
and `priority` (`LOW`, `MEDIUM`, `HIGH`; default `MEDIUM`). Server-controlled fields
are read-only and ignored if submitted. New tickets are `OPEN`, unassigned and not
deleted; creator and initial history actor are the authenticated user. A failed
history write rolls back ticket creation.

Responses contain `id`, `title`, `description`, `priority`, `status`, `created_by`,
`assigned_to`, `created_at`, `updated_at`, `is_deleted` and `deleted_at`. History
events contain `id`, `ticket`, `actor`, `from_status`, `to_status` and `changed_at`.

The status action accepts `{"status":"IN_PROGRESS"}`. Normal transitions are
`OPEN → IN_PROGRESS | RESOLVED`, `IN_PROGRESS → RESOLVED` and
`RESOLVED → CLOSED`. Returning to `OPEN` requires the dedicated Reopen action.
Close and Reopen need no body and always target `CLOSED` and `OPEN` respectively.
Successful mutations return `200` with the full updated ticket. Invalid or
unchanged transitions return `400` without changing timestamps or history.
An Employee can close their own resolved ticket through Close, but cannot use
the normal status or Reopen actions (`403` on visible tickets). Requests targeting
another Employee's ticket return `404`. Every real transition appends exactly one
event with the authenticated actor and previous/new status.

Assignment requires `{"assignee_id":123}` or `{"assignee_id":null}` to unassign.
The target must be an active member of `Support`; being staff or a superuser alone
does not make a user an eligible assignee. Support may assign to self or another
Support member; Admin has the same assignment permissions. Invalid/missing IDs and
ineligible users return `400` without side effects. Employees receive `403` on their
own tickets and `404` on others' tickets. A valid request returns `200` with the full
ticket. Repeating the current assignment is accepted without changing the timestamp.

Message lists are unpaginated arrays ordered by `created_at`, then ID. Any user
allowed to read the ticket can read its messages, including when it is closed.
Responses contain `id`, `ticket`, `author`, `content`, `created_at`, `updated_at`,
`is_deleted` and `deleted_at`; retained messages are not filtered out by deletion flags.

Post `{"content":"Reply text"}` to add a message. Support/Admin may include
`"new_status":"RESOLVED"` using the same normal transition rules as the status
action. Supplying `new_status` as an Employee returns `403`; invalid or unchanged
transitions return `400` without saving a message. `new_status` is optional but
cannot be null. Content is required and cannot be blank. Message author, ticket,
IDs, timestamps and deletion fields are controlled by the server; submitted values
are ignored. Success returns `201` with the created message. Read the ticket or its
history to retrieve the resulting status. Plain replies update the ticket timestamp
without adding a status-history event.

Closed tickets reject messages (`400`) from every role until the dedicated Reopen
action is used; a reply cannot reopen them. Employees can post only on their own
nonclosed tickets, while Support/Admin can post on all nonclosed tickets.

With the server running, replace the token placeholders and use the ID returned
by creation for the detail/history examples:

```bash
API_BASE=http://127.0.0.1:8000/api
EMPLOYEE_TOKEN='<employee-token>'
SUPPORT_TOKEN='<support-token>'
ADMIN_TOKEN='<superuser-token>'

curl -i -X POST "$API_BASE/tickets/" \
  -H "Authorization: Token $EMPLOYEE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Account access","description":"Cannot sign in","priority":"HIGH"}'

TICKET_ID=1 # Replace with the ID returned above.
curl -H "Authorization: Token $EMPLOYEE_TOKEN" "$API_BASE/tickets/"
curl -H "Authorization: Token $EMPLOYEE_TOKEN" "$API_BASE/tickets/?status=OPEN"
curl -H "Authorization: Token $EMPLOYEE_TOKEN" "$API_BASE/tickets/$TICKET_ID/"
curl -H "Authorization: Token $EMPLOYEE_TOKEN" "$API_BASE/tickets/$TICKET_ID/history/"
curl -H "Authorization: Token $SUPPORT_TOKEN" "$API_BASE/tickets/"
curl -H "Authorization: Token $ADMIN_TOKEN" "$API_BASE/tickets/"

curl -X POST "$API_BASE/tickets/$TICKET_ID/status/" \
  -H "Authorization: Token $SUPPORT_TOKEN" \
  -H 'Content-Type: application/json' -d '{"status":"IN_PROGRESS"}'
curl -X POST "$API_BASE/tickets/$TICKET_ID/status/" \
  -H "Authorization: Token $SUPPORT_TOKEN" \
  -H 'Content-Type: application/json' -d '{"status":"RESOLVED"}'
curl -X POST -H "Authorization: Token $EMPLOYEE_TOKEN" "$API_BASE/tickets/$TICKET_ID/close/"
curl -X POST -H "Authorization: Token $SUPPORT_TOKEN" "$API_BASE/tickets/$TICKET_ID/reopen/"

SUPPORT_USER_ID=2 # Replace with an active Support member's ID from Django Admin.
curl -X POST "$API_BASE/tickets/$TICKET_ID/assign/" \
  -H "Authorization: Token $SUPPORT_TOKEN" \
  -H 'Content-Type: application/json' -d "{\"assignee_id\":$SUPPORT_USER_ID}"
curl -X POST "$API_BASE/tickets/$TICKET_ID/assign/" \
  -H "Authorization: Token $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"assignee_id":null}'

curl -H "Authorization: Token $EMPLOYEE_TOKEN" "$API_BASE/tickets/$TICKET_ID/messages/"
curl -X POST "$API_BASE/tickets/$TICKET_ID/messages/" \
  -H "Authorization: Token $EMPLOYEE_TOKEN" \
  -H 'Content-Type: application/json' -d '{"content":"Additional details"}'
curl -X POST "$API_BASE/tickets/$TICKET_ID/messages/" \
  -H "Authorization: Token $SUPPORT_TOKEN" \
  -H 'Content-Type: application/json' -d '{"content":"Access restored","new_status":"RESOLVED"}'
```

## Django Admin

User and group management use the built-in Django Admin. Ticket, message and
status-history screens are read-only, including for superusers: add, change,
delete and bulk actions are disabled. Ticket creation, status, assignment and
message mutations use transactional service/API paths instead of editable Admin forms.

Viewing ticket records in Admin requires both the Support/Admin role and Django's
normal model view/change permissions. Django Admin also requires an active staff
account. Superusers have these permissions; a Support user needs `is_staff` and
explicit model view permissions if Admin access is desired. Group membership alone
does not grant staff status or Django model permissions. Staff Employees cannot
view ticket Admin records, even if given model view permissions.

## Stop the local database

```bash
docker compose stop db
```

The database persists in a Docker volume across container restarts.
