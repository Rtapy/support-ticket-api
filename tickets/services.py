from django.db import transaction

from .models import Ticket, TicketStatusHistory


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
