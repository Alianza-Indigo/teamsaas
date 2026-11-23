# Team Índigo SaaS — operación comercial multitenant

Plataforma web para que **distintas empresas** administren sus catálogos, distribuidores, pedidos, cobros **confirmados manualmente**, renovaciones y comisiones. **Team Índigo** es la primera organización y tiene un catálogo inicial sin productos activos (Claude 6.25x, ChatGPT y Google Workspace).

No es un servidor que comparta cuentas de terceros ni un distribuidor oficial de las marcas incluidas. Los productos deben configurarse y activarse sólo si su comercialización está autorizada.

## Arquitectura

- **FastAPI + Jinja2:** interfaz web adaptable a teléfonos y escritorio, con rutas de administrador, distribuidor y plataforma SaaS.
- **PostgreSQL (Neon) / SQLAlchemy:** datos relacionales, restricciones de integridad y relaciones `tenant_id` compuestas entre pedidos, clientes, productos, distribuidores, pagos y comisiones.
- **`app/domain/orders.py`:** registrar solicitudes y transición de estados con roles y reglas.
- **`app/domain/payments.py`:** pago externo verificado, registro único por periodo y comisión en la **misma transacción**; reintentos idénticos no duplican operaciones.
- **`app/domain/renewals.py`:** periodos mensuales y anuales anclados al día original, incluso cuando el pago es tardío o el mes es corto.
- **`app/domain/commissions.py`:** estados `PENDIENTE → APROBADA → PAGADA`, comprobante de liquidación obligatorio.
- **`app/domain/billing.py`:** mensualidades del SaaS, distintas de los cobros que cada empresa recibe de sus compradores.
- **`app/saas.py`:** alta de empresas, control de suscripciones, límites y permisos de espacio.
- **CRUD solamente para catálogos, usuarios, plan y preferencias de marca.** Las rutas HTTP invocan motores de negocio para movimientos comerciales.

## Roles y aislamiento

| Rol | Alcance | Funciones |
| --- | --- | --- |
| Superadministrador | Plataforma | Crea/suspende empresas, cambia planes, registra mensualidades SaaS; **no ve las ventas privadas de empresas** |
| Administrador | Su empresa | Catálogo, distribuidores, pedidos, pagos, comisiones y exportaciones de su organización |
| Distribuidor | Sus ventas | Captura pedidos, consulta clientes y comisiones, comparte enlace público `/d/CODIGO` |
| Cliente (sin sesión) | Enlace de un distribuidor | Solicita productos activados para esa empresa; **no paga en la web** |

Los datos comerciales siempre se consultan por empresa. Los identificadores cruzados entre organizaciones son rechazados. Las pruebas incluyen intentos de acceso entre empresas, CSRF y suplantación de distribuidor.

## Planes SaaS iniciales (configuración editable)

| Plan | Precio de referencia | Distribuidores | Productos | Pedidos/mes |
| --- | --- | ---: | ---: | ---: |
| Emprendedor | 499 MXN/mes | 3 | 20 | 100 |
| Empresa | 1,499 MXN/mes | 25 | 200 | 1,000 |
| Marca blanca | 3,999 MXN/mes | 250 | 1,000 | 10,000 |

Estos precios son **propuestas configuradas**, no ventas realizadas. El indicador de MRR muestra contratos activos y no incluye pruebas gratuitas; **no equivale a dinero cobrado**. La marca blanca permite configurar nombre y paleta; el aprovisionamiento automático de dominios personalizados todavía no está integrado.

## Instalación local

Requiere **Python 3.12 o superior**, una base SQLite para desarrollo o PostgreSQL 15+ para producción.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=sqlite:///./teamindigo.db
export AUTO_CREATE_TABLES=false
export COOKIE_SECURE=false
export FORM_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export BOOTSTRAP_PLATFORM_EMAIL=plataforma@ejemplo.com
export BOOTSTRAP_PLATFORM_PASSWORD='TU_CLAVE_SUPERADMIN_DE_12_CARACTERES_O_MAS'
export BOOTSTRAP_ADMIN_EMAIL=admin@ejemplo.com
export BOOTSTRAP_ADMIN_PASSWORD='TU_CLAVE_EMPRESA_DE_12_CARACTERES_O_MAS'
python -m app.bootstrap
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- Entra a `http://127.0.0.1:8000/ingresar?espacio=plataforma` como superadministrador.
- Entra a `http://127.0.0.1:8000/ingresar?espacio=team-indigo` como administrador de Team Índigo.
- El bootstrap crea los tres planes y Team Índigo y deja todo su catálogo **desactivado**. No reemplaza contraseñas de cuentas existentes.
- El registro de cada nueva empresa tiene una **prueba de 14 días** y una contraseña provisional que sólo se muestra una vez.
- Se deben definir precio, periodicidad y comisión por producto para habilitar solicitudes.

## Flujo comercial

1. El administrador activa productos, crea distribuidores y asigna el enlace individual de cada uno.
2. El distribuidor o su cliente solicita un producto. El pedido **congela** la tarifa, periodicidad, moneda y porcentaje de comisión de ese momento.
3. El administrador confirma el pedido y, **después de verificar que el pago llegó por un método externo**, lo registra con una referencia.
4. La operación crea exactamente un pago y una comisión vinculados. Una repetición del mismo comprobante no genera una segunda comisión.
5. El servicio pasa por los estados `PENDIENTE → CONFIRMADO → PAGADO → EN_PROCESO/ACTIVO`; también puede cancelarse. Una venta cancelada conserva todo el historial financiero.
6. Las renovaciones se registran en el periodo pendiente: generan una nueva comisión sin modificar las anteriores ni saltarse periodos vencidos.
7. El administrador aprueba la comisión y registra su liquidación con referencia bancaria externa.

**No realiza cargos, transferencias, facturación fiscal ni reembolsos automáticos.** Las confirmaciones son administrativas; requieren que un humano haya verificado los fondos. Si se requiere automatizar, hay que integrar PSP/webhooks verificados y contabilidad de reembolsos aparte de la lógica ya implementada.

## Pruebas automatizadas

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Incluyen aislamiento multitenant, limitación de permisos, creación de empresas, suspensión, pedidos, red de comisiones, confirmación de pagos, idempotencia, renovación tardía con día ancla, cargos SaaS, cambio de contraseña, y protección CSRF. El importador legado se proporciona como herramienta separada y debe probarse con una copia de la base real antes de utilizarse en producción.

## Migración desde Pedidos Índigo

El script `scripts/import_legacy_sqlite.py` importa **una sola vez** una base SQLite del sistema de pedidos anterior hacia la empresa `team-indigo` del nuevo SaaS, conservando usuarios, productos, pedidos, clientes, pagos, comisiones y auditoría. La nueva base **debe inicializarse con bootstrap** y no tener actividad comercial antes de importar. Ejecútalo sólo después de una copia de seguridad completa y valida los totales al concluir:

```bash
python -m scripts.import_legacy_sqlite --legacy /ruta/absoluta/pedidos_indigo.db
```

Si partes de este paquete, no tienes que importar la base de ejemplo: **ningún archivo `.db` real se distribuye**.

## Vercel + Neon (despliegue pendiente de credenciales)

1. Crea un proyecto en Vercel y una base PostgreSQL en Neon bajo la organización correcta.
2. Inicializa el esquema una sola vez en un entorno administrativo seguro con `python -m app.bootstrap` usando la cadena `DATABASE_URL` de Neon.
3. Mantén la URL de base, secretos y contraseñas **fuera del repositorio**. En Vercel configura `DATABASE_URL`, `FORM_SECRET` persistente, `COOKIE_SECURE=true`, `AUTO_CREATE_TABLES=false`.
4. Conecta el código a Git privado; el punto de entrada `index.py` expone la app FastAPI. No habilites `AUTO_CREATE_TABLES` en producción.
5. Verifica despliegue, HTTPS, CSS, inicio de sesión por empresa, operaciones de venta, aislamiento de organizaciones, correo/sesiones y respaldos. Aplica evoluciones del esquema mediante migraciones controladas.

**Estado de entrega:** código y pruebas locales; **no se ha realizado un despliegue en producción**, no se han configurado cuentas de Neon, Stripe ni DNS de `teamindigo.xyz`.

## Seguridad y operaciones

- Contraseñas con PBKDF2, sesiones revocables almacenadas por hash, cookies HttpOnly/SameSite, CSRF, cabeceras de seguridad y formulario público con firma temporal y honeypot.
- Para producción agrega protección contra abuso e intentos de fuerza bruta, vigilancia de errores, copia de seguridad probada y políticas de privacidad/contratos.
- Las relaciones por tenant cuentan con claves foráneas compuestas y los endpoints administrativos filtran por organización. PostgreSQL admite bloqueos de fila al confirmar pagos; SQLite sólo debe usarse para pruebas/desarrollo.
- Validar licencia/autorización comercial de cada proveedor antes de activar productos. Ninguna función crea, comparte o entrega credenciales de proveedores de IA.
