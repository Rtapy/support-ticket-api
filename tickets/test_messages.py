from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import Ticket, TicketMessage
from .services import change_ticket_status, close_ticket, create_ticket, reopen_ticket


class TicketMessageTests(TestCase):
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
        cls.ticket = create_ticket(created_by=cls.employee, title='Help', description='Account access.')
        cls.other_ticket = create_ticket(created_by=cls.other, title='Other request', description='Private to owner.')
        cls.message = TicketMessage.objects.create(
            ticket=cls.ticket, author=cls.support, content='Earlier reply.',
            is_deleted=True, deleted_at=timezone.now(),
        )
        cls.other_message = TicketMessage.objects.create(
            ticket=cls.other_ticket, author=cls.other, content='Other conversation.',
        )

    def client_for(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.tokens[user.pk]}')
        return client

    def snapshot(self, ticket=None):
        ticket = ticket or self.ticket
        return (
            Ticket.objects.filter(pk=ticket.pk).values().get(),
            list(ticket.messages.order_by('pk').values()),
            list(ticket.status_history.values()),
        )

    def test_message_access_follows_ticket_visibility_and_no_delete_is_exposed(self):
        own_url = reverse('ticket-messages', args=[self.ticket.pk])
        other_url = reverse('ticket-messages', args=[self.other_ticket.pk])
        before = (self.snapshot(), self.snapshot(self.other_ticket))
        self.assertEqual(APIClient().get(own_url).status_code, 401)
        self.assertEqual(APIClient().post(own_url, {'content': 'Reply'}, format='json').status_code, 401)
        owner_client = self.client_for(self.employee)
        self.assertEqual(owner_client.get(other_url).status_code, 404)
        self.assertEqual(owner_client.post(other_url, {'content': 'Reply'}, format='json').status_code, 404)
        self.assertEqual(owner_client.delete(own_url).status_code, 405)
        for user in (self.employee, self.support, self.administrator):
            response = self.client_for(user).get(own_url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual([message['id'] for message in response.data], [self.message.pk])
            self.assertTrue(response.data[0]['is_deleted'])
            self.assertIsNotNone(response.data[0]['deleted_at'])
        for user in (self.support, self.administrator):
            response = self.client_for(user).get(other_url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual([message['id'] for message in response.data], [self.other_message.pk])
        response = self.client_for(self.support).generic(
            'HEAD', own_url, data='{"content":"Unexpected write","new_status":"RESOLVED"}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual((self.snapshot(), self.snapshot(self.other_ticket)), before)

    def test_plain_messages_control_server_fields_and_update_ticket_without_history(self):
        before_ticket, _, before_history = self.snapshot()
        expected_ids = [self.message.pk]
        for actor in (self.employee, self.support, self.administrator):
            previous_updated_at = Ticket.objects.get(pk=self.ticket.pk).updated_at
            payload = {
                'content': 'New reply.', 'id': self.other_message.pk,
                'ticket': self.other_ticket.pk, 'author': self.other.pk,
                'status': Ticket.Status.CLOSED, 'is_deleted': True,
                'created_at': '2000-01-01T00:00:00Z', 'updated_at': '2000-01-01T00:00:00Z',
                'deleted_at': '2000-01-01T00:00:00Z',
            }
            with self.subTest(actor=actor.username):
                response = self.client_for(actor).post(
                    reverse('ticket-messages', args=[self.ticket.pk]), payload, format='json',
                )
                self.assertEqual(response.status_code, 201)
                message = TicketMessage.objects.get(pk=response.data['id'])
                expected_ids.append(message.pk)
                self.assertNotEqual(message.pk, self.other_message.pk)
                self.assertEqual((message.ticket_id, message.author_id, message.content), (self.ticket.pk, actor.pk, 'New reply.'))
                self.assertEqual((response.data['ticket'], response.data['author']), (self.ticket.pk, actor.pk))
                self.assertFalse(response.data['is_deleted'])
                self.assertIsNone(response.data['deleted_at'])
                self.assertNotEqual(response.data['created_at'], payload['created_at'])
                self.assertNotEqual(response.data['updated_at'], payload['updated_at'])
                after_ticket, _, after_history = self.snapshot()
                self.assertGreater(after_ticket['updated_at'], previous_updated_at)
                self.assertEqual(after_history, before_history)
                self.assertEqual(
                    {field: value for field, value in after_ticket.items() if field != 'updated_at'},
                    {field: value for field, value in before_ticket.items() if field != 'updated_at'},
                )
        response = self.client_for(self.employee).get(reverse('ticket-messages', args=[self.ticket.pk]))
        self.assertEqual([message['id'] for message in response.data], expected_ids)

    def test_reply_and_status_change_commit_with_exact_history(self):
        previous = Ticket.Status.OPEN
        for actor, new_status in ((self.support, Ticket.Status.RESOLVED), (self.administrator, Ticket.Status.CLOSED)):
            before_ticket, before_messages, before_history = self.snapshot()
            response = self.client_for(actor).post(
                reverse('ticket-messages', args=[self.ticket.pk]),
                {'content': 'Reply with transition.', 'new_status': new_status}, format='json',
            )
            with self.subTest(actor=actor.username, new_status=new_status):
                self.assertEqual(response.status_code, 201)
                message = TicketMessage.objects.get(pk=response.data['id'])
                self.assertEqual((message.ticket_id, message.author_id), (self.ticket.pk, actor.pk))
                after_ticket, after_messages, after_history = self.snapshot()
                self.assertEqual(after_ticket['status'], new_status)
                self.assertGreater(after_ticket['updated_at'], before_ticket['updated_at'])
                self.assertEqual(len(after_messages), len(before_messages) + 1)
                self.assertEqual(len(after_history), len(before_history) + 1)
                event = after_history[-1]
                self.assertEqual((event['actor_id'], event['from_status'], event['to_status']), (actor.pk, previous, new_status))
                self.assertTrue(timezone.is_aware(event['changed_at']))
            previous = new_status

    def test_forbidden_status_and_invalid_payloads_leave_no_message_or_timestamp_changes(self):
        before = self.snapshot()
        cases = (
            (self.employee, {'content': 'Reply', 'new_status': 'RESOLVED'}, 403),
            (self.employee, {'content': 'Reply', 'new_status': None}, 403),
            (self.support, {}, 400),
            (self.support, {'content': '   '}, 400),
            (self.support, {'content': 'Reply', 'new_status': None}, 400),
            (self.support, {'content': 'Reply', 'new_status': 'OPEN'}, 400),
        )
        for actor, payload, expected in cases:
            with self.subTest(actor=actor.username, payload=payload):
                response = self.client_for(actor).post(
                    reverse('ticket-messages', args=[self.ticket.pk]), payload, format='json',
                )
                self.assertEqual(response.status_code, expected)
                self.assertEqual(self.snapshot(), before)

    def test_closed_ticket_is_readable_but_cannot_receive_messages_until_reopened(self):
        change_ticket_status(ticket_id=self.ticket.pk, actor=self.support, new_status=Ticket.Status.RESOLVED)
        close_ticket(ticket_id=self.ticket.pk, actor=self.support)
        url = reverse('ticket-messages', args=[self.ticket.pk])
        before = self.snapshot()
        for actor in (self.employee, self.support, self.administrator):
            client = self.client_for(actor)
            self.assertEqual(client.get(url).status_code, 200)
            self.assertEqual(client.post(url, {'content': 'Closed reply'}, format='json').status_code, 400)
        self.assertEqual(
            self.client_for(self.support).post(url, {'content': 'Reopen reply', 'new_status': 'OPEN'}, format='json').status_code,
            400,
        )
        self.assertEqual(self.snapshot(), before)
        reopen_ticket(ticket_id=self.ticket.pk, actor=self.support)
        history_count = self.ticket.status_history.count()
        self.assertEqual(self.client_for(self.employee).post(url, {'content': 'Reopened reply'}, format='json').status_code, 201)
        self.assertEqual(self.ticket.status_history.count(), history_count)

    def test_history_failure_rolls_back_message_status_and_timestamps(self):
        before = self.snapshot()

        def fail_history(**kwargs):
            stored = Ticket.objects.get(pk=self.ticket.pk)
            self.assertEqual(stored.status, Ticket.Status.RESOLVED)
            self.assertGreater(stored.updated_at, before[0]['updated_at'])
            self.assertTrue(TicketMessage.objects.filter(ticket=self.ticket, content='Atomic reply').exists())
            raise RuntimeError('Simulated reply history failure')

        with patch('tickets.services.TicketStatusHistory.objects.create', side_effect=fail_history):
            with self.assertRaisesMessage(RuntimeError, 'Simulated reply history failure'):
                self.client_for(self.support).post(
                    reverse('ticket-messages', args=[self.ticket.pk]),
                    {'content': 'Atomic reply', 'new_status': 'RESOLVED'}, format='json',
                )
        self.assertEqual(self.snapshot(), before)
