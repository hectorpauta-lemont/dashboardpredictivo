from django.contrib import admin

from orchestrator.models import AllocatorRuntimeState


@admin.register(AllocatorRuntimeState)
class AllocatorRuntimeStateAdmin(admin.ModelAdmin):
    list_display = ["name", "process_status", "token_status", "heartbeat_at", "last_evaluated_count"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
