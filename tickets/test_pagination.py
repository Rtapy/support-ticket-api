from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import Ticket, TicketMessage, TicketStatusHistory
from .services import create_ticket


class TicketPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.employee = User.objects.create_user(username='employee')
        cls.other = User.objects.create_user(username='other')
        cls.support = User.objects.create_user(username='support')
        cls.support.groups.add(Group.objects.get(name='Support'))
        cls.token = Token.objects.create(user=cls.employee).key
        cls.ticket = create_ticket(created_by=cls.employee, title='Own', description='Own request.')
        cls.other_ticket = create_ticket(created_by=cls.other, title='Other', description='Other request.')

    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token}')

    def test_ticket_pages_keep_owner_filter_and_navigation_when_a_new_ticket_is_added(self):
        base = timezone.now() - timedelta(minutes=5)
        tickets = []
        for index in range(21):
            with patch('django.utils.timezone.now', return_value=base + timedelta(seconds=index)):
                tickets.append(Ticket.objects.create(
                    created_by=self.employee, title=f'Visible {index}', description='Matching ticket.',
                    status=Ticket.Status.RESOLVED,
                ))
        Ticket.objects.create(created_by=self.other, title='Foreign', description='Must not leak.', status=Ticket.Status.RESOLVED)
        url = reverse('ticket-list') + '?status=RESOLVED'
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(set(first.data), {'next', 'previous', 'results'})
        self.assertIsNone(first.data['previous'])
        expected = [ticket.pk for ticket in reversed(tickets)]
        self.assertEqual([item['id'] for item in first.data['results']], expected[:20])
        self.assertEqual(parse_qs(urlsplit(first.data['next']).query)['status'], ['RESOLVED'])

        # A newer matching record must not shift the continuation into duplicates.
        Ticket.objects.create(created_by=self.employee, title='New arrival', description='After page one.', status=Ticket.Status.RESOLVED)
        second = self.client.get(first.data['next'])
        self.assertEqual(second.status_code, 200)
        self.assertEqual([item['id'] for item in second.data['results']], expected[20:])
        self.assertIsNone(second.data['next'])
        previous = self.client.get(second.data['previous'])
        self.assertEqual(previous.status_code, 200)
        self.assertEqual([item['id'] for item in previous.data['results']], expected[:20])

    def test_child_pages_preserve_chronological_ties_and_parent_ownership(self):
        messages = []
        history_ids = [self.ticket.status_history.get().pk]
        previous = Ticket.Status.OPEN
        cycle = (Ticket.Status.IN_PROGRESS, Ticket.Status.RESOLVED, Ticket.Status.CLOSED, Ticket.Status.OPEN)
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(seconds=1)):
            for index in range(21):
                messages.append(TicketMessage.objects.create(ticket=self.ticket, author=self.support, content=f'Message {index}'))
            for index in range(20):
                current = cycle[index % len(cycle)]
                event = TicketStatusHistory.objects.create(
                    ticket=self.ticket, actor=self.support, from_status=previous, to_status=current,
                )
                history_ids.append(event.pk)
                previous = current
        for route, expected in (
            ('ticket-messages', [message.pk for message in messages]),
            ('ticket-history', history_ids),
        ):
            with self.subTest(route=route):
                first = self.client.get(reverse(route, args=[self.ticket.pk]))
                self.assertEqual(first.status_code, 200)
                self.assertEqual(set(first.data), {'next', 'previous', 'results'})
                self.assertEqual([item['id'] for item in first.data['results']], expected[:20])
                second = self.client.get(first.data['next'])
                self.assertEqual(second.status_code, 200)
                self.assertEqual([item['id'] for item in second.data['results']], expected[20:])
                self.assertTrue(all(item['ticket'] == self.ticket.pk for item in second.data['results']))
                self.assertIsNone(second.data['next'])
                foreign_url = first.data['next'].replace(f'/tickets/{self.ticket.pk}/', f'/tickets/{self.other_ticket.pk}/')
                self.assertEqual(self.client.get(foreign_url).status_code, 404)

    def test_empty_pages_and_malformed_cursors_use_the_standard_response(self):
        empty = {'next': None, 'previous': None, 'results': []}
        response = self.client.get(reverse('ticket-list'), {'status': 'CLOSED'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, empty)
        response = self.client.get(reverse('ticket-messages', args=[self.ticket.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, empty)
        for url in (
            reverse('ticket-list'),
            reverse('ticket-messages', args=[self.ticket.pk]),
            reverse('ticket-history', args=[self.ticket.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url, {'cursor': 'not-a-valid-cursor'}).status_code, 404)
