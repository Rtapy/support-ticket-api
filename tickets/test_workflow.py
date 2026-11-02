from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import Ticket
from .services import change_ticket_status, create_ticket


class TicketWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.employee = User.objects.create_user(username='employee', is_staff=True)
        cls.other = User.objects.create_user(username='other')
        cls.support = User.objects.create_user(username='support')
        cls.support.groups.add(Group.objects.get(name='Support'))
        cls.administrator = User.objects.create_user(username='administrator', is_superuser=True)
        cls.tokens = {
            user.pk: Token.objects.create(user=user).key
            for user in (cls.employee, cls.other, cls.support, cls.administrator)
        }

    def client_for(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.tokens[user.pk]}')
        return client

    def make_ticket(self, status=Ticket.Status.OPEN):
        ticket = create_ticket(created_by=self.employee, title='Help', description='Account access.')
        if status != Ticket.Status.OPEN:
            intermediate = Ticket.Status.IN_PROGRESS if status == Ticket.Status.IN_PROGRESS else Ticket.Status.RESOLVED
            ticket = change_ticket_status(ticket_id=ticket.pk, actor=self.support, new_status=intermediate)
        if status == Ticket.Status.CLOSED:
            ticket = change_ticket_status(ticket_id=ticket.pk, actor=self.support, new_status=Ticket.Status.CLOSED)
        return ticket

    def snapshot(self, ticket):
        return (
            Ticket.objects.filter(pk=ticket.pk).values().get(),
            list(ticket.status_history.values()),
        )

    def test_valid_workflow_close_and_both_reopen_sources_record_exact_history(self):
        ticket = self.make_ticket()
        steps = (
            (self.support, 'status', {'status': 'IN_PROGRESS'}, Ticket.Status.IN_PROGRESS),
            (self.administrator, 'status', {'status': 'RESOLVED'}, Ticket.Status.RESOLVED),
            (self.employee, 'close', {}, Ticket.Status.CLOSED),
            (self.support, 'reopen', {}, Ticket.Status.OPEN),
            (self.administrator, 'status', {'status': 'RESOLVED'}, Ticket.Status.RESOLVED),
            (self.administrator, 'reopen', {}, Ticket.Status.OPEN),
        )
        previous = ticket.status
        for index, (actor, action, payload, expected) in enumerate(steps, start=1):
            before_updated_at = ticket.updated_at
            response = self.client_for(actor).post(
                reverse(f'ticket-{action}', args=[ticket.pk]), payload, format='json',
            )
            with self.subTest(action=action, expected=expected):
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['status'], expected)
                ticket.refresh_from_db()
                self.assertEqual(ticket.status, expected)
                self.assertGreater(ticket.updated_at, before_updated_at)
                self.assertEqual(ticket.status_history.count(), index + 1)
                event = ticket.status_history.order_by('-changed_at', '-pk').first()
                self.assertEqual((event.actor_id, event.from_status, event.to_status), (actor.pk, previous, expected))
                self.assertTrue(timezone.is_aware(event.changed_at))
            previous = expected

    def test_invalid_and_unchanged_transitions_have_no_side_effects(self):
        cases = (
            (Ticket.Status.OPEN, 'status', {'status': 'OPEN'}),
            (Ticket.Status.CLOSED, 'status', {'status': 'IN_PROGRESS'}),
            (Ticket.Status.IN_PROGRESS, 'status', {'status': 'OPEN'}),
            (Ticket.Status.RESOLVED, 'status', {'status': 'IN_PROGRESS'}),
            (Ticket.Status.OPEN, 'close', {}),
            (Ticket.Status.OPEN, 'reopen', {}),
        )
        client = self.client_for(self.support)
        for initial, action, payload in cases:
            ticket = self.make_ticket(initial)
            before = self.snapshot(ticket)
            with self.subTest(initial=initial, action=action, payload=payload):
                response = client.post(reverse(f'ticket-{action}', args=[ticket.pk]), payload, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.snapshot(ticket), before)

    def test_action_permissions_and_ownership_precede_transition_validation(self):
        ticket = self.make_ticket(Ticket.Status.RESOLVED)
        before = self.snapshot(ticket)
        for action, payload in (('status', {'status': 'CLOSED'}), ('close', {}), ('reopen', {})):
            url = reverse(f'ticket-{action}', args=[ticket.pk])
            with self.subTest(action=action):
                self.assertEqual(APIClient().post(url, payload, format='json').status_code, 401)
                self.assertEqual(self.client_for(self.other).post(url, payload, format='json').status_code, 404)
                if action != 'close':
                    self.assertEqual(self.client_for(self.employee).post(url, payload, format='json').status_code, 403)
        # A malformed status must not expose a foreign ticket or bypass action permissions.
        url = reverse('ticket-status', args=[ticket.pk])
        self.assertEqual(self.client_for(self.other).post(url, {}, format='json').status_code, 404)
        self.assertEqual(self.client_for(self.employee).post(url, {}, format='json').status_code, 403)
        self.assertEqual(self.snapshot(ticket), before)

    def test_invalid_payloads_and_missing_tickets_are_rejected(self):
        ticket = self.make_ticket()
        before = self.snapshot(ticket)
        client = self.client_for(self.support)
        for payload in ({}, {'status': 'UNKNOWN'}, ['RESOLVED'], {'status': ['RESOLVED']}):
            response = client.post(reverse('ticket-status', args=[ticket.pk]), payload, format='json')
            self.assertEqual(response.status_code, 400)
        for ticket_id in (999999, 'invalid-id'):
            response = client.post(reverse('ticket-status', args=[ticket_id]), {'status': 'RESOLVED'}, format='json')
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.snapshot(ticket), before)

    def test_status_and_timestamp_roll_back_when_history_write_fails(self):
        ticket = self.make_ticket()
        before = self.snapshot(ticket)

        def fail_history(**kwargs):
            stored = Ticket.objects.get(pk=ticket.pk)
            self.assertEqual(stored.status, Ticket.Status.IN_PROGRESS)
            self.assertGreater(stored.updated_at, ticket.updated_at)
            raise RuntimeError('Simulated history failure')

        with patch('tickets.services.TicketStatusHistory.objects.create', side_effect=fail_history):
            with self.assertRaisesMessage(RuntimeError, 'Simulated history failure'):
                self.client_for(self.support).post(
                    reverse('ticket-status', args=[ticket.pk]), {'status': 'IN_PROGRESS'}, format='json',
                )
        self.assertEqual(self.snapshot(ticket), before)
