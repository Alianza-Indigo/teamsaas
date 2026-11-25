import csv
import hashlib
import hmac
import io
import os
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from .db import Base, SessionLocal, engine, get_db
from .models import Audit, Commission, Customer, Order, Payment, Product, Session as LoginSession, User, Tenant, TenantSubscription, TenantCharge, SaasPlan, now
from .security import SESSION_COOKIE, clear_session, create_session, hash_password, read_session, verify_password
from .saas import seed_plans, create_tenant, access_enabled, ensure_quota, get_plan, get_subscription
from .domain.common import INTERVALS, STATUSES, money, normalize_email, valid_product
from .domain.orders import make_order, change_order_status
from .domain.payments import record_payment
from .domain.commissions import change_commission_status
from .domain.billing import record_saas_charge, monthly_recurring_revenue

ROOT = os.path.dirname(__file__)
app = FastAPI(title='Team Indigo SaaS', docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=os.path.join(ROOT, 'static')), name='static')
templates = Jinja2Templates(directory=os.path.join(ROOT, 'templates'))
templates.env.filters['money'] = money
COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'false').lower() in ('1', 'true', 'yes')
FORM_SECRET = os.getenv('FORM_SECRET', 'local-dev-only-change-in-production')


from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    if COOKIE_SECURE and FORM_SECRET == 'local-dev-only-change-in-production':
        raise RuntimeError('FORM_SECRET debe configurarse para producción.')
    if os.getenv('SYNC_TEAM_INDIGO_ADMIN_EMAIL', 'false').lower() == 'true':
        from .bootstrap import update_team_indigo_admin_email
        update_team_indigo_admin_email()
    if os.getenv('BOOTSTRAP_ON_START', 'false').lower() == 'true':
        from .bootstrap import bootstrap
        bootstrap()
    elif os.getenv('AUTO_CREATE_TABLES', 'false').lower() == 'true':
        Base.metadata.create_all(bind=engine)
    yield

app.router.lifespan_context = lifespan


@app.middleware('http')
async def auth_middleware(request, call_next):
    if os.getenv('SYNC_TEAM_INDIGO_ADMIN_EMAIL', 'false').lower() == 'true':
        # One-time operational sync on a real request, including serverless
        # runtimes that do not invoke the FastAPI lifespan callback.
        from .bootstrap import update_team_indigo_admin_email
        update_team_indigo_admin_email()
    request.state.user = None
    request.state.session = None
    request.state.tenant = None
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        with SessionLocal() as db:
            request.state.user, request.state.session = read_session(db, cookie)
            if request.state.user and request.state.user.tenant_id:
                if access_enabled(db, request.state.user.tenant_id):
                    request.state.tenant = db.get(Tenant, request.state.user.tenant_id)
                else:
                    request.state.user = None
                    request.state.session = None
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; form-action 'self'; base-uri 'self'; frame-ancestors 'none'"
    return response


def page(request, template, status=200, **context):
    data = {'request': request, 'user': request.state.user, 'csrf': request.state.session.csrf_token if request.state.session else '', 'nav': context.pop('nav', ''), 'error': context.pop('error', None), 'notice': context.pop('notice', None), 'public_layout': context.pop('public_layout', False), 'tenant': request.state.tenant, 'now': now(), **context}
    return templates.TemplateResponse(request, template, data, status_code=status)


def redirect(url):
    return RedirectResponse(url, status_code=303)


def login_required(request):
    user = request.state.user
    if not user:
        raise HTTPException(status_code=303, headers={'Location': '/ingresar'})
    if user.role == 'superadmin' and not request.url.path.startswith('/plataforma') and request.url.path not in ('/perfil', '/salir'):
        raise HTTPException(status_code=403, detail='No tienes acceso a una empresa.')
    if user.must_change_password and request.url.path not in ('/perfil', '/salir'):
        raise HTTPException(status_code=303, headers={'Location': '/perfil'})
    return user


def admin_required(request):
    user = login_required(request)
    if user.role != 'admin' or not user.tenant_id:
        raise HTTPException(status_code=403, detail='No autorizado.')
    return user


def verify_csrf(request, form):
    session = request.state.session
    if not session or not hmac.compare_digest(session.csrf_token, str(form.get('csrf', ''))):
        raise HTTPException(status_code=403, detail='Solicitud inválida. Actualiza la página.')


def get_form_value(form, key, max_len=2000):
    value = str(form.get(key, '')).strip()
    if len(value) > max_len:
        raise ValueError('El texto excede el tamaño permitido.')
    return value


def signed_public_form(code):
    timestamp = str(int(time.time()))
    signature = hmac.new(FORM_SECRET.encode(), f'{code}:{timestamp}'.encode(), hashlib.sha256).hexdigest()
    return f'{timestamp}.{signature}'


def verify_public_form(code, token):
    try:
        timestamp, signature = token.split('.', 1)
        expected = hmac.new(FORM_SECRET.encode(), f'{code}:{timestamp}'.encode(), hashlib.sha256).hexdigest()
        age = int(time.time()) - int(timestamp)
        return 3 <= age <= 1800 and hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        return False


@app.get('/')
def index(request: Request):
    if request.state.user and request.state.user.role == 'superadmin':
        return redirect('/plataforma')
    return redirect('/panel' if request.state.user else '/ingresar')

def platform_required(request):
    user = login_required(request)
    if user.role != 'superadmin' or user.tenant_id is not None:
        raise HTTPException(status_code=403, detail='Acceso restringido a administración de plataforma.')
    return user


@app.get('/ingresar')
def login_page(request: Request):
    if request.state.user:
        return redirect('/plataforma' if request.state.user.role == 'superadmin' else '/panel')
    nonce = secrets.token_urlsafe(28)
    response = page(request, 'login.html', login_csrf=nonce, workspace=request.query_params.get('espacio', 'team-indigo')[:63])
    response.set_cookie('login_csrf', nonce, httponly=True, secure=COOKIE_SECURE, samesite='strict', max_age=1800)
    return response



@app.get('/recuperar-contrasena')
def password_recovery_page(request: Request):
    nonce = secrets.token_urlsafe(28)
    response = page(request, 'forgot_password.html', login_csrf=nonce,
                    workspace=request.query_params.get('espacio', 'team-indigo')[:63])
    response.set_cookie('login_csrf', nonce, httponly=True, secure=COOKIE_SECURE,
                        samesite='strict', max_age=1800)
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.post('/recuperar-contrasena')
async def password_recovery_request(request: Request, db: Session = Depends(get_db)):
    from .password_reset import mail_configured, make_reset_token, send_reset_email
    form = await request.form()
    csrf_token = request.cookies.get('login_csrf', '')
    if not csrf_token or not hmac.compare_digest(csrf_token, str(form.get('login_csrf', ''))):
        raise HTTPException(status_code=403, detail='Formulario vencido.')
    workspace = get_form_value(form, 'workspace', 63).lower()
    try:
        email = normalize_email(get_form_value(form, 'email', 254))
    except ValueError:
        email = ''
    if not mail_configured():
        return page(request, 'forgot_password.html', login_csrf=csrf_token, workspace=workspace,
                    error='El envío de correo todavía no está configurado. Contacta al administrador.', status=503)
    tenant = db.scalar(select(Tenant).where(Tenant.slug == workspace)) if workspace != 'plataforma' else None
    user = db.scalar(select(User).where(User.tenant_id == tenant.id, User.email == email,
                            User.active.is_(True))) if tenant and access_enabled(db, tenant.id) else None
    if user and user.role != 'superadmin':
        recent = db.scalar(select(Audit.id).where(
            Audit.actor_id == user.id, Audit.action == 'password.reset_requested',
            Audit.created_at >= now() - timedelta(minutes=5)).limit(1))
        if not recent:
            token = make_reset_token(user, FORM_SECRET)
            url = os.getenv('PUBLIC_BASE_URL', 'https://ezteams.xyz').rstrip('/') + '/restablecer-contrasena?token=' + token
            try:
                send_reset_email(user.email, url)
            except RuntimeError:
                # Do not disclose whether an address exists. The transport
                # module logs a sanitized failure reason for operations.
                pass
            else:
                db.add(Audit(tenant_id=user.tenant_id, actor_id=user.id,
                             action='password.reset_requested', entity_type='user', entity_id=user.id))
                db.commit()
    return page(request, 'forgot_password.html', login_csrf=csrf_token, workspace=workspace,
                notice='Solicitud recibida. Si la cuenta está registrada y el correo puede enviarse, recibirás un enlace de recuperación.')


@app.get('/restablecer-contrasena')
def password_reset_page(request: Request, token: str = '', db: Session = Depends(get_db)):
    from .password_reset import verify_reset_token
    uid = token.split('.', 1)[0] if len(token) <= 200 else ''
    user = db.get(User, uid) if len(uid) == 36 else None
    valid = bool(user and user.active and user.tenant_id is not None
                 and access_enabled(db, user.tenant_id)
                 and verify_reset_token(token, user, FORM_SECRET))
    nonce = secrets.token_urlsafe(28)
    response = page(request, 'reset_password.html', login_csrf=nonce,
                    token=token if valid else '', valid=valid, status=200 if valid else 400)
    response.set_cookie('login_csrf', nonce, httponly=True, secure=COOKIE_SECURE,
                        samesite='strict', max_age=1800)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.post('/restablecer-contrasena')
async def password_reset_submit(request: Request, db: Session = Depends(get_db)):
    from .password_reset import verify_reset_token
    form = await request.form()
    csrf_token = request.cookies.get('login_csrf', '')
    if not csrf_token or not hmac.compare_digest(csrf_token, str(form.get('login_csrf', ''))):
        raise HTTPException(status_code=403, detail='Formulario vencido.')
    token = get_form_value(form, 'token', 200)
    uid = token.split('.', 1)[0] if len(token) <= 200 else ''
    user = db.get(User, uid) if len(uid) == 36 else None
    if not user or not user.active or user.tenant_id is None or not access_enabled(db, user.tenant_id) or not verify_reset_token(token, user, FORM_SECRET):
        return page(request, 'reset_password.html', login_csrf=csrf_token, token='',
                    valid=False, error='Enlace vencido o utilizado.', status=400)
    password = get_form_value(form, 'password', 1000)
    if password != get_form_value(form, 'confirm', 1000):
        return page(request, 'reset_password.html', login_csrf=csrf_token, token=token,
                    valid=True, error='Las contraseñas no coinciden.', status=400)
    try:
        new_hash = hash_password(password)
    except ValueError as exc:
        return page(request, 'reset_password.html', login_csrf=csrf_token, token=token,
                    valid=True, error=str(exc), status=400)
    user.password_hash = new_hash
    user.must_change_password = False
    db.execute(LoginSession.__table__.delete().where(LoginSession.user_id == user.id))
    db.add(Audit(tenant_id=user.tenant_id, actor_id=user.id,
                 action='password.reset_completed', entity_type='user', entity_id=user.id))
    db.commit()
    return redirect('/ingresar?' + urlencode({'espacio': db.get(Tenant, user.tenant_id).slug, 'recuperada': '1'}))


@app.post('/ingresar')
async def login_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    token = request.cookies.get('login_csrf', '')
    if not token or not hmac.compare_digest(token, str(form.get('login_csrf', ''))):
        raise HTTPException(status_code=403, detail='Formulario vencido.')
    try:
        email = normalize_email(get_form_value(form, 'email', 254))
    except ValueError:
        email = ''
    password = get_form_value(form, 'password', 1000)
    workspace = get_form_value(form, 'workspace', 63).lower()
    tenant = db.scalar(select(Tenant).where(Tenant.slug == workspace)) if workspace != 'plataforma' else None
    if workspace == 'plataforma' and os.getenv('SUPERADMIN_EMAIL') and os.getenv('SUPERADMIN_PASSWORD'):
        configured_email = os.environ['SUPERADMIN_EMAIL'].strip().lower()
        configured_password = os.environ['SUPERADMIN_PASSWORD']
        credentials_ok = (
            hmac.compare_digest(email, configured_email)
            and hmac.compare_digest(password, configured_password)
        )
        user = db.scalar(select(User).where(User.tenant_id.is_(None), User.role == 'superadmin', User.active.is_(True))) if credentials_ok else None
    elif workspace == 'plataforma':
        # Legacy local bootstrap credentials; production uses SUPERADMIN_*.
        user = db.scalar(select(User).where(User.email == email, User.tenant_id.is_(None), User.role == 'superadmin', User.active.is_(True)))
        credentials_ok = bool(user and verify_password(password, user.password_hash))
    else:
        user = db.scalar(select(User).where(User.email == email, User.tenant_id == tenant.id, User.active.is_(True))) if tenant and access_enabled(db, tenant.id) else None
        credentials_ok = bool(user and verify_password(password, user.password_hash))
    if not user or not credentials_ok:
        return page(request, 'login.html', error='Correo o contraseña incorrectos.', login_csrf=token, workspace=workspace, status=401)
    raw = create_session(db, user)
    response = redirect('/perfil' if user.must_change_password else ('/plataforma' if user.role == 'superadmin' else '/panel'))
    response.set_cookie(SESSION_COOKIE, raw, httponly=True, secure=COOKIE_SECURE, samesite='lax', max_age=7 * 86400, path='/')
    response.delete_cookie('login_csrf')
    return response


@app.post('/salir')
async def logout(request: Request, db: Session = Depends(get_db)):
    login_required(request)
    form = await request.form()
    verify_csrf(request, form)
    clear_session(db, request.cookies.get(SESSION_COOKIE))
    response = redirect('/ingresar')
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get('/perfil')
def profile(request: Request):
    user = request.state.user
    if not user:
        return redirect('/ingresar')
    link = f'{str(request.base_url).rstrip("/")}/d/{user.referral_code}' if user.referral_code else None
    return page(request, 'profile.html', nav='perfil', referral_link=link)


@app.post('/perfil')
async def profile_post(request: Request, db: Session = Depends(get_db)):
    user = request.state.user
    if not user:
        return redirect('/ingresar')
    form = await request.form()
    verify_csrf(request, form)
    current = get_form_value(form, 'current', 1000)
    password = get_form_value(form, 'new', 1000)
    if not verify_password(current, user.password_hash):
        return page(request, 'profile.html', nav='perfil', error='La contraseña actual es incorrecta.', referral_link=f'{str(request.base_url).rstrip("/")}/d/{user.referral_code}' if user.referral_code else None)
    try:
        new_hash = hash_password(password)
    except ValueError as exc:
        return page(request, 'profile.html', nav='perfil', error=str(exc), referral_link=f'{str(request.base_url).rstrip("/")}/d/{user.referral_code}' if user.referral_code else None)
    person = db.get(User, user.id)
    person.password_hash = new_hash
    person.must_change_password = False
    db.execute(LoginSession.__table__.delete().where(LoginSession.user_id == user.id))
    db.add(Audit(tenant_id=user.tenant_id, actor_id=user.id, action='user.password_changed', entity_type='user', entity_id=user.id))
    db.commit()
    response = redirect('/ingresar')
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get('/panel')
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = login_required(request)
    scoped = [Order.tenant_id == user.tenant_id] + ([Order.distributor_id == user.id] if user.role != 'admin' else [])
    orders = db.scalars(select(Order).where(*scoped).order_by(Order.created_at.desc()).limit(6)).all()
    count = db.scalar(select(func.count()).select_from(Order).where(*scoped)) or 0
    paid = db.scalar(select(func.count()).select_from(Order).where(*scoped, Order.paid_at.is_not(None))) or 0
    pending = db.scalar(select(func.count()).select_from(Order).where(*scoped, Order.status.in_(['PENDIENTE', 'CONFIRMADO']))) or 0
    commission_filter = [Commission.tenant_id == user.tenant_id] + ([Commission.distributor_id == user.id] if user.role != 'admin' else [])
    commission_rows = db.scalars(select(Commission).where(*commission_filter)).all()
    sums = {}
    for item in commission_rows:
        sums.setdefault(item.currency, 0)
        sums[item.currency] += item.amount_cents if item.status != 'PAGADA' else 0
    return page(request, 'dashboard.html', nav='panel', orders=orders, count=count, paid=paid, pending=pending, sums=sums)


@app.get('/panel/pedidos')
def order_list(request: Request, db: Session = Depends(get_db)):
    user = login_required(request)
    scoped = [Order.tenant_id == user.tenant_id] + ([Order.distributor_id == user.id] if user.role != 'admin' else [])
    rows = db.execute(select(Order, Customer).join(Customer, Order.customer_id == Customer.id).where(*scoped).order_by(Order.created_at.desc()).limit(250)).all()
    return page(request, 'orders.html', nav='pedidos', rows=rows)


@app.get('/panel/pedidos/nuevo')
def order_new(request: Request, db: Session = Depends(get_db)):
    user = login_required(request)
    products = db.scalars(select(Product).where(Product.tenant_id == user.tenant_id, Product.active.is_(True)).order_by(Product.name)).all()
    products = [p for p in products if valid_product(p)]
    distributors = db.scalars(select(User).where(User.tenant_id == user.tenant_id, User.role == 'distribuidor', User.active.is_(True)).order_by(User.name)).all() if user.role == 'admin' else []
    return page(request, 'new_order.html', nav='pedidos', products=products, distributors=distributors)


@app.post('/panel/pedidos/nuevo')
async def order_create(request: Request, db: Session = Depends(get_db)):
    user = login_required(request)
    form = await request.form()
    verify_csrf(request, form)
    try:
        distributor_id = user.id
        if user.role == 'admin':
            distributor_id = get_form_value(form, 'distributor_id', 36) or user.id
            distributor = db.get(User, distributor_id)
            if not distributor or distributor.tenant_id != user.tenant_id or not distributor.active or (
                distributor.id != user.id and distributor.role != 'distribuidor'
            ):
                raise ValueError('Selecciona tu cartera o un distribuidor activo para registrar el pedido.')
        order = make_order(db, user.tenant_id, user.id, distributor_id, get_form_value(form, 'product_id', 36),
                           get_form_value(form, 'customer_name', 120), get_form_value(form, 'customer_email', 254),
                           get_form_value(form, 'phone', 50), get_form_value(form, 'quantity', 4),
                           get_form_value(form, 'note', 2000))
        return redirect('/panel/pedidos?creado=' + order.code)
    except (ValueError, TypeError) as exc:
        products = db.scalars(select(Product).where(Product.tenant_id == user.tenant_id, Product.active.is_(True)).order_by(Product.name)).all()
        return page(request, 'new_order.html', nav='pedidos', products=[p for p in products if valid_product(p)], distributors=db.scalars(select(User).where(User.tenant_id == user.tenant_id, User.role == 'distribuidor', User.active.is_(True)).order_by(User.name)).all() if user.role == 'admin' else [], error=str(exc), status=400)


@app.get('/panel/clientes')
def customers(request: Request, db: Session = Depends(get_db)):
    user = login_required(request)
    scoped = [Customer.tenant_id == user.tenant_id] + ([Customer.distributor_id == user.id] if user.role != 'admin' else [])
    rows = db.scalars(select(Customer).where(*scoped).order_by(Customer.created_at.desc()).limit(250)).all()
    return page(request, 'customers.html', nav='clientes', rows=rows)


@app.post('/admin/clientes')
async def admin_create_customer(request: Request, db: Session = Depends(get_db)):
    admin = admin_required(request)
    form = await request.form()
    verify_csrf(request, form)
    try:
        name = get_form_value(form, 'name', 120)
        email = normalize_email(get_form_value(form, 'email', 254))
        phone = get_form_value(form, 'phone', 50)
        if len(name) < 2:
            raise ValueError('El nombre del cliente debe contener al menos dos caracteres.')
        existing = db.scalar(select(Customer).where(
            Customer.tenant_id == admin.tenant_id,
            Customer.distributor_id == admin.id, Customer.email == email))
        if existing:
            raise ValueError('Ese cliente ya pertenece a tu cartera.')
        customer = Customer(tenant_id=admin.tenant_id, distributor_id=admin.id,
                            name=name, email=email, phone=phone)
        db.add(customer)
        db.flush()
        db.add(Audit(tenant_id=admin.tenant_id, actor_id=admin.id,
                     action='customer.created', entity_type='customer', entity_id=customer.id))
        db.commit()
        return redirect('/panel/clientes?creado=1')
    except ValueError as exc:
        rows = db.scalars(select(Customer).where(Customer.tenant_id == admin.tenant_id)
                          .order_by(Customer.created_at.desc()).limit(250)).all()
        return page(request, 'customers.html', nav='clientes', rows=rows, error=str(exc), status=400)


@app.get('/panel/comisiones')
def distributor_commissions(request: Request, db: Session = Depends(get_db)):
    user = login_required(request)
    scoped = [Commission.tenant_id == user.tenant_id] + ([Commission.distributor_id == user.id] if user.role != 'admin' else [])
    rows = db.execute(select(Commission, Order).join(Order, Commission.order_id == Order.id).where(*scoped).order_by(Commission.created_at.desc()).limit(250)).all()
    return page(request, 'commissions.html', nav='comisiones', rows=rows)


@app.get('/panel/renovaciones')
def renewals(request: Request, db: Session = Depends(get_db)):
    user = login_required(request)
    scoped = [Order.tenant_id == user.tenant_id] + ([Order.distributor_id == user.id] if user.role != 'admin' else [])
    rows = db.execute(select(Order, Customer).join(Customer, Order.customer_id == Customer.id).where(*scoped, Order.next_renewal_date.is_not(None), Order.status != 'CANCELADO').order_by(Order.next_renewal_date.asc()).limit(250)).all()
    return page(request, 'renewals.html', nav='renovaciones', rows=rows)


@app.get('/admin/productos')
def admin_products(request: Request, db: Session = Depends(get_db)):
    admin_required(request)
    products = db.scalars(select(Product).where(Product.tenant_id == request.state.user.tenant_id).order_by(Product.created_at.asc())).all()
    return page(request, 'admin_products.html', nav='productos', products=products)


@app.post('/admin/productos')
async def admin_product_save(request: Request, db: Session = Depends(get_db)):
    admin = admin_required(request)
    form = await request.form()
    verify_csrf(request, form)
    try:
        id_ = get_form_value(form, 'id', 36)
        product = db.get(Product, id_) if id_ else Product(tenant_id=admin.tenant_id)
        if id_ and product and product.tenant_id != admin.tenant_id:
            raise ValueError('Producto inexistente.')
        if not id_:
            ensure_quota(db, admin.tenant_id, 'products')
        if id_ and not product:
            raise ValueError('Producto inexistente.')
        name = get_form_value(form, 'name', 120)
        if len(name) < 2:
            raise ValueError('El nombre del producto es obligatorio.')
        currency = get_form_value(form, 'currency', 3)
        if currency not in ('USD', 'MXN'):
            raise ValueError('Moneda no admitida.')
        interval = get_form_value(form, 'billing_interval', 16)
        if interval and interval not in INTERVALS:
            raise ValueError('Periodicidad no admitida.')
        raw_price = get_form_value(form, 'price', 20)
        if raw_price:
            from decimal import Decimal, InvalidOperation
            try:
                price = Decimal(raw_price)
            except InvalidOperation:
                raise ValueError('Precio inválido.')
            if price <= 0 or price > 1000000 or price.as_tuple().exponent < -2:
                raise ValueError('El precio debe ser positivo y tener máximo dos decimales.')
            cents = int(price * 100)
        else:
            cents = None
        bps = int(get_form_value(form, 'commission_bps', 5))
        if not 0 <= bps <= 10000:
            raise ValueError('Comisión fuera de rango.')
        product.name = name
        product.description = get_form_value(form, 'description', 700)
        product.currency = currency
        product.billing_interval = interval or None
        product.price_cents = cents
        product.commission_bps = bps
        product.active = get_form_value(form, 'active', 5) == 'on'
        if product.active and not valid_product(product):
            raise ValueError('Completa precio, periodicidad y comisión antes de activar el producto.')
        db.add(product)
        db.flush()
        db.add(Audit(tenant_id=admin.tenant_id, actor_id=admin.id, action='product.saved', entity_type='product', entity_id=product.id))
        db.commit()
        return redirect('/admin/productos?guardado=1')
    except (ValueError, TypeError) as exc:
        return page(request, 'admin_products.html', nav='productos', products=db.scalars(select(Product).where(Product.tenant_id == request.state.user.tenant_id).order_by(Product.created_at.asc())).all(), error=str(exc), status=400)


@app.get('/admin/distribuidores')
def admin_distributors(request: Request, db: Session = Depends(get_db)):
    admin_required(request)
    people = db.scalars(select(User).where(User.tenant_id == request.state.user.tenant_id, User.role == 'distribuidor').order_by(User.created_at.desc())).all()
    return page(request, 'admin_distributors.html', nav='distribuidores', people=people)


@app.post('/admin/distribuidores')
async def create_distributor(request: Request, db: Session = Depends(get_db)):
    admin = admin_required(request)
    form = await request.form()
    verify_csrf(request, form)
    try:
        name = get_form_value(form, 'name', 120)
        email = normalize_email(get_form_value(form, 'email', 254))
        if len(name) < 2:
            raise ValueError('Nombre obligatorio.')
        if db.scalar(select(User).where(User.tenant_id == admin.tenant_id, User.email == email)):
            raise ValueError('El correo ya está registrado.')
        ensure_quota(db, admin.tenant_id, 'distributors')
        temp_password = secrets.token_urlsafe(15)
        person = User(tenant_id=admin.tenant_id, name=name, email=email, password_hash=hash_password(temp_password), role='distribuidor',
                      referral_code=secrets.token_hex(5).upper(), must_change_password=True)
        db.add(person)
        db.flush()
        db.add(Audit(tenant_id=admin.tenant_id, actor_id=admin.id, action='distributor.created', entity_type='user', entity_id=person.id))
        db.commit()
        people = db.scalars(select(User).where(User.tenant_id == request.state.user.tenant_id, User.role == 'distribuidor').order_by(User.created_at.desc())).all()
        return page(request, 'admin_distributors.html', nav='distribuidores', people=people,
                    created={'name': name, 'email': email, 'password': temp_password})
    except ValueError as exc:
        people = db.scalars(select(User).where(User.tenant_id == request.state.user.tenant_id, User.role == 'distribuidor').order_by(User.created_at.desc())).all()
        return page(request, 'admin_distributors.html', nav='distribuidores', people=people, error=str(exc), status=400)


@app.post('/admin/distribuidores/{distributor_id}/activo')
async def toggle_distributor(request: Request, distributor_id: str, db: Session = Depends(get_db)):
    admin = admin_required(request)
    form = await request.form()
    verify_csrf(request, form)
    person = db.get(User, distributor_id)
    if not person or person.tenant_id != admin.tenant_id or person.role != 'distribuidor':
        raise HTTPException(status_code=404)
    if not person.active:
        try:
            ensure_quota(db, admin.tenant_id, 'distributors')
        except ValueError as exc:
            return redirect('/admin/distribuidores?' + urlencode({'error': str(exc)}))
    person.active = not person.active
    if not person.active:
        db.execute(LoginSession.__table__.delete().where(LoginSession.user_id == person.id))
    db.add(Audit(tenant_id=admin.tenant_id, actor_id=admin.id, action='distributor.toggled', entity_type='user', entity_id=person.id, detail=str(person.active)))
    db.commit()
    return redirect('/admin/distribuidores')


@app.get('/admin/pedidos')
def admin_orders(request: Request, db: Session = Depends(get_db)):
    admin_required(request)
    rows = db.execute(select(Order, Customer, User).join(Customer, Order.customer_id == Customer.id).join(User, Order.distributor_id == User.id).where(Order.tenant_id == request.state.user.tenant_id, Customer.tenant_id == request.state.user.tenant_id, User.tenant_id == request.state.user.tenant_id).order_by(Order.created_at.desc()).limit(250)).all()
    return page(request, 'admin_orders.html', nav='admin-pedidos', rows=rows)


@app.post('/admin/pedidos/{order_id}/estado')
async def admin_change_order_status(request: Request, order_id: str, db: Session = Depends(get_db)):
    admin = admin_required(request)
    form = await request.form()
    verify_csrf(request, form)
    order = db.get(Order, order_id)
    if not order or order.tenant_id != admin.tenant_id:
        raise HTTPException(status_code=404)
    try:
        change_order_status(db, admin.tenant_id, admin.id, order, get_form_value(form, 'status', 20))
    except ValueError as exc:
        return redirect('/admin/pedidos?' + urlencode({'error': str(exc)}))
    return redirect('/admin/pedidos?estado=actualizado')


@app.post('/admin/pedidos/{order_id}/pago')
async def admin_record_payment(request: Request, order_id: str, db: Session = Depends(get_db)):
    admin = admin_required(request)
    form = await request.form()
    verify_csrf(request, form)
    order = db.get(Order, order_id)
    if not order or order.tenant_id != admin.tenant_id:
        raise HTTPException(status_code=404)
    try:
        kind = get_form_value(form, 'kind', 12)
        record_payment(db, admin.tenant_id, admin.id, order, get_form_value(form, 'reference', 120), kind=kind,
                       period_key=get_form_value(form, 'period_key', 20) or None)
    except ValueError as exc:
        return redirect('/admin/pedidos?' + urlencode({'error': str(exc)}))
    return redirect('/admin/pedidos?pago=registrado')


@app.get('/admin/comisiones')
def admin_commissions(request: Request, db: Session = Depends(get_db)):
    admin_required(request)
    rows = db.execute(select(Commission, Order, User).join(Order, Commission.order_id == Order.id).join(User, Commission.distributor_id == User.id).where(Commission.tenant_id == request.state.user.tenant_id, Order.tenant_id == request.state.user.tenant_id, User.tenant_id == request.state.user.tenant_id).order_by(Commission.created_at.desc()).limit(300)).all()
    return page(request, 'admin_commissions.html', nav='admin-comisiones', rows=rows)


@app.post('/admin/comisiones/{commission_id}/estado')
async def admin_commission_transition(request: Request, commission_id: str, db: Session = Depends(get_db)):
    admin = admin_required(request)
    form = await request.form()
    verify_csrf(request, form)
    commission = db.get(Commission, commission_id)
    if not commission or commission.tenant_id != admin.tenant_id:
        raise HTTPException(status_code=404)
    try:
        change_commission_status(db, admin.tenant_id, admin.id, commission, get_form_value(form, 'status', 16), get_form_value(form, 'reference', 120))
    except ValueError as exc:
        return redirect('/admin/comisiones?' + urlencode({'error': str(exc)}))
    return redirect('/admin/comisiones?estado=actualizado')


@app.get('/admin/exportar/{kind}.csv')
def export_csv(request: Request, kind: str, db: Session = Depends(get_db)):
    admin_required(request)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    def safe(value):
        # Prevent CSV/spreadsheet formula injection from user-supplied text.
        text = str(value)
        if text and text.lstrip()[:1] in ('=', '+', '-', '@', '\t', '\r'):
            return "'" + text
        return text
    if kind == 'pedidos':
        writer.writerow(['Pedido', 'Fecha UTC', 'Distribuidor', 'Cliente', 'Correo', 'Producto', 'Cantidad', 'Total centavos', 'Moneda', 'Estado', 'Pago UTC'])
        for order, customer, user in db.execute(select(Order, Customer, User).join(Customer, Order.customer_id == Customer.id).join(User, Order.distributor_id == User.id).where(Order.tenant_id == request.state.user.tenant_id, Customer.tenant_id == request.state.user.tenant_id, User.tenant_id == request.state.user.tenant_id).order_by(Order.created_at.desc())):
            writer.writerow([safe(v) for v in [order.code, order.created_at.isoformat(), user.email, customer.name, customer.email, order.product_name, order.quantity, order.total_cents, order.currency, order.status, order.paid_at.isoformat() if order.paid_at else '']])
    elif kind == 'comisiones':
        writer.writerow(['Pedido', 'Distribuidor', 'Comision centavos', 'Moneda', 'Estado', 'Fecha UTC', 'Referencia liquidacion'])
        for commission, order, user in db.execute(select(Commission, Order, User).join(Order, Commission.order_id == Order.id).join(User, Commission.distributor_id == User.id).where(Commission.tenant_id == request.state.user.tenant_id, Order.tenant_id == request.state.user.tenant_id, User.tenant_id == request.state.user.tenant_id)):
            writer.writerow([safe(v) for v in [order.code, user.email, commission.amount_cents, commission.currency, commission.status, commission.created_at.isoformat(), commission.payout_reference or '']])
    else:
        raise HTTPException(status_code=404)
    return Response(buffer.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{kind}-indigo.csv"'})


@app.get('/d/{code}')
def referral_page(request: Request, code: str, db: Session = Depends(get_db)):
    distributor = db.scalar(select(User).where(User.referral_code == code, User.role == 'distribuidor', User.active.is_(True)))
    if not distributor:
        raise HTTPException(status_code=404)
    if not access_enabled(db, distributor.tenant_id):
        raise HTTPException(status_code=404)
    products = [p for p in db.scalars(select(Product).where(Product.tenant_id == distributor.tenant_id, Product.active.is_(True)).order_by(Product.name)) if valid_product(p)]
    return page(request, 'referral.html', public_layout=True, tenant=db.get(Tenant, distributor.tenant_id), distributor=distributor, products=products, public_token=signed_public_form(code))


@app.post('/d/{code}')
async def referral_order(request: Request, code: str, db: Session = Depends(get_db)):
    form = await request.form()
    distributor = db.scalar(select(User).where(User.referral_code == code, User.role == 'distribuidor', User.active.is_(True)))
    if not distributor or not access_enabled(db, distributor.tenant_id):
        raise HTTPException(status_code=404)
    if get_form_value(form, 'website', 200) or not verify_public_form(code, get_form_value(form, 'public_token', 150)):
        raise HTTPException(status_code=403, detail='Formulario inválido o vencido. Vuelve a abrir el enlace.')
    try:
        order = make_order(db, distributor.tenant_id, distributor.id, distributor.id, get_form_value(form, 'product_id', 36),
                           get_form_value(form, 'customer_name', 120), get_form_value(form, 'customer_email', 254),
                           get_form_value(form, 'phone', 50), get_form_value(form, 'quantity', 4),
                           get_form_value(form, 'note', 2000), source='enlace')
        return page(request, 'referral_success.html', public_layout=True, tenant=db.get(Tenant, distributor.tenant_id), code=order.code, distributor=distributor)
    except (ValueError, TypeError) as exc:
        products = [p for p in db.scalars(select(Product).where(Product.tenant_id == distributor.tenant_id, Product.active.is_(True)).order_by(Product.name)) if valid_product(p)]
        return page(request, 'referral.html', public_layout=True, tenant=db.get(Tenant, distributor.tenant_id), distributor=distributor, products=products, error=str(exc), public_token=signed_public_form(code), status=400)


@app.get('/salud')
def health():
    return {'status': 'ok', 'product': 'teamindigo-saas'}


@app.get('/admin/suscripcion')
def tenant_billing(request: Request, db: Session = Depends(get_db)):
    user = admin_required(request)
    sub = get_subscription(db, user.tenant_id)
    plan = get_plan(db, user.tenant_id)
    charges = db.scalars(select(TenantCharge).where(TenantCharge.tenant_id == user.tenant_id).order_by(TenantCharge.recorded_at.desc()).limit(24)).all()
    counts = {
        'distribuidores': db.scalar(select(func.count()).select_from(User).where(User.tenant_id == user.tenant_id, User.role == 'distribuidor', User.active.is_(True))) or 0,
        'productos': db.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == user.tenant_id)) or 0,
        'pedidos_mes': db.scalar(select(func.count()).select_from(Order).where(Order.tenant_id == user.tenant_id, Order.created_at >= now().replace(day=1, hour=0, minute=0, second=0, microsecond=0))) or 0,
    }
    return page(request, 'subscription.html', nav='suscripcion', subscription=sub, plan=plan, charges=charges, counts=counts)


def platform_data(db):
    rows = db.execute(select(Tenant, TenantSubscription, SaasPlan).join(TenantSubscription, Tenant.id == TenantSubscription.tenant_id).join(SaasPlan, TenantSubscription.plan_id == SaasPlan.id).order_by(Tenant.created_at.desc())).all()
    plans = db.scalars(select(SaasPlan).where(SaasPlan.active.is_(True)).order_by(SaasPlan.price_cents)).all()
    return rows, plans


@app.get('/plataforma')
def platform_dashboard(request: Request, db: Session = Depends(get_db)):
    platform_required(request)
    rows, plans = platform_data(db)
    return page(request, 'platform.html', nav='empresas', rows=rows, plans=plans, mrr=monthly_recurring_revenue(db), active_count=sum(1 for t, sub, p in rows if t.active and sub.status in ('ACTIVA', 'TRIAL') and sub.ends_at >= now()))


@app.post('/plataforma/empresas')
async def platform_create_tenant(request: Request, db: Session = Depends(get_db)):
    actor = platform_required(request)
    form = await request.form()
    verify_csrf(request, form)
    try:
        password = secrets.token_urlsafe(19)
        tenant, admin = create_tenant(db, slug=get_form_value(form, 'slug', 63), name=get_form_value(form, 'name', 140),
                                      email=get_form_value(form, 'email', 254), admin_name=get_form_value(form, 'admin_name', 120),
                                      plan_id=get_form_value(form, 'plan_id', 36), password=password)
        db.add(Audit(tenant_id=None, actor_id=actor.id, action='platform.tenant_created', entity_type='tenant', entity_id=tenant.id))
        db.commit()
        rows, plans = platform_data(db)
        return page(request, 'platform.html', nav='plataforma', rows=rows, plans=plans, mrr=monthly_recurring_revenue(db), active_count=sum(1 for t, sub, p in rows if t.active and sub.status in ('ACTIVA', 'TRIAL') and sub.ends_at >= now()),
                    created={'name': tenant.name, 'slug': tenant.slug, 'email': admin.email, 'password': password})
    except (ValueError, TypeError) as exc:
        db.rollback()
        rows, plans = platform_data(db)
        return page(request, 'platform.html', nav='plataforma', rows=rows, plans=plans, mrr=monthly_recurring_revenue(db), active_count=sum(1 for t, sub, p in rows if t.active and sub.status in ('ACTIVA', 'TRIAL') and sub.ends_at >= now()), error=str(exc), status=400)


@app.get('/plataforma/planes')
def platform_plans(request: Request, db: Session = Depends(get_db)):
    platform_required(request)
    plans = db.scalars(select(SaasPlan).order_by(SaasPlan.price_cents, SaasPlan.name)).all()
    return page(request, 'platform_plans.html', nav='planes', plans=plans)


@app.post('/plataforma/planes')
async def platform_save_plan(request: Request, db: Session = Depends(get_db)):
    actor = platform_required(request)
    form = await request.form()
    verify_csrf(request, form)
    try:
        plan_id = get_form_value(form, 'id', 36)
        plan = db.get(SaasPlan, plan_id) if plan_id else None
        if plan_id and not plan:
            raise ValueError('Plan inexistente.')
        code = get_form_value(form, 'code', 32).lower()
        name = get_form_value(form, 'name', 80)
        from decimal import Decimal, InvalidOperation
        from .saas import valid_slug
        if not 3 <= len(code) <= 32 or not valid_slug(code):
            raise ValueError('Identificador del plan inválido.')
        if not 2 <= len(name) <= 80:
            raise ValueError('Nombre de plan inválido.')
        currency = get_form_value(form, 'currency', 3)
        if currency not in ('MXN', 'USD'):
            raise ValueError('Moneda no admitida.')
        try:
            price = Decimal(get_form_value(form, 'price', 20))
            if not price.is_finite() or price < 0 or price > 1000000 or price.as_tuple().exponent < -2:
                raise ValueError('Precio inválido.')
            distributors = int(get_form_value(form, 'max_distributors', 9))
            products = int(get_form_value(form, 'max_products', 9))
            orders = int(get_form_value(form, 'max_orders_month', 9))
        except (ValueError, InvalidOperation, OverflowError) as exc:
            raise ValueError('Revisa el precio y los límites del plan.') from exc
        if not all(1 <= n <= 1000000 for n in (distributors, products, orders)):
            raise ValueError('Los límites deben ser positivos y no exceder 1,000,000.')
        duplicate = db.scalar(select(SaasPlan).where(SaasPlan.code == code))
        if duplicate and (not plan or duplicate.id != plan.id):
            raise ValueError('El identificador ya está en uso.')
        if plan is None:
            plan = SaasPlan(code=code)
        else:
            # Identifiers are stable once assigned; subscriptions use plan IDs.
            if plan.code != code:
                raise ValueError('El identificador no se puede cambiar.')
        plan.name = name
        plan.price_cents = int(price * 100)
        plan.currency = currency
        plan.max_distributors = distributors
        plan.max_products = products
        plan.max_orders_month = orders
        plan.active = get_form_value(form, 'active', 5) == 'on'
        db.add(plan)
        db.flush()
        db.add(Audit(tenant_id=None, actor_id=actor.id, action='platform.plan_saved', entity_type='saas_plan', entity_id=plan.id))
        db.commit()
        return redirect('/plataforma/planes?guardado=1')
    except (ValueError, TypeError, IntegrityError) as exc:
        db.rollback()
        plans = db.scalars(select(SaasPlan).order_by(SaasPlan.price_cents, SaasPlan.name)).all()
        return page(request, 'platform_plans.html', nav='planes', plans=plans, error=str(exc), status=400)


@app.post('/plataforma/empresas/{tenant_id}/editar')
async def platform_edit_tenant(request: Request, tenant_id: str, db: Session = Depends(get_db)):
    actor = platform_required(request)
    form = await request.form()
    verify_csrf(request, form)
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404)
    try:
        from .saas import valid_slug
        name = get_form_value(form, 'name', 140)
        slug = get_form_value(form, 'slug', 63).lower()
        brand_name = get_form_value(form, 'brand_name', 140)
        email = normalize_email(get_form_value(form, 'contact_email', 254))
        if not 3 <= len(name) <= 140 or not 3 <= len(brand_name) <= 140 or not valid_slug(slug):
            raise ValueError('Datos de empresa inválidos.')
        if slug != tenant.slug and db.scalar(select(Tenant).where(Tenant.slug == slug)):
            raise ValueError('El identificador de espacio ya existe.')
        old_slug = tenant.slug
        tenant.name = name
        tenant.slug = slug
        tenant.brand_name = brand_name
        tenant.contact_email = email
        db.add(Audit(tenant_id=None, actor_id=actor.id, action='platform.tenant_edited', entity_type='tenant', entity_id=tenant.id, detail=f'{old_slug} -> {slug}'))
        db.commit()
        return redirect('/plataforma?empresa=actualizada')
    except (ValueError, TypeError) as exc:
        db.rollback()
        return redirect('/plataforma?' + urlencode({'error': str(exc)}))


@app.post('/plataforma/empresas/{tenant_id}/estado')
async def platform_toggle_tenant(request: Request, tenant_id: str, db: Session = Depends(get_db)):
    actor = platform_required(request)
    form = await request.form()
    verify_csrf(request, form)
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404)
    tenant.active = not tenant.active
    db.add(Audit(tenant_id=None, actor_id=actor.id, action='platform.tenant_toggled', entity_type='tenant', entity_id=tenant_id, detail=str(tenant.active)))
    db.commit()
    return redirect('/plataforma')


@app.post('/plataforma/empresas/{tenant_id}/plan')
async def platform_change_plan(request: Request, tenant_id: str, db: Session = Depends(get_db)):
    actor = platform_required(request)
    form = await request.form()
    verify_csrf(request, form)
    tenant = db.get(Tenant, tenant_id)
    sub = get_subscription(db, tenant_id)
    plan = db.get(SaasPlan, get_form_value(form, 'plan_id', 36))
    if not tenant or not sub or not plan or not plan.active:
        raise HTTPException(status_code=404)
    previous = sub.plan_id
    sub.plan_id = plan.id
    sub.updated_at = now()
    db.add(Audit(tenant_id=None, actor_id=actor.id, action='platform.plan_changed', entity_type='tenant', entity_id=tenant_id, detail=f'{previous}->{plan.id}'))
    db.commit()
    return redirect('/plataforma')


@app.post('/plataforma/empresas/{tenant_id}/cobro')
async def platform_record_charge(request: Request, tenant_id: str, db: Session = Depends(get_db)):
    actor = platform_required(request)
    form = await request.form()
    verify_csrf(request, form)
    try:
        record_saas_charge(db, actor.id, tenant_id,
                           get_form_value(form, 'period_key', 7),
                           get_form_value(form, 'reference', 120))
    except ValueError as exc:
        return redirect('/plataforma?' + urlencode({'error': str(exc)}))
    return redirect('/plataforma?cobro=registrado')


@app.get('/admin/configuracion')
def tenant_brand(request: Request):
    admin_required(request)
    return page(request, 'brand.html', nav='configuracion')


@app.post('/admin/configuracion')
async def save_tenant_brand(request: Request, db: Session = Depends(get_db)):
    admin = admin_required(request)
    form = await request.form()
    verify_csrf(request, form)
    brand_name = get_form_value(form, 'brand_name', 140)
    theme = get_form_value(form, 'theme', 20)
    if not 3 <= len(brand_name) <= 140 or theme not in ('indigo', 'azul', 'verde', 'naranja'):
        return page(request, 'brand.html', nav='configuracion', error='Nombre o paleta no válido.', status=400)
    tenant = db.get(Tenant, admin.tenant_id)
    tenant.brand_name = brand_name
    tenant.theme = theme
    db.add(Audit(tenant_id=tenant.id, actor_id=admin.id, action='tenant.brand_changed', entity_type='tenant', entity_id=tenant.id))
    db.commit()
    return redirect('/admin/configuracion?guardado=1')
