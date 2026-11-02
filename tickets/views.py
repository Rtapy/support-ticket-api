from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import Ticket
from .roles import is_support_or_admin
from .serializers import TicketSerializer, TicketStatusHistorySerializer


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
