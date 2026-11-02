from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.fields import CharField, IntegerField, empty
from rest_framework.generics import get_object_or_404

from .models import Ticket, TicketMessage, TicketStatusHistory
from .roles import is_support, is_support_or_admin


NORMAL_TRANSITIONS = {
    Ticket.Status.OPEN: (Ticket.Status.IN_PROGRESS, Ticket.Status.RESOLVED),
    Ticket.Status.IN_PROGRESS: (Ticket.Status.RESOLVED,),
    Ticket.Status.RESOLVED: (Ticket.Status.CLOSED,),
    Ticket.Status.CLOSED: (),
}


@transaction.atomic
def create_ticket(*, created_by, title, description, priority=Ticket.Priority.MEDIUM):
    ticket = Ticket.objects.create(
        created_by=created_by,
        title=title,
        description=description,
        priority=priority,
        status=Ticket.Status.OPEN,
        assigned_to=None,
    )
    TicketStatusHistory.objects.create(
        ticket=ticket,
        actor=created_by,
        from_status=None,
        to_status=Ticket.Status.OPEN,
    )
    return ticket


def _get_locked_ticket(*, ticket_id, actor, support_only):
    # Call only inside an atomic service; authorization uses the locked row.
    ticket = get_object_or_404(Ticket.objects.select_for_update(), pk=ticket_id)
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied('An active authenticated user is required.')
    privileged = is_support_or_admin(actor)
    if not privileged and ticket.created_by_id != actor.pk:
        raise NotFound()
    if support_only and not privileged:
        raise PermissionDenied('Support or Admin access is required.')
    return ticket


def _record_status_change(*, ticket, actor, new_status):
    previous_status = ticket.status
    ticket.status = new_status
    ticket.save(update_fields=['status', 'updated_at'])
    TicketStatusHistory.objects.create(
        ticket=ticket, actor=actor, from_status=previous_status, to_status=new_status,
    )
    return ticket


def _validate_status_transition(*, ticket, new_status, field='status'):
    if new_status not in NORMAL_TRANSITIONS.get(ticket.status, ()):
        raise ValidationError({field: 'This status transition is not allowed.'})


@transaction.atomic
def change_ticket_status(*, ticket_id, actor, new_status):
    ticket = _get_locked_ticket(ticket_id=ticket_id, actor=actor, support_only=True)
    _validate_status_transition(ticket=ticket, new_status=new_status)
    return _record_status_change(ticket=ticket, actor=actor, new_status=new_status)


@transaction.atomic
def close_ticket(*, ticket_id, actor):
    ticket = _get_locked_ticket(ticket_id=ticket_id, actor=actor, support_only=False)
    if ticket.status != Ticket.Status.RESOLVED:
        raise ValidationError({'status': 'Only a resolved ticket can be closed.'})
    return _record_status_change(ticket=ticket, actor=actor, new_status=Ticket.Status.CLOSED)


@transaction.atomic
def reopen_ticket(*, ticket_id, actor):
    ticket = _get_locked_ticket(ticket_id=ticket_id, actor=actor, support_only=True)
    if ticket.status not in (Ticket.Status.RESOLVED, Ticket.Status.CLOSED):
        raise ValidationError({'status': 'Only a resolved or closed ticket can be reopened.'})
    return _record_status_change(ticket=ticket, actor=actor, new_status=Ticket.Status.OPEN)


@transaction.atomic
def assign_ticket(*, ticket_id, actor, assignee_id=empty):
    ticket = _get_locked_ticket(ticket_id=ticket_id, actor=actor, support_only=True)
    try:
        assignee_id = IntegerField(min_value=1, allow_null=True).run_validation(assignee_id)
    except ValidationError as exc:
        raise ValidationError({'assignee_id': exc.detail}) from exc
    if assignee_id is not None:
        assignee = get_user_model().objects.filter(pk=assignee_id).first()
        if assignee is None or not is_support(assignee):
            raise ValidationError({'assignee_id': 'Select an active member of Support.'})
    if ticket.assigned_to_id != assignee_id:
        ticket.assigned_to_id = assignee_id
        ticket.save(update_fields=['assigned_to', 'updated_at'])
    return ticket


@transaction.atomic
def post_message(*, ticket_id, actor, content=empty, new_status=empty):
    ticket = _get_locked_ticket(ticket_id=ticket_id, actor=actor, support_only=False)
    if new_status is not empty and not is_support_or_admin(actor):
        raise PermissionDenied('Only Support or Admin may change status with a reply.')
    if ticket.status == Ticket.Status.CLOSED:
        raise ValidationError({'content': 'Reopen a closed ticket before posting messages.'})
    try:
        content = CharField().run_validation(content)
    except ValidationError as exc:
        raise ValidationError({'content': exc.detail}) from exc
    if new_status is not empty:
        _validate_status_transition(ticket=ticket, new_status=new_status, field='new_status')
    message = TicketMessage.objects.create(ticket=ticket, author=actor, content=content)
    if new_status is not empty:
        _record_status_change(ticket=ticket, actor=actor, new_status=new_status)
    else:
        ticket.save(update_fields=['updated_at'])
    return message
