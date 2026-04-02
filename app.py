import os
import random
import re
import fitz  # PyMuPDF
import shutil
import sqlite3
import hashlib
import uuid
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, Request, Depends
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="toolscraft-hub-super-secret-key-xyz-2026")

UPLOAD_DIR = "uploads"
OUTPUT_DIR = "outputs"
TEMPLATE_FILE = "template.pdf"
DB_FILE = "database.sqlite"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (username TEXT PRIMARY KEY, password TEXT, role TEXT, daily_credits INTEGER, last_reset_date TEXT)''')
    try:
        c.execute("ALTER TABLE users ADD COLUMN session_token TEXT")
    except sqlite3.OperationalError:
        pass
    c.execute('''CREATE TABLE IF NOT EXISTS file_history 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, filename TEXT, processed_date TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- Helper Functions ---
def get_bdt_date():
    return (datetime.utcnow() + timedelta(hours=6)).strftime("%Y-%m-%d")

def get_bdt_time():
    return (datetime.utcnow() + timedelta(hours=6)).strftime("%Y-%m-%d %I:%M %p")

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def check_active_session(request: Request):
    username = request.session.get("username")
    token = request.session.get("session_token")
    if not username or not token:
        return False
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT session_token FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    
    if row and row[0] == token:
        return True
    return False

def add_default_admin():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username='admin'")
    if not c.fetchone():
        c.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)", 
                  ("admin", hash_password("123456"), "admin", 100, get_bdt_date(), None))
        conn.commit()
    conn.close()

add_default_admin()

def check_and_reset_credits(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT daily_credits, last_reset_date, role FROM users WHERE username=?", (username,))
    row = c.fetchone()
    today = get_bdt_date()
    
    if row:
        credits, last_date, role = row[0], row[1], row[2]
        if last_date != today:
            new_credits = 100 if role == 'admin' else 5
            c.execute("UPDATE users SET daily_credits=?, last_reset_date=? WHERE username=?", (new_credits, today, username))
            conn.commit()
            credits = new_credits
        conn.close()
        return credits
    conn.close()
    return 0

# --- PDF Processing Logic ---
def process_master_pdf(user_pdf_path, output_path, original_filename, ai_percentage):
    user_doc = fitz.open(user_pdf_path)
    if len(user_doc) > 0:
        user_doc.delete_page(0)
    
    actual_pages_count = len(user_doc)
    actual_words = 0
    actual_chars = 0
    for p in user_doc:
        body_rect = fitz.Rect(0, 38, p.rect.width, p.rect.height - 38)
        text = p.get_text("text", clip=body_rect)
        actual_words += len(text.split())
        actual_chars += len(text)
    
    new_size = f"{os.path.getsize(user_pdf_path) / 1024:.1f} KB"
    new_id = f"trn:oid:::1:{random.randint(1000000000, 9999999999)}"
    
    base_name = os.path.splitext(original_filename)[0].replace("_", " ")
    base_name = re.sub(r'(?i)ai\s*report', '', base_name).strip()
    words = base_name.split()
    new_title = " ".join(words[:5]) if words else "Document"

    now = datetime.utcnow() + timedelta(hours=6)
    sub_time = now - timedelta(minutes=2) 
    sub_date_str = sub_time.strftime(f"%b {sub_time.day}, %Y, {sub_time.strftime('%I').lstrip('0')}:%M %p BDT")
    down_date_str = now.strftime(f"%b {now.day}, %Y, {now.strftime('%I').lstrip('0')}:%M %p BDT")

    template_doc = fitz.open(TEMPLATE_FILE)
    page1_text = template_doc[0].get_text()
    
    old_id_match = re.search(r"trn:oid:::\d:\d+", page1_text)
    old_id = old_id_match.group(0) if old_id_match else None
    old_title_match = re.search(r"Aa Aa\s+(.*?)\s+Quick Submit", page1_text, re.DOTALL)
    old_title = old_title_match.group(1).strip() if old_title_match else "Fresh Template"
    old_fname_match = re.search(r"File Name\s+(.*?)\s+File Size", page1_text, re.DOTALL)
    old_fname_in_details = old_fname_match.group(1).strip() if old_fname_match else None
    old_pages_match = re.search(r"(\d+)\s+Pages", page1_text)
    old_pages_text = old_pages_match.group(0) if old_pages_match else None
    old_words_match = re.search(r"([\d,]+)\s+Words", page1_text)
    old_words_text = old_words_match.group(0) if old_words_match else None
    old_chars_match = re.search(r"([\d,]+)\s+Characters", page1_text)
    old_chars_text = old_chars_match.group(0) if old_chars_match else None
    old_sub_date_match = re.search(r"Submission Date\s+(.*?)\s+Download Date", page1_text, re.DOTALL)
    old_sub_date = old_sub_date_match.group(1).strip() if old_sub_date_match else None
    old_down_date_match = re.search(r"Download Date\s+(.*?)\s+File Name", page1_text, re.DOTALL)
    old_down_date = old_down_date_match.group(1).strip() if old_down_date_match else None

    replacements = {
        old_id: new_id, 
        old_title: new_title,
        old_fname_in_details: original_filename, 
        old_pages_text: f"{actual_pages_count + 2} Pages", 
        old_words_text: f"{actual_words:,} Words",
        old_chars_text: f"{actual_chars:,} Characters", 
        "23.5 KB": new_size
    }
    if old_sub_date: replacements[old_sub_date] = sub_date_str
    if old_down_date: replacements[old_down_date] = down_date_str

    page1 = template_doc[0]
    for old_txt, new_txt in replacements.items():
        if not old_txt: continue
        for inst in page1.search_for(old_txt):
            rect_to_clear = inst
            if old_txt in [old_pages_text, old_words_text, old_chars_text]:
                rect_to_clear = fitz.Rect(inst.x0 - 40, inst.y0, inst.x1 + 10, inst.y1)
            page1.add_redact_annot(rect_to_clear, fill=(1, 1, 1))
            page1.apply_redactions()
            is_main_title = (old_txt == old_title)
            x_pos = inst.x0 - 7 if old_txt in [old_pages_text, old_words_text, old_chars_text] else inst.x0
            page1.insert_text((x_pos, inst.y1 - 2), str(new_txt), 
                             fontsize=18 if is_main_title else 9.5, fontname="hebo" if is_main_title else "helv", color=(0, 0, 0))

    # --- "Aa Aa" পরিবর্তন করার লজিক (সাইজ ২০ এবং গাঢ় কালো রঙ) ---
    aa_matches = page1.search_for("Aa Aa")
    if aa_matches:
        for aa_inst in aa_matches:
            rect_aa = fitz.Rect(aa_inst.x0 - 2, aa_inst.y0 - 2, aa_inst.x1 + 10, aa_inst.y1 + 2)
            page1.add_redact_annot(rect_aa, fill=(1, 1, 1))
            page1.apply_redactions()
            
            custom_names_text = "Labib Hasan"
            page1.insert_text((aa_inst.x0, aa_inst.y1), custom_names_text, fontsize=20, fontname="hebo", color=(0, 0, 0))
    # --------------------------------------------------

    if len(template_doc) > 1:
        page2 = template_doc[1]
        ai_headers = page2.search_for("58% detected as AI")
        if ai_headers:
            inst = ai_headers[0]
            white_box = fitz.Rect(inst.x0, inst.y0 - 2, inst.x1 + 5, inst.y1 - 4)
            page2.add_redact_annot(white_box, fill=(1, 1, 1))
            page2.apply_redactions()
            
            # --- একদম ১০০% সঠিক ফন্ট বসানোর কোড ---
            font_name_to_use = "hebo"
            font_size_to_use = 18
            font_path = os.path.join("static", "LexendDeca-Medium.ttf")
            
            if os.path.exists(font_path):
                try:
                    # ফন্টটি প্রথমে রেজিস্টার করে নেওয়া হলো
                    page2.insert_font(fontname="lexend", fontfile=font_path)
                    font_name_to_use = "lexend"
                    font_size_to_use = 17
                except Exception as e:
                    print(f"Font Load Error: {e}")
            
            # রেজিস্টার করা ফন্ট দিয়ে টেক্সট বসানো হলো
            page2.insert_text((inst.x0, inst.y1 - 4), f"{ai_percentage}% detected as AI", fontsize=font_size_to_use, fontname=font_name_to_use, color=(0, 0, 0))
            # --------------------------------------

        group_inst = page2.search_for("AI-generated only") or page2.search_for("Al-generated only")
        if group_inst:
            pct_rect = fitz.Rect(group_inst[0].x1 + 2, group_inst[0].y0, group_inst[0].x1 + 60, group_inst[0].y1)
            page2.add_redact_annot(pct_rect, fill=(1, 1, 1))
            page2.apply_redactions()
            page2.insert_text((group_inst[0].x1 + 3, group_inst[0].y1 - 2), f"{ai_percentage}%", fontsize=9.5, fontname="helv", color=(0, 0, 0))
            
            left_num_rect = fitz.Rect(group_inst[0].x0 - 12, group_inst[0].y0, group_inst[0].x0 - 2, group_inst[0].y1)
            page2.add_redact_annot(left_num_rect, fill=(1,1,1))
            page2.apply_redactions()
            
            # --- পার্সেন্টেজ অনুযায়ী র‍্যান্ডম নাম্বারের লজিক ---
            try:
                ai_val = int(str(ai_percentage).replace('*', '').strip())
                if ai_val == 0:
                    random_detection_num = 0
                elif ai_val <= 15:
                    random_detection_num = random.randint(1, 4)
                elif ai_val <= 40:
                    random_detection_num = random.randint(5, 15)
                elif ai_val <= 70:
                    random_detection_num = random.randint(16, 35)
                else:
                    random_detection_num = random.randint(36, 77)
            except:
                random_detection_num = random.randint(1, 77)
            # ---------------------------------------------------
            
            x_pos = group_inst[0].x0 - 11 if random_detection_num > 9 else group_inst[0].x0 - 8
            page2.insert_text((x_pos, group_inst[0].y1 - 1.5), str(random_detection_num), fontsize=8.5, fontname="hebo", color=(0, 0, 0))

    template_doc.insert_pdf(user_doc)
    total_pages_final = len(template_doc)

    for i, page in enumerate(template_doc):
        rect = page.rect
        
        header_height = 50 if i < 2 else 38
        footer_height = 50 if i < 2 else 38
        header_title = "Cover Page" if i == 0 else "AI Writing Overview" if i == 1 else "AI Writing Submission"
        header_text = f"Page {i + 1} of {total_pages_final} - {header_title}"

        top_rect = fitz.Rect(0, 0, rect.width, header_height)
        page.draw_rect(top_rect, fill=(1, 1, 1), color=None, overlay=True)
        
        bottom_rect = fitz.Rect(0, rect.height - footer_height, rect.width, rect.height)
        page.draw_rect(bottom_rect, fill=(1, 1, 1), color=None, overlay=True)

        page.insert_image(fitz.Rect(20, 15, 90, 35), filename="static/logo.png")
        page.insert_text(fitz.Point(110, 30), header_text, fontsize=7, color=(0, 0, 0))
        page.insert_text(fitz.Point(rect.width - 200, 30), f"Submission ID {new_id}", fontsize=7, color=(0, 0, 0))

        page.insert_image(fitz.Rect(20, rect.height - 35, 90, rect.height - 15), filename="static/logo.png")
        page.insert_text(fitz.Point(110, rect.height - 20), header_text, fontsize=7, color=(0, 0, 0))
        page.insert_text(fitz.Point(rect.width - 200, rect.height - 20), f"Submission ID {new_id}", fontsize=7, color=(0, 0, 0))

    template_doc.save(output_path)
    template_doc.close()
    user_doc.close()

# --- Auth Routes ---
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT role FROM users WHERE username=? AND password=?", (username, hash_password(password)))
    user = c.fetchone()
    
    if user:
        token = str(uuid.uuid4())
        c.execute("UPDATE users SET session_token=? WHERE username=?", (token, username))
        conn.commit()
        conn.close()

        request.session['username'] = username
        request.session['role'] = user[0]
        request.session['session_token'] = token
        return RedirectResponse(url="/", status_code=303)
        
    conn.close()
    return templates.TemplateResponse("login.html", {"request": request, "error": "ভুল ইউজারনেম বা পাসওয়ার্ড!"})

@app.get("/logout")
async def logout(request: Request):
    username = request.session.get("username")
    if username:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("UPDATE users SET session_token=NULL WHERE username=?", (username,))
        conn.commit()
        conn.close()
        
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

# --- Main App Route ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not check_active_session(request):
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)

    username = request.session.get("username")
    role = request.session.get("role")
    
    credits = check_and_reset_credits(username)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, filename, processed_date FROM file_history WHERE username=? ORDER BY id DESC LIMIT 10", (username,))
    user_files = c.fetchall()
    conn.close()

    return templates.TemplateResponse("index.html", {
        "request": request, 
        "username": username, 
        "role": role,
        "credits": credits,
        "user_files": user_files
    })

@app.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...), ai_percentage: str = Form(...)):
    if not check_active_session(request):
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)

    username = request.session.get("username")
    credits = check_and_reset_credits(username)
    if credits <= 0:
        return HTMLResponse(content="<h3>আপনার আজকের ক্রেডিট শেষ!</h3><br><a href='/'>Back</a>", status_code=403)

    try:
        unique_id = str(uuid.uuid4())[:8]
        saved_filename = f"{unique_id}_{file.filename}"
        
        input_path = os.path.join(UPLOAD_DIR, saved_filename)
        output_path = os.path.join(OUTPUT_DIR, saved_filename)

        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        process_master_pdf(input_path, output_path, file.filename, ai_percentage)

        current_time = get_bdt_time()
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("UPDATE users SET daily_credits=daily_credits-1 WHERE username=?", (username,))
        c.execute("INSERT INTO file_history (username, filename, processed_date) VALUES (?, ?, ?)", 
                  (username, saved_filename, current_time))
        conn.commit()
        conn.close()

        return FileResponse(output_path, media_type="application/pdf", filename=file.filename)
    except Exception as e:
        return HTMLResponse(content=f"<h3>Error: {str(e)}</h3>", status_code=500)

@app.get("/download_past_file/{file_id}")
async def download_past_file(request: Request, file_id: int):
    if not check_active_session(request):
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)
        
    username = request.session.get("username")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT filename FROM file_history WHERE id=? AND username=?", (file_id, username))
    row = c.fetchone()
    conn.close()
    
    if row:
        saved_filename = row[0]
        output_path = os.path.join(OUTPUT_DIR, saved_filename)
        if os.path.exists(output_path):
            original_filename = saved_filename[9:]
            return FileResponse(output_path, media_type="application/pdf", filename=original_filename)
            
    return HTMLResponse("<h3>ফাইলটি সার্ভারে পাওয়া যায়নি! হয়তো আগেই ডিলিট করা হয়েছে।</h3><br><a href='/'>হোমে ফিরে যান</a>", status_code=404)

@app.post("/delete_my_file")
async def delete_my_file(request: Request, file_id: int = Form(...)):
    if not check_active_session(request):
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)
        
    username = request.session.get("username")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT filename FROM file_history WHERE id=? AND username=?", (file_id, username))
    row = c.fetchone()
    
    if row:
        saved_filename = row[0]
        in_path = os.path.join(UPLOAD_DIR, saved_filename)
        out_path = os.path.join(OUTPUT_DIR, saved_filename)
        if os.path.exists(in_path): os.remove(in_path)
        if os.path.exists(out_path): os.remove(out_path)
        
        c.execute("DELETE FROM file_history WHERE id=?", (file_id,))
        conn.commit()
    conn.close()
    return RedirectResponse(url="/", status_code=303)

# --- Admin Panel Routes ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if not check_active_session(request) or request.session.get("role") != "admin":
        return HTMLResponse("Access Denied", status_code=403)
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT username, role, daily_credits FROM users")
    users_raw = c.fetchall()
    
    today = get_bdt_date()
    users = []
    for u in users_raw:
        uname, role, credits = u
        c.execute("SELECT COUNT(*) FROM file_history WHERE username=? AND processed_date LIKE ?", (uname, f"{today}%"))
        used_today = c.fetchone()[0]
        users.append((uname, role, credits, used_today))

    c.execute("SELECT username, filename, processed_date FROM file_history ORDER BY id DESC LIMIT 50")
    history = c.fetchall()
    conn.close()
    
    total_files = len(os.listdir(UPLOAD_DIR)) + len(os.listdir(OUTPUT_DIR))
    
    return templates.TemplateResponse("admin.html", {"request": request, "users": users, "history": history, "total_files": total_files})

@app.post("/admin/create_user")
async def create_user(request: Request, new_username: str = Form(...), new_password: str = Form(...), initial_credits: int = Form(5)):
    if not check_active_session(request) or request.session.get("role") != "admin":
        return HTMLResponse("Access Denied", status_code=403)
        
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)", 
                  (new_username, hash_password(new_password), "user", initial_credits, get_bdt_date(), None))
        conn.commit()
    except sqlite3.IntegrityError:
        pass 
    finally:
        conn.close()
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/update_credits")
async def update_credits(request: Request, up_username: str = Form(...), new_credits: int = Form(...)):
    if not check_active_session(request) or request.session.get("role") != "admin":
        return HTMLResponse("Access Denied", status_code=403)
        
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET daily_credits=? WHERE username=?", (new_credits, up_username))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/delete_user")
async def delete_user(request: Request, del_username: str = Form(...)):
    if not check_active_session(request) or request.session.get("role") != "admin":
        return HTMLResponse("Access Denied", status_code=403)
        
    if del_username == "admin":
        return HTMLResponse("Admin account cannot be deleted!", status_code=400)
        
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE username=?", (del_username,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/clear_all_files")
async def clear_all_files(request: Request):
    if not check_active_session(request) or request.session.get("role") != "admin":
        return HTMLResponse("Access Denied", status_code=403)
    
    for f in os.listdir(UPLOAD_DIR):
        file_path = os.path.join(UPLOAD_DIR, f)
        if os.path.isfile(file_path): os.remove(file_path)
            
    for f in os.listdir(OUTPUT_DIR):
        file_path = os.path.join(OUTPUT_DIR, f)
        if os.path.isfile(file_path): os.remove(file_path)
            
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM file_history")
    conn.commit()
    conn.close()
    
    return RedirectResponse(url="/admin", status_code=303)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)