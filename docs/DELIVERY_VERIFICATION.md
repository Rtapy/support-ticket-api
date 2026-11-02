# Required backend delivery verification

Date: 2026-11-02. Application baseline: `5132aab` (merged task 8).
Task 9 changes documentation only. The required implementation is ready for user
review; this report does not substitute for PR approval or merge.

This is the historical verification before optional cursor pagination. The current
list response contract is documented in [README.md](../README.md); the 37-test and
33-request results below apply to the baseline above, not to the pagination change.

## Environment and clean setup

Verification used an archive of the tracked baseline in a temporary directory,
a newly created virtual environment and a separate PostgreSQL container with a
fresh data volume. It did not reuse the development database or virtual environment.
Available alternate database/HTTP ports were selected to avoid other local projects.

| Component | Verified version |
| --- | --- |
| Python | 3.12.3 |
| Django | 5.2.18 |
| Django REST framework | 3.18.3 |
| Psycopg | 3.3.6 |
| PostgreSQL | 16.15; `read committed` isolation |

The README setup procedure was exercised from the source-copy stage:

- Created the virtual environment and installed `requirements.txt` successfully.
- Generated a Django secret key, stored it shell-quoted in a private `.env`, and
  exported the environment. Credentials were generated only for this verification.
- Validated Compose configuration and waited for the separate database healthcheck.
- Ran `python manage.py check --database default`: no issues.
- Ran `python manage.py migrate --noinput`: all initial migrations applied,
  including both ticket migrations and creation of the Support group.
- Ran `python manage.py makemigrations --check --dry-run`: no changes detected.
- Ran `python -m pip check`: no broken requirements.
- Started `runserver` locally with `--noreload` and performed the HTTP checks below.

Source was copied with `git archive`, not cloned from GitHub during this check.
The clone command and GitHub access permissions were not part of runtime verification.

## Full automated result

```bash
python manage.py test tickets --verbosity 2 --noinput
```

**37 passed, 0 failed, 0 skipped.** The runner created and migrated a fresh
PostgreSQL test database and removed it afterwards.

| Module | Passing tests | Main evidence |
| --- | ---: | --- |
| [tests.py](../tickets/tests.py) | 4 | Defaults/audit fields, deletion preservation, protected references, append-only history |
| [test_roles_auth.py](../tickets/test_roles_auth.py) | 2 | Role identification and command-provisioned TokenAuthentication |
| [test_admin.py](../tickets/test_admin.py) | 2 | Read-only Admin, including forged mutation requests and role visibility |
| [test_api.py](../tickets/test_api.py) | 9 | Creation/read/history, owner isolation, logical-delete visibility, status filter, initial-history rollback |
| [test_workflow.py](../tickets/test_workflow.py) | 5 | Status/Close/Reopen, access, rejection side effects, history-write rollback |
| [test_assignment.py](../tickets/test_assignment.py) | 4 | Assign/reassign/unassign, target eligibility, access and rollback |
| [test_messages.py](../tickets/test_messages.py) | 6 | Message access/author control, closed-state rules, atomic reply/status/history, read-only HEAD |
| [test_concurrency.py](../tickets/test_concurrency.py) | 5 | Real competing writes with independent PostgreSQL connections |

API/Admin tests use Django/DRF test clients backed by PostgreSQL. Failure-injection
tests simulate a failing write and verify actual database rollback; those failures
were not injected into the live HTTP server. Concurrency tests use
`TransactionTestCase` and real connections, observing blocked
`SELECT ... FOR UPDATE` statements. See the
[task-8 audit](WRITE_CONCURRENCY_AUDIT.md) for the five scenarios and their assertions.

## Requirement acceptance mapping

| Criterion | Named automated evidence |
| --- | --- |
| AC-01: authenticated access and role distinction | `RoleTests.test_roles_require_active_users_and_staff_is_not_a_support_role`; `TokenAuthenticationTests.test_global_authentication_with_a_command_provisioned_token` |
| AC-02: owner isolation and Support/Admin visibility | `TicketReadCreateTests.test_employees_see_only_own_records_including_logically_deleted_tickets`; `test_support_and_admin_see_every_ticket_and_history_without_staff_status`; message access test |
| AC-03: valid workflow, rejection and dedicated Reopen | `TicketWorkflowTests.test_valid_workflow_close_and_both_reopen_sources_record_exact_history`; `test_invalid_and_unchanged_transitions_have_no_side_effects` |
| AC-04: initial history and accurate transition audit | `TicketReadCreateTests.test_creation_controls_server_fields_and_records_initial_history_for_each_role`; valid-workflow test; chronological-history test |
| AC-05: atomic creation and reply/status/history | `TicketReadCreateTests.test_creation_rolls_back_when_initial_history_fails`; `TicketMessageTests.test_reply_and_status_change_commit_with_exact_history`; `test_history_failure_rolls_back_message_status_and_timestamps` |
| AC-06: concurrency without duplicate/inconsistent history | All five `TicketConcurrencyTests` cases, R1–R5 in the task-8 audit |
| AC-07: record preservation and append-only history | `TicketModelTests.test_instance_and_queryset_deletion_preserve_records`; `test_deleting_referenced_users_does_not_cascade_or_clear_audit_data`; `test_status_history_cannot_be_changed_or_overwritten`; read-only Admin test |
| AC-08: assignment and message permissions/state | `TicketAssignmentTests`; `TicketMessageTests.test_forbidden_status_and_invalid_payloads_leave_no_message_or_timestamp_changes`; `test_closed_ticket_is_readable_but_cannot_receive_messages_until_reopened` |

## Real local HTTP verification

**33 requests passed their expected status/body assertions:** 32 API requests and
one GET of the Django Admin login page. Requests used Python's standard HTTP
client over a local TCP connection to the running Django server, not APIClient,
forced authentication or a mocked transport.

Four disposable user fixtures represented Employee, another Employee, Support and
Admin. The superuser was created through Django's `createsuperuser` command;
regular fixtures and Support membership were prepared with the ORM. Tokens were
provisioned with `drf_create_token`; no credentials appear in this report.

| HTTP coverage | Verified result |
| --- | --- |
| Ticket creation with spoofed server fields | `201`; authenticated creator, OPEN, MEDIUM, unassigned and not deleted |
| Employee list/detail/filter/history | Own ticket only; initial `NULL → OPEN` event with correct actor |
| Another employee's detail/history/messages | Three `404` responses |
| Support/Admin lists | Both employees' tickets visible |
| Assign/unassign and target eligibility | Support assignment and Admin unassignment succeed; Admin without Support membership rejected as target |
| Employee staff operations | Assignment, status change and reply-with-status rejected with `403` |
| Messages and workflow | Plain reply, reply with IN_PROGRESS, resolve, owner Close, Support Reopen and reply with RESOLVED succeed |
| Closed message / invalid filter | Expected `400` responses |
| Final message/history data | Exactly three committed messages; six correctly ordered events with expected actors and statuses |
| HEAD message request containing a write payload | `200`; final message count/history unchanged |
| Missing authentication / generic DELETE | `401` / `405` |
| Admin login page | GET `200`; this was not a browser or user-management form walkthrough |

Observed response distribution: `200 × 17`, `201 × 5`, `400 × 3`, `401 × 1`,
`403 × 3`, `404 × 3`, `405 × 1`. Expected error responses count as successful checks.

The temporary server was stopped and its PostgreSQL container, network and data
volume removed after verification. The existing development database was untouched.

## Remaining scope and limits

No requirement conflicts or failed/unavailable required checks were found.
README now includes setup/environment commands, every endpoint, token provisioning,
role-testing examples and optional-feature status. Architectural/product decisions
and the service/ORM protection boundary are recorded in [DECISIONS.md](DECISIONS.md).

Optional assignment features remain unimplemented: pagination, title/ID search,
advanced filtering and Swagger/OpenAPI. They require a separately approved task.
Frontend and deployment are outside scope and were not verified. The local HTTP
check is not production/load testing; concurrency evidence covers the named races,
not every possible interleaving. Direct SQL or ticket writes outside the supported
service layer are not given workflow guarantees by this verification.
