import datetime
import json
import os
import sqlitecloud
from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from io import BytesIO

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# --- Email sending (optional) ---
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

DATABASE_URL = "sqlitecloud://cjl7roqqdz.g1.sqlite.cloud:8860/kakama?apikey=bsp6Uyf9IUYFlFbCgzTuK8Dale2LCayOG5Z9bXRQirQ"

UPLOAD_FOLDER = "/tmp/uploads" if os.environ.get("VERCEL") else os.path.join(BASE_DIR, "uploads")
try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
except OSError:
    pass
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


# Safe Dictionary Row Wrapper for SQLite Cloud
class DictRow(dict):

  def __init__(self, cursor, row):
    for idx, col in enumerate(cursor.description):
      self[col[0]] = row[idx]


class DictCursor:

  def __init__(self, cursor):
    self.cursor = cursor

  def execute(self, sql, parameters=None):
    if parameters:
      return self.cursor.execute(sql, parameters)
    return self.cursor.execute(sql)

  def fetchone(self):
    row = self.cursor.fetchone()
    if not row:
      return None
    return DictRow(self.cursor, row)

  def fetchall(self):
    rows = self.cursor.fetchall()
    if not rows:
      return []
    return [DictRow(self.cursor, row) for row in rows]

  def __getattr__(self, name):
    return getattr(self.cursor, name)


class DictConnection:

  def __init__(self, conn):
    self.conn = conn

  def cursor(self):
    return DictCursor(self.conn.cursor())

  def __getattr__(self, name):
    return getattr(self.conn, name)


def get_db():
  if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set."
    )
  conn = sqlitecloud.connect(DATABASE_URL)
  return DictConnection(conn)


def init_db():
  conn = get_db()
  c = conn.cursor()
  c.execute("""CREATE TABLE IF NOT EXISTS medicines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT, category TEXT, stock INTEGER, price REAL, batch_no TEXT, expiry_date TEXT)""")
  c.execute("""CREATE TABLE IF NOT EXISTS customers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT, phone TEXT, email TEXT)""")
  c.execute("""CREATE TABLE IF NOT EXISTS sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sale_date TEXT, cashier TEXT, customer_name TEXT, total_amount REAL, items_json TEXT)""")
  c.execute("""CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    description TEXT, type TEXT, amount REAL)""")
  c.execute("""CREATE TABLE IF NOT EXISTS suppliers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_name TEXT, contact_person TEXT, phone TEXT, email TEXT)""")
  c.execute("""CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT, position TEXT, department TEXT, salary REAL, pin TEXT)""")
  c.execute("""CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_name TEXT, assigned_to TEXT, status TEXT)""")
  c.execute("""CREATE TABLE IF NOT EXISTS chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender TEXT, recipient TEXT, message TEXT, timestamp TEXT, chat_type TEXT, image_url TEXT, audio_url TEXT, is_read INTEGER DEFAULT 0)""")
  c.execute("""CREATE TABLE IF NOT EXISTS email_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recipient TEXT, sender TEXT, body TEXT, timestamp TEXT)""")

  migrations = [
      ("chats", "audio_url", "TEXT"),
      ("chats", "image_url", "TEXT"),
      ("chats", "is_read", "INTEGER DEFAULT 0"),
      ("chats", "chat_type", "TEXT"),
      ("sales", "items_json", "TEXT"),
      ("medicines", "batch_no", "TEXT"),
      ("medicines", "expiry_date", "TEXT"),
  ]
  for table, column, col_type in migrations:
    try:
      c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except Exception:
      pass

  c.execute("SELECT * FROM employees WHERE name='Kakama'")
  if not c.fetchone():
    c.execute(
        "INSERT INTO employees (name, position, department, salary, pin)"
        " VALUES ('Kakama', 'Master', 'Management', 0, ?)",
        (generate_password_hash('4321'),),
    )

  conn.commit()
  conn.close()


init_db()


@app.route('/api/login', methods=['POST'])
def login():
  data = request.get_json() or {}
  username = data.get('username', '')
  password = data.get('password', '')

  conn = get_db()
  c = conn.cursor()
  c.execute('SELECT * FROM employees WHERE name=?', (username,))
  emp = c.fetchone()
  conn.close()

  if emp and emp['pin'] and check_password_hash(emp['pin'], password):
    role = 'Master' if (emp['position'] or '').strip().lower() == 'master' else 'Team Member'
    return jsonify(
        {'status': 'success', 'user': {'name': emp['name'], 'role': role}}
    )
  return jsonify({'status': 'error', 'message': 'Invalid credentials'}), 401


@app.route('/api/stats', methods=['GET'])
def get_stats():
  conn = get_db()
  c = conn.cursor()
  c.execute('SELECT SUM(total_amount) FROM sales')
  rev = c.fetchone()[0] or 0.0
  c.execute('SELECT SUM(stock) FROM medicines')
  inv = c.fetchone()[0] or 0
  c.execute('SELECT COUNT(*) FROM customers')
  cust = c.fetchone()[0] or 0
  c.execute("SELECT SUM(amount) FROM accounts WHERE type='income'")
  income = c.fetchone()[0] or 0.0
  c.execute("SELECT SUM(amount) FROM accounts WHERE type='expense'")
  expense = c.fetchone()[0] or 0.0
  conn.close()
  return jsonify({
      'revenue': rev,
      'inventory': inv,
      'customers': cust,
      'total_income': income,
      'total_expense': expense,
      'net_accounts': income - expense,
  })


@app.route('/api/medicines', methods=['GET', 'POST', 'PUT', 'DELETE'])
@app.route('/api/medicine', methods=['GET', 'POST', 'PUT', 'DELETE'])
def manage_medicines():
  conn = get_db()
  c = conn.cursor()
  if request.method == 'GET':
    c.execute('SELECT * FROM medicines')
    rows = c.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
  elif request.method == 'POST':
    d = request.get_json() or {}
    c.execute(
        'INSERT INTO medicines (name, category, stock, price, batch_no,'
        ' expiry_date) VALUES (?,?,?,?,?,?)',
        (
            d.get('name'),
            d.get('category'),
            d.get('stock'),
            d.get('price'),
            d.get('batch_no'),
            d.get('expiry_date'),
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})
  elif request.method == 'PUT':
    d = request.get_json() or {}
    c.execute(
        'UPDATE medicines SET name=?, category=?, stock=?, price=?, batch_no=?,'
        ' expiry_date=? WHERE id=?',
        (
            d.get('name'),
            d.get('category'),
            d.get('stock'),
            d.get('price'),
            d.get('batch_no'),
            d.get('expiry_date'),
            d.get('id'),
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})
  elif request.method == 'DELETE':
    med_id = request.args.get('id')
    c.execute('DELETE FROM medicines WHERE id=?', (med_id,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})


@app.route('/api/customers', methods=['GET', 'POST', 'PUT', 'DELETE'])
def manage_customers():
  conn = get_db()
  c = conn.cursor()
  if request.method == 'GET':
    c.execute('SELECT * FROM customers')
    rows = c.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
  elif request.method == 'POST':
    d = request.get_json() or {}
    c.execute(
        'INSERT INTO customers (name, phone, email) VALUES (?,?,?)',
        (d.get('name'), d.get('phone'), d.get('email')),
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})
  elif request.method == 'PUT':
    d = request.get_json() or {}
    c.execute(
        'UPDATE customers SET name=?, phone=?, email=? WHERE id=?',
        (d.get('name'), d.get('phone'), d.get('email'), d.get('id')),
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})
  elif request.method == 'DELETE':
    c.execute('DELETE FROM customers WHERE id=?', (request.args.get('id'),))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})


@app.route('/api/sales', methods=['GET', 'POST', 'DELETE'])
def manage_sales():
  conn = get_db()
  c = conn.cursor()
  if request.method == 'GET':
    c.execute('SELECT * FROM sales ORDER BY id DESC')
    rows = c.fetchall()
    sales_list = []
    for r in rows:
      row_dict = dict(r)
      try:
        row_dict['items'] = json.loads(row_dict['items_json'])
      except:
        row_dict['items'] = []
      sales_list.append(row_dict)
    conn.close()
    return jsonify(sales_list)
  elif request.method == 'POST':
    d = request.get_json() or {}
    items_str = json.dumps(d.get('items', []))
    c.execute(
        'INSERT INTO sales (sale_date, cashier, customer_name, total_amount,'
        ' items_json) VALUES (?,?,?,?,?)',
        (
            d.get('date'),
            d.get('cashier'),
            d.get('customer'),
            d.get('total'),
            items_str,
        ),
    )
    sale_id = c.lastrowid
    for item in d.get('items', []):
      c.execute(
          'UPDATE medicines SET stock = stock - ? WHERE id = ?',
          (item.get('qty'), item.get('id')),
      )
    conn.commit()
    conn.close()
    return jsonify({
        'status': 'success',
        'sale_id': sale_id,
        'receipt_no': f'REC-{str(sale_id).zfill(5)}',
    })
  elif request.method == 'DELETE':
    c.execute('DELETE FROM sales WHERE id=?', (request.args.get('id'),))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})


@app.route('/api/accounts', methods=['GET', 'POST', 'PUT', 'DELETE'])
def manage_accounts():
  conn = get_db()
  c = conn.cursor()
  if request.method == 'GET':
    c.execute('SELECT * FROM accounts')
    rows = c.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
  elif request.method == 'POST':
    d = request.get_json() or {}
    c.execute(
        'INSERT INTO accounts (description, type, amount) VALUES (?,?,?)',
        (d.get('description'), d.get('type'), d.get('amount')),
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})
  elif request.method == 'PUT':
    d = request.get_json() or {}
    c.execute(
        'UPDATE accounts SET description=?, type=?, amount=? WHERE id=?',
        (d.get('description'), d.get('type'), d.get('amount'), d.get('id')),
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})
  elif request.method == 'DELETE':
    c.execute('DELETE FROM accounts WHERE id=?', (request.args.get('id'),))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})


@app.route('/api/suppliers', methods=['GET', 'POST', 'PUT', 'DELETE'])
def manage_suppliers():
  conn = get_db()
  c = conn.cursor()
  if request.method == 'GET':
    c.execute('SELECT * FROM suppliers')
    rows = c.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
  elif request.method == 'POST':
    d = request.get_json() or {}
    c.execute(
        'INSERT INTO suppliers (company_name, contact_person, phone, email)'
        ' VALUES (?,?,?,?)',
        (
            d.get('company_name'),
            d.get('contact_person'),
            d.get('phone'),
            d.get('email'),
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})
  elif request.method == 'PUT':
    d = request.get_json() or {}
    c.execute(
        'UPDATE suppliers SET company_name=?, contact_person=?, phone=?, email=?'
        ' WHERE id=?',
        (
            d.get('company_name'),
            d.get('contact_person'),
            d.get('phone'),
            d.get('email'),
            d.get('id'),
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})
  elif request.method == 'DELETE':
    c.execute('DELETE FROM suppliers WHERE id=?', (request.args.get('id'),))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})


@app.route('/api/employees', methods=['GET', 'POST', 'PUT', 'DELETE'])
def manage_employees():
  conn = get_db()
  c = conn.cursor()
  if request.method == 'GET':
    # pin is never sent to the frontend — it already only shows ****
    c.execute('SELECT id, name, position, department, salary FROM employees')
    rows = c.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
  elif request.method == 'POST':
    d = request.get_json() or {}
    c.execute(
        'INSERT INTO employees (name, position, department, salary, pin)'
        ' VALUES (?,?,?,?,?)',
        (
            d.get('name'),
            d.get('position'),
            d.get('department'),
            d.get('salary'),
            generate_password_hash(d.get('pin') or ''),
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})
  elif request.method == 'PUT':
    d = request.get_json() or {}
    # Only hash+update the pin if a new one was actually provided —
    # otherwise leave the existing hash alone so editing someone's
    # department doesn't silently wipe their login.
    if d.get('pin'):
      c.execute(
          'UPDATE employees SET name=?, position=?, department=?, salary=?, pin=?'
          ' WHERE id=?',
          (
              d.get('name'),
              d.get('position'),
              d.get('department'),
              d.get('salary'),
              generate_password_hash(d.get('pin')),
              d.get('id'),
          ),
      )
    else:
      c.execute(
          'UPDATE employees SET name=?, position=?, department=?, salary=?'
          ' WHERE id=?',
          (
              d.get('name'),
              d.get('position'),
              d.get('department'),
              d.get('salary'),
              d.get('id'),
          ),
      )
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})
  elif request.method == 'DELETE':
    c.execute('DELETE FROM employees WHERE id=?', (request.args.get('id'),))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})


@app.route('/api/tasks', methods=['GET', 'POST', 'PUT', 'DELETE'])
def manage_tasks():
  conn = get_db()
  c = conn.cursor()
  if request.method == 'GET':
    c.execute('SELECT * FROM tasks')
    rows = c.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
  elif request.method == 'POST':
    d = request.get_json() or {}
    c.execute(
        'INSERT INTO tasks (task_name, assigned_to, status) VALUES (?,?,?)',
        (d.get('task_name'), d.get('assigned_to'), d.get('status')),
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})
  elif request.method == 'PUT':
    d = request.get_json() or {}
    c.execute(
        'UPDATE tasks SET task_name=?, assigned_to=?, status=? WHERE id=?',
        (
            d.get('task_name'),
            d.get('assigned_to'),
            d.get('status'),
            d.get('id'),
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})
  elif request.method == 'DELETE':
    c.execute('DELETE FROM tasks WHERE id=?', (request.args.get('id'),))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})


@app.route('/api/chats', methods=['GET', 'POST', 'PUT', 'DELETE'])
def manage_chats():
  conn = get_db()
  c = conn.cursor()
  if request.method == 'GET':
    sender = request.args.get('sender')
    recipient = request.args.get('recipient')
    c.execute(
        'SELECT id, sender, recipient, message, timestamp, chat_type,'
        ' image_url, audio_url, is_read FROM chats'
    )
    rows = c.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
  elif request.method == 'POST':
    if request.is_json:
      d = request.get_json() or {}
      sender = d.get('sender')
      recipient = d.get('recipient')
      message = d.get('message')
      timestamp = d.get('timestamp')
      chat_type = d.get('chat_type')
      image_url = d.get('image_url')
      audio_url = d.get('audio_url')
    else:
      sender = request.form.get('sender')
      recipient = request.form.get('recipient')
      message = request.form.get('message')
      timestamp = request.form.get('timestamp')
      chat_type = request.form.get('chat_type')

      image_url = None
      if 'image' in request.files:
        file = request.files['image']
        if file.filename:
          filename = secure_filename(file.filename)
          filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
          file.save(filepath)
          image_url = f'/uploads/{filename}'

      audio_url = None
      if 'audio' in request.files:
        file = request.files['audio']
        if file.filename:
          filename = secure_filename(file.filename)
          filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
          file.save(filepath)
          audio_url = f'/uploads/{filename}'

    c.execute(
        'INSERT INTO chats (sender, recipient, message, timestamp, chat_type,'
        ' image_url, audio_url, is_read) VALUES (?,?,?,?,?,?,?,0)',
        (
            sender,
            recipient,
            message,
            timestamp,
            chat_type,
            image_url,
            audio_url,
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})
  elif request.method == 'PUT':
    d = request.get_json() or {}
    recipient = d.get('recipient')
    c.execute('UPDATE chats SET is_read = 1 WHERE recipient = ?', (recipient,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})
  elif request.method == 'DELETE':
    if request.args.get('action') == 'clear_all':
      c.execute('DELETE FROM chats')
      conn.commit()
    conn.close()
    return jsonify({'status': 'success'})


@app.route('/uploads/<filename>')
def uploaded_file(filename):
  return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/api/chats/unread', methods=['GET'])
def get_unread_count():
  user = request.args.get('user')
  conn = get_db()
  c = conn.cursor()
  c.execute(
      "SELECT COUNT(*) FROM chats WHERE recipient = ? AND is_read = 0 AND"
      " chat_type != 'announcement'",
      (user,),
  )
  count = c.fetchone()[0]
  conn.close()
  return jsonify({'unread_count': count})


@app.route('/api/email_logs', methods=['GET'])
@app.route('/api/email-logs', methods=['GET'])
def get_email_logs():
  sender = request.args.get('sender')
  conn = get_db()
  c = conn.cursor()
  if sender == 'master':
    c.execute(
        'SELECT id, recipient, sender, body, timestamp FROM email_logs ORDER BY'
        ' id DESC'
    )
  else:
    c.execute(
        'SELECT id, recipient, sender, body, timestamp FROM email_logs WHERE'
        ' sender = ? ORDER BY id DESC',
        (sender,),
    )
  rows = c.fetchall()
  conn.close()
  return jsonify([dict(r) for r in rows])


def _try_send_smtp(recipient, subject, body_html):
  """Best-effort real send. Returns True if actually sent, False if
  SMTP isn't configured or the send failed (caller still logs either
  way, so nothing about the sale/receipt flow depends on this)."""
  if not SMTP_USERNAME or not SMTP_PASSWORD:
    return False
  try:
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = SMTP_USERNAME
    msg['To'] = recipient
    msg.attach(MIMEText(body_html, 'html'))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
      server.starttls()
      server.login(SMTP_USERNAME, SMTP_PASSWORD)
      server.sendmail(SMTP_USERNAME, [recipient], msg.as_string())
    return True
  except Exception as exc:
    print(f"SMTP send failed for {recipient}: {exc}")
    return False


@app.route('/api/send-email', methods=['POST'])
def send_email():
  d = request.get_json() or {}
  recipient = d.get('email')
  sender_name = d.get('sender')
  total = d.get('total')
  receipt_no = d.get('receipt_no')

  body_html = (
      '<p>Dear Customer,</p><p>Thank you for shopping at Kakama'
      f' Pharmaceuticals.</p><p><strong>Receipt #:</strong>'
      f' {receipt_no}<br><strong>Total Amount:</strong> UGX'
      f' {total}</p><p>Best regards,<br>{sender_name}</p>'
  )

  really_sent = _try_send_smtp(recipient, f'Your receipt {receipt_no}', body_html)

  conn = get_db()
  c = conn.cursor()
  c.execute(
      'INSERT INTO email_logs (recipient, sender, body, timestamp) VALUES'
      ' (?,?,?,?)',
      (
          recipient,
          sender_name,
          body_html,
          datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
      ),
  )
  conn.commit()
  conn.close()

  message = (
      f'Email successfully dispatched to {recipient}!' if really_sent
      else f'Logged for {recipient} (SMTP not configured — add SMTP_USERNAME/SMTP_PASSWORD in Vercel to actually send).'
  )
  return jsonify({'status': 'success', 'message': message})


@app.route('/api/receipt-pdf/<int:sale_id>', methods=['GET'])
def generate_receipt_pdf(sale_id):
  conn = get_db()
  c = conn.cursor()
  c.execute(
      'SELECT id, sale_date, cashier, customer_name, total_amount, items_json'
      ' FROM sales WHERE id = ?',
      (sale_id,),
  )
  sale = c.fetchone()
  conn.close()

  if not sale:
    return 'Sale not found', 404

  items = []
  try:
    items = json.loads(sale['items_json'])
  except:
    pass

  buffer = BytesIO()
  doc = SimpleDocTemplate(buffer, pagesize=letter)
  elements = []
  styles = getSampleStyleSheet()

  elements.append(
      Paragraph('Kakama Pharmaceuticals LTD', styles['Heading1'])
  )
  elements.append(
      Paragraph(
          'PO BOX 253, MUBENDE HIGHWAY | Tel: 0706118058', styles['Normal']
      )
  )
  elements.append(Spacer(1, 12))

  elements.append(
      Paragraph(
          f"<b>Receipt No:</b> REC-{str(sale['id']).zfill(5)}", styles['Normal']
      )
  )
  elements.append(
      Paragraph(f"<b>Date:</b> {sale['sale_date']}", styles['Normal'])
  )
  elements.append(
      Paragraph(f"<b>Cashier:</b> {sale['cashier']}", styles['Normal'])
  )
  elements.append(
      Paragraph(f"<b>Customer:</b> {sale['customer_name']}", styles['Normal'])
  )
  elements.append(Spacer(1, 12))

  table_data = [['Item', 'Qty', 'Rate (UGX)', 'Total (UGX)']]
  for item in items:
    table_data.append([
        item.get('name'),
        str(item.get('qty')),
        str(item.get('price')),
        str(item.get('qty') * item.get('price')),
    ])

  t = Table(table_data)
  t.setStyle(
      TableStyle([
          ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a73e8')),
          ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
          ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
          ('GRID', (0, 0), (-1, -1), 1, colors.grey),
      ])
  )
  elements.append(t)
  elements.append(Spacer(1, 12))
  elements.append(
      Paragraph(
          f"<b>TOTAL AMOUNT: UGX {sale['total_amount']:,.2f}</b>",
          styles['Heading2'],
      )
  )

  doc.build(elements)
  buffer.seek(0)
  return send_file(
      buffer,
      as_attachment=True,
      download_name=f'receipt_{sale_id}.pdf',
      mimetype='application/pdf',
  )


if __name__ == '__main__':
  app.run(debug=True, port=5000)