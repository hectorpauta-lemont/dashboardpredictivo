from django.contrib import admin

from intake.models import AwardedSurvey


@admin.register(AwardedSurvey)
class AwardedSurveyAdmin(admin.ModelAdmin):
    list_display = [
        "survey_supplier_id",
        "survey_name",
        "buyer_name",
        "type_industry_name",
        "cpi",
        "cpi_client",
        "last_polled_at",
    ]
    search_fields = ["survey_supplier_id", "survey_name", "buyer_name"]
    list_filter = ["type_industry_name"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
