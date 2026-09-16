from rest_framework import serializers

from audit.models import DecisionLog
from intake.models import AwardedSurvey


class DecisionLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = DecisionLog
        fields = [
            "id",
            "verdict",
            "buyer_name",
            "type_industry_name",
            "cpi",
            "cpi_client",
            "computed_profit_percent",
            "profit_floor_used",
            "profit_floor_passed",
            "exclusion_rule_triggered",
            "exclusion_passed",
            "ranking_in_scope",
            "cpi_suggestion_status",
            "evaluated_at",
        ]


class SurveyDecisionSummarySerializer(serializers.ModelSerializer):
    """Vista de lista -- una fila por encuesta AWARDED con su ultima decision."""

    latest_decision = serializers.SerializerMethodField()
    detail_url = serializers.SerializerMethodField()

    def get_latest_decision(self, obj):
        # decisions ya viene pre-ordenado por -evaluated_at via Prefetch en la vista.
        decision = next(iter(obj.decisions.all()), None)
        return DecisionLogSerializer(decision).data if decision else None

    def get_detail_url(self, obj) -> str:
        request = self.context.get("request")
        path = f"/api/decisions/{obj.survey_supplier_id}/"
        return request.build_absolute_uri(path) if request else path

    class Meta:
        model = AwardedSurvey
        fields = [
            "survey_supplier_id",
            "survey_name",
            "buyer_name",
            "type_industry_name",
            "cpi",
            "cpi_client",
            "survey_created_at",
            "last_polled_at",
            "latest_decision",
            "detail_url",
        ]


class SurveyDecisionDetailSerializer(SurveyDecisionSummarySerializer):
    """Vista de detalle -- historial completo de decisiones de una encuesta."""

    decisions = DecisionLogSerializer(many=True, read_only=True)

    class Meta(SurveyDecisionSummarySerializer.Meta):
        fields = SurveyDecisionSummarySerializer.Meta.fields + ["decisions"]
