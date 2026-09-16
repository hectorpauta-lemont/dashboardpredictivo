from django.contrib import admin

from audit.models import DecisionLog


@admin.register(DecisionLog)
class DecisionLogAdmin(admin.ModelAdmin):
    list_display = [
        "survey",
        "verdict",
        "computed_profit_percent",
        "profit_floor_passed",
        "evaluated_at",
    ]
    list_filter = ["verdict"]
    search_fields = ["survey__survey_supplier_id", "buyer_name"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
