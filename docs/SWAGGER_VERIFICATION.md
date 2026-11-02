# Swagger/OpenAPI verification

Application baseline: `7eef201` (merged cursor pagination). Documentation is
generated from view annotations in `tickets/schema.py`; business services and
their request/response behavior were not changed.

## Automated checks

```bash
python manage.py check --database default
python manage.py makemigrations --check --dry-run
python manage.py spectacular --file /tmp/support-ticket-openapi.yaml --validate --fail-on-warn
python manage.py test tickets
python -m pip check
```

Results: no system-check issues, no migration changes, no schema errors/warnings,
**42 tests passed, 0 failed, 0 skipped**, and no broken dependencies.

The schema tests assert all ten operations, unique operation IDs, Token header
security, actual request fields/defaults/nullability declarations, cursor envelopes
for GET lists, and single-object POST responses. Schema introspection and public
documentation tests use `SimpleTestCase`, which prohibits database queries.
Database-backed API/model/race tests run on PostgreSQL.

## Real browser check

Headless Chrome used a temporary Django server and a disposable PostgreSQL test
database. Playwright was installed only as a temporary verification tool outside
the project dependencies.

- Public Swagger loaded with all ten operations.
- Local sidecar JavaScript/CSS loaded: zero external UI asset requests and zero
  JavaScript errors.
- Executing GET tickets before authorization returned `401`.
- The Authorize dialog accepted `Token <key>`; executing again returned `200`.
- The actual browser request contained the expected Authorization header, and the
  response was `{next, previous, results}` containing the fixture owner's ticket.
- Browser, temporary server and test database were cleaned up afterwards.

Only GET tickets was executed from the browser. The other operations were rendered
and schema-checked; their behavior is covered by the PostgreSQL API test suite.
No production deployment or frontend integration was verified.
