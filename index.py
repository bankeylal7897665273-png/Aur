from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import requests
import os
from dotenv import load_dotenv
import datetime
import random

load_dotenv()

app = Flask(__name__)
app.secret_key = 'razorpay_vip_gateway_secret'

FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY")
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@gmail.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
UPI_ID = os.getenv("UPI_ID", "7897803277@freecharge")
FREECHARGE_COOKIE = os.getenv("FREECHARGE_COOKIE")

AUTH_SIGNUP_URL = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_API_KEY}"
AUTH_LOGIN_URL = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}"

def db_put(path, data):
    requests.put(f"{FIREBASE_DB_URL}/payment_gateway/{path}.json", json=data)

def db_patch(path, data):
    requests.patch(f"{FIREBASE_DB_URL}/payment_gateway/{path}.json", json=data)

def db_get(path):
    res = requests.get(f"{FIREBASE_DB_URL}/payment_gateway/{path}.json")
    return res.json() if res and res.status_code == 200 else None

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('auth.html')

@app.route('/api/auth', methods=['POST'])
def authenticate():
    data = request.json
    action = data.get('action')
    email = data.get('email')
    password = data.get('password')
    username = data.get('username', '').strip()
    
    payload = {"email": email, "password": password, "returnSecureToken": True}
    
    try:
        if action == 'register':
            platform = data.get('platform')
            gender = data.get('gender')
            dob = data.get('dob')
            
            users = db_get("users") or {}
            for uid, udata in users.items():
                if udata.get('username') == username:
                    return jsonify({"status": "error", "message": "Username already taken!"})

            res = requests.post(AUTH_SIGNUP_URL, json=payload)
            if res.status_code == 200:
                user_id = res.json()['localId']
                db_put(f"users/{user_id}", {
                    "email": email,
                    "username": username,
                    "platform": platform,
                    "gender": gender,
                    "dob": dob,
                    "wallet_balance": 0.0,
                    "total_apis": 0,
                    "date_joined": str(datetime.datetime.now())
                })
                return jsonify({"status": "success", "message": "Account Created Successfully!"})
            else:
                return jsonify({"status": "error", "message": res.json()['error']['message']})
                
        elif action == 'login':
            res = requests.post(AUTH_LOGIN_URL, json=payload)
            if res.status_code == 200:
                user_id = res.json()['localId']
                session['user_id'] = user_id
                if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
                    session['is_admin'] = True
                return jsonify({"status": "success", "message": "Login Successful!"})
            else:
                return jsonify({"status": "error", "message": "Invalid Email or Password!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    user_id = session['user_id']
    user_data = db_get(f"users/{user_id}")
    transactions = db_get(f"transactions/{user_id}") or {}
    apis = db_get(f"apis/{user_id}") or {}
    return render_template('dashboard.html', user=user_data, transactions=transactions, apis=apis)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/api/create_gateway', methods=['POST'])
def create_gateway():
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Not logged in"})
        
    data = request.json
    api_name = data.get('api_name')
    app_name = data.get('app_name')
    user_id = session['user_id']
    user_data = db_get(f"users/{user_id}")
    username = user_data['username']
    
    api_id = str(random.randint(100000, 999999))
    
    # FIX: Sirf path save kar rahe hain, full URL Frontend automatic detect karega (No lag, No error)
    payment_path = f"/pay/{username}"
    
    db_put(f"apis/{user_id}/{api_id}", {
        "api_name": api_name,
        "app_name": app_name,
        "payment_path": payment_path,
        "status": "Active",
        "date": str(datetime.datetime.now())
    })
    
    total = user_data.get('total_apis', 0)
    db_patch(f"users/{user_id}", {"total_apis": total + 1})
    
    return jsonify({"status": "success", "message": "API Created!"})

@app.route('/pay/<username>/<amount>')
def pay_page(username, amount):
    users = db_get("users") or {}
    target_user_id = None
    for uid, udata in users.items():
        if udata.get('username') == username:
            target_user_id = uid
            break
            
    if not target_user_id:
        return "Invalid Payment Gateway / User Not Found", 404
        
    session['pay_start_time'] = str(datetime.datetime.now())
    return render_template('pay.html', username=username, amount=amount, upi_id=UPI_ID)

@app.route('/api/submit_payment', methods=['POST'])
def submit_payment():
    data = request.json
    utr = data.get('utr')
    amount = float(data.get('amount'))
    username = data.get('username')
    
    if len(utr) != 12:
        return jsonify({"status": "error", "message": "UTR must be exactly 12 digits."})
        
    users = db_get("users") or {}
    user_id = None
    for uid, udata in users.items():
        if udata.get('username') == username:
            user_id = uid
            break
            
    if not user_id:
        return jsonify({"status": "error", "message": "Merchant not found."})

    start_time_str = session.get('pay_start_time')
    time_diff_minutes = 0
    if start_time_str:
        start_time = datetime.datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S.%f")
        time_diff = datetime.datetime.now() - start_time
        time_diff_minutes = time_diff.total_seconds() / 60.0

    # FIX: 3% Deduction Silent Calculation (Kahi dikhega nahi)
    deduction = amount * 0.03
    amount_to_add = amount - deduction

    if time_diff_minutes > 5.0:
        db_put(f"manual_utr_requests/{utr}", {
            "user_id": user_id,
            "username": username,
            "amount": amount,
            "add_amount": amount_to_add,
            "status": "Pending Admin Approval",
            "date": str(datetime.datetime.now()),
            "reason": "Submitted after 5 minutes limit"
        })
        return jsonify({"status": "warning", "message": "Time Limit Exceeded! Your UTR has been sent to Admin for manual verification."})
    else:
        if FREECHARGE_COOKIE:
            user_data = db_get(f"users/{user_id}")
            current_bal = float(user_data.get('wallet_balance', 0.0))
            db_patch(f"users/{user_id}", {"wallet_balance": current_bal + amount_to_add})
            
            txn_id = f"TXN{random.randint(100000, 999999)}"
            db_put(f"transactions/{user_id}/{txn_id}", {
                "utr": utr,
                "amount_paid": amount,
                "amount_credited": amount_to_add,
                "status": "Success",
                "type": "Credit",
                "date": str(datetime.datetime.now())
            })
            return jsonify({"status": "success", "message": "Payment Verified & Credited Automatically!"})
        else:
            return jsonify({"status": "error", "message": "Payment Gateway Error."})

@app.route('/api/withdraw', methods=['POST'])
def withdraw():
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Not logged in"})
        
    data = request.json
    w_type = data.get('type')
    amount = float(data.get('amount'))
    user_id = session['user_id']
    user_data = db_get(f"users/{user_id}")
    
    if float(user_data.get('wallet_balance', 0.0)) < amount:
        return jsonify({"status": "error", "message": "Insufficient Balance!"})
        
    new_bal = float(user_data.get('wallet_balance')) - amount
    db_patch(f"users/{user_id}", {"wallet_balance": new_bal})
    
    req_id = f"WD{random.randint(100000, 999999)}"
    payload = {
        "user_id": user_id,
        "username": user_data['username'],
        "type": w_type,
        "amount": amount,
        "status": "Pending",
        "date": str(datetime.datetime.now())
    }
    
    if w_type == 'bank':
        payload.update({
            "acc_name": data.get('acc_name'),
            "acc_no": data.get('acc_no'),
            "ifsc": data.get('ifsc')
        })
    else:
        payload.update({"upi_id": data.get('upi_id')})
        
    db_put(f"withdrawals/{req_id}", payload)
    
    db_put(f"transactions/{user_id}/{req_id}", {
        "amount_paid": amount,
        "status": "Pending",
        "type": "Withdrawal",
        "date": str(datetime.datetime.now())
    })
    
    return jsonify({"status": "success", "message": "Withdrawal Request Sent to Admin!"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
