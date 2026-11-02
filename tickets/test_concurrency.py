from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Queue
from time import monotonic, sleep

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase
from rest_framework.exceptions import ValidationError

from .models import Ticket
from .services import assign_ticket, change_ticket_status, close_ticket, create_ticket, post_message, reopen_ticket


class TicketConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, 'postgresql')
        User = get_user_model()
        self.employee = User.objects.create_user(username='employee')
        self.support = User.objects.create_user(username='support')
        self.support.groups.add(Group.objects.get_or_create(name='Support')[0])
        self.ticket = create_ticket(created_by=self.employee, title='Race', description='Competing writes.')

    def snapshot(self):
        return (
            Ticket.objects.filter(pk=self.ticket.pk).values().get(),
            list(self.ticket.messages.order_by('pk').values()),
            list(self.ticket.status_history.values()),
        )

    def run_waiting_write(self, *, waiting_write, first_write, actor=None):
        worker_started = Queue()
        actor_id = (actor or self.support).pk

        def competing_writer():
            close_old_connections()
            worker_connection = connections['default']
            try:
                user = get_user_model().objects.get(pk=actor_id)
                with worker_connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute('SELECT pg_backend_pid(), current_database()')
                    worker_started.put(cursor.fetchone())
                try:
                    result = waiting_write(user)
                except ValidationError as exc:
                    return 'rejected', exc.detail
                return 'updated', result.pk
            finally:
                worker_connection.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Ticket.objects.select_for_update().get(pk=self.ticket.pk)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid(), current_database()')
                    main_pid, main_database = cursor.fetchone()
                future = pool.submit(competing_writer)
                try:
                    worker_pid, worker_database = worker_started.get(timeout=5)
                except Empty:
                    if future.done():
                        future.result()  # Surface connection/setup errors instead of hiding them.
                    raise
                self.assertNotEqual(worker_pid, main_pid)
                self.assertEqual(worker_database, main_database)
                deadline = monotonic() + 3
                blocked = False
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_blocking_pids(%s)', [worker_pid])
                        blocked = main_pid in cursor.fetchone()[0]
                    if blocked or future.done():
                        break
                    sleep(0.01)
                if future.done():
                    self.fail(f'Writer finished before acquiring the ticket lock: {future.result()}')
                self.assertTrue(blocked, 'The independent PostgreSQL connection must wait on the ticket lock.')
                with connection.cursor() as cursor:
                    cursor.execute('SELECT query FROM pg_stat_activity WHERE pid = %s', [worker_pid])
                    waiting_query = cursor.fetchone()[0].upper()
                # Waiting on UPDATE alone would not prove that validation occurs after the lock.
                self.assertTrue(waiting_query.lstrip().startswith('SELECT'), waiting_query)
                self.assertIn('FOR UPDATE', waiting_query)
                first_write()
                committed_state = self.snapshot()
            outcome = future.result(timeout=15)
        return committed_state, outcome

    def assert_rejected_without_changes(self, expected_state, outcome, field):
        self.assertEqual(outcome[0], 'rejected')
        self.assertIn(field, outcome[1])
        self.assertEqual(self.snapshot(), expected_state)

    def test_competing_status_transitions_create_only_one_change_event(self):
        state, outcome = self.run_waiting_write(
            waiting_write=lambda actor: change_ticket_status(
                ticket_id=self.ticket.pk, actor=actor, new_status=Ticket.Status.IN_PROGRESS,
            ),
            first_write=lambda: change_ticket_status(
                ticket_id=self.ticket.pk, actor=self.support, new_status=Ticket.Status.IN_PROGRESS,
            ),
        )
        self.assert_rejected_without_changes(state, outcome, 'status')
        self.assertEqual(state[0]['status'], Ticket.Status.IN_PROGRESS)
        self.assertEqual(
            [(event['actor_id'], event['from_status'], event['to_status']) for event in state[2]],
            [(self.employee.pk, None, Ticket.Status.OPEN), (self.support.pk, Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS)],
        )

    def test_reply_waiting_for_close_cannot_add_a_message_or_duplicate_history(self):
        change_ticket_status(ticket_id=self.ticket.pk, actor=self.support, new_status=Ticket.Status.RESOLVED)
        state, outcome = self.run_waiting_write(
            waiting_write=lambda actor: post_message(
                ticket_id=self.ticket.pk, actor=actor, content='Reply and close', new_status=Ticket.Status.CLOSED,
            ),
            first_write=lambda: close_ticket(ticket_id=self.ticket.pk, actor=self.employee),
        )
        self.assert_rejected_without_changes(state, outcome, 'content')
        self.assertEqual(state[0]['status'], Ticket.Status.CLOSED)
        self.assertEqual(state[1], [])
        self.assertEqual(len(state[2]), 3)
        self.assertEqual(state[2][-1]['actor_id'], self.employee.pk)

    def test_close_waiting_for_reopen_rechecks_resolved_state(self):
        change_ticket_status(ticket_id=self.ticket.pk, actor=self.support, new_status=Ticket.Status.RESOLVED)
        state, outcome = self.run_waiting_write(
            actor=self.employee,
            waiting_write=lambda actor: close_ticket(ticket_id=self.ticket.pk, actor=actor),
            first_write=lambda: reopen_ticket(ticket_id=self.ticket.pk, actor=self.support),
        )
        self.assert_rejected_without_changes(state, outcome, 'status')
        self.assertEqual(state[0]['status'], Ticket.Status.OPEN)
        self.assertEqual(len(state[2]), 3)
        self.assertEqual(state[2][-1]['from_status'], Ticket.Status.RESOLVED)

    def test_competing_reopens_do_not_create_duplicate_events(self):
        change_ticket_status(ticket_id=self.ticket.pk, actor=self.support, new_status=Ticket.Status.RESOLVED)
        close_ticket(ticket_id=self.ticket.pk, actor=self.employee)
        state, outcome = self.run_waiting_write(
            waiting_write=lambda actor: reopen_ticket(ticket_id=self.ticket.pk, actor=actor),
            first_write=lambda: reopen_ticket(ticket_id=self.ticket.pk, actor=self.support),
        )
        self.assert_rejected_without_changes(state, outcome, 'status')
        self.assertEqual(state[0]['status'], Ticket.Status.OPEN)
        self.assertEqual(len(state[2]), 4)
        self.assertEqual(state[2][-1]['from_status'], Ticket.Status.CLOSED)

    def test_assignment_waiting_for_reply_preserves_committed_status_message_and_history(self):
        state, outcome = self.run_waiting_write(
            waiting_write=lambda actor: assign_ticket(
                ticket_id=self.ticket.pk, actor=actor, assignee_id=self.support.pk,
            ),
            first_write=lambda: post_message(
                ticket_id=self.ticket.pk, actor=self.support, content='Resolved reply', new_status=Ticket.Status.RESOLVED,
            ),
        )
        self.assertEqual(outcome, ('updated', self.ticket.pk))
        ticket, messages, history = self.snapshot()
        self.assertEqual(ticket['assigned_to_id'], self.support.pk)
        self.assertGreater(ticket['updated_at'], state[0]['updated_at'])
        self.assertEqual(
            {field: value for field, value in ticket.items() if field not in ('assigned_to_id', 'updated_at')},
            {field: value for field, value in state[0].items() if field not in ('assigned_to_id', 'updated_at')},
        )
        self.assertEqual(ticket['status'], Ticket.Status.RESOLVED)
        self.assertEqual(messages, state[1])
        self.assertEqual(len(messages), 1)
        self.assertEqual((messages[0]['author_id'], messages[0]['content']), (self.support.pk, 'Resolved reply'))
        self.assertEqual(history, state[2])
        self.assertEqual(len(history), 2)
        self.assertEqual((history[-1]['from_status'], history[-1]['to_status']), (Ticket.Status.OPEN, Ticket.Status.RESOLVED))
