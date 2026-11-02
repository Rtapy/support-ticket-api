from collections.abc import Mapping

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.fields import empty
from rest_framework.response import Response

from .models import Ticket
from .roles import is_support_or_admin
from .serializers import TicketMessageSerializer, TicketSerializer, TicketStatusHistorySerializer
from .services import assign_ticket, change_ticket_status, close_ticket, post_message, reopen_ticket


class TicketViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = TicketSerializer
    pagination_class = None

    def get_queryset(self):
        queryset = Ticket.objects.all().order_by('-created_at', '-pk')
        if not is_support_or_admin(self.request.user):
            queryset = queryset.filter(created_by=self.request.user)
        if self.action == 'list':
            status = self.request.query_params.get('status')
            if status is not None:
                if status not in Ticket.Status.values:
                    raise ValidationError({'status': 'Select a valid ticket status.'})
                queryset = queryset.filter(status=status)
        return queryset

    @action(detail=True, methods=['get'])
    def history(self, request, pk=None):
        ticket = self.get_object()
        history = ticket.status_history.order_by('changed_at', 'pk')
        return Response(TicketStatusHistorySerializer(history, many=True).data)

    @action(detail=True, methods=['post'])
    def status(self, request, pk=None):
        data = request.data
        new_status = data.get('status') if isinstance(data, Mapping) else None
        ticket = change_ticket_status(ticket_id=pk, actor=request.user, new_status=new_status)
        return Response(self.get_serializer(ticket).data)

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        ticket = close_ticket(ticket_id=pk, actor=request.user)
        return Response(self.get_serializer(ticket).data)

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):
        ticket = reopen_ticket(ticket_id=pk, actor=request.user)
        return Response(self.get_serializer(ticket).data)

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        data = request.data
        assignee_id = data.get('assignee_id', empty) if isinstance(data, Mapping) else empty
        ticket = assign_ticket(ticket_id=pk, actor=request.user, assignee_id=assignee_id)
        return Response(self.get_serializer(ticket).data)

    @action(detail=True, methods=['get', 'post'], serializer_class=TicketMessageSerializer)
    def messages(self, request, pk=None):
        if request.method in ('GET', 'HEAD'):
            ticket = self.get_object()
            messages = ticket.messages.order_by('created_at', 'pk')
            return Response(self.get_serializer(messages, many=True).data)
        data = request.data
        content = data.get('content', empty) if isinstance(data, Mapping) else empty
        new_status = data.get('new_status', empty) if isinstance(data, Mapping) else empty
        message = post_message(ticket_id=pk, actor=request.user, content=content, new_status=new_status)
        return Response(self.get_serializer(message).data, status=201)
