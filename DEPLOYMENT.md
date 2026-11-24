# Publicacion de teamsaas

## Recursos registrados

- Proyecto Vercel: `teamsaas`
- ID: `prj_l1D7FSeRvIh4ravhEyFMpM2v1iXV`
- Equipo Vercel: `cepcco-asesores-projects`
- Framework: FastAPI
- GitHub previsto: repositorio privado `Alianza-Indigo/teamsaas`, rama `main`.

El proyecto de Vercel fue creado, pero no esta vinculado a GitHub ni desplegado.

## Subir a GitHub

1. Crear **un repositorio privado vacio** `teamsaas` bajo la cuenta `Alianza-Indigo`. No inicializar con README, .gitignore ni licencia.
2. Desde la carpeta de este proyecto, ejecutar:

```bash
git init -b main
git add .
git commit -m "Initial Team Indigo SaaS application"
git remote add origin https://github.com/Alianza-Indigo/teamsaas.git
git push -u origin main
```

Si se utiliza el archivo `teamsaas-inicial.bundle` entregado con el proyecto, se puede restaurar el commit inicial sin repetir `git add`.

## Vincular a Vercel

En el proyecto **teamsaas** del equipo `cepcco-asesores-projects`, usar Project Settings > Git > Connect Git Repository y elegir `Alianza-Indigo/teamsaas`, rama principal `main`. No crear un segundo proyecto Vercel.

Antes de habilitar un despliegue funcional:
- Aprovisionar Neon PostgreSQL independiente para este SaaS y definir `DATABASE_URL` en Vercel.
- Agregar `FORM_SECRET` largo, aleatorio y persistente en Vercel; `COOKIE_SECURE=true` y `AUTO_CREATE_TABLES=false`.
- Ejecutar **una sola vez** `python -m app.bootstrap` desde un entorno administrativo seguro con los secretos `BOOTSTRAP_*` indicados en `.env.example`; no ejecutar bootstrap automaticamente en requests serverless.
- No subir secretos ni bases de datos locales. No activar productos sin validar las condiciones de reventa de terceros.
- Probar login, permisos multiempresa, captura de pedidos, verificacion manual de pagos y comisiones en el dominio desplegado.

No se incluyen Stripe, base de datos provisionada, dominio personalizado ni configuracion automatica de usuarios.


## Proteccion temporal mientras faltan secretos o base de datos

- La portada `/` muestra `public/index.html` desde la CDN por medio de `vercel.json`. No invoca Python por cada visita.
- `/favicon.svg` y `/static/styles.css` son recursos estaticos servidos desde `public/`.
- Las rutas dinamicas responden con HTTP 503 de mantenimiento sin importar `app.main` ni abrir SQLite cuando falta PostgreSQL o `FORM_SECRET`.
- **Para habilitar la app real**: provisionar Neon, establecer `DATABASE_URL` de PostgreSQL y `FORM_SECRET` fuerte y persistente; inicializar DB con `python -m app.bootstrap` en un entorno administrativo seguro; confirmar su acceso; **entonces eliminar la regla de reescritura de la portada en `vercel.json`** y publicar una nueva version.
- No desactivar las comprobaciones de arranque ni sustituir PostgreSQL por SQLite efimera en Vercel.
