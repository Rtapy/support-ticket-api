# Decisions

1. **Stack:** Django + Django REST Framework + **PostgreSQL** for development and tests; no SQLite fallback. Database credentials come from environment variables.
2. **Architecture:** One Django backend with a `tickets` app; business mutations live in a small service layer. No extra infrastructure or frontend.
3. **Identity and roles:** Built-in Django User/Groups and Admin. `is_superuser` = Admin; `Support` group = Support; other active users = Employee. `is_staff` is not an API support role. Use DRF TokenAuthentication; no custom auth/user-management API.
4. **Models:** `Ticket`, `TicketMessage`, `TicketStatusHistory`; use Django's User model. Ticket/message references and audit records are protected against cascading hard deletion.
5. **Defaults:** New ticket = `OPEN`, unassigned; priority = `LOW | MEDIUM | HIGH` (default `MEDIUM`).
6. **Normal status transitions:** `OPEN → IN_PROGRESS | RESOLVED`, `IN_PROGRESS → RESOLVED`, `RESOLVED → CLOSED`. No direct `CLOSED → IN_PROGRESS` or no-op transitions.
7. **Reopen:** Dedicated action only for Support/Admin; `RESOLVED | CLOSED → OPEN`. Employees may close only their own `RESOLVED` tickets; closed tickets cannot receive messages until reopened.
8. **Assignment:** Support/Admin may assign to an active Support member (including self) or unassign. Support/Admin can access every ticket; employees only their own.
9. **Messages and history:** Authorized ticket readers can see its messages; no private notes. Record an initial `NULL → OPEN` event and exactly one append-only history event per effective status change.
10. **API surface:** Explicit ticket, message, assignment, status, close, reopen and history endpoints; status filtering on ticket list. Support/Admin may submit a reply with an optional status change in one request. No generic edit, delete or user-management endpoints.
11. **Consistency and race conditions:** For *every mutation of an existing ticket*, enter `transaction.atomic()`, acquire the ticket row using PostgreSQL `select_for_update()`, then recheck permissions, current state and transition rules **after locking**. Commit message/status/history/timestamps together; avoid reading a stale status before the lock. Verify concurrent transitions against PostgreSQL.
12. **Deletion:** Ticket and message use logical-delete fields; no public DELETE endpoint. Block physical deletion through supported admin/application paths and preserve history.
13. **Scope:** No optional pagination, advanced search, OpenAPI, notifications, attachments, frontend or deployment work unless explicitly requested.
