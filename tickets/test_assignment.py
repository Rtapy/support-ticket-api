from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import Ticket
from .services import create_ticket


class TicketAssignmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.employee = User.objects.create_user(username='employee', is_staff=True)
        cls.other = User.objects.create_user(username='other')
        cls.support = User.objects.create_user(username='support')
        cls.other_support = User.objects.create_user(username='other_support')
        cls.inactive_support = User.objects.create_user(username='inactive_support', is_active=False)
        cls.administrator = User.objects.create_user(username='administrator', is_superuser=True)
        support_group = Group.objects.get(name='Support')
        for user in (cls.support, cls.other_support, cls.inactive_support):
            user.groups.add(support_group)
        cls.tokens = {
            user.pk: Token.objects.create(user=user).key
            for user in (cls.employee, cls.other, cls.support, cls.administrator)
        }
        cls.ticket = create_ticket(
            created_by=cls.employee, title='Assignment', description='Need an owner in Support.',
        )

    def client_for(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.tokens[user.pk]}')
        return client

    def snapshot(self):
        return (
            Ticket.objects.filter(pk=self.ticket.pk).values().get(),
            list(self.ticket.status_history.values()),
        )

    def test_support_and_admin_can_assign_reassign_and_unassign_without_status_history(self):
        before_ticket, before_history = self.snapshot()
        for actor, target in (
            (self.support, self.support),
            (self.administrator, self.other_support),
            (self.support, None),
        ):
            previous_updated_at = Ticket.objects.get(pk=self.ticket.pk).updated_at
            target_id = target.pk if target is not None else None
            with self.subTest(actor=actor.username, assignee_id=target_id):
                response = self.client_for(actor).post(
                    reverse('ticket-assign', args=[self.ticket.pk]), {'assignee_id': target_id}, format='json',
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['assigned_to'], target_id)
                after_ticket, after_history = self.snapshot()
                self.assertGreater(after_ticket['updated_at'], previous_updated_at)
                self.assertEqual(after_ticket['assigned_to_id'], target_id)
                self.assertEqual(after_history, before_history)
                for field, value in before_ticket.items():
                    if field not in ('assigned_to_id', 'updated_at'):
                        self.assertEqual(after_ticket[field], value)
        # Repeating the current assignment is an accepted no-op.
        before = self.snapshot()
        response = self.client_for(self.support).post(
            reverse('ticket-assign', args=[self.ticket.pk]), {'assignee_id': None}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_access_requires_support_or_admin_even_for_the_ticket_owner(self):
        url = reverse('ticket-assign', args=[self.ticket.pk])
        before = self.snapshot()
        payload = {'assignee_id': self.support.pk}
        self.assertEqual(APIClient().post(url, payload, format='json').status_code, 401)
        self.assertEqual(self.client_for(self.employee).post(url, payload, format='json').status_code, 403)
        self.assertEqual(self.client_for(self.other).post(url, payload, format='json').status_code, 404)
        self.assertEqual(self.client_for(self.employee).post(url, {}, format='json').status_code, 403)
        self.assertEqual(self.client_for(self.other).post(url, {}, format='json').status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_or_ineligible_assignees_have_no_side_effects(self):
        client = self.client_for(self.support)
        url = reverse('ticket-assign', args=[self.ticket.pk])
        before = self.snapshot()
        payloads = (
            {}, [], {'assignee_id': 'invalid'}, {'assignee_id': True}, {'assignee_id': 0},
            {'assignee_id': 10**100}, {'assignee_id': self.employee.pk},
            {'assignee_id': self.inactive_support.pk}, {'assignee_id': self.administrator.pk},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                response = client.post(url, payload, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn('assignee_id', response.data)
                self.assertEqual(self.snapshot(), before)

    def test_assignment_and_timestamp_roll_back_on_save_failure(self):
        before = self.snapshot()
        original_save = Ticket.save

        def fail_after_save(instance, *args, **kwargs):
            original_save(instance, *args, **kwargs)
            stored = Ticket.objects.get(pk=instance.pk)
            self.assertEqual(stored.assigned_to_id, self.support.pk)
            self.assertGreater(stored.updated_at, before[0]['updated_at'])
            raise RuntimeError('Simulated assignment failure')

        with patch.object(Ticket, 'save', new=fail_after_save):
            with self.assertRaisesMessage(RuntimeError, 'Simulated assignment failure'):
                self.client_for(self.support).post(
                    reverse('ticket-assign', args=[self.ticket.pk]),
                    {'assignee_id': self.support.pk}, format='json',
                )
        self.assertEqual(self.snapshot(), before)
