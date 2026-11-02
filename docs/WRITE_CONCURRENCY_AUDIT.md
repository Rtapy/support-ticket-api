# Task 8: ticket write and concurrency audit

Reviewed application baseline: `735bc8c` (merged task 7). No production-code or
public-contract changes were required. The previous status-race test was moved
from `test_workflow.py` into `test_concurrency.py` and extended with four distinct
cross-operation scenarios; it was not duplicated.

## Supported write paths

Source: [services.py](../tickets/services.py),
[views.py](../tickets/views.py) and [serializers.py](../tickets/serializers.py).

| Operation | Transaction / lock | Checks before writing | Writes committed together |
| --- | --- | --- | --- |
| Create ticket | `create_ticket`: `atomic`; new row | API validates title, description, priority; creator comes from authentication | Ticket and initial `NULL → OPEN` history |
| Normal status | `change_ticket_status`: `atomic` + `_get_locked_ticket` | Locked ownership/role, then normal transition; no-op rejected | Status, `updated_at`, one history event |
| Close | `close_ticket`: `atomic` + `_get_locked_ticket` | Locked owner or Support/Admin; current state must be `RESOLVED` | Status, `updated_at`, one history event |
| Reopen | `reopen_ticket`: `atomic` + `_get_locked_ticket` | Support/Admin after locking; current state must be `RESOLVED` or `CLOSED` | Status, `updated_at`, one history event |
| Assign/unassign | `assign_ticket`: `atomic` + `_get_locked_ticket` | Support/Admin after locking; required nullable ID; target must be active Support | Assignee and `updated_at`; status/history unchanged; same assignee is a no-op |
| Post message | `post_message`: `atomic` + `_get_locked_ticket` | Locked ownership/role; nonclosed state; nonblank content; optional status restricted to Support/Admin and validated against current state | Message, ticket timestamp and optional status/history |

`_get_locked_ticket` evaluates `Ticket.objects.select_for_update()` before checking
ownership and action privileges. `_record_status_change` is called only from the
atomic services after their ticket lock and transition checks. Optional reply
transitions use the same validator as the normal status action. Existing ticket
writes use `update_fields` to avoid overwriting unrelated fields.

The API exposes no generic ticket edit/delete or message edit/delete route.
The combined message GET/POST action handles HEAD as a read. All three ticket
Admin registrations disable add/change/delete/actions. User/group administration
continues to use Django's built-in permissions as previously approved.
Ticket/message deletion guards, protected references and append-only history
remain as implemented in [models.py](../tickets/models.py).

## Executed PostgreSQL evidence

Command, with the project environment exported and PostgreSQL running:

```bash
python manage.py test tickets.test_concurrency --verbosity 2 --noinput
```

Result: **5 passed, 0 failed, 0 skipped** on a newly created PostgreSQL test
database. Django system check passed; `makemigrations --check --dry-run` reported
no changes. The local PostgreSQL connection reported `read committed` isolation.

Every scenario uses `TransactionTestCase`, two different PostgreSQL backend PIDs
in the same database, and a ticket lock held by the first connection. Before the
first operation commits, the test observes the second connection blocked by the
first via `pg_blocking_pids()`. It also checks `pg_stat_activity.query` is a
`SELECT ... FOR UPDATE`, so waiting on a later SQL UPDATE cannot substitute for
locking before validation. The worker has a bounded lock timeout and closes its
connection on completion. Polling detects the actual blocked state; sleep duration
does not determine which operation wins.

Source: [test_concurrency.py](../tickets/test_concurrency.py).

| Case / test method | First operation | Waiting operation | Verified outcome |
| --- | --- | --- | --- |
| R1 `test_competing_status_transitions_create_only_one_change_event` | `OPEN → IN_PROGRESS` | Same transition | Waiter rejects the now-unchanged transition; exactly one change event |
| R2 `test_reply_waiting_for_close_cannot_add_a_message_or_duplicate_history` | Owner closes a resolved ticket | Support replies with `new_status=CLOSED` | Waiter sees CLOSED and rejects; no message, duplicate history or timestamp change |
| R3 `test_close_waiting_for_reopen_rechecks_resolved_state` | Support reopens a resolved ticket | Owner closes it | Waiter sees OPEN and rejects; reopened state and history preserved |
| R4 `test_competing_reopens_do_not_create_duplicate_events` | Support reopens a closed ticket | Another Reopen | Waiter sees OPEN and rejects; one Reopen event |
| R5 `test_assignment_waiting_for_reply_preserves_committed_status_message_and_history` | Support replies and resolves atomically | Assign ticket to active Support | Assignment succeeds after the reply; message, status and exact history retained; only assignee/timestamp change |

Rejected operations are compared with a complete snapshot of the ticket,
messages and status history taken after the first write. This includes timestamp
preservation, rather than checking only event counts.

## Scope of the evidence

These are real service-level PostgreSQL races, with no mocked database locks or
simulated competing writes. They do not exercise an HTTP server, load-test every
possible interleaving, or repeat the existing API access/rollback/Admin suites.
Those checks remain in their focused test modules. This task ran only the five
concurrency scenarios; it does not claim a fresh full-suite pass.

The supported mutation boundary is the application service/API layer. Arbitrary
direct SQL, private ORM methods and direct ticket writes outside these services
are not made transactional or workflow-safe by this audit. No database triggers
or additional infrastructure were added.
