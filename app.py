from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
import sqlite3, json, io, os, hashlib, secrets
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

import os
DDB = os.environ.get('DATABASE_URL', 'abi_invoices.db')

# ── USERS (defined here — change passwords as needed) ──
USERS = {
    'admin': {
        'password': hashlib.sha256('ABI@admin2024'.encode()).hexdigest(),
        'role': 'admin',
        'display': 'Administrator'
    },
    'dad': {
        'password': hashlib.sha256('ABI@dad2024'.encode()).hexdigest(),
        'role': 'admin',
        'display': 'A.B. Industries'
    }
}

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            if request.is_json:
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated

def get_db():
    conn = sqlite3.connect(DDB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            company TEXT,
            address TEXT,
            gstin TEXT,
            email TEXT,
            phone TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT UNIQUE,
            client_id INTEGER,
            invoice_date TEXT,
            po_number TEXT,
            po_date TEXT,
            transport TEXT,
            items TEXT,
            taxable_value REAL,
            gst_type TEXT DEFAULT 'IGST',
            gst_rate REAL DEFAULT 18,
            gst_amount REAL,
            total REAL,
            status TEXT DEFAULT 'unpaid',
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(client_id) REFERENCES clients(id)
        );
    ''')
    conn.commit()
    conn.close()

init_db()

def num_to_words(n):
    ones = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
            'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
            'Seventeen', 'Eighteen', 'Nineteen']
    tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']

    def words_below_1000(n):
        if n == 0: return ''
        elif n < 20: return ones[n]
        elif n < 100: return tens[n // 10] + ((' ' + ones[n % 10]) if n % 10 else '')
        else: return ones[n // 100] + ' Hundred' + ((' ' + words_below_1000(n % 100)) if n % 100 else '')

    n = int(n)
    if n == 0: return 'Zero'
    result = ''
    if n >= 10000000:
        result += words_below_1000(n // 10000000) + ' Crore '
        n %= 10000000
    if n >= 100000:
        result += words_below_1000(n // 100000) + ' Lakh '
        n %= 100000
    if n >= 1000:
        result += words_below_1000(n // 1000) + ' Thousand '
        n %= 1000
    if n > 0:
        result += words_below_1000(n)
    return result.strip() + ' Only'

# ── AUTH ROUTES ──
@app.route('/login', methods=['GET'])
def login_page():
    if 'user' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/api/login', methods=['POST'])
def do_login():
    d = request.json
    username = d.get('username', '').strip().lower()
    password = d.get('password', '')
    user = USERS.get(username)
    if user and user['password'] == hash_pw(password):
        session['user'] = username
        session['role'] = user['role']
        session['display'] = user['display']
        return jsonify({'success': True, 'display': user['display']})
    return jsonify({'success': False, 'error': 'Invalid username or password'}), 401

@app.route('/api/logout', methods=['POST'])
def do_logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/me')
@login_required
def me():
    return jsonify({'user': session['user'], 'role': session['role'], 'display': session['display']})

# ── MAIN APP ──
@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/api/stats')
@login_required
def stats():
    conn = get_db()
    revenue = conn.execute("SELECT COALESCE(SUM(total),0) as t FROM invoices WHERE status='paid'").fetchone()['t']
    pending = conn.execute("SELECT COALESCE(SUM(total),0) as t FROM invoices WHERE status='unpaid'").fetchone()['t']
    total_inv = conn.execute("SELECT COUNT(*) as c FROM invoices").fetchone()['c']
    total_cli = conn.execute("SELECT COUNT(*) as c FROM clients").fetchone()['c']
    conn.close()
    return jsonify({'revenue': revenue, 'pending': pending, 'invoices': total_inv, 'clients': total_cli})

@app.route('/api/clients', methods=['GET', 'POST'])
@login_required
def clients():
    conn = get_db()
    if request.method == 'POST':
        d = request.json
        conn.execute(
            "INSERT INTO clients (name, company, address, gstin, email, phone) VALUES (?,?,?,?,?,?)",
            (d['name'], d.get('company',''), d.get('address',''), d.get('gstin',''), d.get('email',''), d.get('phone',''))
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    rows = conn.execute("SELECT * FROM clients ORDER BY name ASC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/clients/<int:cid>', methods=['DELETE', 'PUT'])
@login_required
def client_detail(cid):
    conn = get_db()
    if request.method == 'DELETE':
        conn.execute("DELETE FROM clients WHERE id=?", (cid,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    d = request.json
    conn.execute(
        "UPDATE clients SET name=?, company=?, address=?, gstin=?, email=?, phone=? WHERE id=?",
        (d['name'], d.get('company',''), d.get('address',''), d.get('gstin',''), d.get('email',''), d.get('phone',''), cid)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/invoices', methods=['GET', 'POST'])
@login_required
def invoices():
    conn = get_db()
    if request.method == 'POST':
        d = request.json
        inv_num = d.get('invoice_number', '').strip()
        if not inv_num:
            last = conn.execute("SELECT invoice_number FROM invoices ORDER BY id DESC LIMIT 1").fetchone()
            if last:
                try:
                    inv_num = str(int(last['invoice_number']) + 1)
                except:
                    inv_num = datetime.now().strftime('%Y%m%d%H%M%S')
            else:
                inv_num = '1'

        items_json = json.dumps(d['items'])
        taxable = sum(i['qty'] * i['rate'] for i in d['items'])
        gst_rate = float(d.get('gst_rate', 18))
        gst_amount = taxable * (gst_rate / 100)
        total = taxable + gst_amount

        conn.execute("""INSERT INTO invoices
            (invoice_number, client_id, invoice_date, po_number, po_date, transport,
             items, taxable_value, gst_type, gst_rate, gst_amount, total, status, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (inv_num, d['client_id'], d.get('invoice_date', datetime.now().strftime('%d.%m.%Y')),
             d.get('po_number',''), d.get('po_date',''), d.get('transport',''),
             items_json, taxable, d.get('gst_type','IGST'), gst_rate, gst_amount, total,
             d.get('status','unpaid'), d.get('notes',''))
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'invoice_number': inv_num})

    rows = conn.execute("""SELECT i.*, c.name as client_name, c.company, c.address as client_address,
        c.gstin as client_gstin FROM invoices i LEFT JOIN clients c ON i.client_id=c.id
        ORDER BY i.id DESC""").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/invoices/<int:iid>', methods=['DELETE'])
@login_required
def delete_invoice(iid):
    conn = get_db()
    conn.execute("DELETE FROM invoices WHERE id=?", (iid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/invoices/<int:iid>/status', methods=['PUT'])
@login_required
def update_status(iid):
    conn = get_db()
    conn.execute("UPDATE invoices SET status=? WHERE id=?", (request.json['status'], iid))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/invoices/<int:iid>/pdf')
@login_required
def generate_pdf(iid):
    conn = get_db()
    inv = conn.execute("""SELECT i.*, c.name as client_name, c.company, c.address as client_address,
        c.gstin as client_gstin, c.phone as client_phone
        FROM invoices i LEFT JOIN clients c ON i.client_id=c.id WHERE i.id=?""", (iid,)).fetchone()
    conn.close()
    if not inv:
        return "Not found", 404

    inv = dict(inv)
    items = json.loads(inv['items'])

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4

    # ══════════════════════════════════════════
    #  BEAUTIFUL ABI TAX INVOICE PDF
    # ══════════════════════════════════════════

    # Background — clean white
    c.setFillColor(colors.white)
    c.rect(0, 0, W, H, fill=1, stroke=0)

    # ── TOP ACCENT BAR (dark with lime stripe) ──
    c.setFillColor(colors.HexColor('#0d0d14'))
    c.rect(0, H - 38*mm, W, 38*mm, fill=1, stroke=0)

    # Lime accent line at bottom of dark header
    c.setFillColor(colors.HexColor('#d4f53c'))
    c.rect(0, H - 39.5*mm, W, 1.5*mm, fill=1, stroke=0)

    # Small lime block left accent
    c.setFillColor(colors.HexColor('#d4f53c'))
    c.rect(0, H - 38*mm, 6*mm, 38*mm, fill=1, stroke=0)

    # ── COMPANY NAME (in header) ──
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 26)
    c.drawString(14*mm, H - 18*mm, "A.B. INDUSTRIES")

    c.setFillColor(colors.HexColor('#d4f53c'))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(14*mm, H - 24*mm, "GST NO: 27AEMPN3800R1ZX")

    c.setFillColor(colors.HexColor('#aaaacc'))
    c.setFont("Helvetica", 7.5)
    c.drawString(14*mm, H - 29*mm, "Office: Plot No 27, Anand Swaroop Park, Mahalaxmi Park Area, Kasaba Bawada, Kolhapur - 416003")
    c.drawString(14*mm, H - 33.5*mm, "Works: Plot No G-36, Gajendra Udyog Compound, MIDC, Gokul Shirgaon, Kolhapur - 416232")

    # TAX INVOICE badge (top right)
    badge_x = W - 58*mm
    c.setFillColor(colors.HexColor('#d4f53c'))
    c.roundRect(badge_x, H - 26*mm, 48*mm, 13*mm, 3*mm, fill=1, stroke=0)
    c.setFillColor(colors.HexColor('#0d0d14'))
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(badge_x + 24*mm, H - 20.5*mm, "TAX INVOICE")

    # ── INVOICE META STRIP ──
    strip_y = H - 52*mm
    c.setFillColor(colors.HexColor('#f7f7fb'))
    c.rect(0, strip_y, W, 12*mm, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor('#e0e0ec'))
    c.setLineWidth(0.5)
    c.line(0, strip_y, W, strip_y)
    c.line(0, strip_y + 12*mm, W, strip_y + 12*mm)

    meta_items = [
        ("INVOICE NO.", str(inv['invoice_number'])),
        ("DATE", inv.get('invoice_date', '')),
        ("PO NUMBER", inv.get('po_number', '—') or '—'),
        ("PO DATE", inv.get('po_date', '—') or '—'),
        ("STATUS", inv.get('status', '').upper()),
    ]
    meta_x = 14*mm
    for label, value in meta_items:
        c.setFont("Helvetica", 6.5)
        c.setFillColor(colors.HexColor('#888888'))
        c.drawString(meta_x, strip_y + 7.5*mm, label)
        c.setFont("Helvetica-Bold", 8.5)
        if label == "STATUS":
            col = colors.HexColor('#23d18b') if value == 'PAID' else colors.HexColor('#ff3b5c')
            c.setFillColor(col)
        else:
            c.setFillColor(colors.HexColor('#0d0d14'))
        c.drawString(meta_x, strip_y + 2.5*mm, value)
        meta_x += 38*mm

    # ── BILL TO / SHIP TO SECTION ──
    bill_y = strip_y - 5*mm
    # Bill To box
    c.setFillColor(colors.HexColor('#f7f7fb'))
    c.roundRect(10*mm, bill_y - 30*mm, (W - 25*mm)/2, 30*mm, 4*mm, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor('#e0e0ec'))
    c.setLineWidth(0.5)
    c.roundRect(10*mm, bill_y - 30*mm, (W - 25*mm)/2, 30*mm, 4*mm, fill=0, stroke=1)

    c.setFillColor(colors.HexColor('#d4f53c'))
    c.roundRect(10*mm, bill_y - 7*mm, 22*mm, 6*mm, 2*mm, fill=1, stroke=0)
    c.setFillColor(colors.HexColor('#0d0d14'))
    c.setFont("Helvetica-Bold", 7)
    c.drawString(13*mm, bill_y - 4.5*mm, "BILL TO")

    c.setFillColor(colors.HexColor('#0d0d14'))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(13*mm, bill_y - 13*mm, inv.get('client_name', '') or '')

    if inv.get('company'):
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor('#555577'))
        c.drawString(13*mm, bill_y - 18*mm, inv['company'])

    addr = (inv.get('client_address', '') or '').replace('\n', ', ')
    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor('#666688'))
    # Word wrap address
    words = addr.split()
    line, lines = [], []
    for w in words:
        test = ' '.join(line + [w])
        if c.stringWidth(test, "Helvetica", 7.5) < (W - 25*mm)/2 - 8*mm:
            line.append(w)
        else:
            lines.append(' '.join(line))
            line = [w]
    if line:
        lines.append(' '.join(line))
    for li, ln in enumerate(lines[:2]):
        c.drawString(13*mm, bill_y - 23*mm + (li * -4.5*mm) + 4*mm, ln)

    if inv.get('client_gstin'):
        c.setFont("Helvetica-Bold", 7.5)
        c.setFillColor(colors.HexColor('#0d0d14'))
        c.drawString(13*mm, bill_y - 30*mm + 2*mm, f"GSTIN: {inv['client_gstin']}")

    # ── ITEMS TABLE ──
    table_top = bill_y - 36*mm
    col_x = [10*mm, 30*mm, 52*mm, 128*mm, 145*mm, 165*mm, W - 10*mm]
    col_labels = ["ITEM CODE", "HSN", "GOODS DESCRIPTION", "QTY", "UNIT PRICE (₹)", "AMOUNT (₹)"]

    # Table header background
    c.setFillColor(colors.HexColor('#0d0d14'))
    c.rect(10*mm, table_top - 8*mm, W - 20*mm, 8*mm, fill=1, stroke=0)

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 7.5)
    for i, label in enumerate(col_labels):
        cx = col_x[i] + 1.5*mm
        c.drawString(cx, table_top - 5.5*mm, label)

    # Item rows
    row_y = table_top - 8*mm
    min_row_y = 68*mm

    for ri, item in enumerate(items):
        amt = item['qty'] * item['rate']
        row_h = 10*mm
        bg = colors.HexColor('#fafafd') if ri % 2 == 0 else colors.white
        c.setFillColor(bg)
        c.rect(10*mm, row_y - row_h, W - 20*mm, row_h, fill=1, stroke=0)

        c.setStrokeColor(colors.HexColor('#ebebf5'))
        c.setLineWidth(0.3)
        c.line(10*mm, row_y - row_h, W - 10*mm, row_y - row_h)

        c.setFillColor(colors.HexColor('#333355'))
        c.setFont("Helvetica", 8)
        c.drawString(col_x[0] + 1.5*mm, row_y - 6.5*mm, str(item.get('item_code', '')))
        c.drawString(col_x[1] + 1.5*mm, row_y - 6.5*mm, str(item.get('hsn_code', '')))

        desc = str(item.get('description', ''))
        max_w = col_x[3] - col_x[2] - 3*mm
        if c.stringWidth(desc, "Helvetica", 8) > max_w:
            wds = desc.split()
            l1, l2 = [], []
            for w in wds:
                if c.stringWidth(' '.join(l1 + [w]), "Helvetica", 8) <= max_w:
                    l1.append(w)
                else:
                    l2.append(w)
            c.drawString(col_x[2] + 1.5*mm, row_y - 5*mm, ' '.join(l1))
            if l2:
                c.setFont("Helvetica", 7)
                c.drawString(col_x[2] + 1.5*mm, row_y - 9*mm, ' '.join(l2))
                c.setFont("Helvetica", 8)
        else:
            c.drawString(col_x[2] + 1.5*mm, row_y - 6.5*mm, desc)

        c.drawString(col_x[3] + 1.5*mm, row_y - 6.5*mm, str(item['qty']))
        c.drawString(col_x[4] + 1.5*mm, row_y - 6.5*mm, f"{item['rate']:,.3f}")
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(colors.HexColor('#0d0d14'))
        c.drawString(col_x[5] + 1.5*mm, row_y - 6.5*mm, f"{amt:,.2f}")
        row_y -= row_h

    # Vertical column lines
    c.setStrokeColor(colors.HexColor('#e0e0ec'))
    c.setLineWidth(0.4)
    for vx in col_x:
        c.line(vx, table_top, vx, row_y)
    c.line(10*mm, table_top, W - 10*mm, table_top)

    # ── TOTALS SECTION ──
    totals_y = row_y - 4*mm
    right_x = W - 10*mm
    label_x = 130*mm
    val_x = 168*mm

    # Taxable value row
    c.setFillColor(colors.HexColor('#f7f7fb'))
    c.rect(label_x, totals_y - 8*mm, right_x - label_x, 8*mm, fill=1, stroke=0)
    c.setFont("Helvetica", 8.5)
    c.setFillColor(colors.HexColor('#555577'))
    c.drawString(label_x + 2*mm, totals_y - 5.5*mm, "Taxable Value")
    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColor(colors.HexColor('#0d0d14'))
    c.drawRightString(right_x - 2*mm, totals_y - 5.5*mm, f"₹ {inv['taxable_value']:,.2f}")

    # GST row
    gst_type = inv.get('gst_type', 'IGST')
    gst_rate_v = inv.get('gst_rate', 18)
    c.setFillColor(colors.white)
    c.rect(label_x, totals_y - 16*mm, right_x - label_x, 8*mm, fill=1, stroke=0)
    c.setFont("Helvetica", 8.5)
    c.setFillColor(colors.HexColor('#555577'))
    c.drawString(label_x + 2*mm, totals_y - 13.5*mm, f"{gst_type} @ {gst_rate_v:.0f}%")
    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColor(colors.HexColor('#0d0d14'))
    c.drawRightString(right_x - 2*mm, totals_y - 13.5*mm, f"₹ {inv['gst_amount']:,.2f}")

    # TOTAL box — highlighted
    c.setFillColor(colors.HexColor('#0d0d14'))
    c.roundRect(label_x, totals_y - 26*mm, right_x - label_x, 9*mm, 3*mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(label_x + 3*mm, totals_y - 22.5*mm, "TOTAL AMOUNT")
    c.setFillColor(colors.HexColor('#d4f53c'))
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(right_x - 3*mm, totals_y - 22.5*mm, f"₹ {inv['total']:,.2f}")

    # Left side — amount in words + transport (same y level)
    c.setFillColor(colors.HexColor('#f7f7fb'))
    c.roundRect(10*mm, totals_y - 26*mm, 115*mm, 26*mm, 3*mm, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor('#e0e0ec'))
    c.setLineWidth(0.5)
    c.roundRect(10*mm, totals_y - 26*mm, 115*mm, 26*mm, 3*mm, fill=0, stroke=1)

    words_str = num_to_words(inv['total'])
    c.setFont("Helvetica-Bold", 7.5)
    c.setFillColor(colors.HexColor('#888888'))
    c.drawString(13*mm, totals_y - 5*mm, "AMOUNT IN WORDS")
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor('#0d0d14'))
    # Wrap if long
    full_words = f"Rs. {words_str}"
    if c.stringWidth(full_words, "Helvetica", 8) > 108*mm:
        mid = len(full_words)//2
        sp = full_words.rfind(' ', 0, mid)
        c.drawString(13*mm, totals_y - 11*mm, full_words[:sp])
        c.drawString(13*mm, totals_y - 16*mm, full_words[sp+1:])
    else:
        c.drawString(13*mm, totals_y - 11*mm, full_words)

    if inv.get('transport'):
        c.setFont("Helvetica-Bold", 7.5)
        c.setFillColor(colors.HexColor('#888888'))
        c.drawString(13*mm, totals_y - 20*mm, "TRANSPORT")
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor('#0d0d14'))
        c.drawString(13*mm, totals_y - 25*mm, inv['transport'])

    # ── FOOTER SECTION ──
    footer_y = totals_y - 32*mm
    c.setStrokeColor(colors.HexColor('#e0e0ec'))
    c.setLineWidth(0.5)
    c.line(10*mm, footer_y, W - 10*mm, footer_y)

    # Declaration left
    c.setFont("Helvetica-Bold", 7)
    c.setFillColor(colors.HexColor('#888888'))
    c.drawString(12*mm, footer_y - 6*mm, "DECLARATION")
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor('#666666'))
    c.drawString(12*mm, footer_y - 11*mm, "We declare that this invoice shows the actual price of the goods described")
    c.drawString(12*mm, footer_y - 15*mm, "and that all particulars are true and correct.")

    # Signature box right
    sig_x = W - 65*mm
    c.setFillColor(colors.HexColor('#f7f7fb'))
    c.roundRect(sig_x, footer_y - 28*mm, 53*mm, 27*mm, 3*mm, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor('#e0e0ec'))
    c.roundRect(sig_x, footer_y - 28*mm, 53*mm, 27*mm, 3*mm, fill=0, stroke=1)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(colors.HexColor('#0d0d14'))
    c.drawCentredString(sig_x + 26.5*mm, footer_y - 6*mm, "For, A.B. INDUSTRIES")
    c.setStrokeColor(colors.HexColor('#ccccdd'))
    c.setLineWidth(0.5)
    c.line(sig_x + 5*mm, footer_y - 22*mm, sig_x + 48*mm, footer_y - 22*mm)
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor('#888888'))
    c.drawCentredString(sig_x + 26.5*mm, footer_y - 26.5*mm, "Authorised Signatory")

    # ── BOTTOM BAR ──
    c.setFillColor(colors.HexColor('#0d0d14'))
    c.rect(0, 0, W, 12*mm, fill=1, stroke=0)
    c.setFillColor(colors.HexColor('#d4f53c'))
    c.rect(0, 0, 6*mm, 12*mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(W/2, 4.5*mm, "THANK YOU FOR YOUR BUSINESS")
    c.setFillColor(colors.HexColor('#888888'))
    c.setFont("Helvetica", 7)
    c.drawString(10*mm, 4.5*mm, "A.B. INDUSTRIES")

    c.save()
    buf.seek(0)
    return send_file(buf, mimetype='application/pdf',
                     download_name=f"ABI-Invoice-{inv['invoice_number']}.pdf",
                     as_attachment=True)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
