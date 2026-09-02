# employee_service.py - FIXED VERSION
# Change the Pydantic model to accept string for employee_db_id

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import snowflake.connector
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from werkzeug.security import check_password_hash
from datetime import datetime
import calendar
import os

# ==========================================
# DATABASE CONNECTION
# ==========================================
def get_db_connection():
    try:
        with open("rsa_key.p8", "rb") as key_file:
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
            database='VSRSUBHASH$CHAKORA_DB',
            schema="CHAKORA"
        )
        print("✅ Employee Service: Connected to Snowflake")
        return conn
        
    except Exception as e:
        print(f"❌ Employee Service DB Error: {e}")
        return None

# ==========================================
# FASTAPI APP
# ==========================================
app = FastAPI(title="Employee Microservice", version="1.0")

# Add CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# MODELS - FIXED employee_db_id to Optional[str]
# ==========================================
class EmployeeLogin(BaseModel):
    employee_id: str
    password: str

class EmployeeLoginResponse(BaseModel):
    success: bool
    message: str
    employee_id: Optional[str] = None
    employee_db_id: Optional[str] = None  # 🔧 FIXED: Changed from int to str
    employee_name: Optional[str] = None
    employee_email: Optional[str] = None
    employee_department: Optional[str] = None
    employee_designation: Optional[str] = None
    profile_pic: Optional[str] = None


class LeaveApplyRequest(BaseModel):
    employee_id: str
    leave_type: str
    from_date: str
    to_date: str
    reason: Optional[str] = None

class LeaveApplyResponse(BaseModel):
    success: bool
    message: str


class LeaveActionResponse(BaseModel):
    """Used by /api/manager/approve-leave and /api/manager/reject-leave."""
    success: bool
    message: str
    leave_id: Optional[int] = None


# ==========================================
# ROUTES
# ==========================================

# Health check
@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "employee"}

# Employee Login - FIXED
@app.post("/api/employee/login", response_model=EmployeeLoginResponse)
def employee_login(data: EmployeeLogin):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(500, "Database connection failed")
    
    try:
        cursor = conn.cursor(snowflake.connector.DictCursor)
        
        # Step 1: Get login credentials
        cursor.execute("""
            SELECT EMPLOYEE_ID, PASSWORD
            FROM EMP_NRM_LOGINS
            WHERE EMPLOYEE_ID = %s
            LIMIT 1
        """, (data.employee_id,))
        
        login_row = cursor.fetchone()
        
        if not login_row:
            raise HTTPException(400, "Employee ID not found")
        
        # Step 2: Verify password
        db_password = (login_row.get("PASSWORD") or "").strip()
        
        if db_password.startswith(("scrypt:", "$2a$", "$2b$", "pbkdf2:")):
            valid = check_password_hash(db_password, data.password)
        else:
            valid = (db_password == data.password)
        
        if not valid:
            raise HTTPException(401, "Invalid password")
        
        # Step 3: Get employee master data
        cursor.execute("""
            SELECT APPLICATION_ID, EMPLOYEE_NAME, EMAIL, STATUS
            FROM EMP_NRM_EMPLOYEES
            WHERE EMPLOYEE_ID = %s
            LIMIT 1
        """, (data.employee_id,))
        
        emp = cursor.fetchone()
        
        if not emp:
            raise HTTPException(404, "Employee data not found")
        
        # Check status
        if emp.get("STATUS") and emp["STATUS"].upper() != "ACTIVE":
            raise HTTPException(403, "Employee account is not active")
        
        # Step 4: Get personal info
        cursor.execute("""
            SELECT FIRST_NAME, LAST_NAME, PROFILE_PIC
            FROM EMP_NRM_PERSONAL
            WHERE EMPLOYEE_ID = %s
            LIMIT 1
        """, (data.employee_id,))
        
        personal = cursor.fetchone() or {}
        
        # Build display name
        display_name = emp.get("EMPLOYEE_NAME")
        if not display_name:
            first = personal.get('FIRST_NAME', '').strip()
            last = personal.get('LAST_NAME', '').strip()
            display_name = f"{first} {last}".strip() or "Employee"
        
        profile_pic = personal.get("PROFILE_PIC") or "profile_photo/defaultpicture.jpg"
        
        # Step 5: Get job info
        cursor.execute("""
            SELECT d.DEPT_NAME, des.TITLE
            FROM EMP_NRM_JOB_WORK j
            LEFT JOIN EMP_NRM_DEPARTMENTS d ON j.DEPT_ID = d.DEPT_ID
            LEFT JOIN EMP_NRM_DESIGNATIONS des ON j.DESIGNATION_ID = des.DESIGNATION_ID
            WHERE j.EMPLOYEE_ID = %s
            LIMIT 1
        """, (data.employee_id,))
        
        job = cursor.fetchone() or {}
        
        cursor.close()
        conn.close()
        
        print(f"✅ Employee login successful: {data.employee_id} - {display_name}")
        
        return EmployeeLoginResponse(
            success=True,
            message="Login successful",
            employee_id=data.employee_id,
            employee_db_id=emp.get("APPLICATION_ID"),  # ✅ Now returns string like 'APP005'
            employee_name=display_name,
            employee_email=emp.get("EMAIL", ""),
            employee_department=job.get("DEPT_NAME", "N/A"),
            employee_designation=job.get("TITLE", "N/A"),
            profile_pic=profile_pic
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Login error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Login error: {str(e)}")
    finally:
        if conn:
            conn.close()

@app.get("/api/employee/leave/history")
def get_leave_history(employee_id: str):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(500, "DB connection failed")

    try:
        cursor = conn.cursor(snowflake.connector.DictCursor)

        cursor.execute("""
            SELECT LEAVE_ID, START_DATE, END_DATE, REASON, STATUS, APPLIED_AT
            FROM EMP_NRM_LEAVE
            WHERE EMPLOYEE_ID = %s
            ORDER BY APPLIED_AT DESC
        """, (employee_id,))

        data = cursor.fetchall()
        cursor.close()
        return data

    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()



@app.get("/api/employee/festivals")
def get_festivals(year: int, month: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(500, "DB connection failed")

    try:
        cursor = conn.cursor(snowflake.connector.DictCursor)

        cursor.execute("""
            SELECT FESTIVAL_NAME, FESTIVAL_DATE
            FROM EMP_NRM_FESTIVALS
            WHERE EXTRACT(YEAR FROM FESTIVAL_DATE) = %s
            AND EXTRACT(MONTH FROM FESTIVAL_DATE) = %s
        """, (year, month))

        data = cursor.fetchall()
        cursor.close()
        return data

    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@app.post("/api/employee/leave/apply")
def apply_leave(data: LeaveApplyRequest):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(500, "DB connection failed")

    # EMP_NRM_LEAVE.TOTAL_DAYS is NOT NULL — must be supplied at INSERT time.
    # Compute on the Python side so we don't depend on Snowflake DATEDIFF syntax.
    try:
        from datetime import date as _date_cls
        start = _date_cls.fromisoformat(str(data.from_date)[:10])
        end   = _date_cls.fromisoformat(str(data.to_date)[:10])
    except Exception:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD for from_date and to_date.")

    if end < start:
        raise HTTPException(400, "End date must be on or after start date.")
    total_days = (end - start).days + 1

    try:
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO EMP_NRM_LEAVE
            (EMPLOYEE_ID, LEAVE_TYPE, START_DATE, END_DATE, REASON, STATUS, APPLIED_AT, TOTAL_DAYS)
            VALUES (%s, %s, %s, %s, %s, 'Pending', CURRENT_TIMESTAMP, %s)
        """, (
            data.employee_id,
            data.leave_type,
            data.from_date,
            data.to_date,
            data.reason,
            total_days,
        ))

        conn.commit()
        cursor.close()

        return {
            "success": True,
            "message": "Leave applied successfully",
            "total_days": total_days,
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, str(e))

    finally:
        conn.close()


# ==========================================
# MANAGER / ADMIN LEAVE APPROVAL ENDPOINTS
# (Consumed by Flask proxy /admin-leave-approval, /approve-leave, /reject-leave)
# ==========================================

# One-shot schema migration: EMP_NRM_LEAVE needs APPROVED_AT / REJECTED_AT
# columns so we can correctly report "approved today" and "rejected today".
# Legacy table only had APPLIED_AT. We auto-add the columns the first time
# the manager endpoints run, then never try again.
_LEAVE_ACTION_COLUMNS_READY = False


def _ensure_leave_action_columns(cursor):
    """Idempotently add APPROVED_AT and REJECTED_AT columns to EMP_NRM_LEAVE."""
    global _LEAVE_ACTION_COLUMNS_READY
    if _LEAVE_ACTION_COLUMNS_READY:
        return True
    try:
        cursor.execute(
            "ALTER TABLE EMP_NRM_LEAVE "
            "ADD COLUMN IF NOT EXISTS APPROVED_AT TIMESTAMP_NTZ"
        )
        cursor.execute(
            "ALTER TABLE EMP_NRM_LEAVE "
            "ADD COLUMN IF NOT EXISTS REJECTED_AT TIMESTAMP_NTZ"
        )
        _LEAVE_ACTION_COLUMNS_READY = True
        print("✅ EMP_NRM_LEAVE.APPROVED_AT / REJECTED_AT columns ready")
        return True
    except Exception as e:
        print(f"⚠️  Could not add APPROVED_AT/REJECTED_AT columns: {e}")
        return False


@app.get("/api/manager/pending-leaves")
def manager_pending_leaves(manager_id: str):
    """Return pending leave requests visible to this manager.

    Initial implementation returns ALL pending leaves system-wide (admin view).
    To restrict to a single manager's reportees, add a join via
    EMP_NRM_JOB_WORK.MANAGER_ID once that linkage is confirmed.
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(500, "DB connection failed")
    try:
        cursor = conn.cursor(snowflake.connector.DictCursor)
        cursor.execute("""
            SELECT
                l.LEAVE_ID,
                l.EMPLOYEE_ID,
                COALESCE(
                    NULLIF(TRIM(COALESCE(p.FIRST_NAME, '') || ' ' || COALESCE(p.LAST_NAME, '')), ''),
                    l.EMPLOYEE_ID
                ) AS EMPLOYEE_NAME,
                l.LEAVE_TYPE,
                l.START_DATE,
                l.END_DATE,
                l.REASON,
                l.APPLIED_AT,
                DATEDIFF(day, l.START_DATE, l.END_DATE) + 1 AS DURATION
            FROM EMP_NRM_LEAVE l
            LEFT JOIN EMP_NRM_PERSONAL p
                   ON l.EMPLOYEE_ID = p.EMPLOYEE_ID
            WHERE l.STATUS = 'Pending'
            ORDER BY l.APPLIED_AT DESC
        """)
        rows = cursor.fetchall() or []
        cursor.close()
        return rows
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@app.post("/api/manager/approve-leave/{leave_id}", response_model=LeaveActionResponse)
def manager_approve_leave(leave_id: int):
    """Mark a Pending leave as Approved and stamp APPROVED_AT. Idempotent."""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(500, "DB connection failed")
    try:
        cursor = conn.cursor()
        has_action_cols = _ensure_leave_action_columns(cursor)

        if has_action_cols:
            cursor.execute("""
                UPDATE EMP_NRM_LEAVE
                SET STATUS = 'Approved',
                    APPROVED_AT = CURRENT_TIMESTAMP()
                WHERE LEAVE_ID = %s
                  AND STATUS = 'Pending'
            """, (leave_id,))
        else:
            cursor.execute("""
                UPDATE EMP_NRM_LEAVE
                SET STATUS = 'Approved'
                WHERE LEAVE_ID = %s
                  AND STATUS = 'Pending'
            """, (leave_id,))
        rows_affected = cursor.rowcount or 0
        conn.commit()
        cursor.close()

        if rows_affected == 0:
            return {
                "success": False,
                "message": f"Leave #{leave_id} not found or already processed.",
                "leave_id": leave_id,
            }
        return {
            "success": True,
            "message": f"Leave #{leave_id} approved successfully.",
            "leave_id": leave_id,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@app.post("/api/manager/reject-leave/{leave_id}", response_model=LeaveActionResponse)
def manager_reject_leave(leave_id: int):
    """Mark a Pending leave as Rejected and stamp REJECTED_AT. Idempotent."""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(500, "DB connection failed")
    try:
        cursor = conn.cursor()
        has_action_cols = _ensure_leave_action_columns(cursor)

        if has_action_cols:
            cursor.execute("""
                UPDATE EMP_NRM_LEAVE
                SET STATUS = 'Rejected',
                    REJECTED_AT = CURRENT_TIMESTAMP()
                WHERE LEAVE_ID = %s
                  AND STATUS = 'Pending'
            """, (leave_id,))
        else:
            cursor.execute("""
                UPDATE EMP_NRM_LEAVE
                SET STATUS = 'Rejected'
                WHERE LEAVE_ID = %s
                  AND STATUS = 'Pending'
            """, (leave_id,))
        rows_affected = cursor.rowcount or 0
        conn.commit()
        cursor.close()

        if rows_affected == 0:
            return {
                "success": False,
                "message": f"Leave #{leave_id} not found or already processed.",
                "leave_id": leave_id,
            }
        return {
            "success": True,
            "message": f"Leave #{leave_id} rejected.",
            "leave_id": leave_id,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@app.get("/api/manager/approved-today")
def manager_approved_today():
    """Return count of leaves moved to Approved today (Snowflake server time)."""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(500, "DB connection failed")
    try:
        cursor = conn.cursor()
        _ensure_leave_action_columns(cursor)
        try:
            cursor.execute("""
                SELECT COUNT(*) FROM EMP_NRM_LEAVE
                WHERE STATUS = 'Approved'
                  AND APPROVED_AT IS NOT NULL
                  AND DATE(APPROVED_AT) = CURRENT_DATE()
            """)
            count = cursor.fetchone()[0] or 0
        except Exception as e:
            print(f"approved-today query error (column missing?): {e}")
            count = 0
        cursor.close()
        return {"approved_today": int(count)}
    except Exception as e:
        print(f"approved-today error: {e}")
        return {"approved_today": 0}
    finally:
        conn.close()


@app.get("/api/manager/rejected-today")
def manager_rejected_today():
    """Return count of leaves moved to Rejected today (Snowflake server time)."""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(500, "DB connection failed")
    try:
        cursor = conn.cursor()
        _ensure_leave_action_columns(cursor)
        try:
            cursor.execute("""
                SELECT COUNT(*) FROM EMP_NRM_LEAVE
                WHERE STATUS = 'Rejected'
                  AND REJECTED_AT IS NOT NULL
                  AND DATE(REJECTED_AT) = CURRENT_DATE()
            """)
            count = cursor.fetchone()[0] or 0
        except Exception as e:
            print(f"rejected-today query error (column missing?): {e}")
            count = 0
        cursor.close()
        return {"rejected_today": int(count)}
    except Exception as e:
        print(f"rejected-today error: {e}")
        return {"rejected_today": 0}
    finally:
        conn.close()


# ==========================================
# RUN SERVER
# ==========================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)