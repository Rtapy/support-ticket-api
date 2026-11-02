from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import Ticket, TicketStatusHistory


class TicketReadCreateTests(TestCase):
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
        cls.own_ticket = Ticket.objects.create(
            title='My request', description='Help with access.', created_by=cls.employee,
        )
        cls.other_ticket = Ticket.objects.create(
            title='Other request', description='Another account.', created_by=cls.other,
        )
        cls.own_deleted = Ticket.objects.create(
            title='My deleted request', description='Retained record.', created_by=cls.employee,
            status=Ticket.Status.RESOLVED, is_deleted=True, deleted_at=timezone.now(),
        )
        cls.other_deleted = Ticket.objects.create(
            title='Other deleted request', description='Retained record.', created_by=cls.other,
            status=Ticket.Status.RESOLVED, is_deleted=True, deleted_at=timezone.now(),
        )
        for ticket in (cls.own_ticket, cls.other_ticket, cls.own_deleted, cls.other_deleted):
            TicketStatusHistory.objects.create(
                ticket=ticket, actor=ticket.created_by, from_status=None, to_status=Ticket.Status.OPEN,
            )
            if ticket.status == Ticket.Status.RESOLVED:
                TicketStatusHistory.objects.create(
                    ticket=ticket, actor=cls.support,
                    from_status=Ticket.Status.OPEN, to_status=Ticket.Status.RESOLVED,
                )

    def client_for(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.tokens[user.pk]}')
        return client

    def test_real_routes_require_authentication(self):
        client = APIClient()
        requests = (
            ('get', reverse('ticket-list')),
            ('post', reverse('ticket-list')),
            ('get', reverse('ticket-detail', args=[self.own_ticket.pk])),
            ('get', reverse('ticket-history', args=[self.own_ticket.pk])),
        )
        for method, url in requests:
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(client, method)(url).status_code, 401)

    def test_employees_see_only_own_records_including_logically_deleted_tickets(self):
        cases = (
            (self.employee, (self.own_ticket, self.own_deleted), (self.other_ticket, self.other_deleted)),
            (self.other, (self.other_ticket, self.other_deleted), (self.own_ticket, self.own_deleted)),
        )
        for user, visible, hidden in cases:
            client = self.client_for(user)
            with self.subTest(user=user.username):
                response = client.get(reverse('ticket-list'))
                self.assertEqual(response.status_code, 200)
                self.assertEqual({item['id'] for item in response.data}, {ticket.pk for ticket in visible})
                for ticket in visible:
                    detail = client.get(reverse('ticket-detail', args=[ticket.pk]))
                    self.assertEqual(detail.status_code, 200)
                    self.assertEqual(detail.data['is_deleted'], ticket.is_deleted)
                    if ticket.is_deleted:
                        self.assertIsNotNone(detail.data['deleted_at'])
                    history = client.get(reverse('ticket-history', args=[ticket.pk]))
                    self.assertEqual(history.status_code, 200)
                    self.assertTrue(history.data)
                    self.assertTrue(all(event['ticket'] == ticket.pk for event in history.data))
                for ticket in hidden:
                    for route in ('ticket-detail', 'ticket-history'):
                        self.assertEqual(client.get(reverse(route, args=[ticket.pk])).status_code, 404)

    def test_support_and_admin_see_every_ticket_and_history_without_staff_status(self):
        all_tickets = (self.own_ticket, self.other_ticket, self.own_deleted, self.other_deleted)
        for user in (self.support, self.administrator):
            client = self.client_for(user)
            with self.subTest(user=user.username):
                response = client.get(reverse('ticket-list'))
                self.assertEqual(response.status_code, 200)
                self.assertEqual({item['id'] for item in response.data}, {ticket.pk for ticket in all_tickets})
                for ticket in all_tickets:
                    detail = client.get(reverse('ticket-detail', args=[ticket.pk]))
                    self.assertEqual(detail.status_code, 200)
                    self.assertEqual(detail.data['is_deleted'], ticket.is_deleted)
                    self.assertEqual(client.get(reverse('ticket-history', args=[ticket.pk])).status_code, 200)

    def test_status_filter_preserves_ownership_scope(self):
        client = self.client_for(self.employee)
        for status, ticket in ((Ticket.Status.OPEN, self.own_ticket), (Ticket.Status.RESOLVED, self.own_deleted)):
            response = client.get(reverse('ticket-list'), {'status': status})
            self.assertEqual(response.status_code, 200)
            self.assertEqual([item['id'] for item in response.data], [ticket.pk])
        client = self.client_for(self.support)
        response = client.get(reverse('ticket-list'), {'status': Ticket.Status.RESOLVED})
        self.assertEqual({item['id'] for item in response.data}, {self.own_deleted.pk, self.other_deleted.pk})
        response = client.get(reverse('ticket-list'), {'status': 'INVALID'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('status', response.data)

    def test_creation_controls_server_fields_and_records_initial_history_for_each_role(self):
        for user, priority in (
            (self.employee, None), (self.support, Ticket.Priority.HIGH), (self.administrator, Ticket.Priority.LOW),
        ):
            payload = {
                'title': 'New request', 'description': 'Please investigate.',
                'id': self.other_ticket.pk, 'created_by': self.other.pk,
                'assigned_to': self.support.pk, 'status': Ticket.Status.CLOSED,
                'is_deleted': True, 'deleted_at': '2000-01-01T00:00:00Z',
                'created_at': '2000-01-01T00:00:00Z', 'updated_at': '2000-01-01T00:00:00Z',
            }
            if priority is not None:
                payload['priority'] = priority
            with self.subTest(user=user.username):
                response = self.client_for(user).post(reverse('ticket-list'), payload, format='json')
                self.assertEqual(response.status_code, 201)
                ticket = Ticket.objects.get(pk=response.data['id'])
                self.assertNotEqual(ticket.pk, self.other_ticket.pk)
                self.assertEqual(ticket.created_by_id, user.pk)
                self.assertEqual(response.data['created_by'], user.pk)
                self.assertEqual(ticket.status, Ticket.Status.OPEN)
                self.assertEqual(response.data['status'], Ticket.Status.OPEN)
                self.assertIsNone(ticket.assigned_to_id)
                self.assertIsNone(response.data['assigned_to'])
                self.assertEqual(ticket.priority, priority or Ticket.Priority.MEDIUM)
                self.assertFalse(response.data['is_deleted'])
                self.assertIsNone(response.data['deleted_at'])
                self.assertNotEqual(response.data['created_at'], payload['created_at'])
                self.assertNotEqual(response.data['updated_at'], payload['updated_at'])
                self.assertEqual(ticket.status_history.count(), 1)
                event = ticket.status_history.get()
                self.assertEqual(event.actor_id, user.pk)
                self.assertIsNone(event.from_status)
                self.assertEqual(event.to_status, Ticket.Status.OPEN)
                self.assertTrue(timezone.is_aware(event.changed_at))

    def test_invalid_creation_does_not_write_ticket_or_history(self):
        before = (Ticket.objects.count(), TicketStatusHistory.objects.count())
        for field, invalid_value in (('title', ''), ('priority', 'URGENT')):
            payload = {'title': 'New request', 'description': 'Help needed.', field: invalid_value}
            response = self.client_for(self.employee).post(reverse('ticket-list'), payload, format='json')
            self.assertEqual(response.status_code, 400)
            self.assertIn(field, response.data)
        self.assertEqual((Ticket.objects.count(), TicketStatusHistory.objects.count()), before)

    def test_creation_rolls_back_when_initial_history_fails(self):
        before = (Ticket.objects.count(), TicketStatusHistory.objects.count())

        def fail_history(**kwargs):
            self.assertTrue(Ticket.objects.filter(pk=kwargs['ticket'].pk).exists())
            raise RuntimeError('Simulated history failure')

        with patch('tickets.services.TicketStatusHistory.objects.create', side_effect=fail_history):
            with self.assertRaisesMessage(RuntimeError, 'Simulated history failure'):
                self.client_for(self.employee).post(
                    reverse('ticket-list'), {'title': 'Rollback', 'description': 'Fail atomically.'}, format='json',
                )
        self.assertEqual((Ticket.objects.count(), TicketStatusHistory.objects.count()), before)

    def test_history_is_returned_in_chronological_order(self):
        ticket = Ticket.objects.create(
            title='Resolved request', description='History ordering.',
            created_by=self.employee, status=Ticket.Status.RESOLVED,
        )
        base = timezone.now()
        # Insert out of timestamp order to distinguish chronological ordering from PK ordering.
        for previous, current, offset in (
            (Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS, 1),
            (Ticket.Status.IN_PROGRESS, Ticket.Status.RESOLVED, 2),
            (None, Ticket.Status.OPEN, 0),
        ):
            with patch('django.utils.timezone.now', return_value=base + timedelta(seconds=offset)):
                TicketStatusHistory.objects.create(
                    ticket=ticket, actor=self.employee, from_status=previous, to_status=current,
                )
        response = self.client_for(self.employee).get(reverse('ticket-history', args=[ticket.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [event['to_status'] for event in response.data],
            [Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS, Ticket.Status.RESOLVED],
        )
        timestamps = [event['changed_at'] for event in response.data]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_generic_mutation_methods_are_not_exposed(self):
        client = self.client_for(self.employee)
        before = Ticket.objects.filter(pk=self.own_ticket.pk).values().get()
        detail_url = reverse('ticket-detail', args=[self.own_ticket.pk])
        for method in ('put', 'patch', 'delete'):
            self.assertEqual(getattr(client, method)(detail_url, {}).status_code, 405)
        self.assertEqual(client.post(reverse('ticket-history', args=[self.own_ticket.pk]), {}).status_code, 405)
        self.assertEqual(Ticket.objects.filter(pk=self.own_ticket.pk).values().get(), before)
