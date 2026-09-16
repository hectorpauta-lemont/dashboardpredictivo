# Contributing — EROS Allocator

## Ramas

| Rama | Rol |
| --- | --- |
| `main` | Producción. Protegida. Sólo entra por PR revisado. Es la que despliegan los scripts de `deploy/`. |
| `develop` | Staging / integración. |
| `feature/JIRA-XXX-nombre-corto` | Trabajo del día a día. Sale de `develop`, se mergea a `develop` por PR. |

> El repo local todavía usa `master`. Al crear el repo en GitHub se
> renombra a `main`; `deploy/scripts/*` asumen `main`.

## Commits

Conventional Commits, en minúscula, con scope cuando aplique:

```
feat(dashboard): read-only decisions API
fix: paginate survey list calls, they were silently truncated at 50
chore: gitignore data_imports/ and stray zips
```

Tipos: `feat`, `fix`, `chore`, `refactor`, `test`, `docs`.

## Antes de abrir un PR

```bash
venv/Scripts/python.exe manage.py check
venv/Scripts/python.exe manage.py test
venv/Scripts/python.exe manage.py makemigrations --check --dry-run
```

- No commitear `.env`, `db.sqlite3*`, `*.log`, `*.csv`, `data_imports/`,
  ni `docs/DEPLOY_INTERNAL.md` (ver `.gitignore`).
- Si tocás variables de entorno, actualizá `.env.example` **y** la tabla del
  `README.md`.
- Migraciones: un solo archivo por PR cuando se pueda; nombre descriptivo.

## Gobernanza (no negociable)

- Ningún registro se auto-activa en la plataforma del partner. Este servicio
  es **solo lectura** contra su API. Escribir de vuelta es alcance de una
  fase posterior y requiere aprobación formal del negocio.
- `Profit%`, exclusiones de buyer/provider y ranking son **lógica Python
  determinista y auditable** — nunca dependen de un modelo de IA.
