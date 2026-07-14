import csv
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

import aiofiles
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

try:
    from . import db
except ImportError:  # pragma: no cover - allows script execution
    import db

app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / 'templates'))
app.mount('/static', StaticFiles(directory=str(BASE_DIR / 'static')), name='static')
templates.env.globals['getenv'] = os.getenv


def _load_proxy_data():
    candidates = [
        PROJECT_ROOT / 'proxies' / 'all' / 'data.csv',
        PROJECT_ROOT / 'proxies' / 'all' / 'data.json',
    ]
    for path in candidates:
        if not path.exists():
            continue
        if path.suffix == '.csv':
            with open(path, 'r', encoding='utf-8') as fh:
                rows = list(csv.DictReader(fh))
                return rows
        if path.suffix == '.json':
            import json
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
                if isinstance(data, list):
                    return data
    return []


def get_proxy_stats():
    proxies = _load_proxy_data()
    countries = {}
    protocols = {}
    for row in proxies:
        country = (row.get('country') or 'Unknown').strip()
        protocol = (row.get('protocol') or 'Unknown').strip()
        countries[country] = countries.get(country, 0) + 1
        protocols[protocol] = protocols.get(protocol, 0) + 1
    return {
        'total': len(proxies),
        'countries': len(countries),
        'protocols': len(protocols),
        'country_list': sorted(countries.items(), key=lambda item: item[1], reverse=True)[:8],
        'protocol_list': sorted(protocols.items(), key=lambda item: item[1], reverse=True),
        'featured': proxies[:8],
    }


def get_filtered_proxies(page: int = 1, per_page: int = 40, country: str | None = None, protocol: str | None = None, search: str | None = None):
    rows = _load_proxy_data()
    if country:
        rows = [r for r in rows if (r.get('country') or '').lower() == country.lower()]
    if protocol:
        rows = [r for r in rows if (r.get('protocol') or '').lower() == protocol.lower()]
    if search:
        s = search.lower()
        rows = [r for r in rows if any(s in str(r.get(key, '')).lower() for key in ['ip', 'country', 'city', 'protocol', 'isp'])]
    start = (page - 1) * per_page
    end = start + per_page
    return rows[start:end], len(rows)


@app.on_event('startup')
def startup():
    db.init_db()
    uploads = BASE_DIR / 'uploads'
    uploads.mkdir(parents=True, exist_ok=True)
    load_dotenv(BASE_DIR / '.env')


def send_email_receipt(to_email: str, subject: str, body: str):
    smtp_host = os.getenv('SMTP_HOST')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    smtp_user = os.getenv('SMTP_USER')
    smtp_pass = os.getenv('SMTP_PASS')
    from_email = os.getenv('FROM_EMAIL') or f"no-reply@{os.getenv('BRAND_NAME', 'nexorian')}.local"
    if not smtp_host or not smtp_user or not smtp_pass:
        print('SMTP not configured; skipping email')
        return False
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = to_email
    msg.set_content(body)
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as smtp:
            smtp.starttls()
            smtp.login(smtp_user, smtp_pass)
            smtp.send_message(msg)
        return True
    except Exception as exc:
        print('Email send failed', exc)
        return False


def verify_tx_hash(tx_hash: str, chain: str = 'eth') -> bool:
    tx = tx_hash.strip()
    chain = (chain or 'eth').lower()
    try:
        if chain in ('eth', 'ethereum'):
            api_key = os.getenv('ETHERSCAN_API_KEY')
            url = f'https://api.etherscan.io/api?module=transaction&action=gettxreceiptstatus&txhash={tx}'
            if api_key:
                url += f'&apikey={api_key}'
            response = requests.get(url, timeout=10)
            data = response.json()
            return data.get('result', {}).get('status') == '1'
        if chain in ('bsc', 'bscscan'):
            api_key = os.getenv('BSCSCAN_API_KEY')
            url = f'https://api.bscscan.com/api?module=transaction&action=gettxreceiptstatus&txhash={tx}'
            if api_key:
                url += f'&apikey={api_key}'
            response = requests.get(url, timeout=10)
            data = response.json()
            return data.get('result', {}).get('status') == '1'
        if chain in ('btc', 'bitcoin'):
            token = os.getenv('BLOCKCYPHER_TOKEN')
            url = f'https://api.blockcypher.com/v1/btc/main/txs/{tx}'
            if token:
                url += f'?token={token}'
            response = requests.get(url, timeout=10)
            data = response.json()
            return int(data.get('confirmations', 0)) > 0
    except Exception as exc:
        print('verify_tx_hash error', exc)
    return False


def create_flutterwave_payment_link(payment_id: int, email: str, amount: float, tx_ref: str | None = None):
    key = os.getenv('FLUTTERWAVE_SECRET_KEY')
    if not key:
        return None
    url = 'https://api.flutterwave.com/v3/payments'
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
    payload = {
        'tx_ref': tx_ref or f'pay_{payment_id}',
        'amount': str(amount),
        'currency': 'USD',
        'redirect_url': os.getenv('FLUTTERWAVE_REDIRECT_URL', ''),
        'customer': {'email': email},
        'meta': {'payment_id': payment_id},
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        data = response.json()
        return data.get('data', {}).get('link') or data.get('data', {}).get('authorization', {}).get('redirect')
    except Exception as exc:
        print('flutterwave create link error', exc)
        return None


@app.get('/')
def index(request: Request):
    plans = db.get_plans()
    stats = get_proxy_stats()
    return templates.TemplateResponse('index.html', {'request': request, 'plans': plans, 'stats': stats})


@app.get('/plans')
def plans(request: Request):
    plans = db.get_plans()
    return templates.TemplateResponse('plans.html', {'request': request, 'plans': plans})


@app.get('/proxy-library')
def proxy_library(request: Request, page: int = 1, country: str | None = None, protocol: str | None = None, search: str | None = None):
    proxies, total = get_filtered_proxies(page=page, country=country, protocol=protocol, search=search)
    stats = get_proxy_stats()
    return templates.TemplateResponse('proxy_library.html', {'request': request, 'proxies': proxies, 'total': total, 'page': page, 'country': country, 'protocol': protocol, 'search': search, 'stats': stats})


@app.get('/countries')
def countries(request: Request):
    stats = get_proxy_stats()
    rows = _load_proxy_data()
    country_rows = []
    for country in sorted({(r.get('country') or 'Unknown').strip() for r in rows}):
        country_rows.append((country, sum(1 for r in rows if (r.get('country') or '').strip() == country)))
    return templates.TemplateResponse('countries.html', {'request': request, 'country_rows': country_rows[:50], 'stats': stats})


@app.get('/about')
def about(request: Request):
    return templates.TemplateResponse('about.html', {'request': request})


@app.get('/contact')
def contact(request: Request):
    return templates.TemplateResponse('contact.html', {'request': request})


@app.get('/faq')
def faq(request: Request):
    return templates.TemplateResponse('faq.html', {'request': request})


@app.post('/purchase')
async def purchase(request: Request, email: str = Form(...), plan_id: str = Form(...)):
    db.ensure_user(email)
    plan = db.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=400, detail='invalid plan')
    payment = db.create_payment(email, plan_id, method='flutterwave', amount=plan['price'])
    link = create_flutterwave_payment_link(payment['id'], email, plan['price'])
    return templates.TemplateResponse('payment_instructions.html', {'request': request, 'payment': payment, 'plan': plan, 'flutterwave_link': link})


@app.get('/dashboard')
def dashboard(request: Request, email: str | None = None):
    if not email:
        return templates.TemplateResponse('dashboard.html', {'request': request, 'subs': [], 'payments': []})
    subs = db.get_subscriptions_by_email(email)
    payments = db.get_payments_by_email(email)
    return templates.TemplateResponse('dashboard.html', {'request': request, 'subs': subs, 'email': email, 'payments': payments})


@app.get('/api/get-proxy')
def api_get_proxy(token: str | None = None):
    if not token:
        raise HTTPException(status_code=400, detail='token required')
    proxy, err = db.allocate_proxy_for_subscription(token)
    if err:
        raise HTTPException(status_code=403, detail=err)
    return {'proxy': proxy}


@app.get('/api/proxies')
def api_proxies(country: str | None = None, protocol: str | None = None, search: str | None = None, page: int = 1):
    proxies, total = get_filtered_proxies(page=page, country=country, protocol=protocol, search=search)
    return {'proxies': proxies, 'total': total, 'page': page}


@app.get('/api/stats')
def api_stats():
    return get_proxy_stats()


@app.post('/submit-crypto')
async def submit_crypto(request: Request, payment_id: int = Form(...), tx_hash: str = Form(...), chain: str = Form('eth'), screenshot: UploadFile = File(...)):
    payment = db.get_payment(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail='payment not found')
    filename = f"payment_{payment_id}_" + os.path.basename(screenshot.filename)
    out_path = BASE_DIR / 'uploads' / filename
    async with aiofiles.open(out_path, 'wb') as fh:
        content = await screenshot.read()
        await fh.write(content)
    ok = verify_tx_hash(tx_hash, chain)
    if ok:
        db.update_payment_status(payment_id, 'confirmed', tx_hash=tx_hash, proof_file=str(out_path))
        sub = db.create_subscription(payment['email'], payment['plan_id'])
        send_email_receipt(payment['email'], f"{os.getenv('BRAND_NAME', 'NEXORIAN')} payment confirmed", f"Payment {payment_id} confirmed. Subscription token: {sub['token']}")
    else:
        db.update_payment_status(payment_id, 'submitted', tx_hash=tx_hash, proof_file=str(out_path))
    return templates.TemplateResponse('submit_success.html', {'request': request, 'payment': db.get_payment(payment_id)})


@app.post('/submit-manual')
async def submit_manual(request: Request, payment_id: int = Form(...), account: str = Form(...), screenshot: UploadFile = File(...)):
    payment = db.get_payment(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail='payment not found')
    filename = f"payment_{payment_id}_manual_" + os.path.basename(screenshot.filename)
    out_path = BASE_DIR / 'uploads' / filename
    async with aiofiles.open(out_path, 'wb') as fh:
        content = await screenshot.read()
        await fh.write(content)
    db.update_payment_status(payment_id, 'submitted', proof_file=str(out_path))
    return templates.TemplateResponse('submit_success.html', {'request': request, 'payment': db.get_payment(payment_id)})


@app.post('/webhook/flutterwave')
async def webhook_flutterwave(request: Request):
    payload = await request.json()
    pid = payload.get('data', {}).get('meta', {}).get('payment_id') or payload.get('payment_id')
    status = payload.get('data', {}).get('status') or payload.get('status')
    if not pid:
        raise HTTPException(status_code=400, detail='payment_id required')
    payment = db.get_payment(int(pid))
    if not payment:
        raise HTTPException(status_code=404, detail='payment not found')
    if status in ('successful', 'completed'):
        db.update_payment_status(payment['id'], 'confirmed')
        sub = db.create_subscription(payment['email'], payment['plan_id'])
        send_email_receipt(payment['email'], f"{os.getenv('BRAND_NAME', 'NEXORIAN')} payment confirmed", f"Payment {payment['id']} confirmed. Subscription token: {sub['token']}")
        return {'ok': True, 'subscription': sub}
    return {'ok': False}


@app.get('/admin/confirm')
def admin_confirm(payment_id: int):
    payment = db.get_payment(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail='payment not found')
    db.update_payment_status(payment_id, 'confirmed')
    sub = db.create_subscription(payment['email'], payment['plan_id'])
    send_email_receipt(payment['email'], f"{os.getenv('BRAND_NAME', 'NEXORIAN')} payment confirmed", f"Payment {payment_id} confirmed. Subscription token: {sub['token']}")
    return RedirectResponse(url=f"/dashboard?email={payment['email']}")
