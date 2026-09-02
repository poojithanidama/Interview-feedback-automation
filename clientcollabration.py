# =============================================================
#  brs_service.py  –  BRS Microservice (Standalone Flask)
#  Run on a separate port, e.g.  python brs_service.py
#  app.py acts as HTTP proxy → this service → Snowflake
# =============================================================

import os
import uuid
import json
import base64
import pathlib
import datetime
import snowflake.connector

from flask       import Flask, request, jsonify
from dotenv      import load_dotenv
from waitress    import serve
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

# ------------------------------------------------------------------
# ENV LOADING  (same .env as app.py)
# ------------------------------------------------------------------
_script_dir = pathlib.Path(__file__).resolve().parent
for _candidate in [
    _script_dir / ".env",
    _script_dir / ".env.txt",
    pathlib.Path.cwd() / ".env",
    pathlib.Path.cwd() / ".env.txt",
]:
    if _candidate.exists():
        load_dotenv(dotenv_path=_candidate, override=True)
        print(f"[brs_service] dotenv loaded: {_candidate}")
        break

# ------------------------------------------------------------------
# RSA KEY  (same rsa_key.p8 as app.py)
# ------------------------------------------------------------------
_key_path = _script_dir / "rsa_key.p8"
with open(_key_path, "rb") as _kf:
    _private_key = serialization.load_pem_private_key(
        _kf.read(), password=None, backend=default_backend()
    )
_PKB = _private_key.private_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)

# ------------------------------------------------------------------
# SNOWFLAKE CONNECTION  (uses same credentials as app.py)
# ------------------------------------------------------------------
def get_db_connection():
    """
    Uses the SAME Snowflake account details already in app.py:
        user      = ChakoraHub
        account   = gpguymt-ta88699
        warehouse = APP_READONLY_WH
        database  = VSRSUBHASH$CHAKORA_DB
        schema    = CHAKORA
    """
    conn = snowflake.connector.connect(
        user      = "ChakoraHub",
        account   = "gpguymt-ta88699",
        private_key = _PKB,
        warehouse = "APP_READONLY_WH",
        database  = "VSRSUBHASH$CHAKORA_DB",
        schema    = "CHAKORA",
    )
    # Wake warehouse
    cur = conn.cursor()
    cur.execute("SELECT 1")
    cur.close()
    return conn


# ------------------------------------------------------------------
# FLASK APP
# ------------------------------------------------------------------
app = Flask(__name__)


# ══════════════════════════════════════════════════════════════════
#  POST /brs/submit
#  Receives JSON from app.py proxy, validates Application ID,
#  inserts BRS record into Snowflake.
#  (S3 upload removed – Lambda-only feature; file stored as name only)
# ══════════════════════════════════════════════════════════════════
@app.route("/brs/submit", methods=["POST"])
def brs_submit():

    conn   = None
    cursor = None

    try:
        body = request.get_json(silent=True) or {}

        # ── Required field ──────────────────────────────────────────
        application_id = (body.get("project_id") or "").strip()
        if not application_id:
            return jsonify({"error": "Application ID (project_id) is required"}), 400

        conn   = get_db_connection()
        cursor = conn.cursor(snowflake.connector.DictCursor)

        # ── Validate Application ID exists ──────────────────────────
        cursor.execute(
            """
            SELECT 1
            FROM CHAKORA.INDUSTRY_APPLICATIONS
            WHERE APPLICATION_ID = %s
            LIMIT 1
            """,
            (application_id,),
        )
        if not cursor.fetchone():
            return jsonify({
                "error": (
                    "Invalid Application ID. "
                    "Please submit a valid application before uploading BRS."
                )
            }), 400

        # ── Generate BRS ID ─────────────────────────────────────────
        brs_id = f"BRS-{datetime.datetime.now().year}-{uuid.uuid4().hex[:8].upper()}"

        filename = (body.get("filename") or "").strip() or None

        # ── INSERT into Snowflake ────────────────────────────────────
        cursor.execute(
            """
            INSERT INTO CHAKORA.BRS_RECORDS (
                BRS_ID, PROJECT_ID, PROJECT_NAME, PROJECT_DESCRIPTION,
                CLIENT_NAME, DEPARTMENT, REQUIREMENT_TYPE, PRIORITY,
                START_DATE, END_DATE, CONTACT_EMAIL, CONTACT_PHONE,
                FILE_NAME, STATUS, CREATED_DATE
            )
            VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, 'Submitted', CURRENT_TIMESTAMP()
            )
            """,
            (
                brs_id,
                application_id,
                body.get("project_name"),
                body.get("project_description"),
                body.get("client_name"),
                body.get("department"),
                body.get("requirement_type"),
                body.get("priority"),
                body.get("start_date"),
                body.get("end_date"),
                body.get("contact_email"),
                body.get("contact_phone"),
                filename,
            ),
        )
        conn.commit()

        print(f"✅ BRS inserted: {brs_id} | app_id={application_id}")
        return jsonify({
            "message": "BRS submitted successfully",
            "brs_id":  brs_id,
        }), 200

    except Exception as exc:
        print(f"❌ BRS submit error: {exc}")
        return jsonify({"error": f"Internal error: {str(exc)}"}), 500

    finally:
        try:
            if cursor: cursor.close()
            if conn:   conn.close()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
#  POST /brs/alignment-confirm
#  Called by app.py after user checks alignment charter checkbox.
#  Marks the BRS record as alignment-confirmed in Snowflake.
# ══════════════════════════════════════════════════════════════════
@app.route("/brs/alignment-confirm", methods=["POST"])
def alignment_confirm():

    conn   = None
    cursor = None

    try:
        body   = request.get_json(silent=True) or {}
        brs_id = (body.get("brs_id") or "").strip()

        if not brs_id:
            # No BRS ID passed → alignment done without a BRS (allowed)
            return jsonify({"message": "Alignment noted (no brs_id)"}), 200

        conn   = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE CHAKORA.BRS_RECORDS
            SET    STATUS       = 'Alignment Confirmed',
                   CREATED_DATE = CURRENT_TIMESTAMP()
            WHERE  BRS_ID = %s
            """,
            (brs_id,),
        )
        conn.commit()
        print(f"✅ Alignment confirmed for BRS: {brs_id}")
        return jsonify({"message": "Alignment confirmed", "brs_id": brs_id}), 200

    except Exception as exc:
        print(f"❌ Alignment confirm error: {exc}")
        return jsonify({"error": str(exc)}), 500

    finally:
        try:
            if cursor: cursor.close()
            if conn:   conn.close()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
#  Health check
# ══════════════════════════════════════════════════════════════════
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "brs_service"}), 200


# ------------------------------------------------------------------
# ENTRY POINT  –  runs on port 5050 (different from app.py's 8080)
# ------------------------------------------------------------------
if __name__ == "__main__":
    print("🚀 BRS Microservice starting on 0.0.0.0:5050 ...")
    serve(app, host="0.0.0.0", port=8020, threads=10)