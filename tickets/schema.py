from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import serializers

from .models import Ticket
from .serializers import TicketMessageSerializer, TicketSerializer, TicketStatusHistorySerializer


class TicketCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ticket
        fields = ('title', 'description', 'priority')
        extra_kwargs = {'priority': {'default': Ticket.Priority.MEDIUM}}


class StatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Ticket.Status.choices)


class AssignmentSerializer(serializers.Serializer):
    assignee_id = serializers.IntegerField(
        min_value=1, allow_null=True,
        help_text='Active Support member ID, or null to unassign. Admin without Support membership is not an eligible target.',
    )


class MessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(allow_blank=False)
    new_status = serializers.ChoiceField(
        choices=Ticket.Status.choices, required=False,
        help_text='Support/Admin only. Must be a valid normal transition; unchanged or null status is rejected.',
    )


class DetailErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()


VALIDATION_ERROR = OpenApiResponse(
    response={
        'type': 'object',
        'additionalProperties': {
            'oneOf': [{'type': 'string'}, {'type': 'array', 'items': {'type': 'string'}}],
        },
    },
    description='Invalid input or workflow transition. Fields map to an error string or a list of strings. No side effects.',
)
AUTH_ERROR = OpenApiResponse(DetailErrorSerializer, description='Missing/invalid token, or inactive user.')
FORBIDDEN = OpenApiResponse(DetailErrorSerializer, description='Operation forbidden on a visible ticket.')
NOT_FOUND = OpenApiResponse(DetailErrorSerializer, description='Ticket missing/not visible to this employee, or invalid cursor.')

ticket_schema = extend_schema_view(
    list=extend_schema(
        operation_id='tickets_list', summary='List visible tickets',
        description='Employee: own tickets; Support/Admin: all. Includes logically deleted records. Newest first, fixed 20-item cursor pages. Status filtering precedes pagination.',
        parameters=[OpenApiParameter('status', str, enum=Ticket.Status.values, description='Exact ticket status.')],
        responses={200: TicketSerializer(many=True), 400: VALIDATION_ERROR, 401: AUTH_ERROR, 404: NOT_FOUND},
    ),
    create=extend_schema(
        operation_id='tickets_create', summary='Create a ticket for yourself',
        description='All active authenticated roles. Only title, description and priority are writable. Creates OPEN/unassigned ticket and initial NULL → OPEN history atomically; creator/actor are the authenticated user.',
        request=TicketCreateSerializer,
        responses={201: TicketSerializer, 400: VALIDATION_ERROR, 401: AUTH_ERROR},
        examples=[OpenApiExample('Create ticket', value={'title': 'Account access', 'description': 'Cannot sign in', 'priority': 'HIGH'}, request_only=True)],
    ),
    retrieve=extend_schema(
        operation_id='tickets_retrieve', summary='Read a visible ticket',
        description='Employee: own tickets only (404 otherwise); Support/Admin: any ticket. is_deleted does not hide a visible record.',
        responses={200: TicketSerializer, 401: AUTH_ERROR, 404: NOT_FOUND},
    ),
    history=extend_schema(
        operation_id='tickets_history_list', summary='List status history',
        description='Same ticket visibility rules. Append-only events ordered by changed_at, then ID ascending. Fixed 20-item cursor pages.',
        responses={200: TicketStatusHistorySerializer(many=True), 401: AUTH_ERROR, 404: NOT_FOUND},
    ),
    status=extend_schema(
        operation_id='tickets_change_status', summary='Change ticket status',
        description='Support/Admin only. OPEN → IN_PROGRESS | RESOLVED; IN_PROGRESS → RESOLVED; RESOLVED → CLOSED. Reopen uses its dedicated endpoint. Invalid/unchanged transitions return 400; status, timestamp and one history event commit atomically.',
        request=StatusChangeSerializer,
        responses={200: TicketSerializer, 400: VALIDATION_ERROR, 401: AUTH_ERROR, 403: FORBIDDEN, 404: NOT_FOUND},
        examples=[OpenApiExample('Start investigation', value={'status': 'IN_PROGRESS'}, request_only=True)],
    ),
    assign=extend_schema(
        operation_id='tickets_assign', summary='Assign or unassign a ticket',
        description='Support/Admin only. Target must be an active Support member, including self; null unassigns. Repeating the current assignment is a no-op. Updates assignee/timestamp only, without status history.',
        request=AssignmentSerializer,
        responses={200: TicketSerializer, 400: VALIDATION_ERROR, 401: AUTH_ERROR, 403: FORBIDDEN, 404: NOT_FOUND},
        examples=[
            OpenApiExample('Assign', value={'assignee_id': 2}, request_only=True),
            OpenApiExample('Unassign', value={'assignee_id': None}, request_only=True),
        ],
    ),
    close=extend_schema(
        operation_id='tickets_close', summary='Close a resolved ticket',
        description='Owner Employee or Support/Admin. Requires current RESOLVED state; otherwise 400. Other employees receive 404. No request body. Appends one history event atomically.',
        request=None,
        responses={200: TicketSerializer, 400: VALIDATION_ERROR, 401: AUTH_ERROR, 404: NOT_FOUND},
    ),
    reopen=extend_schema(
        operation_id='tickets_reopen', summary='Reopen a resolved or closed ticket',
        description='Support/Admin only. RESOLVED | CLOSED → OPEN; all other states return 400. Employees receive 403 on own tickets, 404 on others. No request body. Appends one history event atomically.',
        request=None,
        responses={200: TicketSerializer, 400: VALIDATION_ERROR, 401: AUTH_ERROR, 403: FORBIDDEN, 404: NOT_FOUND},
    ),
    messages=[
        extend_schema(
            methods=['GET'], operation_id='tickets_messages_list', summary='List ticket messages',
            description='Same ticket visibility rules, including closed tickets and retained messages. Ordered by created_at, then ID ascending; fixed 20-item cursor pages.',
            responses={200: TicketMessageSerializer(many=True), 401: AUTH_ERROR, 404: NOT_FOUND},
        ),
        extend_schema(
            methods=['POST'], operation_id='tickets_messages_create', summary='Reply, optionally changing status',
            description='Owner Employee or Support/Admin on any nonclosed ticket. Only Support/Admin may supply new_status (Employee gets 403 even for null). Closed tickets require dedicated Reopen first. Invalid/unchanged transitions save nothing. Message, optional status/history and ticket timestamp commit atomically. Returns the message; read ticket/history for resulting status. Author/ticket/IDs/timestamps/deletion fields are server-controlled and ignored if submitted.',
            request=MessageCreateSerializer,
            responses={201: TicketMessageSerializer, 400: VALIDATION_ERROR, 401: AUTH_ERROR, 403: FORBIDDEN, 404: NOT_FOUND},
            examples=[
                OpenApiExample('Plain reply', value={'content': 'Additional details'}, request_only=True),
                OpenApiExample('Reply and resolve', value={'content': 'Access restored', 'new_status': 'RESOLVED'}, request_only=True),
            ],
        ),
    ],
)
