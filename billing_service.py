from fastapi import Fast ,FastAPI, HTTPException, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import snowflake.connector
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import secrets
import traceback
import os  
from fastapi.middleware.cors import CORSMiddleware
# =====================================================
# APP
# =====================================================
app = FastAPI(title="ChakoraHub Billing Service")
security = HTTPBasic()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# =====================================================
# BASIC AUTH
# =====================================================
def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, "admin")
    correct_password = secrets.compare_digest(credentials.password, "admin")
    if not (correct_username and correct_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return credentials.username

# =====================================================
# SNOWFLAKE CONNECTION (RSA)
# =====================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RSA_KEY_PATH = os.path.join(BASE_DIR, "rsa_key.p8")

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

        return snowflake.connector.connect(
            user="ChakoraHub",
            account="gpguymt-ta88699",
            private_key=pkb,
            role="SYSADMIN",
            warehouse="COMPUTE_WH",
            database='"VSRSUBHASH$CHAKORA_DB"',
            schema="CHAKORA"
        )
    except Exception as e:
        print("❌ Snowflake connection error:", e)
        traceback.print_exc()
        return None

# =====================================================
# REQUEST MODELS
# =====================================================
class BillingRequest(BaseModel):
    billing_type: str  # individual/corporate/institutional
    billing_category: str  # project_collaboration/student_workshop
    payment_method: str  # upi/bank_transfer/cash/card
    amount: float
    phone: str
    currency: str = "INR"
    upi_txn_id: Optional[str] = None
    receipt_file_path: Optional[str] = None
    payload: Optional[dict] = {}

class StatusUpdate(BaseModel):
    status: str  # SUCCESS/PENDING/FAILED

# =====================================================
# CREATE BILLING ENTRY - FIXED VERSION
# =====================================================
@app.post("/billing/create")
def create_billing(data: BillingRequest, username: str = Depends(verify_credentials)):
    """
    Create a new billing entry with correct table structure
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(snowflake.connector.DictCursor)

    try:
        # -------------------------------------------------
        # 1️⃣ GET USER_ID FROM NRM_USERS BY PHONE
        # -------------------------------------------------
        cursor.execute("""
            SELECT ID, USERNAME, EMAIL
            FROM NRM_USERS
            WHERE PHONE = %s
            LIMIT 1
        """, (data.phone,))
        user = cursor.fetchone()

        if not user:
            raise HTTPException(
                status_code=404, 
                detail=f"User with phone {data.phone} not found in NRM_USERS"
            )

        user_id = user["ID"]
        user_name = user["USERNAME"]
        user_email = user["EMAIL"]

        # -------------------------------------------------
        # 2️⃣ FIND OR CREATE CUSTOMER IN NRM_BILLING
        # -------------------------------------------------
        cursor.execute("""
            SELECT CUSTOMER_ID, NAME, EMAIL
            FROM NRM_BILLING
            WHERE PHONE = %s
            LIMIT 1
        """, (data.phone,))
        customer = cursor.fetchone()

        if customer:
            customer_id = customer["CUSTOMER_ID"]
            print(f"✅ Found existing customer: {customer_id}")
        else:
            # Create new customer
            cursor.execute("""
                INSERT INTO NRM_BILLING
                (CUSTOMER_TYPE, NAME, PHONE, EMAIL, CREATED_AT)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP())
            """, (
                data.billing_type,
                user_name or "Customer",
                data.phone,
                user_email
            ))
            conn.commit()

            # Get the newly created customer_id
            cursor.execute("SELECT MAX(CUSTOMER_ID) as CID FROM NRM_BILLING WHERE PHONE = %s", (data.phone,))
            new_customer = cursor.fetchone()
            customer_id = new_customer["CID"]
            print(f"✅ Created new customer: {customer_id}")

        # -------------------------------------------------
        # 3️⃣ GET PAYMENT METHOD ID FROM PAYMENT_METHODS TABLE
        # -------------------------------------------------
        cursor.execute("""
            SELECT PAYMENT_METHOD_ID
            FROM PAYMENT_METHODS
            WHERE LOWER(CODE) = LOWER(%s)
            LIMIT 1
        """, (data.payment_method,))
        pm = cursor.fetchone()

        if not pm:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid payment method: {data.payment_method}. Available: upi, bank_transfer, cash, card"
            )

        payment_method_id = pm["PAYMENT_METHOD_ID"]
        print(f"✅ Payment method ID: {payment_method_id}")

        # -------------------------------------------------
        # 4️⃣ INSERT INTO BILLING_TRANSACTIONS
        # Fixed column mapping based on your table structure
        # -------------------------------------------------
        import uuid
        transaction_uuid = str(uuid.uuid4())
        
        cursor.execute("""
            INSERT INTO BILLING_TRANSACTIONS
            (
                TRANSACTION_UUID,
                CUSTOMER_ID,
                BILLING_TYPE,
                BILLING_CATEGORY,
                PAYMENT_METHOD,
                UPI_TXN_ID,
                AMOUNT,
                CURRENCY,
                PHONE,
                RECEIPT_FILE_PATH,
                STATUS,
                CREATED_AT
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP())
        """, (
            transaction_uuid,
            customer_id,
            data.billing_type,
            data.billing_category,
            data.payment_method,  # Store as string (matches your table design)
            data.upi_txn_id,
            data.amount,
            data.currency,
            data.phone,
            data.receipt_file_path,
            "PENDING"
        ))
        conn.commit()

        # Get the newly created transaction ID
        cursor.execute("""
            SELECT TRANSACTION_ID 
            FROM BILLING_TRANSACTIONS 
            WHERE TRANSACTION_UUID = %s
        """, (transaction_uuid,))
        transaction = cursor.fetchone()
        transaction_id = transaction["TRANSACTION_ID"]
        print(f"✅ Transaction created: {transaction_id}")

        # -------------------------------------------------
        # 5️⃣ GET STATUS ID FOR 'Pending'
        # -------------------------------------------------
        cursor.execute("""
            SELECT ID 
            FROM NRM_PAYMENT_STATUSES 
            WHERE UPPER(STATUS) = 'PENDING' 
            LIMIT 1
        """)
        status_row = cursor.fetchone()
        status_id = status_row["ID"] if status_row else None

        if not status_id:
            raise HTTPException(status_code=500, detail="Payment status 'Pending' not found in database")

        # -------------------------------------------------
        # 6️⃣ INSERT INTO NRM_BILLING_ENTRIES (Link to USER)
        # -------------------------------------------------
        cursor.execute("""
    INSERT INTO NRM_BILLING_ENTRIES
    (ID, USER_ID, COURSE_ID, UPI_ID, AMOUNT, DISCOUNT, STATUS_ID, BILLING_TIMESTAMP)
    SELECT
        NVL(MAX(ID), 0) + 1,
        %s, NULL, %s, %s, 0, %s, CURRENT_TIMESTAMP()
    FROM NRM_BILLING_ENTRIES
""", (user_id, data.upi_txn_id, data.amount, status_id))
        conn.commit()

        print(f"✅ Billing entry created for user_id: {user_id}")

        # -------------------------------------------------
        # 7️⃣ INSERT RECEIPT (IF PROVIDED)
        # -------------------------------------------------
        if data.receipt_file_path:
            cursor.execute("""
                INSERT INTO RECEIPTS
                (TRANSACTION_ID, FILE_NAME, FILE_PATH, UPLOADED_AT)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP())
            """, (
                transaction_id,
                data.receipt_file_path.split("/")[-1],
                data.receipt_file_path
            ))
            conn.commit()
            print(f"✅ Receipt uploaded: {data.receipt_file_path}")

        return {
            "status": "success",
            "message": "Billing entry created successfully",
            "customer_id": customer_id,
            "transaction_id": transaction_id,
            "transaction_uuid": transaction_uuid,
            "amount": data.amount,
            "payment_method": data.payment_method,
            "billing_status": "PENDING"
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        print(f"❌ Create billing error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()

# =====================================================
# GET ALL BILLING TRANSACTIONS (ADMIN)
# =====================================================
@app.get("/billing/admin/list")
def admin_list_billing(
    category: Optional[str] = None,
    username: str = Depends(verify_credentials)
):
    """Get all billing transactions for admin review"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(snowflake.connector.DictCursor)

    try:
        query = """
            SELECT 
                bt.TRANSACTION_ID,
                bt.TRANSACTION_UUID,
                nb.CUSTOMER_TYPE,
                nb.NAME AS CUSTOMER_NAME,
                nb.PHONE,
                bt.BILLING_TYPE,
                bt.BILLING_CATEGORY,
                bt.PAYMENT_METHOD,
                bt.AMOUNT,
                bt.CURRENCY,
                bt.STATUS,
                bt.CREATED_AT,
                bt.UPI_TXN_ID
            FROM BILLING_TRANSACTIONS bt
            JOIN NRM_BILLING nb ON bt.CUSTOMER_ID = nb.CUSTOMER_ID
        """

        if category:
            query += " WHERE bt.BILLING_CATEGORY = %s"
            cursor.execute(query + " ORDER BY bt.CREATED_AT DESC", (category,))
        else:
            cursor.execute(query + " ORDER BY bt.CREATED_AT DESC")

        transactions = cursor.fetchall()

        return {
            "status": "success",
            "count": len(transactions),
            "transactions": transactions
        }

    except Exception as e:
        print(f"❌ Admin list error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()

# =====================================================
# UPDATE BILLING STATUS (ADMIN)
# =====================================================
@app.put("/billing/{billing_id}/status")
def update_billing_status(
    billing_id: int,
    status_data: StatusUpdate,
    username: str = Depends(verify_credentials)
):
    """Update billing transaction status (admin only)"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(snowflake.connector.DictCursor)

    try:
        # Update transaction status
        cursor.execute("""
            UPDATE BILLING_TRANSACTIONS
            SET STATUS = %s, UPDATED_AT = CURRENT_TIMESTAMP()
            WHERE TRANSACTION_ID = %s
        """, (status_data.status, billing_id))

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Transaction not found")

        # Get corresponding payment status ID
        cursor.execute("""
            SELECT ID FROM NRM_PAYMENT_STATUSES 
            WHERE UPPER(STATUS) = UPPER(%s) 
            LIMIT 1
        """, (status_data.status,))
        status_row = cursor.fetchone()
        
        if status_row:
            new_status_id = status_row["ID"]
            
            # Update corresponding NRM_BILLING_ENTRIES
            cursor.execute("""
                UPDATE NRM_BILLING_ENTRIES
                SET STATUS_ID = %s
                WHERE USER_ID IN (
                    SELECT u.ID FROM NRM_USERS u
                    JOIN NRM_BILLING nb ON u.PHONE = nb.PHONE
                    JOIN BILLING_TRANSACTIONS bt ON nb.CUSTOMER_ID = bt.CUSTOMER_ID
                    WHERE bt.TRANSACTION_ID = %s
                )
            """, (new_status_id, billing_id))

        conn.commit()

        return {
            "status": "success",
            "message": f"Transaction {billing_id} status updated to {status_data.status}"
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        print(f"❌ Update status error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()

# =====================================================
# GET USER'S OWN BILLING (STUDENT)
# =====================================================
@app.get("/billing/list")
def list_user_billing(username: str = Depends(verify_credentials)):
    """Get billing entries for logged-in user"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(snowflake.connector.DictCursor)

    try:
        cursor.execute("""
            SELECT 
                bt.TRANSACTION_ID,
                bt.TRANSACTION_UUID,
                bt.BILLING_TYPE,
                bt.BILLING_CATEGORY,
                bt.PAYMENT_METHOD,
                bt.AMOUNT,
                bt.CURRENCY,
                bt.STATUS,
                bt.CREATED_AT,
                bt.UPI_TXN_ID
            FROM BILLING_TRANSACTIONS bt
            JOIN NRM_BILLING nb ON bt.CUSTOMER_ID = nb.CUSTOMER_ID
            WHERE nb.PHONE IN (
                SELECT PHONE FROM NRM_USERS WHERE EMAIL = %s OR USERNAME = %s
            )
            ORDER BY bt.CREATED_AT DESC
        """, (username, username))

        transactions = cursor.fetchall()

        return {
            "status": "success",
            "count": len(transactions),
            "transactions": transactions
        }

    except Exception as e:
        print(f"❌ List user billing error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()

# =====================================================
# HEALTH CHECK
# =====================================================
@app.get("/health")
def health_check():
    conn = get_db_connection()
    db_status = "connected" if conn else "disconnected"
    if conn:
        conn.close()
    
    return {
        "status": "healthy",
        "database": db_status,
        "timestamp": datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)