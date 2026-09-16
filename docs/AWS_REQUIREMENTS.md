# Requisitos AWS — EROS Allocator

Documento de infraestructura y despliegue. Define las decisiones de AWS; el
procedimiento operativo está en [`EC2_DEPLOYMENT.md`](EC2_DEPLOYMENT.md).

> Este documento usa **placeholders** (`<...>`). Los valores reales de cada
> entorno (Elastic IP, endpoint de RDS, dominio, security groups) no se
> guardan en el repositorio; van en `docs/DEPLOY_INTERNAL.md`, que está en
> `.gitignore`.

## 1. Arquitectura elegida

No se utiliza Elastic Beanstalk en esta etapa. La API y los workers de sondeo
se ejecutan en una sola instancia EC2, con PostgreSQL en una instancia RDS
separada.

```text
Internet
   |
   v
<dominio-del-allocator>
   |
   v
Elastic IP -> EC2 Ubuntu 24.04
                |- Nginx :80/:443
                |- Gunicorn/Django :8000 local  (eros-allocator-api, siempre activo)
                `- systemd eros-allocator-worker@run_allocator
                   systemd eros-allocator-worker@run_earnings_tracker
                        (manuales; sondean la API real de Link, solo lectura)
                           |
                           v
                  PostgreSQL RDS privado
```

Motivos:

- Los workers son procesos de larga duración, no peticiones HTTP.
- La API tendrá poco tráfico (la consume el frontend de Link vía `X-API-Key`).
- Una sola EC2 evita duplicar instancias y procedimientos.
- La API permanece activa; los workers se arrancan a mano en la ventana pedida.
- **Nunca** se escribe en la base de datos de producción de Link. Todo el
  tráfico hacia `api.thinknow.link` es de solo lectura (`GET`).

## 2. Dos entornos

El mismo repositorio y los mismos scripts sirven para las dos instancias EC2.
Sólo cambia el archivo `/etc/eros-allocator/eros.env` de cada una (dominio,
`DJANGO_ALLOWED_HOSTS`, credenciales de su RDS). El mapa de qué instancia es
cuál, con IPs y hostnames reales, está en `docs/DEPLOY_INTERNAL.md`.

| Entorno | Uso | Dominio |
| --- | --- | --- |
| Producción (empresa) | Instancia definitiva | `<dominio-del-allocator>` |
| Pruebas | Validación previa antes de tocar producción | `<dominio-de-pruebas>` |

## 3. Instancia EC2

- SO: Ubuntu Server 24.04 LTS, x86-64.
- Tipo inicial: `t3.small` (2 vCPU, 2 GiB RAM). Sin Chromium, la carga es baja;
  medir y ajustar.
- Volumen raíz: gp3 de 20 GiB.
- Subred pública con ruta `0.0.0.0/0` hacia un Internet Gateway.
- IMDSv2 obligatorio.
- Sin puertos públicos para Gunicorn (8000) ni PostgreSQL (5432).

Salida HTTPS requerida hacia:

- `api.thinknow.link` (API interna real de Link).
- Repositorio de código (`git@github.com:thinknow-us/eros-allocator.git`) y
  paquetes de Python durante el despliegue.

## 4. IP y DNS

- El registro A del dominio debe apuntar a una **Elastic IP** asociada a la
  instancia (la IP pública automática cambia tras `stop/start`).

```text
Tipo: A
Nombre: <subdominio-del-allocator>
Valor: <Elastic IP de la EC2>
TTL: 300
```

No se utiliza Application Load Balancer en esta etapa.

## 5. Security groups

### EC2

Entradas:

| Protocolo | Puerto | Origen | Uso |
|---|---:|---|---|
| TCP | 80 | `0.0.0.0/0` | Validación SSL y redirección a HTTPS |
| TCP | 443 | `0.0.0.0/0` | API HTTPS |
| TCP | 22 | IP administrativa `/32` | Solo si no se usa Session Manager |

No abrir públicamente 8000 ni 5432.

Salidas: HTTPS 443 a internet, PostgreSQL 5432 hacia el security group de RDS,
DNS mediante el resolver de la VPC.

### RDS

| Protocolo | Puerto | Origen |
|---|---:|---|
| TCP | 5432 | Security group de la EC2 |

No permitir `0.0.0.0/0`; la instancia RDS no debe ser pública.

## 6. PostgreSQL RDS

- Motor: PostgreSQL.
- Base nueva y separada de producción de `tnr-link` y de `eros-prime-gate`.
- Nombre sugerido: `eros_allocator_db`.
- Instancia privada dentro de la misma VPC de la EC2.
- Cifrado en reposo y backups automáticos habilitados.
- Conexiones SSL obligatorias (`DB_SSLMODE=require`).

```dotenv
DB_ENGINE=django.db.backends.postgresql
DB_PORT=5432
DB_HOSTNAME=<endpoint-privado-de-rds>
DB_NAME=eros_allocator_db
DB_USERNAME=<usuario-de-rds>
DB_PASSWORD='<contrasena-segura>'
DB_SSLMODE=require
DATABASE_CONN_MAX_AGE=60
```

En desarrollo, `DJANGO_DEBUG=True` selecciona SQLite y estas variables se ignoran.

## 7. Subdominio y certificado SSL

El DNS apunta directo a la EC2; Nginx termina HTTPS y el certificado se
gestiona con Certbot (no ACM). Después de que el DNS resuelva:

```bash
sudo certbot --nginx -d <dominio-del-allocator>
sudo certbot renew --dry-run
```

HSTS permanece en `0` hasta validar HTTPS por completo.

## 8. Variables de aplicación

Las variables reales viven únicamente en `/etc/eros-allocator/eros.env`
(`0640`, `root:eros-allocator`). Plantilla y referencia completa: `.env.example`
y el `README.md`.

Configuración mínima de producción:

```dotenv
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<clave-aleatoria-de-al-menos-50-caracteres>
DJANGO_ALLOWED_HOSTS=<dominio-del-allocator>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<dominio-del-allocator>

DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SESSION_COOKIE_SECURE=True
DJANGO_CSRF_COOKIE_SECURE=True
DJANGO_SECURE_HSTS_SECONDS=0

DB_ENGINE=django.db.backends.postgresql
DB_PORT=5432
DB_HOSTNAME=<endpoint-privado-de-rds>
DB_NAME=eros_allocator_db
DB_USERNAME=<usuario-de-rds>
DB_PASSWORD='<contrasena-segura>'
DB_SSLMODE=require
DATABASE_CONN_MAX_AGE=60

API_KEY=<api-key-de-la-aplicacion>
LINK_API_TOKEN=<token-de-servicio-de-link>
LINK_JWT_TOKEN=<jwt-personal-temporal-o-vacio>
LOG_LEVEL=INFO
WORKER_HEALTH_MAX_AGE_SECONDS=600
```

`API_KEY` es una credencial propia de la aplicación, no una credencial AWS.
`LINK_API_TOKEN` es fijo. `LINK_JWT_TOKEN` vence rápido y sólo afecta al
breakdown de allocations; si está vacío/vencido, la API sigue respondiendo.

## 9. Servicios Linux

```text
Proyecto:   /opt/eros-allocator
Virtualenv: /opt/eros-allocator/venv
Variables:  /etc/eros-allocator/eros.env
Runtime:    /run/eros-allocator/worker-<cmd>.env  (tmpfs)
```

- `eros-allocator-api.service`: Gunicorn/Django en `127.0.0.1:8000`. Habilitado.
- `eros-allocator-worker@run_allocator.service`
- `eros-allocator-worker@run_earnings_tracker.service`: **deshabilitados** por
  defecto; se arrancan con `deploy/scripts/start-worker.sh`. `deploy.sh` los
  detiene y deshabilita en cada despliegue.
- `nginx.service`, `rsyslog.service`.

## 10. Health checks

| Endpoint | Autenticación | Función |
|---|---|---|
| `/health/live/` | No | Django/Gunicorn responde |
| `/health/ready/` | No | Conexión con PostgreSQL |
| `/health/worker/` | `X-API-Key` | Heartbeat, token y último poll de los dos workers |

`/health/worker/` devuelve HTTP 503 si algún worker está detenido, con
heartbeat vencido (> `WORKER_HEALTH_MAX_AGE_SECONDS`) o con token rechazado.

## 11. Logs locales

Sin CloudWatch Logs ni CloudWatch Agent. La aplicación no necesita API keys ni
permisos AWS para logging (SES usa el rol IAM de la instancia).

```text
API:     /var/log/eros-allocator/api.log
Workers: /var/log/eros-allocator/worker.log
Nginx:   /var/log/nginx/{access,error}.log
```

`journalctl -u eros-allocator-api -f` y
`journalctl -u 'eros-allocator-worker@*' -f` también sirven. `logrotate` rota
diariamente y conserva 14 rotaciones.

## 12. IAM

La instancia necesita un rol con permiso `ses:SendEmail` (y `ses:SendRawEmail`)
sólo si se activan las alertas de performance por email. El dominio/dirección
remitente debe estar verificado en SES. Sin ese rol, las alertas de email se
omiten en silencio; Telegram funciona sin AWS.

## 13. Checklist

- [ ] EC2 Ubuntu 24.04 en subred pública (una por entorno).
- [ ] Elastic IP asociada y registro A actualizado.
- [ ] Security group de EC2: 80/443 abiertos, 8000/5432 cerrados.
- [ ] RDS PostgreSQL privada, cifrada, con backups y base separada.
- [ ] RDS permite 5432 únicamente desde el security group de EC2.
- [ ] El subdominio resuelve a la Elastic IP.
- [ ] Rol IAM con `ses:SendEmail` si se usan alertas por email.
- [ ] Acceso administrativo por Session Manager o SSH restringido.
- [ ] `docs/DEPLOY_INTERNAL.md` actualizado con los valores reales de cada entorno.
- [ ] Continuar con [`EC2_DEPLOYMENT.md`](EC2_DEPLOYMENT.md).
