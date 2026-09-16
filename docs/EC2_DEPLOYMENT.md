# Despliegue y operación en EC2 — EROS Allocator

Procedimiento operativo. Las decisiones de infraestructura están en
[`AWS_REQUIREMENTS.md`](AWS_REQUIREMENTS.md). Los valores reales de cada entorno
(IPs, hostnames, alias SSH) están en `docs/DEPLOY_INTERNAL.md` (fuera de Git).

Contrato de rutas: proyecto en `/opt/eros-allocator`, venv en
`/opt/eros-allocator/venv`, variables en `/etc/eros-allocator/eros.env`.

---

## 1. Primer despliegue

Requisitos previos (ver `AWS_REQUIREMENTS.md`): EC2, RDS, security groups,
Elastic IP y registro DNS ya creados.

### 1.1 Clonar el repositorio

La instancia usa una **deploy key de solo lectura** guardada fuera del repo:

```text
/var/lib/eros-allocator/github-deploy-key      # clave privada, chmod 0400
/var/lib/eros-allocator/known_hosts            # ssh-keyscan github.com
```

```bash
sudo install -d -m 0755 /opt/eros-allocator
sudo GIT_SSH_COMMAND="ssh -i /var/lib/eros-allocator/github-deploy-key -o IdentitiesOnly=yes -o UserKnownHostsFile=/var/lib/eros-allocator/known_hosts" \
    git clone git@github.com:thinknow-us/eros-allocator.git /opt/eros-allocator
```

### 1.2 Bootstrap (una sola vez)

```bash
cd /opt/eros-allocator
sudo SERVER_NAME=<dominio-del-allocator> bash deploy/scripts/bootstrap-ubuntu.sh
```

Instala paquetes de sistema, crea el usuario `eros-allocator` y los
directorios, crea el venv e instala `requirements.txt`, instala las unidades
`systemd`, la config de Nginx (`__SERVER_NAME__` → dominio), `rsyslog` y
`logrotate`, habilita `eros-allocator-api` y deja los workers deshabilitados.

### 1.3 Completar las variables de producción

```bash
sudoedit /etc/eros-allocator/eros.env
```

Como mínimo: `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY` (≥ 50 chars),
`DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, todos los `DB_*` de RDS,
`API_KEY`, `LINK_API_TOKEN`. Ver `AWS_REQUIREMENTS.md` §8 y `.env.example`.

Generar valores seguros:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"  # DJANGO_SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"  # API_KEY
```

### 1.4 Desplegar

```bash
sudo bash /opt/eros-allocator/deploy/scripts/deploy.sh
```

Instala dependencias, valida (`manage.py check`), aplica migraciones, corre
`collectstatic`, reinstala unidades, reinicia `eros-allocator-api` + Nginx +
rsyslog y comprueba `http://127.0.0.1:8000/health/ready/`.

### 1.5 HTTPS

Cuando el DNS resuelva a la Elastic IP:

```bash
sudo certbot --nginx -d <dominio-del-allocator>
sudo certbot renew --dry-run
```

Después de validar HTTPS end-to-end, subir `DJANGO_SECURE_HSTS_SECONDS` a
`31536000` en `eros.env` y volver a ejecutar `deploy.sh`.

### 1.6 Superusuario (opcional, para `/admin/`)

```bash
sudo bash /opt/eros-allocator/deploy/scripts/manage-production.sh createsuperuser
```

### 1.7 Arrancar los workers

```bash
sudo bash /opt/eros-allocator/deploy/scripts/start-worker.sh run_allocator --hours 24
sudo bash /opt/eros-allocator/deploy/scripts/start-worker.sh run_earnings_tracker --hours 24
```

Verificar:

```bash
curl -s -H "X-API-Key: <API_KEY>" https://<dominio-del-allocator>/health/worker/
```

---

## 2. Actualizaciones

```bash
ssh <alias-de-la-instancia>            # ver docs/DEPLOY_INTERNAL.md
cd /opt/eros-allocator
sudo bash deploy/scripts/pull-main.sh  # ff-only, rechaza cambios locales
# si main agrega variables de entorno nuevas:
sudoedit /etc/eros-allocator/eros.env  # añadir SOLO las nuevas
sudo bash deploy/scripts/deploy.sh
```

`deploy.sh` **detiene y deshabilita los dos workers** a propósito (para no
dejar corriendo código viejo). Rearrancarlos después:

```bash
sudo bash deploy/scripts/start-worker.sh run_allocator --hours 24
sudo bash deploy/scripts/start-worker.sh run_earnings_tracker --hours 24
```

Si se omite este paso, no se guardan datos nuevos hasta que alguien lo note.

---

## 3. Operación

### Estado y logs

```bash
sudo systemctl status eros-allocator-api nginx
sudo systemctl status 'eros-allocator-worker@run_allocator'
sudo journalctl -u eros-allocator-api -f
sudo journalctl -u 'eros-allocator-worker@run_allocator' -f
sudo tail -f /var/log/eros-allocator/{api,worker}.log
```

### Parar / arrancar un worker

```bash
sudo systemctl stop  'eros-allocator-worker@run_earnings_tracker'
sudo bash deploy/scripts/start-worker.sh run_earnings_tracker --hours 12 --limit 200
```

### Health

| Endpoint | Espera |
|---|---|
| `GET /health/live/` | `200` siempre que Gunicorn responda |
| `GET /health/ready/` | `200` si PostgreSQL responde, `503` si no |
| `GET /health/worker/` (`X-API-Key`) | `200` si ambos workers tienen heartbeat fresco; `503` con `reasons` si no |

---

## 4. Rollback

```bash
cd /opt/eros-allocator
sudo -u eros-allocator git log --oneline -n 10
sudo -u eros-allocator env \
  GIT_SSH_COMMAND="ssh -i /var/lib/eros-allocator/github-deploy-key -o IdentitiesOnly=yes -o UserKnownHostsFile=/var/lib/eros-allocator/known_hosts" \
  git checkout <commit-anterior>
sudo bash deploy/scripts/deploy.sh
```

Las migraciones no se revierten solas. Si el commit malo incluía una
migración, revertirla explícitamente con
`manage-production.sh migrate <app> <migracion_anterior>` antes de `deploy.sh`.
`pull-main.sh` sólo avanza a `main`; para volver a `main` tras un rollback,
`git checkout main` con el mismo `GIT_SSH_COMMAND` y luego `deploy.sh`.

---

## 5. Diagnóstico

| Síntoma | Revisar |
|---|---|
| `deploy.sh` falla en el check HTTP | `journalctl -u eros-allocator-api -n 50`; casi siempre `eros.env` incompleto o RDS inalcanzable |
| `502` en el dominio | `systemctl status eros-allocator-api`; ¿Gunicorn escuchando en `127.0.0.1:8000`? |
| `400 Bad Request` | `DJANGO_ALLOWED_HOSTS` no incluye el dominio |
| `/health/ready/` da `503` | Security group de RDS, `DB_HOSTNAME`, `DB_SSLMODE` |
| `/health/worker/` da `503` con `token_invalid` | `LINK_API_TOKEN` vencido/incorrecto en `eros.env` |
| `/health/worker/` da `503` con `heartbeat_stale` | El worker no está corriendo: `start-worker.sh` |
| Workers no arrancan tras deploy | Es el comportamiento esperado; hay que arrancarlos a mano |
| Alertas de email no llegan | Rol IAM sin `ses:SendEmail` o remitente no verificado en SES |

---

## 6. Checklist de primer despliegue

- [ ] Repo clonado en `/opt/eros-allocator` con la deploy key.
- [ ] `bootstrap-ubuntu.sh` ejecutado una vez.
- [ ] `/etc/eros-allocator/eros.env` completo (`DJANGO_DEBUG=False`, secret key, RDS, `API_KEY`, `LINK_API_TOKEN`).
- [ ] `deploy.sh` termina con "la API responde correctamente".
- [ ] Certbot instaló el certificado y `renew --dry-run` pasa.
- [ ] `/health/ready/` responde `200` sobre HTTPS.
- [ ] Workers arrancados y `/health/worker/` responde `200`.
- [ ] Superusuario creado si se necesita `/admin/`.
- [ ] Se pasó al dev de Link: URL base, `API_KEY` y `/api/docs/`.
