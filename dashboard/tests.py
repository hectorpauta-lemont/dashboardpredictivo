from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from audit.models import DecisionLog
from intake.models import AwardedSurvey


@override_settings(API_KEY="test-key")
class DecisionApiTests(TestCase):
    def setUp(self):
        self.survey = AwardedSurvey.objects.create(
            survey_supplier_id="s-1",
            survey_name="Test Survey",
            buyer_name="Symmetric API",
            type_industry_name="Consumer Electronics",
            cpi=1.0,
            cpi_client=2.0,
            survey_created_at=timezone.now(),
        )
        self.old_decision = DecisionLog.objects.create(
            survey=self.survey,
            buyer_name="Symmetric API",
            verdict="blocked_profit_floor",
            computed_profit_percent=10.0,
        )
        self.latest_decision = DecisionLog.objects.create(
            survey=self.survey,
            buyer_name="Symmetric API",
            verdict="eligible_needs_cpi_input",
            computed_profit_percent=50.0,
        )

    def test_requires_api_key(self):
        response = self.client.get("/api/decisions/")
        self.assertEqual(response.status_code, 403)

    def test_wrong_api_key_rejected(self):
        response = self.client.get("/api/decisions/", HTTP_X_API_KEY="wrong")
        self.assertEqual(response.status_code, 403)

    def test_list_returns_latest_decision_only(self):
        response = self.client.get("/api/decisions/", HTTP_X_API_KEY="test-key")
        self.assertEqual(response.status_code, 200)
        row = response.json()["results"][0]
        self.assertEqual(row["latest_decision"]["verdict"], "eligible_needs_cpi_input")

    def test_buyer_name_filter(self):
        AwardedSurvey.objects.create(survey_supplier_id="s-2", buyer_name="Open Lab API")
        response = self.client.get("/api/decisions/?buyer_name=Symmetric API", HTTP_X_API_KEY="test-key")
        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["survey_supplier_id"], "s-1")

    def test_detail_returns_full_history(self):
        response = self.client.get("/api/decisions/s-1/", HTTP_X_API_KEY="test-key")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["decisions"]), 2)

    def test_old_surveys_require_show_all(self):
        self.survey.survey_created_at = timezone.now() - timedelta(days=31)
        self.survey.save(update_fields=["survey_created_at"])
        response = self.client.get("/api/decisions/", HTTP_X_API_KEY="test-key")
        self.assertEqual(response.json()["results"], [])
        response = self.client.get("/api/decisions/?show_all=true", HTTP_X_API_KEY="test-key")
        self.assertEqual(len(response.json()["results"]), 1)
