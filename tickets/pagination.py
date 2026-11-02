from rest_framework.pagination import CursorPagination


class TicketCursorPagination(CursorPagination):
    page_size = 20
    ordering = ('-created_at', '-pk')


class MessageCursorPagination(TicketCursorPagination):
    ordering = ('created_at', 'pk')


class HistoryCursorPagination(TicketCursorPagination):
    ordering = ('changed_at', 'pk')
