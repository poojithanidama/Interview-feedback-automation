"""
Resources Microservice - Handles authenticated user resources
Standalone Flask service for resources page operations
"""

import sys
import io
import logging
import os
from flask import Flask, redirect, render_template, request, jsonify, session, send_from_directory, url_for
from datetime import datetime
import snowflake.connector
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

# Encoding setup
if sys.stdout.encoding != 'UTF-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'UTF-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'temporary123')

# Upload configurations
app.config['UPLOAD_FOLDER'] = '/home/vsrsubhash/uploads'

COURSE_MAP = {
    "informatica": "Informatica",
    "unix": "Unix",
    "oracle": "Oracle(SQL & PLSQL)",
    "iics": "IICS",
    "python for web development": "Python for Web Development",
    "informatica mdm": "MDM",
    "informatica bdm": "BDM",
    "python for automation": "Python for Automation",
    "snowflake": "Snowflake",
}

# Database connection
def get_db_connection():
    try:
        with open('rsa_key.p8', 'rb') as key_file:
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
        return conn
    except Exception as e:
        print(f"❌ DB Connection Error: {e}")
        return None

# ==========================================
# RESOURCES API ENDPOINTS
# ==========================================

@app.route('/resources/user-info', methods=['GET'])
def get_user_info():
    """API endpoint to get user information"""
    user_id = request.args.get('user_id')
    
    if not user_id:
        return jsonify({'error': 'User ID required'}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cursor = conn.cursor(snowflake.connector.DictCursor)
    
    try:
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
        """, (user_id,))
        
        user_row = cursor.fetchone()
        
        if not user_row:
            return jsonify({'error': 'User not found'}), 404
        
        db_username = user_row.get("USERNAME")
        email = user_row.get("EMAIL")
        phone = user_row.get("PHONE")
        
        # Generate username
        if db_username:
            username = db_username
        elif email and "@" in email:
            username = email.split("@")[0]
        else:
            username = phone or "User"
        
        profile_pic = user_row.get("PROFILE_PIC") or "profile_photo/defaultpicture.jpg"
        usertype = (user_row.get("USERTYPE") or "student").lower()
        
        return jsonify({
            'success': True,
            'user': {
                'username': username,
                'email': email,
                'phone': phone,
                'usertype': usertype,
                'profile_pic': profile_pic
            }
        })
        
    except Exception as e:
        print(f"❌ User info error: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/resources/files', methods=['GET'])
def get_resource_files():
    """API endpoint to get resource files for a subject"""
    subject = request.args.get('subject')
    file_type = request.args.get('file_type')  # ppts, code, interview
    
    if not subject or not file_type:
        return jsonify({'error': 'Subject and file_type required'}), 400
    
    base_path = app.config.get("UPLOAD_FOLDER", "/home/vsrsubhash/uploads")
    folder = subject.replace(" ", "_")
    
    # Map file_type to folder name
    folder_map = {
        'ppts': 'ppts',
        'code': 'code',
        'interview': 'interview_questions'
    }
    
    folder_name = folder_map.get(file_type)
    if not folder_name:
        return jsonify({'error': 'Invalid file_type'}), 400
    
    full_path = os.path.join(base_path, folder_name, folder)
    
    try:
        if os.path.isdir(full_path):
            files = sorted(os.listdir(full_path))
        else:
            files = []
        
        return jsonify({
            'success': True,
            'subject': subject,
            'file_type': file_type,
            'files': files
        })
        
    except Exception as e:
        print(f"❌ Resource files error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/resources/offers', methods=['GET'])
def get_offers():
    """API endpoint to get active offers"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cursor = conn.cursor(snowflake.connector.DictCursor)
    
    try:
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
        
        offers = {}
        for r in cursor.fetchall():
            fee = float(r["COURSE_FEE"] or 0)
            disc = float(r["DISCOUNT_PERCENTAGE"] or 0)
            offers[r["COURSE_NAME"]] = {
                "original_fee": int(fee),
                "discounted_fee": int(fee - (fee * disc / 100)),
                "discount_percentage": int(disc)
            }
        
        return jsonify({
            'success': True,
            'offers': offers
        })
        
    except Exception as e:
        print(f"❌ Offers fetch error: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/resources/festivals', methods=['GET'])
def get_festival():
    """API endpoint to get today's festival"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cursor = conn.cursor(snowflake.connector.DictCursor)
    
    try:
        today = datetime.today().strftime("%Y-%m-%d")
        
        cursor.execute("""
            SELECT FESTIVAL_NAME
            FROM NRM_FESTIVALS
            WHERE FESTIVAL_DATE = %s
        """, (today,))
        
        fr = cursor.fetchone()
        festival_today = fr["FESTIVAL_NAME"] if fr else None
        greeting = f"Happy {festival_today}!" if festival_today else None
        
        return jsonify({
            'success': True,
            'festival_today': festival_today,
            'greeting': greeting
        })
        
    except Exception as e:
        print(f"❌ Festival fetch error: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/resources/videos', methods=['GET'])
def get_videos():
    """API endpoint to get video sessions"""
    tech = request.args.get('tech')
    lang = request.args.get('lang')
    
    if not tech or not lang:
        return jsonify({'error': 'Tech and lang required'}), 400
    
    # Normalize the tech name
    key = tech.lower().strip()
    real_course_name = COURSE_MAP.get(key)
    
    if not real_course_name:
        return jsonify({'error': f'No mapping found for course: {tech}'}), 404
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cursor = conn.cursor(snowflake.connector.DictCursor)
    
    try:
        # Fetch course_id
        cursor.execute("""
            SELECT ID 
            FROM nrm_courses 
            WHERE LOWER(COURSE_NAME) = LOWER(%s)
        """, (real_course_name,))
        course = cursor.fetchone()
        
        if not course:
            return jsonify({'error': f'Course not found: {real_course_name}'}), 404
        
        course_id = course['ID']
        
        # Fetch language_id
        cursor.execute("""
            SELECT ID 
            FROM NRM_LANGUAGES
            WHERE LOWER(LANGUAGE) = LOWER(%s)
        """, (lang,))
        language = cursor.fetchone()
        
        if not language:
            return jsonify({'error': f'Language not found: {lang}'}), 404
        
        language_id = language['ID']
        
        # Fetch videos
        cursor.execute("""
            SELECT youtube_id, title, session_number
            FROM nrm_video_sessions
            WHERE course_id = %s AND language_id = %s
            ORDER BY session_number
        """, (course_id, language_id))
        
        sessions = [
            {
                'youtube_id': row['youtube_id'],
                'title': row['title'],
                'session_number': row['session_number']
            }
            for row in cursor.fetchall()
        ]
        
        return jsonify({
            'success': True,
            'tech': real_course_name,
            'lang': lang,
            'sessions': sessions
        })
        
    except Exception as e:
        print(f"❌ Videos fetch error: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/log-video-click', methods=['POST'])
def log_video_click():
    """API endpoint to log video clicks (analytics)"""
    data = request.get_json()
    subject = data.get('subject')
    lang = data.get('lang')
    
    # Log to database or analytics service
    print(f"📊 Video click: {subject} - {lang}")
    
    return jsonify({'success': True})



# ---------------- RESOURCE ACTION ROUTES ----------------

@app.route("/resources/files/<tech>/<file_type>")
def view_category_files(tech, file_type):
    return f"<h2>{file_type.upper()} files for {tech}</h2>"

@app.route("/resources/videos/<tech>/<lang>")
def video_sessions(tech, lang):
    return f"<h2>🎥 Videos for {tech} ({lang})</h2>"


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'resources-microservice',
        'timestamp': datetime.now().isoformat()
    })
@app.route('/', methods=['GET'])
def resources():
    """Render resources page"""
    return render_template('resources.html')
@app.route("/logout")
def logout():
    print("🔓 LOGOUT")
    session.clear()
    return redirect(url_for("home"))

@app.route("/home/batches", methods=["GET"])
def batches():
    print("🏠 [HOME] batches")
    return jsonify({
        "current_batches": [],
        "upcoming_batches": []
    })
@app.route("/home/feedback", methods=["GET"])
def feedback():
    feedbacks = []

    import snowflake.connector
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    import os

    # --- DB CONNECTION ---
    script_dir = os.path.dirname(os.path.abspath(__file__))
    key_path = os.path.join(script_dir, "rsa_key.p8")

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
        user="ChakoraHub",
        account="gpguymt-ta88699",
        private_key=pkb,
        warehouse="COMPUTE_WH",
        database="VSRSUBHASH$CHAKORA_DB",
        schema="CHAKORA"
    )

    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            FEEDBACK_MESSAGE,
            COALESCE(NAME, 'Anonymous')
        FROM NRM_FEEDBACK
        WHERE FEEDBACK_MESSAGE IS NOT NULL
        ORDER BY SUBMITTED_AT DESC
        LIMIT 5
    """)

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    for r in rows:
        feedbacks.append({
            "message": r[0],
            "username": r[1]
        })

    return jsonify({"feedbacks": feedbacks})




@app.route("/home/health", methods=["GET"])
def health():
    print("🏠 [HOME] health")
    return jsonify({"status": "healthy"})
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=True)