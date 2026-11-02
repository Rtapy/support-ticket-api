from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.deletion import ProtectedError


class RetainedQuerySet(models.QuerySet):
    def delete(self):
        raise ProtectedError('Support ticket records cannot be physically deleted.', self)


class RetainedModel(models.Model):
    objects = RetainedQuerySet.as_manager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        raise ProtectedError('Support ticket records cannot be physically deleted.', [self])


class Ticket(RetainedModel):
    class Priority(models.TextChoices):
        LOW = 'LOW', 'Low'
        MEDIUM = 'MEDIUM', 'Medium'
        HIGH = 'HIGH', 'High'

    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        IN_PROGRESS = 'IN_PROGRESS', 'In progress'
        RESOLVED = 'RESOLVED', 'Resolved'
        CLOSED = 'CLOSED', 'Closed'

    title = models.CharField(max_length=255)
    description = models.TextField()
    priority = models.CharField(max_length=6, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=11, choices=Status.choices, default=Status.OPEN)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_tickets',
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='assigned_tickets',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.title


class TicketMessage(RetainedModel):
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name='messages')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='ticket_messages',
    )
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)


class StatusHistoryQuerySet(RetainedQuerySet):
    def update(self, **kwargs):
        raise ValidationError('Status history is append-only.')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError('Status history is append-only.')

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        if update_conflicts:
            raise ValidationError('Status history is append-only.')
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )


class TicketStatusHistory(RetainedModel):
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name='status_history')
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='ticket_status_changes',
    )
    from_status = models.CharField(max_length=11, choices=Ticket.Status.choices, null=True, blank=True)
    to_status = models.CharField(max_length=11, choices=Ticket.Status.choices)
    changed_at = models.DateTimeField(auto_now_add=True)

    objects = StatusHistoryQuerySet.as_manager()

    class Meta:
        ordering = ['changed_at', 'pk']

    def save(self, *, force_insert=False, force_update=False, using=None, update_fields=None):
        if not self._state.adding or force_update or update_fields is not None:
            raise ValidationError('Status history is append-only.')
        # An explicitly supplied existing PK must fail rather than overwrite an event.
        return super().save(force_insert=True, using=using)
