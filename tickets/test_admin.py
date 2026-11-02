from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from .models import Ticket, TicketMessage, TicketStatusHistory


class TicketAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin_user = User.objects.create_user(
            username='admin', is_superuser=True, is_staff=True,
        )
        cls.employee = User.objects.create_user(username='employee', is_staff=True)
        cls.support = User.objects.create_user(username='support', is_staff=True)
        cls.support.groups.add(Group.objects.get(name='Support'))
        cls.support_without_permissions = User.objects.create_user(
            username='support_without_permissions', is_staff=True,
        )
        cls.support_without_permissions.groups.add(Group.objects.get(name='Support'))
        view_permissions = Permission.objects.filter(
            content_type__app_label='tickets', codename__startswith='view_',
        )
        # Even explicit model permissions must not bypass employee ownership.
        cls.employee.user_permissions.add(*view_permissions)
        cls.support.user_permissions.add(*view_permissions)
        cls.ticket = Ticket.objects.create(
            title='Account problem', description='Need help.', created_by=cls.admin_user,
        )
        cls.message = TicketMessage.objects.create(
            ticket=cls.ticket, author=cls.admin_user, content='Investigating.',
        )
        cls.history = TicketStatusHistory.objects.create(
            ticket=cls.ticket, actor=cls.admin_user, to_status=Ticket.Status.OPEN,
        )

    def test_admin_records_are_read_only_even_for_superusers(self):
        self.client.force_login(self.admin_user)
        for record in (self.ticket, self.message, self.history):
            model = type(record)
            prefix = f'admin:tickets_{record._meta.model_name}'
            before = list(model.objects.order_by('pk').values())
            with self.subTest(model=model.__name__):
                self.assertEqual(self.client.get(reverse(f'{prefix}_changelist')).status_code, 200)
                detail_url = reverse(f'{prefix}_change', args=[record.pk])
                self.assertEqual(self.client.get(detail_url).status_code, 200)
                self.assertEqual(self.client.post(detail_url, {'status': 'CLOSED'}).status_code, 403)
                self.assertEqual(self.client.post(reverse(f'{prefix}_add'), {}).status_code, 403)
                self.assertEqual(
                    self.client.post(reverse(f'{prefix}_delete', args=[record.pk]), {'post': 'yes'}).status_code,
                    403,
                )
                response = self.client.post(
                    reverse(f'{prefix}_changelist'),
                    {'action': 'delete_selected', '_selected_action': [record.pk], 'post': 'yes'},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(list(model.objects.order_by('pk').values()), before)

    def test_admin_ticket_visibility_requires_support_role_and_django_permissions(self):
        for user, expected_status in (
            (self.employee, 403), (self.support_without_permissions, 403), (self.support, 200),
        ):
            self.client.force_login(user)
            for record in (self.ticket, self.message, self.history):
                prefix = f'admin:tickets_{record._meta.model_name}'
                with self.subTest(user=user.username, model=type(record).__name__):
                    self.assertEqual(
                        self.client.get(reverse(f'{prefix}_changelist')).status_code, expected_status,
                    )
                    self.assertEqual(
                        self.client.get(reverse(f'{prefix}_change', args=[record.pk])).status_code,
                        expected_status,
                    )
