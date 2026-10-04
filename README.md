# Dashboard Predictivo

Dashboard de consulta que lee una fuente empresarial, guarda históricos y muestra indicadores y recomendaciones. Las recomendaciones no ejecutan cambios en la plataforma de origen.

Esta guía explica cómo volver a arrancar, qué claves se necesitan, cómo adaptar la fuente a otra empresa y cómo publicar los cambios en GitHub.

El proyecto sirve como base reutilizable para nuevos dashboards. Para que una
IA de programación lo adapte, completar la ficha de la sección 5 y darle las
instrucciones de esa misma sección. La reutilización incluye código y estructura;
los datos, accesos, fórmulas y reglas de cada empresa se configuran por separado.

## 1. Alcance actual

El conector implementado consulta la API de **Link**, mediante `intake/link_client.py`. Los modelos y las fórmulas actuales representan encuestas, compradores, proveedores, ingresos y márgenes.

| Conexión | Propósito | Soporte actual |
| --- | --- | --- |
| Fuente empresarial | Leer la información que se mostrará | API de Link; otra API o base necesita un adaptador |
| Base del dashboard | Guardar registros, históricos y resultados | SQLite local o PostgreSQL remoto |

**`DB_*` configura la base interna del dashboard, no la base externa de la empresa.** Cambiar una URL o contraseña no crea un conector universal. MySQL, SQL Server, Oracle u otras fuentes necesitan su controlador, consultas, normalización y pruebas.

Para otra empresa, usar una instalación, una base y credenciales separadas. El proyecto actual no aísla varias empresas dentro de una misma base.

## 2. Volver a arrancar en esta PC

El inicio automático de Windows está desactivado. El antiguo lanzador está fuera de Inicio; su respaldo local está en `deploy/local-backups/`. El watchdog exige `-ManualStart`.

### Paso 1: abrir PowerShell

```powershell
Set-Location C:\eros-allocator
Test-Path .\venv\Scripts\python.exe
Test-Path .\.env
```

Si ambos resultados son `True`, reutilizar la instalación. **No sobrescribir `.env`**: puede contener las claves actuales. Si falta el entorno virtual, seguir la sección 3.

### Paso 2: revisar las claves

Revisar `.env` localmente con la tabla de la sección 4. Reutilizar claves vigentes y renovar las vencidas o revocadas en el servicio correspondiente. Después de cambiar `.env`, reiniciar el servidor y los recolectores para cargar los valores nuevos.

### Paso 3: comprobar la instalación

```powershell
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py migrate
```

`migrate` actualiza las tablas; no importa información de la empresa. Antes de actualizar una instalación con datos importantes, respaldar la base con las herramientas del motor. Para SQLite, detener los procesos que escriben y hacer una copia consistente.

### Paso 4: iniciar el servidor

```powershell
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Mantener la consola abierta y acceder a:

- Dashboard: <http://127.0.0.1:8000/dashboard/>
- Estado del servidor: <http://127.0.0.1:8000/health/live/>
- Estado de dependencias: <http://127.0.0.1:8000/health/ready/>
- Estado de recolectores: <http://127.0.0.1:8000/health/worker/>
- Documentación de API: <http://127.0.0.1:8000/api/docs/>

El servidor puede mostrar datos ya guardados sin activar recolectores. Una base nueva estará vacía hasta realizar la primera importación.

### Paso 5: probar la lectura de Link una vez

En otra consola, dentro del proyecto:

```powershell
.\venv\Scripts\python.exe manage.py run_allocator --once --limit 5
.\venv\Scripts\python.exe manage.py run_earnings_tracker --once --limit 20
.\venv\Scripts\python.exe manage.py build_dashboard_rollups --days 3
```

Consultan Link y guardan resultados en la base del dashboard. Revisar antes los destinatarios de alertas: pueden usarse los canales configurados.

### Paso 6: activar actualizaciones continuas

Para controlar fácilmente inicio y parada, ejecutar cada comando en su propia consola:

```powershell
.\venv\Scripts\python.exe manage.py run_allocator --hours 24
```

```powershell
.\venv\Scripts\python.exe manage.py run_earnings_tracker --hours 24
```

Usar `Ctrl+C` en cada consola para detener el proceso correspondiente. Detener el servidor no detiene los recolectores.

Como alternativa, iniciar el supervisor manual:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\watchdog_pollers.ps1 -ManualStart
```

El supervisor relanza recolectores y realiza agregaciones y sincronizaciones periódicas. No inicia el servidor ni un túnel y no registra un inicio automático de Windows. `ExecutionPolicy Bypass` afecta únicamente a esa ejecución.

Sus recolectores se ejecutan en procesos separados: detener el watchdog no los detiene automáticamente. Para parar esa sesión, detener primero el watchdog y después los procesos específicos del proyecto desde el Administrador de tareas, verificando sus líneas de comandos. No detener indiscriminadamente todos los procesos Python.

### Acceso externo

Para uso local no se necesita Nginx ni ngrok. Los archivos Nginx incluidos corresponden al despliegue Linux.

La ruta `/dashboard/` actual no exige inicio de sesión y entrega al navegador la clave con la que consulta la API. Antes de exponer un túnel o dominio público, implementar control de acceso para todas las rutas del dashboard y la API. La clave API por sí sola no protege una página publicada que la entrega al visitante.

## 3. Instalar en otra PC o recuperar la instalación

Requisitos: Git y Python compatible con `requirements.txt`. La instalación local revisada usa Python 3.12.10; las dependencias están fijadas en [requirements.txt](requirements.txt).

```powershell
git clone https://github.com/hectorpauta-lemont/dashboardpredictivo.git
Set-Location dashboardpredictivo
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Elegir la rama o versión deseada: la rama principal no incluye cambios que todavía no se hayan publicado o integrado. Copiar la plantilla solo cuando no exista `.env`. Completar la sección 4 y seguir los pasos 3 a 5 de la sección 2.

GitHub no guarda los secretos ni la base real. Para recuperar históricos, restaurar un respaldo por separado. Consultar nuevamente la fuente no garantiza recuperar todos los cambios ocurridos mientras los recolectores estuvieron apagados.

## 4. Qué claves y parámetros se necesitan

Usar [.env.example](.env.example) como plantilla. Los valores reales van en `.env` o en el almacén de secretos del servidor, nunca en GitHub.

### Aplicación y conector actual

| Variable | Uso | Obtención o renovación |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Clave criptográfica de Django | Generarla por entorno; producción exige al menos 50 caracteres. Conservarla al restaurar si se desea mantener la validez de sesiones |
| `API_KEY` | Acceso a API de datos con `X-API-Key` | Generarla por entorno; actualizar consumidores al rotarla |
| `LINK_API_BASE_URL` | URL de origen | Dirección facilitada por el administrador de Link |
| `LINK_API_TOKEN` | Lecturas principales de Link | Token de servicio autorizado; renovar si fue revocado |
| `LINK_JWT_TOKEN` | Desglose de asignaciones y detalles adicionales de survey partner | JWT Bearer vigente obtenido mediante el acceso autorizado a Link; renovar al caducar |
| `DJANGO_DEBUG` | Modo de ejecución y base interna | `True`: SQLite local; `False`: PostgreSQL remoto |
| `DJANGO_ALLOWED_HOSTS` | Hosts aceptados | Local: `localhost,127.0.0.1`; producción: dominios explícitos |

Generar las dos claves propias:

```powershell
.\venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
.\venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copiar el primer resultado a `DJANGO_SECRET_KEY` y el segundo a `API_KEY` en `.env`. No compartir los resultados. Si el JWT falta o caduca, pueden faltar desgloses y detalles aunque las lecturas principales sigan funcionando.

### Base interna del dashboard

SQLite local usa `db.sqlite3` sin usuario ni contraseña. Para PostgreSQL remoto, solicitar al administrador:

| Variable | Valor esperado |
| --- | --- |
| `DB_ENGINE` | `django.db.backends.postgresql` |
| `DB_HOSTNAME` | Host remoto accesible; producción rechaza `localhost`, `127.0.0.1` y `::1` |
| `DB_PORT` | Puerto, normalmente `5432` |
| `DB_NAME` | Base dedicada al dashboard |
| `DB_USERNAME` | Usuario con permisos sobre tablas internas y migraciones |
| `DB_PASSWORD` | Contraseña de ese usuario |
| `DB_SSLMODE` | `require`, `verify-ca` o `verify-full`; configurar certificados cuando se verifique la identidad del servidor |

Aunque algunos comentarios mencionan RDS, el backend es PostgreSQL y puede apuntar a otro PostgreSQL remoto con la conectividad necesaria. Otros motores como base interna requieren cambios en `config/settings.py` y sus controladores. No hay una variable `DATABASE_URL` implementada.

### Opcionales

- Email: `AWS_SES_REGION_NAME`, `DEFAULT_FROM_EMAIL`, `PERFORMANCE_ALERT_EMAILS` y permisos AWS para SES mediante rol o credenciales administradas fuera del repositorio.
- Telegram: `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` autorizados.
- HTTPS: dominio, certificados, `DJANGO_CSRF_TRUSTED_ORIGINS` y las variables de seguridad indicadas en `.env.example`, según el despliegue.
- GitHub: sesión o credencial Git con acceso al repositorio. No utiliza los tokens de Link ni las contraseñas de las bases.

## 5. Conectar los datos de otra empresa: paso a paso

Las variables `SOURCE_*` que se proponen aquí **no están implementadas**; deben añadirse con el adaptador nuevo.

1. **Definir los indicadores.** Acordar fórmulas, unidades, zona horaria, frecuencia y tratamiento de datos faltantes.
2. **Identificar la fuente.** Elegir API, vistas SQL de lectura o exportaciones. Pedir esquema documentado y muestras anonimizadas.
3. **Solicitar el acceso.** Para API: URL, token o cliente OAuth, permisos, caducidad, paginación y límites. Para SQL: motor y versión, host, puerto, base/esquema, usuario de solo lectura, contraseña, tablas/vistas, TLS y VPN/firewall. Para archivos: formato, ubicación y entrega autorizada.
4. **Separar las conexiones.** Mantener `DB_*` para la base del dashboard. Definir `SOURCE_DB_HOST`, `SOURCE_DB_PORT`, `SOURCE_DB_NAME`, `SOURCE_DB_USER`, `SOURCE_DB_PASSWORD` o `SOURCE_API_URL` y `SOURCE_API_TOKEN`. Añadir nombres sin secretos a `.env.example` y leerlos desde el adaptador.
5. **Implementar el adaptador en `intake/`.** Instalar y fijar el controlador necesario. Añadir lecturas acotadas, paginación, tiempos de espera y manejo de errores. El usuario de la fuente empresarial debe tener solo lectura; el usuario de la base interna sí necesita guardar datos.
6. **Normalizar los registros.** Mapear identificadores estables, entidades, fechas, estados e importes. Si el negocio no usa encuestas, crear modelos y migraciones adecuados en lugar de forzar otros datos en los modelos actuales.
7. **Crear un comando de sincronización.** Importar a la base interna, evitar duplicados y permitir reintentos. Definir cómo manejar correcciones, bajas, históricos y cortes de conexión.
8. **Adaptar indicadores y pantalla.** Revisar `dashboard/metrics.py`, `dashboard/views.py`, `dashboard/page.html` y `rules_engine/`. Confirmar los umbrales y documentar las fórmulas con la empresa.
9. **Validar una muestra pequeña.** Comparar totales con el origen. Probar duplicados, faltantes, claves vencidas y reintentos. Los comandos de Link no sirven para una fuente distinta hasta adaptar sus implementaciones.
10. **Activar la operación.** Importación inicial controlada, comprobación de indicadores y luego sincronización periódica. Establecer respaldos, alertas de datos atrasados, rotación de claves y recuperación.

### Ficha de la nueva empresa: completar antes de programar

Copiar esta ficha y completar lo disponible. Registrar referencias a secretos,
nunca contraseñas o tokens reales dentro de esta ficha versionada.

```text
Empresa / nombre del dashboard:
Usuarios y quién autoriza el acceso:
Objetivo y decisiones que debe ayudar a tomar:
Fuente: API / motor SQL / archivos:
Documentación del esquema y muestra anonimizada:
Conectividad: VPN, firewall, TLS, dominios autorizados:
Nombres de las variables de credenciales (sin valores):
Entidades, identificadores únicos y relaciones:
Campo de fecha, zona horaria y frecuencia de actualización:
Histórico a importar y tratamiento de correcciones/bajas:
Indicadores: nombre, fórmula, unidad, filtros y total de referencia:
Tratamiento de nulos, duplicados, ceros y monedas:
Gráficos, tablas, filtros y navegación necesarios:
Umbrales y reglas aprobados por esta empresa:
Marca: nombre, colores, idioma y formato de números:
Entorno: local / servidor, base interna y acceso de usuarios:
Alertas: canales y destinatarios autorizados:
Criterios de aceptación y responsable de validación:
```

### Mapa de cambios para la IA

| Área | Archivos que debe revisar | Resultado esperado |
| --- | --- | --- |
| Acceso a la fuente | `intake/link_client.py`, nuevo adaptador en `intake/`, `requirements.txt` | Conector de lectura adecuado al nuevo origen |
| Configuración | `config/settings.py`, `.env.example` | Variables explícitas, sin secretos ni conexiones heredadas activas |
| Almacenamiento | `intake/models.py`, `intake/migrations/` | Modelos del nuevo dominio y migraciones que preserven los datos necesarios |
| Sincronización | `orchestrator/management/commands/`, `orchestrator/models.py` | Importación inicial, actualización incremental, reintentos y estado de ejecución |
| Indicadores | `dashboard/metrics.py`, agregaciones en `orchestrator/` | Fórmulas y períodos acordados, con trazabilidad al origen |
| Contrato API | `dashboard/views.py`, `dashboard/serializers.py`, `dashboard/urls.py` | Respuestas coherentes con los filtros y las visualizaciones nuevas |
| Interacción y marca | `dashboard/page.html` | Dashboard adaptable a escritorio y móvil, con filtros, gráficos y estados de carga/error/vacío |
| Reglas y recomendaciones | `rules_engine/`, `audit/` | Reglas confirmadas por la nueva empresa; recomendaciones consultivas |
| Acceso de usuarios | `dashboard/permissions.py`, `dashboard/views.py`, configuración del servidor | Autenticación y autorización verificadas antes del acceso externo |
| Operación | `watchdog_pollers.ps1`, `deploy/`, `config/health.py` | Arranque y parada documentados para los comandos de la nueva integración |
| Verificación y documentación | Pruebas de las apps, `docs/INDICATORS.md`, este README | Cálculos comprobados, instrucciones actualizadas y limitaciones claras |

Buscar también referencias heredadas a Link, nombres de clientes/proveedores,
estados de encuestas, columnas, rutas, destinatarios y umbrales. Por ejemplo,
`rules_engine/rules_config.py` contiene reglas del caso original: no asumir que
un piso de margen o una exclusión anterior aplica a la empresa nueva.

### Instrucción lista para entregar a otra IA de programación

```text
Adapta este repositorio para la empresa descrita en la ficha adjunta.
Lee README.md, las instrucciones del repositorio y los archivos del mapa de
cambios. Inspecciona el código real antes de decidir qué reutilizar.

1. Identifica información faltante que afecte las fórmulas, acceso o esquema.
   Pide solo lo necesario y avanza en las tareas independientes. No inventes
   campos, indicadores ni reglas de negocio. No pidas secretos en el chat:
   documenta los nombres de variables y su configuración local segura.
2. Conserva la estructura reutilizable y sustituye las dependencias del caso
   Link que no correspondan. Usa una instalación y base separadas. No copies
   datos ni credenciales de la empresa anterior a la nueva configuración.
3. Implementa el conector de solo lectura y la normalización. Mantén separadas
   fuente empresarial y base interna. Crea migraciones y sincronización que
   eviten duplicados y permitan reintentar. Documenta cómo importar históricos.
4. Implementa las fórmulas acordadas y su contrato API. Para cada indicador,
   documenta origen, cálculo, unidad, período, filtros y tratamiento de nulos.
   Actualiza las agregaciones cuando cambien esas fórmulas.
5. Entrega un dashboard interactivo con filtros coherentes en todas las
   secciones, gráficos y tablas útiles, diseño adaptable y estados de carga,
   error, falta de datos y datos atrasados. Indica última actualización,
   unidades y período. No presentes datos de demostración como datos reales.
6. Revisa reglas, alertas, recomendaciones y accesos. No escribas cambios en
   la fuente empresarial. No expongas la página sin autenticación apropiada.
   Mantén manual el arranque de la PC salvo una petición expresa del usuario.
7. Prueba con datos sintéticos o anonimizados. Contrasta indicadores contra
   totales de referencia, prueba filtros, duplicados y fallos de conexión.
   Ejecuta check, test y makemigrations --check --dry-run. Abre el dashboard
   y comprueba visualmente escritorio y móvil cuando dispongas de navegador.
8. Actualiza README.md, .env.example y docs/INDICATORS.md con los comandos
   reales. Entrega archivos, resumen de cambios, verificaciones realizadas
   y pendientes que dependan de accesos o de validación empresarial.
```

### Cuándo considerar terminada la adaptación

- Una instalación limpia arranca siguiendo únicamente el README actualizado.
- Los filtros modifican todos los indicadores y gráficos correspondientes;
  la pantalla distingue cero, dato faltante y error de conexión.
- Las fórmulas coinciden con muestras y totales de referencia acordados.
- Repetir una sincronización no duplica datos y una falla no borra históricos.
- Se revisó la pantalla en escritorio y móvil, o se indica expresamente qué
  verificación visual quedó pendiente.
- No se reutilizan secretos, datos reales, destinatarios ni reglas específicas
  de la empresa anterior sin autorización para el nuevo caso.
- Las pruebas y verificaciones pasan; se documentan límites del origen,
  frecuencia, arranque, parada, respaldos y renovación de claves.

Esta guía permite encargar una adaptación concreta a otra IA; la facilidad del
cambio dependerá del esquema de origen y de los indicadores solicitados.
Actualmente la generalización es un procedimiento documentado, no un selector
automático de empresa ni un catálogo de conectores ya implementados.

## 6. Problemas frecuentes

| Síntoma | Qué revisar |
| --- | --- |
| No abre el dashboard | Servidor activo, puerto y errores de consola |
| Abre pero no actualiza | Recolectores, tokens vigentes y `/health/worker/` |
| Instalación nueva vacía | Primera importación o restauración de respaldo |
| API del dashboard devuelve 403 | Header `X-API-Key` correcto |
| Origen devuelve 401/403 | Token, JWT, caducidad y permisos |
| Faltan desgloses o detalles de precio | JWT y sincronización adicional |
| PostgreSQL no conecta | Host, puerto, VPN/firewall, TLS, usuario y `DB_*` |
| SQLite está bloqueada | Recolectores duplicados u otros procesos escribiendo |
| Faltan decisiones antiguas | La lista filtra los últimos 30 días; consultar con `show_all=true` |

## 7. Despliegue en servidor

Los scripts Linux actuales usan `/opt/eros-allocator` y `/etc/eros-allocator/eros.env`. Gunicorn escucha en `127.0.0.1:8000` detrás de Nginx.

Consultar [deploy/README.md](deploy/README.md), [docs/EC2_DEPLOYMENT.md](docs/EC2_DEPLOYMENT.md) y [docs/AWS_REQUIREMENTS.md](docs/AWS_REQUIREMENTS.md). Revisar las referencias a AWS si se elige otro proveedor; ajustar rutas si cambia el nombre de la instalación.

Los scripts Linux pueden habilitar la API y Nginx al arrancar el servidor. Eso es independiente del inicio desactivado en Windows. Los workers Linux se inician manualmente, pero usan `Restart=always`: para detenerlos se necesita `systemctl stop`, incluso cuando se indicó `--hours`.

## 8. Preparar y subir a GitHub

Repositorio: <https://github.com/hectorpauta-lemont/dashboardpredictivo>.
Cuenta prevista: `hectorpauta-lemont`, asociada a `mscdanielpauta@gmail.com`. El correo del autor de Git no selecciona la cuenta de autenticación.

### Paso 1: verificar cuenta, destino y rama

```powershell
gh auth status
gh api user --jq '{login: .login, email: .email}'
git credential-manager github list
git config --get user.email
git remote -v
git branch --show-current
```

Si hace falta renovar acceso, usar `gh auth login --hostname github.com` con la cuenta correcta. No guardar el token de GitHub dentro del proyecto.

### Paso 2: ejecutar comprobaciones

```powershell
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py test --noinput
.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run
git diff --check
```

### Paso 3: revisar y preparar el commit

```powershell
git status --short
git diff
git add README.md .env.example .gitignore watchdog_pollers.ps1
```

Añadir por sus rutas los cambios de código, pruebas, migraciones y documentación que se desean incluir. Revisar cada archivo nuevo antes de añadirlo.

```powershell
git diff --cached --stat
git diff --cached
git check-ignore .env db.sqlite3 deploy/local-backups/EROS-Allocator-Watchdog.vbs.disabled
git commit -m "docs: document manual startup and company data integration"
```

`.gitignore` excluye secretos locales, bases, logs, respaldos, exportaciones y borradores internos. No elimina secretos ya versionados: revisar también el historial. Si aparece una clave expuesta, revocarla y limpiar el historial antes de publicar.

### Paso 4: enviar la rama revisada

```powershell
git push -u origin HEAD
```

Publica la rama actual. Revisar el nombre y abrir un PR según [CONTRIBUTING.md](CONTRIBUTING.md). Los scripts de despliegue esperan `main`; publicar una rama de trabajo no despliega por sí solo el servidor. No resolver un rechazo usando `--force`.

## 9. Mapa del proyecto

| Carpeta | Responsabilidad |
| --- | --- |
| `config/` | Configuración, rutas, estado y logs |
| `intake/` | Conector y modelos del origen |
| `orchestrator/` | Recolectores, sincronizaciones y agregaciones |
| `rules_engine/` | Reglas de elegibilidad, exclusiones y margen |
| `dashboard/` | API de indicadores y página HTML |
| `audit/` | Registro de evaluaciones y decisiones |
| `deploy/` | Despliegue Linux |

Fórmulas: [docs/INDICATORS.md](docs/INDICATORS.md).
