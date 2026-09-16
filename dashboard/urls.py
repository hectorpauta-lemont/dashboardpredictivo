from django.urls import path

from dashboard import views

urlpatterns = [
    path("decisions/", views.SurveyDecisionListView.as_view(), name="decision-list"),
    path("decisions/<str:survey_supplier_id>/", views.SurveyDecisionDetailView.as_view(), name="decision-detail"),
    path("business-summary/", views.BusinessSummaryView.as_view(), name="business-summary"),
    path("dashboard-metrics/", views.DashboardMetricsView.as_view(), name="dashboard-metrics"),
    path("agent-insights/", views.AgentInsightsView.as_view(), name="agent-insights"),
]
