"""
Configuracion versionada de las reglas de negocio -- mismo espiritu que
screening/providers_config.py en eros-prime-gate: un solo archivo dice cual es
la regla, nada de valores magicos sueltos en el codigo.

Todo lo que aparece aqui debe estar confirmado con Piyush antes de habilitarse.
Ver README.md, seccion "Still open" -- no se adivina.
"""

# Confirmado con Piyush (llamada 2026-08-11): 30% es el piso correcto para
# empezar. No confundir con SurveySupplier.profit_percentage de Link (default
# 50 en tnr-link) -- ese es un campo distinto, no este piso de negocio.
PROFIT_FLOOR_PERCENT = 30.0

# Piso absoluto que nadie puede bajar sin aprobacion explicita del negocio
# (gobernanza, ver README). Hoy coincide con PROFIT_FLOOR_PERCENT.
ABSOLUTE_MIN_PROFIT_FLOOR_PERCENT = 30.0

# Reglas de exclusion buyer/proveedor. Cada entrada queda "enabled: False"
# hasta que el nombre exacto del buyer este confirmado -- activarla con un
# nombre adivinado es peor que no tener la regla.
EXCLUSION_GROUPS = [
    {
        "rule_id": "prime_vs_lucid_cint_buyer42",
        # Piyush menciono "Buyer 42" en la llamada del 2026-08-11 pero el
        # nombre exacto del Client en tnr-link no quedo claro en la
        # transcripcion -- re-preguntar antes de habilitar.
        "buyer_name_contains": "Buyer 42",
        "mutually_exclusive_suppliers": ["Prime", "Lucid", "Cint"],
        "enabled": False,
    },
]

# Confirmado con Piyush (2026-08-11): ranking solo para consumer jobs,
# excluyendo la categoria "beauty" (CPI bajo, no vale la pena lanzar).
# Comparacion case-insensitive contra SurveySupplier.type_industry.name.
RANKING_EXCLUDED_TYPE_INDUSTRIES = ["beauty"]

# Confirmado por el jefe (2026-08-19): el piso real de IR es 10% -- cuando
# una encuesta llega ahi, los suppliers empiezan a llamar preocupados. La
# alarma dispara un punto antes (11%), para ser proactivos en vez de
# reactivos.
IR_FLOOR_PERCENT = 10.0
IR_ALARM_THRESHOLD_PERCENT = 11.0

# Sin confirmar un numero exacto de "el jefe" para terminacion -- se usa 50%
# como zona de peligro evidente: el caso real reportado (SY AH EN 8450128)
# tenia 83% (15 de 18 prescreens terminados) y se senalo como el problema.
# Revisar con el negocio si el umbral real debe ser otro.
TERMINATION_ALARM_THRESHOLD_PERCENT = 50.0
