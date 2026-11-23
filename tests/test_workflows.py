import hashlib
import hmac
import time
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app.main import app
from app.db import SessionLocal
from app.models import Order, Commission, Product, User, Tenant, TenantSubscription, TenantCharge, SaasPlan, Customer
from .conftest import login, csrf


def order_as(client, product='prod-a', distributor='dist-a'):
    return client.post('/panel/pedidos/nuevo', data={
        'csrf': csrf(client), 'distributor_id': distributor, 'product_id': product, 'quantity': '1',
        'customer_name': 'Cliente de prueba', 'customer_email': 'client@example.com', 'phone': ''}, follow_redirects=False)


def test_two_organizations_same_email_and_isolated_catalog():
    with TestClient(app) as a, TestClient(app) as b:
        login(a, 'equipo-a')
        login(b, 'equipo-b')
        assert 'Servicio exclusivo A' in a.get('/panel/pedidos/nuevo').text
        assert 'Servicio exclusivo B' not in a.get('/panel/pedidos/nuevo').text
        assert 'Servicio exclusivo B' in b.get('/panel/pedidos/nuevo').text
        assert 'Servicio exclusivo A' not in b.get('/panel/pedidos/nuevo').text
        assert a.get('/admin/suscripcion').status_code == 200
        assert a.get('/plataforma').status_code == 403


def test_cross_tenant_order_injection_denied():
    with TestClient(app) as a:
        login(a)
        r = order_as(a, product='prod-b')
        assert r.status_code == 400
        assert 'Producto no disponible' in r.text
        r = order_as(a, distributor='dist-b')
        assert r.status_code == 400
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Order)) == 0


def test_orders_and_customer_listing_private():
    with TestClient(app) as a, TestClient(app) as b:
        login(a)
        login(b, 'equipo-b')
        assert order_as(a).status_code == 303
        assert 'Cliente de prueba' in a.get('/panel/clientes').text
        assert 'Cliente de prueba' not in b.get('/panel/clientes').text
        assert 'Servicio exclusivo A' in a.get('/panel/pedidos').text
        assert 'Servicio exclusivo A' not in b.get('/panel/pedidos').text
        assert 'Cliente de prueba' in a.get('/admin/exportar/pedidos.csv').text
        assert 'Cliente de prueba' not in b.get('/admin/exportar/pedidos.csv').text


def test_cross_tenant_order_status_and_payment_idor_denied():
    with TestClient(app) as a, TestClient(app) as b:
        login(a)
        login(b, 'equipo-b')
        order_as(a)
        with SessionLocal() as db:
            order_id = db.scalar(select(Order)).id
        assert b.post(f'/admin/pedidos/{order_id}/estado', data={'csrf': csrf(b), 'status': 'CONFIRMADO'}, follow_redirects=False).status_code == 404
        assert b.post(f'/admin/pedidos/{order_id}/pago', data={'csrf': csrf(b), 'kind': 'INICIAL', 'reference': 'TRANS-001'}, follow_redirects=False).status_code == 404
        assert a.post(f'/admin/pedidos/{order_id}/estado', data={'csrf': csrf(a), 'status': 'CONFIRMADO'}, follow_redirects=False).status_code == 303
        assert a.post(f'/admin/pedidos/{order_id}/pago', data={'csrf': csrf(a), 'kind': 'INICIAL', 'reference': 'TRANS-001'}, follow_redirects=False).status_code == 303
        with SessionLocal() as db:
            c = db.scalar(select(Commission))
            assert c.amount_cents == 1500
            c_id = c.id
        assert b.post(f'/admin/comisiones/{c_id}/estado', data={'csrf': csrf(b), 'status': 'APROBADA'}, follow_redirects=False).status_code == 404
        assert 'TRANS-001' not in b.get('/admin/exportar/comisiones.csv').text


def test_cross_tenant_product_edit_and_user_toggle_denied():
    with TestClient(app) as a:
        login(a)
        r = a.post('/admin/productos', data={'csrf': csrf(a), 'id': 'prod-b', 'name': 'Hacked', 'price': '5', 'currency': 'USD', 'billing_interval': 'UNICO', 'commission_bps': '5', 'active': 'on'})
        assert r.status_code == 400
        assert a.post('/admin/distribuidores/dist-b/activo', data={'csrf': csrf(a)}, follow_redirects=False).status_code == 404
        with SessionLocal() as db:
            assert db.get(Product, 'prod-b').name == 'Servicio exclusivo B'
            assert db.get(User, 'dist-b').active is True


def test_public_referral_respects_tenant_catalog_and_request():
    with TestClient(app) as client:
        response = client.get('/d/REF-A')
        assert response.status_code == 200
        assert 'Servicio exclusivo A' in response.text
        assert 'Servicio exclusivo B' not in response.text
        timestamp = str(int(time.time()) - 5)
        sig = hmac.new(b'a-test-form-secret-not-for-production', f'REF-A:{timestamp}'.encode(), hashlib.sha256).hexdigest()
        data = {'public_token': f'{timestamp}.{sig}', 'website': '', 'product_id': 'prod-b', 'quantity': 1,
                'customer_name': 'Cliente enlace', 'customer_email': 'social@example.com'}
        assert client.post('/d/REF-A', data=data).status_code == 400
        data['product_id'] = 'prod-a'
        assert '¡Gracias!' in client.post('/d/REF-A', data=data).text
        with SessionLocal() as db:
            o = db.scalar(select(Order))
            assert o.tenant_id == 'tenant-a'
            assert o.source == 'enlace'


def test_distributor_cannot_manage_saas_or_other_users():
    with TestClient(app) as dist:
        login(dist, role='distribuidor')
        assert dist.get('/admin/productos').status_code == 403
        assert dist.get('/admin/suscripcion').status_code == 403
        assert dist.get('/plataforma').status_code == 403
        assert dist.get('/panel/pedidos/nuevo').status_code == 200
        assert dist.post('/panel/pedidos/nuevo', data={'csrf': csrf(dist), 'product_id': 'prod-b', 'quantity': 1, 'customer_name': 'Prueba', 'customer_email': 'a@b.com'}).status_code == 400


def test_platform_admin_register_tenant_change_plan_suspend_and_payment():
    with TestClient(app) as root:
        login(root, 'plataforma', 'superadmin')
        assert root.get('/plataforma').status_code == 200
        assert root.get('/admin/productos').status_code == 403
        with SessionLocal() as db:
            plan = db.scalar(select(SaasPlan).where(SaasPlan.code == 'emprendedor'))
            plan_id = plan.id
        response = root.post('/plataforma/empresas', data={
            'csrf': csrf(root), 'slug': 'empresa-nueva', 'name': 'Empresa Nueva', 'admin_name': 'Responsable',
            'email': 'responsable@example.com', 'plan_id': plan_id})
        assert response.status_code == 200
        assert 'Contraseña provisional' in response.text
        with SessionLocal() as db:
            t = db.scalar(select(Tenant).where(Tenant.slug == 'empresa-nueva'))
            t_id = t.id
            sub = db.get(TenantSubscription, t_id)
            assert sub.status == 'TRIAL'
        assert root.post(f'/plataforma/empresas/{t_id}/cobro', data={'csrf': csrf(root), 'period_key': '2026-11', 'reference': 'BANCO-001'}, follow_redirects=False).status_code == 303
        with SessionLocal() as db:
            sub = db.get(TenantSubscription, t_id)
            assert sub.status == 'ACTIVA'
            assert db.scalar(select(TenantCharge).where(TenantCharge.tenant_id == t_id)).amount_cents == 49900
        assert root.post(f'/plataforma/empresas/{t_id}/cobro', data={'csrf': csrf(root), 'period_key': '2026-11', 'reference': 'BANCO-002'}, follow_redirects=False).status_code == 303
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(TenantCharge).where(TenantCharge.tenant_id == t_id)) == 1
        assert root.post(f'/plataforma/empresas/{t_id}/estado', data={'csrf': csrf(root)}, follow_redirects=False).status_code == 303
        with SessionLocal() as db:
            assert db.get(Tenant, t_id).active is False


def test_suspension_invalidates_existing_session_and_public_link():
    with TestClient(app) as seller:
        login(seller, 'equipo-a', 'distribuidor')
        assert seller.get('/panel').status_code == 200
        with SessionLocal() as db:
            db.get(Tenant, 'tenant-a').active = False
            db.commit()
        assert seller.get('/panel', follow_redirects=False).status_code == 303
        assert seller.get('/d/REF-A').status_code == 404


def test_password_change_revokes_all_sessions():
    with TestClient(app) as seller:
        login(seller, 'equipo-a', 'distribuidor')
        r = seller.post('/perfil', data={'csrf': csrf(seller), 'current': 'Strong-Seller-Password-2026', 'new': 'A-New-Long-Password-2027'}, follow_redirects=False)
        assert r.status_code == 303
        assert seller.get('/panel', follow_redirects=False).status_code == 303


def test_csrf_and_bad_login_workspace():
    with TestClient(app) as client:
        login_page = client.get('/ingresar?espacio=equipo-a')
        assert login_page.status_code == 200
        invalid = client.post('/ingresar', data={'workspace': 'equipo-b', 'email': 'seller@example.com', 'password': 'wrong', 'login_csrf': client.cookies['login_csrf']})
        assert invalid.status_code == 401
        login(client)
        assert client.post('/admin/productos', data={'csrf': 'wrong'}).status_code == 403
