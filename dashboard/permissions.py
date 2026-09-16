from django.conf import settings
from rest_framework.permissions import BasePermission


class HasAPIKey(BasePermission):
    """Exige el header 'X-API-Key' con el valor de settings.API_KEY.

    Mismo patron que eros-prime-gate/screening/permissions.py. Protege los
    endpoints de datos (/api/decisions/...); el Swagger UI y el schema
    quedan sin proteger a proposito -- solo documentan la forma de la API.
    """

    def has_permission(self, request, view):
        return bool(settings.API_KEY) and request.headers.get("X-API-Key") == settings.API_KEY
