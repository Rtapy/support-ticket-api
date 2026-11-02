from django.contrib import admin

from .models import Ticket, TicketMessage, TicketStatusHistory
from .roles import is_support_or_admin


class ReadOnlyTicketAdmin(admin.ModelAdmin):
    actions = None

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_view_permission(self, request, obj=None):
        return is_support_or_admin(request.user) and super().has_view_permission(request, obj)

    def has_module_permission(self, request):
        return is_support_or_admin(request.user) and super().has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Ticket)
class TicketAdmin(ReadOnlyTicketAdmin):
    list_display = ('id', 'title', 'status', 'priority', 'created_by', 'assigned_to', 'created_at')
    list_select_related = ('created_by', 'assigned_to')


@admin.register(TicketMessage)
class TicketMessageAdmin(ReadOnlyTicketAdmin):
    list_display = ('id', 'ticket', 'author', 'created_at')
    list_select_related = ('ticket', 'author')


@admin.register(TicketStatusHistory)
class TicketStatusHistoryAdmin(ReadOnlyTicketAdmin):
    list_display = ('id', 'ticket', 'actor', 'from_status', 'to_status', 'changed_at')
    list_select_related = ('ticket', 'actor')
