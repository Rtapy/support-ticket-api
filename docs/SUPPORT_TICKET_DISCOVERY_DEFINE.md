# Support Ticket System — Backend Define

**Scope:** Backend REST API only. 

## Requirements

- **Employee:** create a ticket (title, description, priority); list/read only own tickets; add messages; close own resolved ticket.
- **Support:** read all tickets, filter by status, reply, assign to self/others, change status.
- **Admin:** manage users/roles through Django Admin; read, assign and change status on all tickets.
- **Rules:** enforce ownership/role authorization; valid `OPEN`, `IN_PROGRESS`, `RESOLVED`, `CLOSED` workflow and Reopen; no physical ticket/message deletion; track creator/timestamps/complete status history; reply plus status change must be atomic.

## Data contract

| Model | Fields |
| --- | --- |
| `Ticket` | `id`, `title`, `description`, `priority`, `status`, `created_by`, nullable `assigned_to`, `created_at`, `updated_at`, `is_deleted`, nullable `deleted_at` |
| `TicketMessage` | `id`, `ticket`, `author`, `content`, `created_at`, `updated_at`, `is_deleted`, nullable `deleted_at` |
| `TicketStatusHistory` | `id`, `ticket`, `actor`, nullable `from_status`, `to_status`, `changed_at` |

Users and roles use Django's built-in User, Groups and Admin. Server-controlled fields (`created_by`, `author`, initial `status`, `assigned_to` and timestamps) cannot be set by ticket creators.

User/group management follows Django Admin's built-in permissions; no additional custom Admin role or superuser-only management restriction is introduced. The ticket API identifies Admin by `is_superuser`, independently of Admin-site permissions.

## Authorization and workflow

| Operation | Employee | Support / Admin |
| --- | --- | --- |
| Create ticket | For self | For self |
| List/detail/messages/history | Own tickets only | All tickets |
| Post message | Own ticket, unless closed | All nonclosed tickets |
| Assign or normal status change | No | Yes |
| Close a `RESOLVED` ticket | Own only | Any |
| Reopen `RESOLVED`/`CLOSED` ticket | No | Yes |

Transitions: `OPEN → IN_PROGRESS | RESOLVED`; `IN_PROGRESS → RESOLVED`; `RESOLVED → CLOSED`. Dedicated Reopen: `RESOLVED | CLOSED → OPEN`. All other transitions are rejected. Every new ticket has initial history `NULL → OPEN`; every real transition creates one history event. A denied or unchanged transition creates none.

## REST API

All paths require authentication; base prefix: `/api/`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`, `POST` | `tickets/` | Role-scoped list (`?status=OPEN`) / create |
| `GET` | `tickets/{id}/` | Ticket detail |
| `GET`, `POST` | `tickets/{id}/messages/` | List / add messages; Support/Admin may include `new_status` |
| `POST` | `tickets/{id}/assign/` | Set/unset `assignee_id` |
| `POST` | `tickets/{id}/status/` | Normal status transition |
| `POST` | `tickets/{id}/close/` | Close resolved ticket |
| `POST` | `tickets/{id}/reopen/` | Reopen to `OPEN` |
| `GET` | `tickets/{id}/history/` | Chronological status history |

Do not expose generic `PUT/PATCH/DELETE` or custom user-management REST routes. An employee requesting another employee's ticket by ID receives `404`; an authenticated user attempting a forbidden operation on a visible ticket receives `403`.

Logically deleted tickets remain visible under the same ownership/role rules, including their history. Ticket responses include `is_deleted` and `deleted_at`; clients decide how to display them.

## Consistency and concurrency

- **PostgreSQL is mandatory** for application development and automated tests; configure via environment variables. No SQLite fallback.
- Ticket creation and its initial history event must be atomic. Each operation modifying an existing ticket must perform `transaction.atomic()` + `Ticket.objects.select_for_update()` on that ticket, then validate the locked, current row. This includes posting messages, assignment and every status/close/reopen action.
- Save reply, optional status change, status history and ticket timestamps in the **same transaction**. Exceptions roll back the entire operation; rejected transitions cause no side effects.
- Use owner-scoped querysets and explicit action permissions. Preserve ticket/message records with logical deletion; block hard deletion through API/Admin. Status history is append-only.

## Minimum automated tests

Write only focused tests that prove these essential behaviors (avoid exhaustive/duplicated suites):

1. **Access:** employee ownership isolation; Support/Admin visibility; forbidden staff operations for employees.
2. **Workflow:** one valid transition, an invalid transition including direct `CLOSED → IN_PROGRESS`, and Reopen.
3. **History:** initial event plus actor, previous/new status and timestamp for actual changes; no event on rejection.
4. **Atomicity:** response + status/history commit together; simulated failure rolls everything back.
5. **Race condition:** two competing writes to one ticket do not bypass workflow or produce duplicate/inconsistent history; use PostgreSQL and `TransactionTestCase` with separate DB connections.
6. **No hard deletion:** ticket/message preservation through supported application and admin paths.

## Deliverables / Done

- Source code pushed to GitHub/GitLab with incremental commits (feature branches from `develop`; user handles PR/review/merge).
- Working PostgreSQL configuration, migrations and role-specific API behavior.
- Essential automated tests pass on PostgreSQL.
- Root `README.md`: setup, environment, migration/run/test commands, endpoint list, authentication and role-testing examples.
- `docs/DECISIONS.md`: important architectural/product assumptions and limitations.

**Out of scope:** frontend, deployments, notifications, attachments, SLA, advanced reporting, pagination, advanced filters/search and Swagger/OpenAPI unless separately approved.
