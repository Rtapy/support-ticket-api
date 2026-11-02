from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from .models import Ticket, TicketMessage, TicketStatusHistory


class TicketModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(username='owner')
        cls.assignee = User.objects.create_user(username='assignee')
        cls.author = User.objects.create_user(username='author')
        cls.actor = User.objects.create_user(username='actor')
        cls.ticket = Ticket.objects.create(
            title='Cannot sign in', description='Account access issue.', created_by=cls.owner,
        )
        cls.message = TicketMessage.objects.create(
            ticket=cls.ticket, author=cls.author, content='Please investigate.',
        )
        # Creation with initial history becomes one atomic service operation in task 4.
        cls.history = TicketStatusHistory.objects.create(
            ticket=cls.ticket, actor=cls.actor, from_status=None, to_status=Ticket.Status.OPEN,
        )

    def test_defaults_and_audit_fields_are_persisted(self):
        ticket = Ticket.objects.get(pk=self.ticket.pk)
        message = TicketMessage.objects.get(pk=self.message.pk)
        history = TicketStatusHistory.objects.get(pk=self.history.pk)
        self.assertEqual(ticket.priority, Ticket.Priority.MEDIUM)
        self.assertEqual(ticket.status, Ticket.Status.OPEN)
        self.assertIsNone(ticket.assigned_to_id)
        self.assertEqual(ticket.created_by_id, self.owner.pk)
        self.assertEqual(message.author_id, self.author.pk)
        for record in (ticket, message):
            self.assertFalse(record.is_deleted)
            self.assertIsNone(record.deleted_at)
            self.assertTrue(timezone.is_aware(record.created_at))
            self.assertGreaterEqual(record.updated_at, record.created_at)
        self.assertEqual(history.actor_id, self.actor.pk)
        self.assertIsNone(history.from_status)
        self.assertEqual(history.to_status, Ticket.Status.OPEN)
        self.assertTrue(timezone.is_aware(history.changed_at))

    def test_instance_and_queryset_deletion_preserve_records(self):
        # A ticket without dependants must also survive deletion attempts.
        empty_ticket = Ticket.objects.create(
            title='New request', description='No messages yet.', created_by=self.owner,
        )
        for record in (empty_ticket, self.ticket, self.message, self.history):
            model = type(record)
            before = model.objects.filter(pk=record.pk).values().get()
            with self.subTest(model=model.__name__, pk=record.pk):
                with self.assertRaises(ProtectedError):
                    record.delete()
                with self.assertRaises(ProtectedError):
                    model.objects.filter(pk=record.pk).delete()
                self.assertEqual(model.objects.filter(pk=record.pk).values().get(), before)

    def test_deleting_referenced_users_does_not_cascade_or_clear_audit_data(self):
        assigned_ticket = Ticket.objects.create(
            title='Assigned request', description='Keep the assignee reference.',
            created_by=self.owner, assigned_to=self.assignee,
        )
        for user in (self.owner, self.assignee, self.author, self.actor):
            with self.subTest(user=user.username):
                with self.assertRaises(ProtectedError):
                    user.delete()
                with self.assertRaises(ProtectedError):
                    get_user_model().objects.filter(pk=user.pk).delete()
                self.assertTrue(get_user_model().objects.filter(pk=user.pk).exists())
        assigned_ticket.refresh_from_db()
        self.assertEqual(assigned_ticket.assigned_to_id, self.assignee.pk)
        self.assertTrue(Ticket.objects.filter(pk=self.ticket.pk).exists())
        self.assertTrue(TicketMessage.objects.filter(pk=self.message.pk).exists())
        self.assertTrue(TicketStatusHistory.objects.filter(pk=self.history.pk).exists())

    def test_status_history_cannot_be_changed_or_overwritten(self):
        before = TicketStatusHistory.objects.filter(pk=self.history.pk).values().get()
        self.history.to_status = Ticket.Status.CLOSED
        with self.assertRaises(ValidationError):
            self.history.save()
        with self.assertRaises(ValidationError):
            TicketStatusHistory.objects.filter(pk=self.history.pk).update(to_status=Ticket.Status.CLOSED)
        with self.assertRaises(ValidationError):
            TicketStatusHistory.objects.bulk_update([self.history], ['to_status'])

        replacement = TicketStatusHistory(
            pk=self.history.pk, ticket=self.ticket, actor=self.actor,
            from_status=None, to_status=Ticket.Status.CLOSED,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            replacement.save()
        with self.assertRaises(ValidationError):
            TicketStatusHistory.objects.bulk_create(
                [replacement], update_conflicts=True,
                update_fields=['to_status'], unique_fields=['pk'],
            )
        self.assertEqual(TicketStatusHistory.objects.filter(pk=self.history.pk).values().get(), before)
