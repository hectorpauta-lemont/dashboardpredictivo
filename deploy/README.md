# Archivos de despliegue

Scripts y configuraciones versionados para ejecutar EROS Allocator en una
instancia EC2 Ubuntu. No es la guía de operación completa.

## Contenido

| Ruta | Responsabilidad |
| --- | --- |
| `scripts/bootstrap-ubuntu.sh` | Preparación inicial de una instancia nueva (una sola vez, antes del primer deploy y de Certbot). |
| `scripts/pull-main.sh` | Descarga no interactiva y de solo lectura de la rama `main` mediante la deploy key de la instancia. |
| `scripts/deploy.sh` | Instalación o actualización de una versión de la aplicación. |
| `scripts/start-worker.sh` | Inicio manual y parametrizado de `run_allocator` o `run_earnings_tracker`. |
| `scripts/manage-production.sh` | Ejecución segura de comandos `manage.py` en producción. |
| `systemd/eros-allocator-api.service` | Gunicorn/Django en `127.0.0.1:8000`, habilitado por defecto. |
| `systemd/eros-allocator-worker@.service` | Unidad instanciada de los workers (`%i` = `run_allocator` \| `run_earnings_tracker`), **deshabilitada** por defecto. |
| `nginx/eros-allocator.conf` | Proxy inverso, dominio y archivos estáticos (HTTP inicial; Certbot añade HTTPS). |
| `rsyslog/30-eros-allocator.conf` | Separa la salida de los servicios en `/var/log/eros-allocator/{api,worker}.log`. |
| `logrotate/eros-allocator` | Rotación diaria, 14 rotaciones, compresión. |

## Contrato de rutas

- Proyecto: `/opt/eros-allocator`
- Entorno virtual: `/opt/eros-allocator/venv`
- Variables y secretos: `/etc/eros-allocator/eros.env` (fuera de Git, `0640 root:eros-allocator`)
- Parámetros de worker en runtime: `/run/eros-allocator/worker-<cmd>.env` (tmpfs, los escribe `start-worker.sh`)
- Gunicorn: `127.0.0.1:8000`, accesible públicamente solo mediante Nginx
- Logs: `/var/log/eros-allocator/`, locales, sin CloudWatch

## Workers: manuales, a propósito

Igual que `eros-screening` en `eros-prime-gate`, los dos workers están
**deshabilitados y detenidos por defecto**. `deploy.sh` corta cualquier corrida
activa en cada despliegue. Para arrancarlos después de un deploy:

```bash
sudo bash deploy/scripts/start-worker.sh run_allocator --hours 24
sudo bash deploy/scripts/start-worker.sh run_earnings_tracker --hours 24
```

Tienen `Restart=always`: al cumplirse `--hours` (o si el proceso cae) `systemd`
los reinicia con los mismos parámetros. La única forma de pararlos es
`sudo systemctl stop eros-allocator-worker@<cmd>` o un nuevo deploy.

## Documentación relacionada

- Infraestructura y decisiones de AWS: [`../docs/AWS_REQUIREMENTS.md`](../docs/AWS_REQUIREMENTS.md)
- Primer despliegue, actualizaciones, operación y diagnóstico: [`../docs/EC2_DEPLOYMENT.md`](../docs/EC2_DEPLOYMENT.md)
- Descripción funcional, entorno local y variables: [`../README.md`](../README.md)

No copies secretos dentro de `deploy/`. Certbot modifica la configuración activa
de Nginx en `/etc/nginx/`, no el archivo versionado de este directorio.
