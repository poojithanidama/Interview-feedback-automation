"""
Student Microservice - FastAPI
Handles all student/user-related operations
Port: 8001
"""

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import snowflake.connector
from snowflake.connector import DictCursor
import os
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from werkzeug.security import check_password_hash
import uvicorn

app = FastAPI(title="Student Service", version="1.0")

# ==========================================
# DATABASE CONNECTION
# ==========================================
def get_db_connection():
    """Connect to Snowflake using RSA key authentication"""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        key_path = os.path.join(script_dir, 'rsa_key.p8')
        
        if not os.path.exists(key_path):
            print(f"❌ RSA key file not found at: {key_path}")
            return None
        
        with open(key_path, 'rb') as key_file:
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
            schema='CHAKORA',
            login_timeout=30,
            network_timeout=30
        )
        
        print("✅ Student Service: Connected to Snowflake")
        return conn
        
    except Exception as e:
        print(f"❌ Student Service DB Connection Error: {e}")
        return None

# ==========================================
# PYDANTIC MODELS
# ==========================================
class StudentLoginRequest(BaseModel):
    username: str
    password: str
    login_type: str = "user"

class StudentLoginResponse(BaseModel):
    success: bool
    message: str
    user_id: Optional[int] = None
    username: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    usertype: Optional[str] = None
    profile_pic: Optional[str] = None

class StudentResourcesRequest(BaseModel):
    user_id: int

class StudentProfileRequest(BaseModel):
    user_id: int
    
class UpdateProfileRequest(BaseModel):
    user_id: int
    address: Optional[str] = None
    phone: Optional[str] = None
    
class FeedbackRequest(BaseModel):
    name: str
    email: EmailStr
    phone: str
    feedback_text: str

class EnquiryRequest(BaseModel):
    name: str
    email: EmailStr
    phone: str
    enquiry_text: str
    user_id: Optional[int] = None

# ==========================================
# HEALTH CHECK
# ==========================================
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    conn = get_db_connection()
    db_status = "connected" if conn else "disconnected"
    if conn:
        conn.close()
    
    return {
        "status": "healthy",
        "service": "student-service",
        "database": db_status,
        "timestamp": datetime.now().isoformat()
    }

# ==========================================
# STUDENT LOGIN
# ==========================================
@app.post("/api/student/login", response_model=StudentLoginResponse)
async def student_login(login_data: StudentLoginRequest):
    """
    Authenticate student/user login
    """
    print(f"👤 Student Login Attempt: {login_data.username}")
    
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    
    try:
        cursor = conn.cursor(DictCursor)
        
        # Query to get user details
        cursor.execute("""
            SELECT 
                u.ID AS USER_ID,
                u.EMAIL,
                u.PHONE,
                u.USERTYPE,
                u.PROFILE_PIC,
                l.ID AS LOGIN_ID,
                l.PASSWORD
            FROM NRM_USERS u
            JOIN NRM_LOGINS l ON u.ID = l.USER_ID
            WHERE (
                LOWER(TRIM(u.EMAIL)) = LOWER(TRIM(%s))
             OR TRIM(u.PHONE) = TRIM(%s)
            )
            ORDER BY l.CREATED_AT DESC
            LIMIT 1
        """, (login_data.username, login_data.username))
        
        user = cursor.fetchone()
        
        if not user:
            print(f"❌ User not found: {login_data.username}")
            return StudentLoginResponse(
                success=False,
                message="User not found"
            )
        
        # Verify password
        db_password = user.get("PASSWORD") or ""
        
        if db_password.startswith(("scrypt:", "$2a$", "$2b$", "pbkdf2:")):
            valid = check_password_hash(db_password, login_data.password)
        else:
            valid = (db_password == login_data.password)
        
        if not valid:
            print(f"❌ Invalid password for user: {login_data.username}")
            return StudentLoginResponse(
                success=False,
                message="Incorrect password"
            )
        
        # Update login status
        cursor.execute("""
            UPDATE NRM_LOGINS
            SET IS_ACTIVE = 'Y',
                LAST_LOGIN = CURRENT_TIMESTAMP()
            WHERE USER_ID = %s
        """, (user["USER_ID"],))
        conn.commit()
        
        print(f"✅ Student login successful: {login_data.username}")
        
        return StudentLoginResponse(
            success=True,
            message="Login successful",
            user_id=user["USER_ID"],
            username=user.get("EMAIL") or user.get("PHONE"),
            email=user.get("EMAIL"),
            phone=user.get("PHONE"),
            usertype=(user.get("USERTYPE") or "student").lower(),
            profile_pic=user.get("PROFILE_PIC") or "profile_photo/defaultpicture.jpg"
        )
        
    except Exception as e:
        print(f"❌ Student login error: {e}")
        raise HTTPException(status_code=500, detail=f"Login failed: {str(e)}")
    finally:
        cursor.close()
        conn.close()

# ==========================================
# GET STUDENT RESOURCES DATA
# ==========================================
@app.post("/api/student/resources")
async def get_student_resources(data: StudentResourcesRequest):
    """Get student resources including offers and festivals"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    
    try:
        cursor = conn.cursor(DictCursor)
        
        # Get user details
        cursor.execute("""
            SELECT
                ID,
                USERNAME,
                EMAIL,
                PHONE,
                USERTYPE,
                PROFILE_PIC
            FROM NRM_USERS
            WHERE ID = %s
            LIMIT 1
        """, (data.user_id,))
        
        user = cursor.fetchone()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get today's offers
        today = datetime.today().strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT
                c.COURSE_NAME,
                c.COURSE_FEE,
                o.DISCOUNT_PERCENTAGE
            FROM NRM_OFFERS o
            JOIN NRM_COURSES c ON c.ID = o.COURSE_ID
            WHERE o.IS_ACTIVE = TRUE
              AND (%s BETWEEN o.VALID_FROM AND o.VALID_TO
                   OR o.VALID_FROM IS NULL
                   OR o.VALID_TO IS NULL)
        """, (today,))
        
        offers_rows = cursor.fetchall()
        offers = {}
        for r in offers_rows:
            fee = float(r["COURSE_FEE"] or 0)
            disc = float(r["DISCOUNT_PERCENTAGE"] or 0)
            offers[r["COURSE_NAME"]] = {
                "original_fee": int(fee),
                "discounted_fee": int(fee - (fee * disc / 100)),
                "discount_percentage": int(disc)
            }
        
        # Get today's festival
        cursor.execute("""
            SELECT FESTIVAL_NAME
            FROM NRM_FESTIVALS
            WHERE FESTIVAL_DATE = %s
        """, (today,))
        fr = cursor.fetchone()
        festival_today = fr["FESTIVAL_NAME"] if fr else None
        
        cursor.close()
        conn.close()
        
        return {
            "success": True,
            "user": {
                "username": user.get("USERNAME") or user.get("EMAIL", "").split("@")[0],
                "email": user.get("EMAIL"),
                "phone": user.get("PHONE"),
                "usertype": (user.get("USERTYPE") or "student").lower(),
                "profile_pic": user.get("PROFILE_PIC") or "profile_photo/defaultpicture.jpg"
            },
            "offers": offers,
            "festival_today": festival_today
        }
        
    except Exception as e:
        print(f"❌ Resources error: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching resources: {str(e)}")

# ==========================================
# GET STUDENT PROFILE
# ==========================================
@app.post("/api/student/profile")
async def get_student_profile(data: StudentProfileRequest):
    """Get student profile information"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    
    try:
        cursor = conn.cursor(DictCursor)
        
        # Get user email first
        cursor.execute("SELECT EMAIL FROM NRM_USERS WHERE ID = %s", (data.user_id,))
        user = cursor.fetchone()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get address from nrm_students
        cursor.execute("SELECT ADDRESS FROM NRM_STUDENTS WHERE EMAIL = %s", (user["EMAIL"],))
        result = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        return {
            "success": True,
            "address": result["ADDRESS"] if result and result["ADDRESS"] else ""
        }
        
    except Exception as e:
        print(f"❌ Profile error: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching profile: {str(e)}")

# ==========================================
# UPDATE STUDENT PROFILE
# ==========================================
@app.put("/api/student/profile")
async def update_student_profile(data: UpdateProfileRequest):
    """Update student profile"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    
    try:
        cursor = conn.cursor(DictCursor)
        
        # Get user email
        cursor.execute("SELECT EMAIL FROM NRM_USERS WHERE ID = %s", (data.user_id,))
        user = cursor.fetchone()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Update address
        if data.address is not None:
            cursor.execute(
                "UPDATE NRM_STUDENTS SET ADDRESS = %s WHERE EMAIL = %s",
                (data.address, user["EMAIL"])
            )
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return {
            "success": True,
            "message": "Profile updated successfully"
        }
        
    except Exception as e:
        print(f"❌ Update profile error: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating profile: {str(e)}")

# ==========================================
# SUBMIT FEEDBACK
# ==========================================
@app.post("/api/student/feedback")
async def submit_feedback(feedback: FeedbackRequest):
    """Submit student feedback"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    
    try:
        cursor = conn.cursor(DictCursor)
        
        # Find or create student
        cursor.execute("""
            SELECT ID FROM NRM_STUDENTS 
            WHERE EMAIL = %s OR PHONE = %s
            LIMIT 1
        """, (feedback.email, feedback.phone))
        
        student_row = cursor.fetchone()
        
        if student_row:
            student_id = student_row["ID"]
        else:
            # Create new student
            first_name = feedback.name.split()[0] if feedback.name else ''
            last_name = ' '.join(feedback.name.split()[1:]) if feedback.name and len(feedback.name.split()) > 1 else ''
            
            cursor.execute("""
                INSERT INTO NRM_STUDENTS 
                (FIRST_NAME, LAST_NAME, EMAIL, PHONE, REGISTRATION_SOURCE)
                VALUES (%s, %s, %s, %s, 'website_feedback')
            """, (first_name, last_name, feedback.email, feedback.phone))
            
            cursor.execute("""
                SELECT ID FROM NRM_STUDENTS 
                WHERE EMAIL = %s 
                ORDER BY ID DESC LIMIT 1
            """, (feedback.email,))
            new_student = cursor.fetchone()
            student_id = new_student["ID"] if new_student else None
        
        # Insert feedback
        if student_id:
            cursor.execute("""
                INSERT INTO NRM_FEEDBACK 
                (STUDENT_ID, FEEDBACK_MESSAGE, SUBMITTED_AT)
                VALUES (%s, %s, CURRENT_TIMESTAMP())
            """, (student_id, feedback.feedback_text))
            
            conn.commit()
        
        cursor.close()
        conn.close()
        
        return {
            "success": True,
            "message": "Feedback submitted successfully"
        }
        
    except Exception as e:
        print(f"❌ Feedback error: {e}")
        raise HTTPException(status_code=500, detail=f"Error submitting feedback: {str(e)}")


# ==========================================
# GET FEEDBACKS
# ==========================================
@app.get("/api/student/feedbacks")
async def get_feedbacks():
    """Get all feedbacks for display"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    
    try:
        cursor = conn.cursor(DictCursor)
        
        cursor.execute("""
            SELECT 
                f.FEEDBACK_MESSAGE,
                COALESCE(u.USERNAME, f.NAME, 'Anonymous') as USERNAME,
                f.SUBMITTED_AT
            FROM NRM_FEEDBACK f
            LEFT JOIN NRM_USERS u ON f.STUDENT_ID = u.ID
            WHERE f.FEEDBACK_MESSAGE IS NOT NULL 
              AND TRIM(f.FEEDBACK_MESSAGE) != ''
            ORDER BY f.SUBMITTED_AT DESC
            LIMIT 20
        """)
        
        feedbacks = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return {
            "success": True,
            "feedbacks": feedbacks
        }
        
    except Exception as e:
        print(f"❌ Feedbacks error: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching feedbacks: {str(e)}")

# ==========================================
# STUDENT LOGOUT
# ==========================================
@app.post("/api/student/logout")
async def student_logout(data: Dict[str, int]):
    """Handle student logout"""
    user_id = data.get("user_id")
    
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID required")
    
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    
    try:
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE NRM_LOGINS
            SET IS_ACTIVE = 'N'
            WHERE USER_ID = %s
        """, (user_id,))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return {
            "success": True,
            "message": "Logged out successfully"
        }
        
    except Exception as e:
        print(f"❌ Logout error: {e}")
        raise HTTPException(status_code=500, detail=f"Logout failed: {str(e)}")

# ==========================================
# COURSE NAME NORMALIZER
# Maps frontend subject names → exact DB COURSE_NAME values
# ==========================================
COURSE_NAME_MAP = {
    "informatica":                "Informatica",
    "unix":                       "Unix",
    "oracle":                     "Oracle(SQL & PLSQL)",
    "iics":                       "IICS",
    "python for web development": "Python for Web Development",
    "informatica mdm":            "MDM",
    "informatica bdm":            "BDM",
    "python for automation":      "Python for Automation",
    "snowflake":                  "Snowflake",
}


# ==========================================
# GET COURSE VIDEOS
# ==========================================
@app.get("/api/student/course-videos")
async def get_course_videos(subject: str, lang: str = "telugu"):
    """
    Fetch videos for a subject+language from nrm_video_sessions.
    Called by app.py's /api/course-videos/<subject> route.
    Falls back to any available language if requested language has no videos.
    """
    subject          = subject.strip()
    lang             = lang.strip() or "telugu"
    real_course_name = COURSE_NAME_MAP.get(subject.lower().strip(), subject)

    print(f"📹 course-videos: subject='{subject}' → '{real_course_name}', lang='{lang}'")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(DictCursor)

        # Step 1: get course_id
        cursor.execute("""
            SELECT ID FROM nrm_courses
            WHERE LOWER(COURSE_NAME) = LOWER(%s)
            LIMIT 1
        """, (real_course_name,))
        course = cursor.fetchone()

        if not course:
            print(f"⚠️ No course found for: '{real_course_name}'")
            return {"success": True, "videos": []}

        course_id = course["ID"]

        # Step 2: get language_id
        cursor.execute("""
            SELECT ID FROM NRM_LANGUAGES
            WHERE LOWER(LANGUAGE) = LOWER(%s)
            LIMIT 1
        """, (lang,))
        language = cursor.fetchone()

        if not language:
            print(f"⚠️ No language found for: '{lang}'")
            return {"success": True, "videos": []}

        language_id = language["ID"]

        # Step 3: fetch videos with language filter
        cursor.execute("""
            SELECT YOUTUBE_ID, TITLE, SESSION_NUMBER
            FROM nrm_video_sessions
            WHERE COURSE_ID = %s AND LANGUAGE_ID = %s
            ORDER BY SESSION_NUMBER ASC
        """, (course_id, language_id))

        rows = cursor.fetchall() or []

        # Step 4: fallback — if no videos for requested language, fetch any language
        if not rows:
            print(f"⚠️ No videos for lang='{lang}' (id={language_id}), trying language fallback...")
            cursor.execute("""
                SELECT YOUTUBE_ID, TITLE, SESSION_NUMBER
                FROM nrm_video_sessions
                WHERE COURSE_ID = %s
                ORDER BY SESSION_NUMBER ASC
            """, (course_id,))
            rows = cursor.fetchall() or []
            if rows:
                print(f"✅ Fallback found {len(rows)} videos for course_id={course_id}")

        print(f"✅ Videos returning: {len(rows)} for '{real_course_name}'")

        videos = [
            {
                "youtube_url":    f"https://www.youtube.com/watch?v={r['YOUTUBE_ID']}",
                "video_title":    r["TITLE"],
                "session_number": r["SESSION_NUMBER"],
                "youtube_id":     r["YOUTUBE_ID"]
            }
            for r in rows
        ]

        return {"success": True, "videos": videos}

    except Exception as e:
        print(f"❌ get_course_videos error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ==========================================
# GET COURSE RESOURCES (PPTs, Code, Interview)
# ==========================================
@app.get("/api/student/course-resources")
async def get_course_resources(subject: str):
    """
    Fetch PPTs, Code, Interview files for a subject from nrm_COURSE_FILES.
    Called by app.py's /api/course-resources/<subject> route.
    """
    subject          = subject.strip()
    real_course_name = COURSE_NAME_MAP.get(subject.lower().strip(), subject)

    print(f"📁 course-resources: subject='{subject}' → '{real_course_name}'")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(DictCursor)

        cursor.execute("""
            SELECT cf.FILE_TYPE, cf.FILE_TITLE, cf.FILE_LABEL, cf.FILE_URL
            FROM nrm_COURSE_FILES cf
            JOIN NRM_COURSES c ON c.ID = cf.COURSE_ID
            WHERE LOWER(c.COURSE_NAME) = LOWER(%s)
              AND cf.IS_ACTIVE = TRUE
            ORDER BY cf.FILE_TYPE, cf.FILE_ID ASC
        """, (real_course_name,))

        rows = cursor.fetchall() or []
        print(f"✅ Resources found: {len(rows)} for '{real_course_name}'")

        ppts, code, interview = [], [], []

        for r in rows:
            item = {
                "title": r.get("FILE_TITLE") or "",
                "label": r.get("FILE_LABEL") or "",
                "url":   r.get("FILE_URL")   or ""
            }
            ftype = (r.get("FILE_TYPE") or "").upper()
            if ftype == "PPT":
                ppts.append(item)
            elif ftype == "CODE":
                code.append(item)
            elif ftype == "INTERVIEW":
                interview.append(item)

        return {
            "success":   True,
            "subject":   subject,
            "ppts":      ppts,
            "code":      code,
            "interview": interview
        }

    except Exception as e:
        print(f"❌ get_course_resources error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ==========================================
# STUDENT REPORT (admin) — list / single view / certificate
# Implements the endpoints app.py calls from /generate-student-report
# and /student-report-view (were missing → "report not fetching").
#   NRM_REGISTRATIONS(REGISTRATION_ID, STUDENT_ID, COURSE_ID, START_DATE, STATUS_ID)
#   NRM_STUDENTS(ID, FIRST_NAME, LAST_NAME, ADDRESS, LOCATION, EMPLOYED, EXPERIENCE, USER_ID)
#   NRM_USERS(ID, EMAIL, PHONE)  NRM_COURSES(ID, COURSE_NAME)  NRM_STATUSES(ID, STATUS)
# ==========================================
class ReportCertRequest(BaseModel):
    reg_id: str


@app.get("/api/student/report/students")
async def report_students():
    """List registered students (one row per registration) for the report page."""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        cursor = conn.cursor(DictCursor)
        cursor.execute("""
            SELECT s.FIRST_NAME, s.LAST_NAME, r.REGISTRATION_ID,
                   u.EMAIL, u.PHONE, s.ADDRESS
            FROM NRM_REGISTRATIONS r
            JOIN NRM_STUDENTS s ON r.STUDENT_ID = s.ID
            JOIN NRM_USERS    u ON s.USER_ID    = u.ID
            ORDER BY r.CREATED_DT DESC
        """)
        rows = cursor.fetchall()
        data = [{
            "first_name":      row.get("FIRST_NAME") or "",
            "last_name":       row.get("LAST_NAME") or "",
            "registration_id": row.get("REGISTRATION_ID") or "",
            "email":           row.get("EMAIL") or "",
            "phone":           row.get("PHONE") or "",
            "address":         row.get("ADDRESS") or "",
        } for row in rows]
        return {"success": True, "data": data}
    except Exception as e:
        print(f"Student report list error: {e}")
        raise HTTPException(status_code=500, detail=f"Report fetch failed: {str(e)}")
    finally:
        try:
            cursor.close(); conn.close()
        except Exception:
            pass


@app.get("/api/student/report/student-view")
async def report_student_view(reg_id: str):
    """Single student detail for report.html (returns a positional array student[0..6])."""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        cursor = conn.cursor(DictCursor)
        cursor.execute("""
            SELECT s.FIRST_NAME, s.LAST_NAME, s.LOCATION, s.EMPLOYED,
                   s.EXPERIENCE, r.REGISTRATION_ID, c.COURSE_NAME
            FROM NRM_REGISTRATIONS r
            JOIN NRM_STUDENTS s ON r.STUDENT_ID = s.ID
            JOIN NRM_COURSES  c ON r.COURSE_ID  = c.ID
            WHERE UPPER(r.REGISTRATION_ID) = UPPER(%s)
            LIMIT 1
        """, (reg_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Student not found")
        student = [
            row.get("FIRST_NAME") or "",
            row.get("LAST_NAME") or "",
            row.get("LOCATION") or "",
            row.get("EMPLOYED") or "",
            row.get("EXPERIENCE") or "",
            row.get("REGISTRATION_ID") or "",
            row.get("COURSE_NAME") or "",
        ]
        return {"student": student}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Student report view error: {e}")
        raise HTTPException(status_code=500, detail=f"Report view failed: {str(e)}")
    finally:
        try:
            cursor.close(); conn.close()
        except Exception:
            pass


@app.post("/api/student/report/certificate")
async def report_certificate(payload: ReportCertRequest):
    """Certificate details for a registration ID (used by /generate-student-report)."""
    reg_id = (payload.reg_id or "").strip()
    if not reg_id:
        raise HTTPException(status_code=400, detail="reg_id is required")
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        cursor = conn.cursor(DictCursor)
        cursor.execute("""
            SELECT s.FIRST_NAME, s.LAST_NAME, c.COURSE_NAME,
                   r.START_DATE, st.STATUS
            FROM NRM_REGISTRATIONS r
            JOIN NRM_STUDENTS s  ON r.STUDENT_ID = s.ID
            JOIN NRM_COURSES  c  ON r.COURSE_ID  = c.ID
            JOIN NRM_STATUSES st ON r.STATUS_ID   = st.ID
            WHERE UPPER(r.REGISTRATION_ID) = UPPER(%s)
            LIMIT 1
        """, (reg_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No student found for this registration ID")
        reg_date = row.get("START_DATE")
        student = {
            "status":            row.get("STATUS") or "",
            "first_name":        row.get("FIRST_NAME") or "",
            "last_name":         row.get("LAST_NAME") or "",
            "course_name":       row.get("COURSE_NAME") or "",
            "registration_date": reg_date.strftime("%d %B %Y") if hasattr(reg_date, "strftime") else (str(reg_date) if reg_date else ""),
        }
        if (student["status"] or "").lower() == "completed":
            email_status = "success|Certificate generated successfully."
        else:
            email_status = f"warning|Certificate not available — course status is '{student['status']}'."
        return {"success": True, "student": student, "email_status": email_status}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Student report certificate error: {e}")
        raise HTTPException(status_code=500, detail=f"Certificate fetch failed: {str(e)}")
    finally:
        try:
            cursor.close(); conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)