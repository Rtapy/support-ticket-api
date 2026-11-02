from rest_framework import serializers

from .models import Ticket, TicketMessage, TicketStatusHistory
from .services import create_ticket


class TicketSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ticket
        fields = (
            'id', 'title', 'description', 'priority', 'status', 'created_by',
            'assigned_to', 'created_at', 'updated_at', 'is_deleted', 'deleted_at',
        )
        read_only_fields = (
            'id', 'status', 'created_by', 'assigned_to', 'created_at',
            'updated_at', 'is_deleted', 'deleted_at',
        )

    def create(self, validated_data):
        return create_ticket(created_by=self.context['request'].user, **validated_data)


class TicketStatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketStatusHistory
        fields = ('id', 'ticket', 'actor', 'from_status', 'to_status', 'changed_at')
        read_only_fields = fields


class TicketMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketMessage
        fields = ('id', 'ticket', 'author', 'content', 'created_at', 'updated_at', 'is_deleted', 'deleted_at')
        read_only_fields = fields
