"""
Internship Microservice - Fixed Version with Admin Review Flow
Maintains original workflow: PENDING → Admin Review → ACCEPTED/REJECTED
"""

import os
import uuid
import logging
import boto3
import snowflake.connector
import sys
import io
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Encoding setup
if sys.stdout.encoding != 'UTF-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'UTF-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ==========================================
# AWS Configuration
# ==========================================
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_KEY")
AWS_REGION = os.getenv("AWS_REGION", "eu-north-1")
S3_BUCKET = os.getenv("S3_BUCKET", "chakorahub-internship-docs")
SES_SENDER = os.getenv("SES_SENDER_EMAIL", "noreply@chakorahub.com")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "chakora_admin_2026")

# Debug AWS status
print("=" * 50)
print("AWS Configuration Status:")
print(f"AWS_ACCESS_KEY: {'✅ Set' if AWS_ACCESS_KEY else '❌ Missing'}")
print(f"AWS_SECRET_KEY: {'✅ Set' if AWS_SECRET_KEY else '❌ Missing'}")
print(f"AWS_REGION: {AWS_REGION}")
print(f"S3_BUCKET: {S3_BUCKET}")
print("=" * 50)

# Initialize AWS clients
s3 = None
ses = None

if AWS_ACCESS_KEY and AWS_SECRET_KEY:
    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=AWS_ACCESS_KEY,
            aws_secret_access_key=AWS_SECRET_KEY,
            region_name=AWS_REGION,
        )
        ses = boto3.client(
            "ses",
            aws_access_key_id=AWS_ACCESS_KEY,
            aws_secret_access_key=AWS_SECRET_KEY,
            region_name=AWS_REGION,
        )
        print("✅ AWS clients initialized successfully")
        
        # Test S3 bucket access (optional - won't fail if bucket doesn't exist)
        try:
            s3.head_bucket(Bucket=S3_BUCKET)
            print(f"✅ S3 bucket '{S3_BUCKET}' is accessible")
        except Exception as e:
            print(f"⚠️ S3 bucket '{S3_BUCKET}' not accessible: {e}")
            print("   Files will still upload, but bucket must exist")
    except Exception as e:
        print(f"❌ Failed to initialize AWS clients: {e}")
else:
    print("❌ AWS credentials missing - S3 and SES will not work")

ALLOWED_EXT = {"pdf", "jpg", "jpeg", "png", "webp"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def upload_to_s3(file_storage, doc_type, app_ref):
    """Upload to S3 using temp reference (pending review)"""
    if not s3:
        error_msg = "S3 client not initialized. Check AWS credentials."
        print(f"❌ {error_msg}")
        raise Exception(error_msg)
    
    ext = secure_filename(file_storage.filename).rsplit(".", 1)[-1].lower()
    s3_key = f"internships/pending/{app_ref}/{doc_type}/{uuid.uuid4().hex}.{ext}"
    ct = file_storage.content_type or "application/octet-stream"
    file_storage.stream.seek(0)
    
    try:
        s3.upload_fileobj(
            file_storage.stream,
            S3_BUCKET,
            s3_key,
            ExtraArgs={"ContentType": ct}
        )
        url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"
        print(f"✅ S3 upload OK: {url}")
        return url
    except Exception as e:
        print(f"❌ S3 upload failed: {e}")
        raise

# ==========================================
# Snowflake Connection
# ==========================================
def get_db_connection():
    """Connect to Snowflake using RSA key"""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        key_path = os.path.join(script_dir, "rsa_key.p8")
        
        if not os.path.exists(key_path):
            print(f"❌ RSA key not found at: {key_path}")
            return None
            
        with open(key_path, "rb") as key_file:
            private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None,
                backend=default_backend()
            )
        
        pkb = private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        
        conn = snowflake.connector.connect(
            user='ChakoraHub',
            account='gpguymt-ta88699',
            private_key=pkb,
            warehouse='COMPUTE_WH',
            database='"VSRSUBHASH$CHAKORA_DB"',
            schema="CHAKORA"
        )
        print("✅ Snowflake connected")
        return conn
    except Exception as e:
        print(f"❌ DB Connection Error: {e}")
        import traceback
        traceback.print_exc()
        return None

def generate_intern_id(conn):
    """Generate INTERN_ID only at acceptance: CH26I01, CH26I02 ..."""
    yy = datetime.now().strftime("%y")
    prefix = f"CH{yy}I"
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM NRM_INTERNSHIP_APPLICATIONS WHERE INTERN_ID LIKE %s",
        (f"{prefix}%",)
    )
    row = cur.fetchone()
    cur.close()
    seq = (row[0] if row else 0) + 1
    if seq > 99:
        raise ValueError("Maximum 99 internship slots for this year are filled")
    return f"{prefix}{str(seq).zfill(2)}"

def send_ses_email(to_email, subject, html_body):
    """Send email via AWS SES"""
    if not ses:
        print("⚠️ SES not initialized - skipping email")
        return False
    try:
        ses.send_email(
            Source=SES_SENDER,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}},
            },
        )
        print(f"✅ Email sent to {to_email}")
        return True
    except ClientError as e:
        print(f"❌ SES error: {e.response['Error']['Message']}")
        return False

def build_accepted_email(name, intern_id, domain, duration, start_date, reason):
    """Build HTML email for accepted application"""
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Inter,Arial,sans-serif;background:#f3f4f6;padding:40px 20px;margin:0;">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.08);">
  <div style="background:linear-gradient(135deg,#6366f1,#8b5cf6);padding:36px 40px;text-align:center;">
    <h1 style="color:#fff;margin:0;font-size:1.8rem;font-weight:800;">Congratulations!</h1>
    <p style="color:rgba(255,255,255,0.9);margin:10px 0 0;">Your internship application has been accepted</p>
  </div>
  <div style="padding:36px 40px;">
    <p style="color:#374151;">Dear <strong>{name}</strong>,</p>
    <p style="color:#374151;line-height:1.7;">We are delighted to inform you that your application has been <strong style="color:#10b981;">accepted</strong>.</p>
    <div style="background:linear-gradient(135deg,#6366f1,#8b5cf6);border-radius:14px;padding:24px;text-align:center;margin:28px 0;">
      <div style="color:rgba(255,255,255,0.85);font-size:0.85rem;margin-bottom:6px;">Your Internship ID</div>
      <div style="color:#fff;font-size:2.2rem;font-weight:800;letter-spacing:3px;">{intern_id}</div>
    </div>
    <p style="color:#374151;">Warm regards,<br><strong>ChakoraHub Internship Team</strong></p>
  </div>
</div>
</body>
</html>"""

def build_rejected_email(name, domain, reason):
    """Build HTML email for rejected application"""
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Inter,Arial,sans-serif;background:#f3f4f6;padding:40px 20px;margin:0;">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.08);">
  <div style="background:linear-gradient(135deg,#6b7280,#4b5563);padding:36px 40px;text-align:center;">
    <h1 style="color:#fff;margin:0;font-size:1.8rem;font-weight:800;">Application Update</h1>
  </div>
  <div style="padding:36px 40px;">
    <p style="color:#374151;">Dear <strong>{name}</strong>,</p>
    <p style="color:#374151;line-height:1.7;">Thank you for your interest. After careful review, we are unable to offer you a position at this time.</p>
    <div style="background:#fef2f2;border-left:4px solid #ef4444;border-radius:0 10px 10px 0;padding:18px 20px;margin:24px 0;">
      <div style="font-weight:700;color:#991b1b;margin-bottom:8px;">Feedback</div>
      <p style="color:#374151;margin:0;line-height:1.7;">{reason}</p>
    </div>
    <p style="color:#374151;">Best regards,<br><strong>ChakoraHub Internship Team</strong></p>
  </div>
</div>
</body>
</html>"""

# ==========================================
# STUDENT: Submit Application (PENDING status)
# ==========================================
@app.route('/api/internship/apply', methods=['POST'])
def apply_internship():
    conn = None
    try:
        f = request.form
        files = request.files
        
        print("=" * 50)
        print("📝 Received internship application")
        print(f"Form fields: {list(f.keys())}")
        print(f"Files: {list(files.keys())}")
        
        # Map form fields (handle both naming conventions)
        full_name = f.get("full_name") or f.get("fullname", "")
        email = f.get("email", "")
        phone = f.get("phone") or f.get("mobile", "")
        dob = f.get("date_of_birth") or f.get("dob", "")
        gender = f.get("gender", "")
        address = f.get("address", "")
        college_name = f.get("college_name") or f.get("college", "")
        branch = f.get("branch", "")
        year_of_study = f.get("year_of_study") or f.get("year", "")
        cgpa = f.get("cgpa", "")
        graduation_year = f.get("graduation_year", "")
        internship_domain = f.get("internship_domain") or f.get("area_of_interest", "")
        internship_duration = f.get("internship_duration") or f.get("duration", "")
        start_date = f.get("start_date", "")
        why_chakora = f.get("why_chakora", "")
        skills = f.get("skills", "")
        portfolio = f.get("portfolio", "")
        
        # Validate required fields
        required_fields = {
            "full_name": full_name,
            "email": email,
            "phone": phone,
            "dob": dob,
            "gender": gender,
            "address": address,
            "college_name": college_name,
            "branch": branch,
            "year_of_study": year_of_study,
            "graduation_year": graduation_year,
            "internship_domain": internship_domain,
            "internship_duration": internship_duration,
            "why_chakora": why_chakora
        }
        
        missing = [k for k, v in required_fields.items() if not v or not str(v).strip()]
        if missing:
            return jsonify({
                "success": False,
                "message": f"Missing fields: {', '.join(missing)}"
            }), 400
        
        # Validate files
        for fkey in ["resume", "id_card", "noc"]:
            if fkey not in files or not files[fkey].filename:
                return jsonify({
                    "success": False,
                    "message": f"Missing file: {fkey}"
                }), 400
            if not allowed_file(files[fkey].filename):
                return jsonify({
                    "success": False,
                    "message": f"Invalid file type for {fkey}. Allowed: PDF, JPG, PNG"
                }), 400
        
        # Connect to database
        conn = get_db_connection()
        if not conn:
            return jsonify({
                "success": False,
                "message": "Database connection failed"
            }), 500
        
        # Generate temporary reference ID for pending application
        app_ref = uuid.uuid4().hex[:16].upper()
        print(f"📝 Temp reference: {app_ref}")
        
        # Upload files to S3
        try:
            resume_url = upload_to_s3(files["resume"], "resume", app_ref)
            idcard_url = upload_to_s3(files["id_card"], "id_card", app_ref)
            noc_url = upload_to_s3(files["noc"], "noc", app_ref)
            print("✅ All files uploaded to S3")
        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"File upload failed: {str(e)}"
            }), 500
        
        # Insert into database with PENDING status
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO NRM_INTERNSHIP_APPLICATIONS (
                    INTERN_ID, FULL_NAME, EMAIL, PHONE, DATE_OF_BIRTH, GENDER, ADDRESS,
                    COLLEGE_NAME, BRANCH, YEAR_OF_STUDY, CGPA, GRADUATION_YEAR,
                    INTERNSHIP_DOMAIN, INTERNSHIP_DURATION, START_DATE,
                    WHY_CHAKORA, SKILLS, PORTFOLIO_URL,
                    STATUS, SUBMITTED_AT
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    'PENDING', CURRENT_TIMESTAMP()
                )
            """, (
                app_ref,
                full_name,
                email,
                phone,
                dob if dob else None,
                gender if gender else None,
                address if address else None,
                college_name,
                branch,
                year_of_study,
                cgpa if cgpa else None,
                graduation_year,
                internship_domain,
                internship_duration,
                start_date if start_date else None,
                why_chakora,
                skills if skills else None,
                portfolio if portfolio else None,
            ))
            
            # Insert documents
            for doc_type, url, orig in [
                ("RESUME", resume_url, files["resume"].filename),
                ("ID_CARD", idcard_url, files["id_card"].filename),
                ("NOC", noc_url, files["noc"].filename),
            ]:
                cur.execute("""
                    INSERT INTO NRM_INTERNSHIP_DOCUMENTS (INTERN_ID, DOC_TYPE, S3_URL, ORIGINAL_NAME)
                    VALUES (%s, %s, %s, %s)
                """, (app_ref, doc_type, url, secure_filename(orig)))
            
            conn.commit()
            print(f"✅ Application saved (ref: {app_ref}) — pending admin review")
            
            return jsonify({
                "success": True,
                "message": "Application submitted successfully! Our team will review it and send you an email within 3–5 business days."
            })
            
        except Exception as e:
            print(f"❌ Database insert error: {e}")
            import traceback
            traceback.print_exc()
            if conn:
                conn.rollback()
            return jsonify({
                "success": False,
                "message": f"Database error: {str(e)}"
            }), 500
        finally:
            cur.close()
            
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": f"Server error: {str(e)}"
        }), 500
    finally:
        if conn:
            conn.close()
            print("🔌 Database connection closed")

# ==========================================
# ADMIN: List all applications
# ==========================================
@app.route('/api/admin/internships', methods=['GET'])
def admin_list_applications():
    if request.headers.get("X-Admin-Secret") != ADMIN_SECRET:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    status_filter = request.args.get("status", "ALL")
    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "message": "DB connection failed"}), 500

    try:
        cur = conn.cursor()
        base_sql = """
            SELECT INTERN_ID, FULL_NAME, EMAIL, PHONE, COLLEGE_NAME, BRANCH,
                   INTERNSHIP_DOMAIN, INTERNSHIP_DURATION, START_DATE,
                   WHY_CHAKORA, SKILLS, STATUS, SUBMITTED_AT, CGPA,
                   YEAR_OF_STUDY, GRADUATION_YEAR, GENDER, DATE_OF_BIRTH, ADDRESS, PORTFOLIO_URL
            FROM NRM_INTERNSHIP_APPLICATIONS
        """
        if status_filter == "ALL":
            cur.execute(base_sql + " ORDER BY SUBMITTED_AT DESC")
        else:
            cur.execute(base_sql + " WHERE STATUS = %s ORDER BY SUBMITTED_AT DESC", (status_filter,))

        cols = [d[0].lower() for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

        for row in rows:
            cur.execute(
                "SELECT DOC_TYPE, S3_URL, ORIGINAL_NAME FROM NRM_INTERNSHIP_DOCUMENTS WHERE INTERN_ID = %s",
                (row["intern_id"],)
            )
            row["documents"] = [{"type": r[0], "url": r[1], "name": r[2]} for r in cur.fetchall()]
            for k, v in row.items():
                if hasattr(v, 'isoformat'):
                    row[k] = v.isoformat()

        cur.close()
        return jsonify({"success": True, "applications": rows, "count": len(rows)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        conn.close()

# ==========================================
# ADMIN: Accept or Reject Application
# ==========================================
@app.route('/api/admin/internships/review', methods=['POST'])
def admin_review_application():
    if request.headers.get("X-Admin-Secret") != ADMIN_SECRET:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    data = request.get_json() or {}
    app_ref = data.get("app_ref", "").strip()
    decision = data.get("decision", "").strip().upper()
    reason = data.get("reason", "").strip()

    if not app_ref or decision not in ("ACCEPTED", "REJECTED"):
        return jsonify({"success": False, "message": "app_ref and decision (ACCEPTED/REJECTED) are required"}), 400
    if not reason:
        return jsonify({"success": False, "message": "Reason/justification is required"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "message": "DB connection failed"}), 500

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT FULL_NAME, EMAIL, INTERNSHIP_DOMAIN, INTERNSHIP_DURATION, START_DATE, STATUS
            FROM NRM_INTERNSHIP_APPLICATIONS WHERE INTERN_ID = %s
        """, (app_ref,))
        row = cur.fetchone()
        if not row:
            return jsonify({"success": False, "message": "Application not found"}), 404

        full_name, email, domain, duration, start_date, current_status = row

        if current_status not in ("PENDING", "UNDER_REVIEW"):
            return jsonify({"success": False, "message": f"Application already {current_status}"}), 400

        intern_id = None

        if decision == "ACCEPTED":
            intern_id = generate_intern_id(conn)
            cur.execute("""
                UPDATE NRM_INTERNSHIP_APPLICATIONS
                SET STATUS = 'ACCEPTED', INTERN_ID = %s,
                    REVIEWED_AT = CURRENT_TIMESTAMP(), REVIEW_REASON = %s
                WHERE INTERN_ID = %s
            """, (intern_id, reason, app_ref))
            cur.execute("""
                UPDATE NRM_INTERNSHIP_DOCUMENTS SET INTERN_ID = %s WHERE INTERN_ID = %s
            """, (intern_id, app_ref))
            html = build_accepted_email(full_name, intern_id, domain, duration,
                                       str(start_date) if start_date else "To be confirmed", reason)
            subject = f"Congratulations! Your ChakoraHub Internship is Confirmed — {intern_id}"
        else:
            cur.execute("""
                UPDATE NRM_INTERNSHIP_APPLICATIONS
                SET STATUS = 'REJECTED',
                    REVIEWED_AT = CURRENT_TIMESTAMP(), REVIEW_REASON = %s
                WHERE INTERN_ID = %s
            """, (reason, app_ref))
            html = build_rejected_email(full_name, domain, reason)
            subject = "Update on Your ChakoraHub Internship Application"

        conn.commit()
        email_sent = send_ses_email(email, subject, html)
        cur.close()

        return jsonify({
            "success": True,
            "decision": decision,
            "intern_id": intern_id,
            "email_sent": email_sent,
            "message": f"Application {decision.lower()}. " + ("Email sent." if email_sent else "Email failed — check SES.")
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        conn.close()

# ==========================================
# ADMIN: Serve Panel HTML
# ==========================================
@app.route('/admin/internships', methods=['GET'])
def admin_panel():
    secret = request.args.get("secret", "")
    if secret != ADMIN_SECRET:
        return "<h3 style='font-family:sans-serif;color:red;padding:40px'>Unauthorized. Add ?secret=YOUR_ADMIN_SECRET to the URL.</h3>", 401
    try:
        with open(os.path.join(os.path.dirname(__file__), "admin_internships.html"), "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h3 style='font-family:sans-serif;padding:40px'>admin_internships.html not found</h3>", 404

# ==========================================
# HEALTH CHECK
# ==========================================
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'service': 'internship-service',
        'timestamp': datetime.now().isoformat()
    })

# ==========================================
# START THE SERVICE
# ==========================================
if __name__ == '__main__':
    port = int(os.getenv("INTERNSHIP_PORT", 5050))
    print(f"🚀 Starting internship service on port {port}")
    print(f"📁 Working directory: {os.getcwd()}")
    print(f"🔑 RSA key path: {os.path.join(os.path.dirname(__file__), 'rsa_key.p8')}")
    app.run(host='0.0.0.0', port=port, debug=True)