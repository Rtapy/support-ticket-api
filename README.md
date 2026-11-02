# Support Ticket API

Django/DRF backend for a support ticket system. The project foundation and ticket
models are implemented; roles, mutation services and API endpoints are subsequent tasks.
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
The local Compose database role has that permission. The focused model tests cover
defaults/audit fields, record preservation, protected user references and append-only history.

## Data preservation

`Ticket`, `TicketMessage` and `TicketStatusHistory` reject instance and queryset
deletion with `ProtectedError`. Ticket/message logical-delete fields are stored
without filtering records out of the default manager; no delete/restore operation
is exposed. User and ticket foreign keys use `PROTECT` to preserve references.

Status history rejects updates through `save()`, queryset `update()`,
`bulk_update()` and conflict-updating `bulk_create()`. New events can be inserted;
an existing primary key cannot be overwritten by `save()`. These are application
ORM protections, not database triggers or restrictions on direct SQL access.

The initial status-history event and workflow rules will be enforced by the
transactional service layer in subsequent tasks. Creating a `Ticket` directly
through the ORM does not automatically create a history event.

## Authentication

DRF defaults to `TokenAuthentication` and `IsAuthenticated`. Future API requests
will use `Authorization: Token <token>`. Django's token app and migrations are
installed; token provisioning, roles and ticket endpoints are subsequent tasks.
The built-in Django Admin is available at `/admin/`; create a local administrator
with `python manage.py createsuperuser` if needed.

## Stop the local database

```bash
docker compose stop db
```

The database persists in a Docker volume across container restarts.
