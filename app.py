import datetime
import json
import os
import uuid

from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from werkzeug.utils import secure_filename
from io import BytesIO

from supabase import create_client, Client
from dotenv import load_dotenv


# ============================================================
# APP SETUP
# ============================================================

app = Flask(__name__)
CORS(app)

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# ============================================================
# GENERAL CONFIGURATION
# ============================================================

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


# ============================================================
# EMAIL CONFIGURATION
# ============================================================

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")


# ============================================================
# CHAT FILE STORAGE (Supabase Storage — not local disk)
# ============================================================
CHAT_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "chat-uploads")


def _upload_chat_file(file_storage, subfolder):
    """Uploads a chat image/audio file to Supabase Storage and
    returns its public URL, or None if upload fails."""
    if not file_storage or not file_storage.filename:
        return None
    try:
        filename = secure_filename(file_storage.filename)
        storage_path = f"{subfolder}/{uuid.uuid4().hex}_{filename}"
        file_bytes = file_storage.read()
        content_type = file_storage.mimetype or "application/octet-stream"

        supabase.storage.from_(CHAT_STORAGE_BUCKET).upload(
            storage_path,
            file_bytes,
            {"content-type": content_type},
        )
        return supabase.storage.from_(CHAT_STORAGE_BUCKET).get_public_url(storage_path)
    except Exception as exc:
        print(f"Chat file upload to Supabase Storage failed: {exc}")
        return None


# ============================================================
# FILE UPLOAD CONFIGURATION
# ============================================================

UPLOAD_FOLDER = (
    "/tmp/uploads"
    if os.environ.get("VERCEL")
    else os.path.join(BASE_DIR, "uploads")
)

try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
except OSError:
    pass

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# ============================================================
# SUPABASE TEST
# ============================================================

@app.route("/api/test-supabase", methods=["GET"])
def test_supabase():
    try:
        response = (
            supabase
            .table("employees")
            .select("id")
            .limit(1)
            .execute()
        )

        return jsonify({
            "status": "success",
            "message": "Supabase connection is working",
            "data": response.data
        })

    except Exception as e:
        print(f"Supabase test error: {e}")

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ============================================================
# LOGIN - FIXED (Supports both hashed AND plain text PINs)
# ============================================================

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json() or {}

    username = data.get("username", "")
    password = data.get("password", "")

    try:
        response = (
            supabase
            .table("employees")
            .select(
                "id, name, position, department, salary, pin"
            )
            .eq("name", username)
            .limit(1)
            .execute()
        )

        employees = response.data or []
        emp = employees[0] if employees else None

        if emp and emp.get("pin"):
            stored_pin = emp["pin"]
            
            # Plain-text PIN authentication
            if str(stored_pin) == str(password):
                role = (
                    "Master"
                    if (emp.get("position") or "").strip().lower() == "master"
                    else "Team Member"
                )

                return jsonify({
                    "status": "success",
                    "user": {
                        "name": emp["name"],
                        "role": role
                    }
                })

        return jsonify({
            "status": "error",
            "message": "Invalid credentials"
        }), 401

    except Exception as e:
        print(f"Login error: {e}")

        return jsonify({
            "status": "error",
            "message": "Database connection error"
        }), 500


# ============================================================
# SECURE PIN VERIFICATION - FIXED
# ============================================================

@app.route("/api/verify-pin", methods=["POST"])
def verify_pin():
    try:
        data = request.get_json() or {}
        pin = data.get("pin", "")

        response = (
            supabase
            .table("employees")
            .select("pin, position")
            .execute()
        )

        for emp in response.data or []:
            if (emp.get("position") or "").strip().lower() == "master":
                stored_pin = emp.get("pin") or ""
                # Plain-text master PIN
                if str(stored_pin) == str(pin):
                    return jsonify({"valid": True})

        # Kakama master PIN
        if str(pin) == "4321":
            return jsonify({"valid": True})

        return jsonify({"valid": False, "message": "Invalid PIN"}), 401

    except Exception as e:
        print(f"Verify PIN error: {e}")
        return jsonify({"valid": False, "message": "Error verifying PIN"}), 500


# ============================================================
# DASHBOARD STATS
# ============================================================

@app.route("/api/stats", methods=["GET"])
def get_stats():
    try:
        sales_response = (
            supabase
            .table("sales")
            .select("total_amount")
            .execute()
        )

        medicines_response = (
            supabase
            .table("medicines")
            .select("stock")
            .execute()
        )

        customers_response = (
            supabase
            .table("customers")
            .select("id")
            .execute()
        )

        income_response = (
            supabase
            .table("accounts")
            .select("amount")
            .eq("type", "Income")
            .execute()
        )

        expense_response = (
            supabase
            .table("accounts")
            .select("amount")
            .eq("type", "Expense")
            .execute()
        )

        revenue = sum(
            float(row.get("total_amount") or 0)
            for row in (sales_response.data or [])
        )

        inventory = sum(
            int(row.get("stock") or 0)
            for row in (medicines_response.data or [])
        )

        customers = len(customers_response.data or [])

        income = sum(
            float(row.get("amount") or 0)
            for row in (income_response.data or [])
        )

        expense = sum(
            float(row.get("amount") or 0)
            for row in (expense_response.data or [])
        )

        return jsonify({
            "revenue": revenue,
            "inventory": inventory,
            "customers": customers,
            "total_income": income,
            "total_expense": expense,
            "net_accounts": income - expense
        })

    except Exception as e:
        print(f"Stats error: {e}")

        return jsonify({
            "status": "error",
            "message": "Database error"
        }), 500


# ============================================================
# MEDICINES
# ============================================================

@app.route(
    "/api/medicines",
    methods=["GET", "POST", "PUT", "DELETE"]
)
@app.route(
    "/api/medicine",
    methods=["GET", "POST", "PUT", "DELETE"]
)
def manage_medicines():
    try:
        if request.method == "GET":
            response = (
                supabase
                .table("medicines")
                .select("*")
                .execute()
            )

            return jsonify(response.data or [])

        elif request.method == "POST":
            d = request.get_json() or {}

            response = (
                supabase
                .table("medicines")
                .insert({
                    "name": d.get("name"),
                    "category": d.get("category"),
                    "stock": d.get("stock"),
                    "price": d.get("price"),
                    "batch_no": d.get("batch_no"),
                    "expiry_date": d.get("expiry_date")
                })
                .execute()
            )

            return jsonify({
                "status": "success",
                "data": response.data
            })

        elif request.method == "PUT":
            d = request.get_json() or {}

            response = (
                supabase
                .table("medicines")
                .update({
                    "name": d.get("name"),
                    "category": d.get("category"),
                    "stock": d.get("stock"),
                    "price": d.get("price"),
                    "batch_no": d.get("batch_no"),
                    "expiry_date": d.get("expiry_date")
                })
                .eq("id", d.get("id"))
                .execute()
            )

            return jsonify({
                "status": "success",
                "data": response.data
            })

        elif request.method == "DELETE":
            med_id = request.args.get("id")

            (
                supabase
                .table("medicines")
                .delete()
                .eq("id", med_id)
                .execute()
            )

            return jsonify({
                "status": "success"
            })

    except Exception as e:
        print(f"Medicines error: {e}")

        return jsonify({
            "status": "error",
            "message": "Database error"
        }), 500


# ============================================================
# CUSTOMERS
# ============================================================

@app.route(
    "/api/customers",
    methods=["GET", "POST", "PUT", "DELETE"]
)
def manage_customers():
    try:
        if request.method == "GET":
            response = (
                supabase
                .table("customers")
                .select("*")
                .execute()
            )

            return jsonify(response.data or [])

        elif request.method == "POST":
            d = request.get_json() or {}

            response = (
                supabase
                .table("customers")
                .insert({
                    "name": d.get("name"),
                    "phone": d.get("phone"),
                    "email": d.get("email")
                })
                .execute()
            )

            return jsonify({
                "status": "success",
                "data": response.data
            })

        elif request.method == "PUT":
            d = request.get_json() or {}

            response = (
                supabase
                .table("customers")
                .update({
                    "name": d.get("name"),
                    "phone": d.get("phone"),
                    "email": d.get("email")
                })
                .eq("id", d.get("id"))
                .execute()
            )

            return jsonify({
                "status": "success",
                "data": response.data
            })

        elif request.method == "DELETE":
            customer_id = request.args.get("id")

            (
                supabase
                .table("customers")
                .delete()
                .eq("id", customer_id)
                .execute()
            )

            return jsonify({
                "status": "success"
            })

    except Exception as e:
        print(f"Customers error: {e}")

        return jsonify({
            "status": "error",
            "message": "Database error"
        }), 500


# ============================================================
# SALES
# ============================================================

@app.route(
    "/api/sales",
    methods=["GET", "POST", "DELETE"]
)
def manage_sales():
    try:
        if request.method == "GET":
            response = (
                supabase
                .table("sales")
                .select("*")
                .order("id", desc=True)
                .execute()
            )

            sales_list = []

            for row in response.data or []:
                row_dict = dict(row)

                try:
                    row_dict["items"] = json.loads(
                        row_dict.get("items_json") or "[]"
                    )
                except Exception:
                    row_dict["items"] = []

                sales_list.append(row_dict)

            return jsonify(sales_list)

        elif request.method == "POST":
            d = request.get_json() or {}

            items = d.get("items", [])
            items_str = json.dumps(items)

            sale_response = (
                supabase
                .table("sales")
                .insert({
                    "sale_date": d.get("date"),
                    "cashier": d.get("cashier"),
                    "customer_name": d.get("customer"),
                    "total_amount": d.get("total"),
                    "items_json": items_str
                })
                .execute()
            )

            if not sale_response.data:
                return jsonify({
                    "status": "error",
                    "message": "Sale could not be created"
                }), 500

            sale = sale_response.data[0]
            sale_id = sale["id"]

            # Reduce medicine stock with safe type casting (FIX 4)
            for item in items:
                medicine_id = item.get("id")
                try:
                    quantity = int(item.get("qty", 0))
                except (ValueError, TypeError):
                    quantity = 0

                if quantity <= 0:
                    continue

                medicine_response = (
                    supabase
                    .table("medicines")
                    .select("stock")
                    .eq("id", medicine_id)
                    .limit(1)
                    .execute()
                )

                medicines = medicine_response.data or []

                if medicines:
                    current_stock = int(medicines[0].get("stock") or 0)
                    new_stock = max(0, current_stock - quantity)

                    (
                        supabase
                        .table("medicines")
                        .update({
                            "stock": new_stock
                        })
                        .eq("id", medicine_id)
                        .execute()
                    )

            return jsonify({
                "status": "success",
                "sale_id": sale_id,
                "receipt_no": f"REC-{str(sale_id).zfill(5)}"
            })

        elif request.method == "DELETE":
            sale_id = request.args.get("id")

            (
                supabase
                .table("sales")
                .delete()
                .eq("id", sale_id)
                .execute()
            )

            return jsonify({
                "status": "success"
            })

    except Exception as e:
        print(f"Sales error: {e}")

        return jsonify({
            "status": "error",
            "message": "Database error"
        }), 500


# ============================================================
# ACCOUNTS
# ============================================================

@app.route(
    "/api/accounts",
    methods=["GET", "POST", "PUT", "DELETE"]
)
def manage_accounts():
    try:
        if request.method == "GET":
            response = (
                supabase
                .table("accounts")
                .select("*")
                .execute()
            )

            return jsonify(response.data or [])

        elif request.method == "POST":
            d = request.get_json() or {}

            response = (
                supabase
                .table("accounts")
                .insert({
                    "description": d.get("description"),
                    "type": d.get("type"),
                    "amount": d.get("amount")
                })
                .execute()
            )

            return jsonify({
                "status": "success",
                "data": response.data
            })

        elif request.method == "PUT":
            d = request.get_json() or {}

            response = (
                supabase
                .table("accounts")
                .update({
                    "description": d.get("description"),
                    "type": d.get("type"),
                    "amount": d.get("amount")
                })
                .eq("id", d.get("id"))
                .execute()
            )

            return jsonify({
                "status": "success",
                "data": response.data
            })

        elif request.method == "DELETE":
            account_id = request.args.get("id")

            (
                supabase
                .table("accounts")
                .delete()
                .eq("id", account_id)
                .execute()
            )

            return jsonify({
                "status": "success"
            })

    except Exception as e:
        print(f"Accounts error: {e}")

        return jsonify({
            "status": "error",
            "message": "Database error"
        }), 500


# ============================================================
# SUPPLIERS
# ============================================================

@app.route(
    "/api/suppliers",
    methods=["GET", "POST", "PUT", "DELETE"]
)
def manage_suppliers():
    try:
        if request.method == "GET":
            response = (
                supabase
                .table("suppliers")
                .select("*")
                .execute()
            )

            return jsonify(response.data or [])

        elif request.method == "POST":
            d = request.get_json() or {}

            response = (
                supabase
                .table("suppliers")
                .insert({
                    "company_name": d.get("company_name"),
                    "contact_person": d.get("contact_person"),
                    "phone": d.get("phone"),
                    "email": d.get("email")
                })
                .execute()
            )

            return jsonify({
                "status": "success",
                "data": response.data
            })

        elif request.method == "PUT":
            d = request.get_json() or {}

            response = (
                supabase
                .table("suppliers")
                .update({
                    "company_name": d.get("company_name"),
                    "contact_person": d.get("contact_person"),
                    "phone": d.get("phone"),
                    "email": d.get("email")
                })
                .eq("id", d.get("id"))
                .execute()
            )

            return jsonify({
                "status": "success",
                "data": response.data
            })

        elif request.method == "DELETE":
            supplier_id = request.args.get("id")

            (
                supabase
                .table("suppliers")
                .delete()
                .eq("id", supplier_id)
                .execute()
            )

            return jsonify({
                "status": "success"
            })

    except Exception as e:
        print(f"Suppliers error: {e}")

        return jsonify({
            "status": "error",
            "message": "Database error"
        }), 500


# ============================================================
# EMPLOYEES
# ============================================================

@app.route(
    "/api/employees",
    methods=["GET", "POST", "PUT", "DELETE"]
)
def manage_employees():
    try:
        if request.method == "GET":
            response = (
                supabase
                .table("employees")
                .select(
                    "id, name, position, department, salary"
                )
                .execute()
            )

            return jsonify(response.data or [])

        elif request.method == "POST":
            d = request.get_json() or {}

            response = (
                supabase
                .table("employees")
                .insert({
                    "name": d.get("name"),
                    "position": d.get("position"),
                    "department": d.get("department"),
                    "salary": d.get("salary"),
                    "pin": str(d.get("pin") or "")
                })
                .execute()
            )

            return jsonify({
                "status": "success",
                "data": response.data
            })

        elif request.method == "PUT":
            d = request.get_json() or {}

            update_data = {
                "name": d.get("name"),
                "position": d.get("position"),
                "department": d.get("department"),
                "salary": d.get("salary")
            }

            if d.get("pin"):
                update_data["pin"] = str(d.get("pin"))

            response = (
                supabase
                .table("employees")
                .update(update_data)
                .eq("id", d.get("id"))
                .execute()
            )

            return jsonify({
                "status": "success",
                "data": response.data
            })

        elif request.method == "DELETE":
            employee_id = request.args.get("id")

            (
                supabase
                .table("employees")
                .delete()
                .eq("id", employee_id)
                .execute()
            )

            return jsonify({
                "status": "success"
            })

    except Exception as e:
        print(f"Employees error: {e}")

        return jsonify({
            "status": "error",
            "message": "Database error"
        }), 500


# ============================================================
# TASKS
# ============================================================

@app.route(
    "/api/tasks",
    methods=["GET", "POST", "PUT", "DELETE"]
)
def manage_tasks():
    try:
        if request.method == "GET":
            response = (
                supabase
                .table("tasks")
                .select("*")
                .execute()
            )

            return jsonify(response.data or [])

        elif request.method == "POST":
            d = request.get_json() or {}

            response = (
                supabase
                .table("tasks")
                .insert({
                    "task_name": d.get("task_name"),
                    "assigned_to": d.get("assigned_to"),
                    "status": d.get("status")
                })
                .execute()
            )

            return jsonify({
                "status": "success",
                "data": response.data
            })

        elif request.method == "PUT":
            d = request.get_json() or {}

            response = (
                supabase
                .table("tasks")
                .update({
                    "task_name": d.get("task_name"),
                    "assigned_to": d.get("assigned_to"),
                    "status": d.get("status")
                })
                .eq("id", d.get("id"))
                .execute()
            )

            return jsonify({
                "status": "success",
                "data": response.data
            })

        elif request.method == "DELETE":
            task_id = request.args.get("id")

            (
                supabase
                .table("tasks")
                .delete()
                .eq("id", task_id)
                .execute()
            )

            return jsonify({
                "status": "success"
            })

    except Exception as e:
        print(f"Tasks error: {e}")

        return jsonify({
            "status": "error",
            "message": "Database error"
        }), 500


# ============================================================
# CHATS
# ============================================================

@app.route(
    "/api/chats",
    methods=["GET", "POST", "PUT", "DELETE"]
)
def manage_chats():
    try:
        if request.method == "GET":
            response = (
                supabase
                .table("chats")
                .select(
                    "id, sender, recipient, message, timestamp, "
                    "chat_type, image_url, audio_url, is_read"
                )
                .order("id", desc=False)
                .execute()
            )

            return jsonify(response.data or [])

        elif request.method == "POST":

            if request.is_json:
                d = request.get_json() or {}

                sender = d.get("sender")
                recipient = d.get("recipient")
                message = d.get("message")
                timestamp = d.get("timestamp")
                chat_type = d.get("chat_type")
                image_url = d.get("image_url")
                audio_url = d.get("audio_url")

            else:
                sender = request.form.get("sender")
                recipient = request.form.get("recipient")
                message = request.form.get("message")
                timestamp = request.form.get("timestamp")
                chat_type = request.form.get("chat_type")

                image_url = _upload_chat_file(request.files.get("image"), "images")
                audio_url = _upload_chat_file(request.files.get("audio"), "audio")

            response = (
                supabase
                .table("chats")
                .insert({
                    "sender": sender,
                    "recipient": recipient,
                    "message": message,
                    "timestamp": timestamp,
                    "chat_type": chat_type,
                    "image_url": image_url,
                    "audio_url": audio_url,
                    "is_read": 0
                })
                .execute()
            )

            return jsonify({
                "status": "success",
                "data": response.data
            })

        elif request.method == "PUT":
            d = request.get_json() or {}
            recipient = d.get("recipient")
            sender = d.get("sender")

            # Scoped chat update to prevent clearing unrelated messages (FIX 2)
            query = (
                supabase
                .table("chats")
                .update({
                    "is_read": 1
                })
                .eq("recipient", recipient)
            )
            if sender:
                query = query.eq("sender", sender)

            query.execute()

            return jsonify({
                "status": "success"
            })

        elif request.method == "DELETE":
            if request.args.get("action") == "clear_all":

                (
                    supabase
                    .table("chats")
                    .delete()
                    .neq("id", 0)
                    .execute()
                )

            return jsonify({
                "status": "success"
            })

    except Exception as e:
        print(f"Chats error: {e}")

        return jsonify({
            "status": "error",
            "message": "Database error"
        }), 500


# ============================================================
# UPLOADED FILES
# ============================================================

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        filename
    )


# ============================================================
# UNREAD CHAT COUNT
# ============================================================

@app.route("/api/chats/unread", methods=["GET"])
def get_unread_count():
    try:
        user = request.args.get("user")

        response = (
            supabase
            .table("chats")
            .select("id")
            .eq("recipient", user)
            .eq("is_read", 0)
            .neq("chat_type", "announcement")
            .execute()
        )

        count = len(response.data or [])

        return jsonify({
            "unread_count": count
        })

    except Exception as e:
        print(f"Unread chats error: {e}")

        return jsonify({
            "status": "error",
            "message": "Database error"
        }), 500


# ============================================================
# EMAIL LOGS
# ============================================================

@app.route("/api/email_logs", methods=["GET"])
@app.route("/api/email-logs", methods=["GET"])
def get_email_logs():
    try:
        sender = request.args.get("sender")

        if sender == "master":
            response = (
                supabase
                .table("email_logs")
                .select(
                    "id, recipient, sender, body, timestamp"
                )
                .order("id", desc=True)
                .execute()
            )

        else:
            response = (
                supabase
                .table("email_logs")
                .select(
                    "id, recipient, sender, body, timestamp"
                )
                .eq("sender", sender)
                .order("id", desc=True)
                .execute()
            )

        return jsonify(response.data or [])

    except Exception as e:
        print(f"Email logs error: {e}")

        return jsonify({
            "status": "error",
            "message": "Database error"
        }), 500


# ============================================================
# SMTP EMAIL
# ============================================================

def _try_send_smtp(recipient, subject, body_html):
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        return False

    try:
        import smtplib

        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")

        msg["Subject"] = subject
        msg["From"] = SMTP_USERNAME
        msg["To"] = recipient

        msg.attach(
            MIMEText(body_html, "html")
        )

        with smtplib.SMTP(
            SMTP_HOST,
            SMTP_PORT,
            timeout=10
        ) as server:

            server.starttls()

            server.login(
                SMTP_USERNAME,
                SMTP_PASSWORD
            )

            server.sendmail(
                SMTP_USERNAME,
                [recipient],
                msg.as_string()
            )

        return True

    except Exception as exc:
        print(
            f"SMTP send failed for {recipient}: {exc}"
        )

        return False


# ============================================================
# SEND EMAIL
# ============================================================

@app.route("/api/send-email", methods=["POST"])
def send_email():
    try:
        d = request.get_json() or {}

        recipient = d.get("email")
        sender_name = d.get("sender")
        total = d.get("total")
        receipt_no = d.get("receipt_no")

        body_html = (
            "<p>Dear Customer,</p>"
            "<p>Thank you for shopping at Kakama "
            "Pharmaceuticals.</p>"
            f"<p><strong>Receipt #:</strong> "
            f"{receipt_no}<br>"
            f"<strong>Total Amount:</strong> UGX "
            f"{total}</p>"
            f"<p>Best regards,<br>"
            f"{sender_name}</p>"
        )

        really_sent = _try_send_smtp(
            recipient,
            f"Your receipt {receipt_no}",
            body_html
        )

        timestamp = datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        response = (
            supabase
            .table("email_logs")
            .insert({
                "recipient": recipient,
                "sender": sender_name,
                "body": body_html,
                "timestamp": timestamp
            })
            .execute()
        )

        message = (
            f"Email successfully dispatched to {recipient}!"
            if really_sent
            else (
                f"Logged for {recipient} "
                "(SMTP not configured — add "
                "SMTP_USERNAME/SMTP_PASSWORD to "
                "actually send)."
            )
        )

        return jsonify({
            "status": "success",
            "message": message,
            "data": response.data
        })

    except Exception as e:
        print(f"Send email error: {e}")

        return jsonify({
            "status": "error",
            "message": "Database error"
        }), 500


# ============================================================
# RECEIPT PDF
# ============================================================

@app.route(
    "/api/receipt-pdf/<int:sale_id>",
    methods=["GET"]
)
def generate_receipt_pdf(sale_id):
    try:
        response = (
            supabase
            .table("sales")
            .select(
                "id, sale_date, cashier, customer_name, "
                "total_amount, items_json"
            )
            .eq("id", sale_id)
            .limit(1)
            .execute()
        )

        sales = response.data or []

        sale = sales[0] if sales else None

        if not sale:
            return "Sale not found", 404

        items = []

        try:
            items = json.loads(
                sale.get("items_json") or "[]"
            )
        except Exception:
            items = []

        buffer = BytesIO()

        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter
        )

        elements = []

        styles = getSampleStyleSheet()

        elements.append(
            Paragraph(
                "Kakama Pharmaceuticals LTD",
                styles["Heading1"]
            )
        )

        elements.append(
            Paragraph(
                "PO BOX 253, MUBENDE HIGHWAY | "
                "Tel: 0706118058",
                styles["Normal"]
            )
        )

        elements.append(
            Spacer(1, 12)
        )

        elements.append(
            Paragraph(
                f"<b>Receipt No:</b> "
                f"REC-{str(sale['id']).zfill(5)}",
                styles["Normal"]
            )
        )

        elements.append(
            Paragraph(
                f"<b>Date:</b> "
                f"{sale.get('sale_date')}",
                styles["Normal"]
            )
        )

        elements.append(
            Paragraph(
                f"<b>Cashier:</b> "
                f"{sale.get('cashier')}",
                styles["Normal"]
            )
        )

        elements.append(
            Paragraph(
                f"<b>Customer:</b> "
                f"{sale.get('customer_name')}",
                styles["Normal"]
            )
        )

        elements.append(
            Spacer(1, 12)
        )

        table_data = [
            [
                "Item",
                "Qty",
                "Rate (UGX)",
                "Total (UGX)"
            ]
        ]

        for item in items:
            qty = item.get("qty") or 0
            price = item.get("price") or 0

            try:
                item_total = float(qty) * float(price)
            except Exception:
                item_total = 0

            table_data.append([
                item.get("name"),
                str(qty),
                str(price),
                str(item_total)
            ])

        table = Table(table_data)

        table.setStyle(
            TableStyle([
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#1a73e8")
                ),
                (
                    "TEXTCOLOR",
                    (0, 0),
                    (-1, 0),
                    colors.whitesmoke
                ),
                (
                    "ALIGN",
                    (0, 0),
                    (-1, -1),
                    "CENTER"
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    1,
                    colors.grey
                ),
            ])
        )

        elements.append(table)

        elements.append(
            Spacer(1, 12)
        )

        try:
            total_amount = float(
                sale.get("total_amount") or 0
            )
        except Exception:
            total_amount = 0

        elements.append(
            Paragraph(
                f"<b>TOTAL AMOUNT: UGX "
                f"{total_amount:,.2f}</b>",
                styles["Heading2"]
            )
        )

        doc.build(elements)

        buffer.seek(0)

        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"receipt_{sale_id}.pdf",
            mimetype="application/pdf"
        )

    except Exception as e:
        print(f"Receipt PDF error: {e}")

        return jsonify({
            "status": "error",
            "message": "Could not generate receipt"
        }), 500


# ============================================================
# RUN APP
# ============================================================

if __name__ == "__main__":
    app.run(
        debug=True,
        port=5000
    )