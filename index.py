"""Vercel entrypoint: safely disable the web app until production is configured.

SQLite is deliberately not used on serverless functions, because writes are not
persistent there. A misconfigured deployment must not start a login or order flow.
"""
import os


def production_ready():
    if not os.getenv('VERCEL'):
        return True
    database_url = os.getenv('DATABASE_URL', '')
    secret = os.getenv('FORM_SECRET', '')
    return (
        database_url.startswith(('postgresql://', 'postgres://', 'postgresql+psycopg://'))
        and len(secret) >= 32
        and secret != 'local-dev-only-change-in-production'
    )


if production_ready():
    from app.main import app
else:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    app = FastAPI(title='Team Indigo - mantenimiento', docs_url=None, redoc_url=None)
    _PAGE = (
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="robots" content="noindex,nofollow">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Team Indigo | Configuracion en curso</title></head>'
        '<body style="font:16px system-ui;text-align:center;padding:3rem;'
        'background:#14122d;color:#eee"><h1>Team Indigo</h1>'
        '<p>Estamos preparando el acceso seguro a la plataforma.</p></body></html>'
    )

    @app.api_route('/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'])
    async def maintenance(path: str):
        return HTMLResponse(
            _PAGE,
            status_code=503,
            headers={'Cache-Control': 'no-store', 'Retry-After': '300',
                     'X-Robots-Tag': 'noindex, nofollow'},
        )
