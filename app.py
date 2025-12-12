import sqlite3
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_file, render_template_string
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from flask_apscheduler import APScheduler 
import csv
from io import StringIO
import os
from werkzeug.utils import secure_filename

# --- KONFIGURASI APLIKASI ---
app = Flask(__name__)
DATABASE = 'database.db'
app.config['SECRET_KEY'] = 'KUNCI_RAHASIA_ANDA_YANG_SANGAT_PANJANG_DAN_AMAN_123456789' 

# --- KONFIGURASI UPLOAD FOTO ---
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Inisialisasi Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' 
login_manager.login_message = 'Anda harus login untuk mengakses halaman ini.'
login_manager.login_message_category = 'warning'

# --- KONFIGURASI GLOBAL BOT ---
DAILY_REPORT = {} 
scheduler = APScheduler()

# --- FUNGSI HELPER ---

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- MODEL PENGGUNA UNTUK FLASK-LOGIN ---

class User(UserMixin):
    def __init__(self, id, username, password_hash, profile_pic='default.png'):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.profile_pic = profile_pic

    @staticmethod
    def get(user_id):
        conn = get_db_connection()
        # AMBIL KOLOM profile_pic dari database
        user_data = conn.execute("SELECT id, username, password_hash, profile_pic FROM users WHERE id = ?", (user_id,)).fetchone() 
        conn.close()
        if user_data:
            return User(user_data['id'], user_data['username'], user_data['password_hash'], user_data['profile_pic']) 
        return None

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)


# --- FUNGSI DATABASE & SETUP ---

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def create_table():
    conn = get_db_connection()
    
    # Tabel Tugas (Tasks)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            priority TEXT DEFAULT 'Medium', 
            price REAL NOT NULL,
            paid REAL NOT NULL,
            completion_date TEXT,
            client_id INTEGER,
            progress INTEGER DEFAULT 0
        )
    ''')
    
    # MIGRATION SAFEGUARD: client_id dan progress
    try: conn.execute("ALTER TABLE tasks ADD COLUMN client_id INTEGER")
    except sqlite3.OperationalError: pass 
    try: conn.execute("ALTER TABLE tasks ADD COLUMN progress INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass 
    
    # Tabel Pengeluaran (Expenses)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            date TEXT NOT NULL
        )
    ''')
    
    # Tabel Klien (Clients)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            contact TEXT,
            email TEXT
        )
    ''')
    
    # Tabel Pengguna (Users) - Diperbarui dengan profile_pic
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            profile_pic TEXT DEFAULT 'default.png'
        )
    ''')
    
    # MIGRATION SAFEGUARD: profile_pic
    try: conn.execute("ALTER TABLE users ADD COLUMN profile_pic TEXT DEFAULT 'default.png'")
    except sqlite3.OperationalError: pass 
    
    # --- BUAT PENGGUNA ADMIN DEFAULT ---
    hashed_password = generate_password_hash('admin123', method='pbkdf2:sha256')
    try:
        conn.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', 
                     ('admin', hashed_password))
        conn.commit()
    except sqlite3.IntegrityError:
        pass 

    conn.close()

create_table()

# --- FUNGSI BOT ASISTEN (JOB YANG DIJADWALKAN) ---

def bot_job_generate_daily_report():
    """Fungsi Bot yang memeriksa tugas prioritas tinggi yang berdekatan dengan deadline."""
    global DAILY_REPORT
    print(f"--- Bot Job: Generating Daily Risk Report at {datetime.now()} ---")
    
    conn = get_db_connection()
    seven_days_from_now = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')

    risky_tasks = conn.execute(f'''
        SELECT name, completion_date, priority
        FROM tasks 
        WHERE status IN ('To Do', 'In Progress') 
          AND priority = 'High'
          AND completion_date BETWEEN date('now') AND ?
    ''', (seven_days_from_now,)).fetchall()
    
    conn.close()
    
    risky_count = len(risky_tasks)
    
    if risky_count > 0:
        message = f"🚨 PERINGATAN RISIKO BOT: Ada {risky_count} Tugas Prioritas Tinggi yang Deadline-nya dalam 7 hari ke depan. Harap segera dialokasikan waktu!"
        category = 'danger'
    else:
        message = "✅ LAPORAN BOT: Tidak ada risiko deadline High-Priority dalam minggu ini. Semua terkendali."
        category = 'success'
        
    DAILY_REPORT = {
        'message': message, 
        'category': category, 
        'timestamp': datetime.now().strftime('%d %B %Y, %H:%M')
    }
    print(f"Report Generated: {message}")


# --- ROUTES OTENTIKASI ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('all_pages', page='dashboard'))
        
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        user_data = conn.execute("SELECT id, username, password_hash, profile_pic FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()

        if user_data:
            user = User(user_data['id'], user_data['username'], user_data['password_hash'], user_data['profile_pic'])
            if check_password_hash(user.password_hash, password):
                login_user(user)
                flash('Login berhasil!', 'success')
                next_page = request.args.get('next')
                return redirect(next_page or url_for('all_pages', page='dashboard'))
            else:
                flash('Sandi salah.', 'danger')
        else:
            flash('Nama pengguna tidak ditemukan.', 'danger')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Anda telah keluar.', 'success')
    return redirect(url_for('login'))


# --- ROUTE UTAMA (MENGIRIM SEMUA DATA KE index.html) ---

@app.route('/', defaults={'page': 'dashboard'})
@app.route('/<page>')
@login_required
def all_pages(page='dashboard'):
    """Route tunggal yang memuat index.html dengan data spesifik halaman."""
    
    conn = get_db_connection()
    context = {}
    
    # --- Data Tugas & Dasar ---
    tasks = conn.execute('SELECT * FROM tasks ORDER BY id DESC').fetchall()
    context['tasks'] = tasks
    
    # --- Data Finansial ---
    expenses = conn.execute('SELECT * FROM expenses ORDER BY date DESC').fetchall()
    context['expenses'] = expenses

    # --- Data Klien Rinci ---
    clients_list = conn.execute('SELECT * FROM clients ORDER BY name').fetchall()
    clients_data = []
    for client in clients_list:
        jobs = conn.execute('SELECT name, status, price, paid FROM tasks WHERE client_id = ? ORDER BY id DESC', (client['id'],)).fetchall()
        client_dict = dict(client)
        client_dict['jobs'] = [dict(job) for job in jobs]
        client_dict['total_revenue'] = sum(job['price'] for job in jobs)
        client_dict['jobs_done'] = sum(1 for job in jobs if job['status'] == 'Done')
        clients_data.append(client_dict)
    context['clients_list'] = clients_data
    
    conn.close()
    
    # --- INJEKSI LAPORAN BOT KE TEMPLATE ---
    global DAILY_REPORT
    context['bot_report'] = DAILY_REPORT 
    
    # --- PERBAIKAN KRITIS: Kirim objek datetime ke template ---
    context['datetime'] = datetime 

    return render_template('index.html', current_page=page, user=current_user, **context)

# --- CRUD APIS (Diperbarui untuk Progress) ---

@app.route('/add_task', methods=['POST'])
@login_required
def add_task():
    data = request.form
    name = data['name']
    priority = data['priority']
    price = float(data['price'])
    paid = float(data['paid'])
    completion_date = data.get('completion_date')
    
    progress = int(data.get('progress', 0)) 
    
    if progress == 100:
        calculated_status = 'Done'
    elif progress >= 10:
        calculated_status = 'In Progress'
    else: 
        calculated_status = 'To Do'

    if not completion_date: completion_date = None
    
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO tasks (name, status, priority, price, paid, completion_date, progress) VALUES (?, ?, ?, ?, ?, ?, ?)', 
        (name, calculated_status, priority, price, paid, completion_date, progress)
    )
    conn.commit()
    conn.close()
    flash('Tugas berhasil ditambahkan!', 'success')
    return redirect(url_for('all_pages', page='dashboard'))

@app.route('/delete_task/<int:task_id>', methods=['POST'])
@login_required
def delete_task(task_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/add_client', methods=['POST'])
@login_required
def add_client():
    data = request.form
    name = data['name']
    contact = data.get('contact')
    email = data.get('email')
    conn = get_db_connection()
    conn.execute('INSERT INTO clients (name, contact, email) VALUES (?, ?, ?)', (name, contact, email))
    conn.commit()
    conn.close()
    flash('Klien berhasil ditambahkan!', 'success')
    return redirect(url_for('all_pages', page='clients'))

@app.route('/add_expense', methods=['POST'])
@login_required
def add_expense():
    data = request.form
    description = data['description']
    amount = float(data['amount'])
    date = data['date']
    conn = get_db_connection()
    conn.execute('INSERT INTO expenses (description, amount, date) VALUES (?, ?, ?)', (description, amount, date))
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "message": "Expense added successfully"})

# --- ROUTES PENGATURAN YANG BERFUNGSI ---

# 1. UBAH KATA SANDI
@app.route('/change_password', methods=['POST'])
@login_required
def change_password():
    old_password = request.form['old_password']
    new_password = request.form['new_password']
    confirm_password = request.form['confirm_password']

    conn = get_db_connection()
    user_data = conn.execute("SELECT id, password_hash FROM users WHERE id = ?", (current_user.id,)).fetchone()
    conn.close()

    if not check_password_hash(user_data['password_hash'], old_password):
        flash('Kata sandi lama salah.', 'danger')
        return redirect(url_for('all_pages', page='settings'))

    if new_password != confirm_password:
        flash('Kata sandi baru dan konfirmasi tidak cocok.', 'danger')
        return redirect(url_for('all_pages', page='settings'))
    
    if len(new_password) < 6:
        flash('Kata sandi baru minimal 6 karakter.', 'danger')
        return redirect(url_for('all_pages', page='settings'))

    hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
    conn = get_db_connection()
    conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', (hashed_password, current_user.id))
    conn.commit()
    conn.close()

    flash('Kata sandi berhasil diubah.', 'success')
    return redirect(url_for('all_pages', page='settings'))

# 2. UPLOAD FOTO PROFIL
@app.route('/upload_profile_pic', methods=['POST'])
@login_required
def upload_profile_pic():
    if 'profile_pic' not in request.files:
        flash('Tidak ada file yang diunggah.', 'danger')
        return redirect(url_for('all_pages', page='settings'))
        
    file = request.files['profile_pic']
    
    if file.filename == '':
        flash('Tidak ada file yang dipilih.', 'danger')
        return redirect(url_for('all_pages', page='settings'))
        
    if file and allowed_file(file.filename):
        filename = secure_filename(f"{current_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Hapus file lama jika ada (optional)
        if current_user.profile_pic and current_user.profile_pic != 'default.png':
            old_file_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.profile_pic)
            if os.path.exists(old_file_path):
                os.remove(old_file_path)

        file.save(file_path)
        
        conn = get_db_connection()
        conn.execute('UPDATE users SET profile_pic = ? WHERE id = ?', (filename, current_user.id))
        conn.commit()
        conn.close()
        
        # Perbarui objek current_user di session
        user_data = User.get(current_user.id)
        current_user.profile_pic = user_data.profile_pic
        
        flash('Foto profil berhasil diunggah dan diperbarui!', 'success')
        return redirect(url_for('all_pages', page='settings'))
    else:
        flash('Jenis file tidak diizinkan. Gunakan PNG, JPG, atau GIF.', 'danger')
        return redirect(url_for('all_pages', page='settings'))


# 3. EXPORT DATA (BACKUP CSV)
@app.route('/export_data')
@login_required
def export_data():
    conn = get_db_connection()
    tables = {'tasks': 'tasks', 'clients': 'clients', 'expenses': 'expenses'}
    
    output = StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['--- JOB JOKI PRO DATA EXPORT ---'])
    writer.writerow(['Exported At', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
    writer.writerow([''])

    for table_name in tables.keys():
        data = conn.execute(f"SELECT * FROM {table_name}").fetchall()
        
        if not data:
            writer.writerow([f'Tabel {table_name} kosong.'])
            writer.writerow([''])
            continue

        writer.writerow([f'TABLE: {table_name.upper()}'])
        
        headers = data[0].keys()
        writer.writerow(headers)
        
        for row in data:
            writer.writerow(list(row))
        writer.writerow([''])
        
    conn.close()

    response = app.make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=jokipro_backup_" + datetime.now().strftime('%Y%m%d') + ".csv"
    response.headers["Content-type"] = "text/csv"
    return response


# 4. HAPUS SEMUA DATA (RESET)
@app.route('/reset_all_data', methods=['POST'])
@login_required
def reset_all_data():
    conn = get_db_connection()
    conn.execute('DELETE FROM tasks')
    conn.execute('DELETE FROM clients')
    conn.execute('DELETE FROM expenses')
    conn.commit()
    conn.close()
    
    flash('Semua data proyek (Tugas, Klien, Biaya) berhasil direset!', 'warning')
    return redirect(url_for('all_pages', page='settings'))


# --- API DATA (AJAX) ---
# ... (Semua API data tetap sama seperti sebelumnya) ...

@app.route('/api/financial_summary')
@login_required
def get_financial_summary():
    conn = get_db_connection()
    summary = conn.execute('SELECT SUM(price) as total_revenue, SUM(paid) as total_paid FROM tasks').fetchone()
    expense_summary = conn.execute('SELECT SUM(amount) as total_expenses FROM expenses').fetchone()
    conn.close()
    total_revenue = summary['total_revenue'] if summary['total_revenue'] else 0
    total_paid = summary['total_paid'] if summary['total_paid'] else 0
    total_expenses = expense_summary['total_expenses'] if expense_summary['total_expenses'] else 0
    remaining_due = total_revenue - total_paid
    net_profit = total_paid - total_expenses 
    return jsonify({'total_revenue': total_revenue, 'total_paid': total_paid, 'remaining_due': remaining_due, 'total_expenses': total_expenses, 'net_profit': net_profit})

@app.route('/api/revenue_pipeline')
@login_required
def get_revenue_pipeline():
    conn = get_db_connection()
    pipeline = conn.execute("SELECT SUM(price) as projected_revenue FROM tasks WHERE status IN ('To Do', 'In Progress', 'Review')").fetchone()
    conn.close()
    projected_revenue = pipeline['projected_revenue'] if pipeline['projected_revenue'] else 0
    return jsonify({'projected_revenue': projected_revenue})

@app.route('/api/client_retention')
@login_required
def get_client_retention():
    conn = get_db_connection()
    retention_data = conn.execute('''
        SELECT c.name, COUNT(t.id) as total_jobs
        FROM tasks t
        JOIN clients c ON t.client_id = c.id
        WHERE t.status = 'Done' AND t.client_id IS NOT NULL 
        GROUP BY c.name
        HAVING COUNT(t.id) > 1
        ORDER BY total_jobs DESC
    ''').fetchall()
    total_clients_row = conn.execute("SELECT COUNT(DISTINCT id) FROM clients").fetchone()
    total_clients = total_clients_row[0] if total_clients_row else 0
    conn.close()
    return jsonify({'retained_clients_count': len(retention_data), 'total_clients': total_clients})

@app.route('/api/aging_analysis')
@login_required
def get_aging_analysis():
    conn = get_db_connection()
    today = datetime.now().strftime('%Y-%m-%d')
    
    aging_data = conn.execute(f'''
        SELECT 
            name, 
            completion_date, 
            (price - paid) AS remaining_due,
            (julianday('{today}') - julianday(completion_date)) AS days_overdue
        FROM tasks 
        WHERE status = 'Done' AND remaining_due > 0 AND completion_date IS NOT NULL
        HAVING days_overdue > 0
        ORDER BY days_overdue DESC
    ''').fetchall()
    
    aging_summary = {
        'total_overdue': 0.0,
        'aging_1_30': 0.0,
        'aging_31_60': 0.0,
        'aging_60_plus': 0.0,
        'risky_tasks': []
    }
    
    for row in aging_data:
        days = row['days_overdue']
        due_amount = row['remaining_due']
        aging_summary['total_overdue'] += due_amount
        
        if days <= 30:
            aging_summary['aging_1_30'] += due_amount
        elif days <= 60:
            aging_summary['aging_31_60'] += due_amount
        else:
            aging_summary['aging_60_plus'] += due_amount
            
        if days >= 31:
             aging_summary['risky_tasks'].append({
                 'name': row['name'],
                 'days': int(days),
                 'amount': due_amount
             })

    paid_row = conn.execute("SELECT SUM(paid) as total_paid, SUM(price) as total_revenue FROM tasks").fetchone()
    total_paid = paid_row['total_paid'] if paid_row['total_paid'] else 0
    total_revenue = paid_row['total_revenue'] if paid_row['total_revenue'] else 0
    
    collection_ratio = (total_paid / total_revenue) * 100 if total_revenue > 0 else 0
    
    conn.close()

    return jsonify({
        'aging_summary': aging_summary,
        'collection_ratio': round(collection_ratio, 2),
        'risky_tasks': sorted(aging_summary['risky_tasks'], key=lambda x: x['days'], reverse=True)
    })

@app.route('/api/monthly_cashflow')
@login_required
def get_monthly_cashflow():
    conn = get_db_connection()
    cashflow = conn.execute("SELECT strftime('%Y-%m', completion_date) as month, SUM(paid) as total_paid FROM tasks WHERE completion_date IS NOT NULL AND paid > 0 GROUP BY month ORDER BY month").fetchall()
    conn.close()
    labels = [row['month'] for row in cashflow]
    data = [row['total_paid'] for row in cashflow]
    return jsonify({'labels': labels, 'data': data})

@app.route('/api/priority_data')
@login_required
def get_priority_data():
    conn = get_db_connection()
    counts = conn.execute('SELECT priority, COUNT(id) as count FROM tasks GROUP BY priority').fetchall()
    conn.close()
    priority_order = {'High': 0, 'Medium': 1, 'Low': 2}
    sorted_counts = sorted(counts, key=lambda x: priority_order.get(x['priority'], 99))
    labels = [row['priority'] for row in sorted_counts]
    data = [row['count'] for row in sorted_counts]
    return jsonify({'labels': labels, 'data': data})

@app.route('/api/deadline_risk')
@login_required
def get_deadline_risk():
    conn = get_db_connection()
    tasks = conn.execute("SELECT name, completion_date, priority FROM tasks WHERE status IN ('To Do', 'In Progress') AND completion_date IS NOT NULL").fetchall()
    conn.close()
    risk_data = []
    today = datetime.now().date()
    workload_score = 0
    for task in tasks:
        try:
            deadline = datetime.strptime(task['completion_date'], '%Y-%m-%d').date()
            days_left = (deadline - today).days
            priority_weight = {'High': 3, 'Medium': 2, 'Low': 1}.get(task['priority'], 1)
            if days_left >= 0 and days_left <= 14:
                workload_score += priority_weight
                risk_value = max(0, (priority_weight * 10) - (days_left * 5))
                normalized_risk = min(100, risk_value * 5) 
                risk_level = 'High' if normalized_risk >= 70 else ('Medium' if normalized_risk >= 30 else 'Low')
                risk_data.append({'name': task['name'], 'days_left': days_left, 'risk_level': risk_level, 'risk_score': normalized_risk})
        except ValueError:
            continue
    risk_data.sort(key=lambda x: x['risk_score'], reverse=True)
    overall_workload = 'Heavy' if workload_score >= 10 else ('Moderate' if workload_score >= 5 else 'Light')
    return jsonify({'overall_workload': overall_workload, 'risky_tasks': risk_data[:5]})

if __name__ == '__main__':
    # Pastikan direktori uploads ada saat start
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    
    # 1. Inisialisasi dan Start Scheduler
    scheduler.init_app(app)
    
    # 2. Tambahkan Job untuk Bot (Setiap hari pukul 08:00 AM)
    scheduler.add_job(
        id='DailyRiskReport', 
        func=bot_job_generate_daily_report, 
        trigger='cron', 
        hour=8, 
        minute=0
    )
    
    # 3. Jalankan Job sekali saat startup agar ada laporan saat aplikasi baru dibuka
    with app.app_context():
        bot_job_generate_daily_report() 

    scheduler.start()
    
    app.run(debug=True)