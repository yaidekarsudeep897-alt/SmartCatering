import sqlite3, os, secrets, io
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_file
from werkzeug.security import generate_password_hash, check_password_hash
app=Flask(__name__); app.secret_key=os.environ.get("SECRET_KEY","change-this-secret-before-deploying")
DB=os.path.join(os.path.dirname(__file__),"smartcatering.db")
def conn():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def init_db():
 with conn() as c:
  c.executescript("""
  CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,email TEXT UNIQUE,password TEXT,role TEXT,phone TEXT);
  CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,owner_id INTEGER,title TEXT,event_type TEXT,event_date TEXT,event_time TEXT,venue TEXT,budget REAL,notes TEXT);
  CREATE TABLE IF NOT EXISTS guests(id INTEGER PRIMARY KEY,event_id INTEGER,name TEXT,email TEXT,phone TEXT,token TEXT UNIQUE,response TEXT DEFAULT 'Pending',checked_in INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS dishes(id INTEGER PRIMARY KEY,caterer_id INTEGER,name TEXT,category TEXT,description TEXT,price REAL,available INTEGER DEFAULT 1);
  CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY,event_id INTEGER,caterer_id INTEGER,guest_count INTEGER,total REAL,status TEXT DEFAULT 'Pending',notes TEXT);
  CREATE TABLE IF NOT EXISTS booking_items(id INTEGER PRIMARY KEY,booking_id INTEGER,dish_id INTEGER,quantity INTEGER,unit_price REAL);
  CREATE TABLE IF NOT EXISTS worker_profiles(id INTEGER PRIMARY KEY,user_id INTEGER UNIQUE,skills TEXT,experience TEXT,area TEXT,availability TEXT DEFAULT 'Available');
  CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY,worker_id INTEGER,booking_id INTEGER,role_title TEXT,status TEXT DEFAULT 'Applied',salary REAL DEFAULT 0,advance REAL DEFAULT 0,hours REAL DEFAULT 0,payment TEXT DEFAULT 'Unpaid');
  """)
init_db()
def user(): 
 if not session.get("uid"): return None
 with conn() as c: return c.execute("SELECT * FROM users WHERE id=?",(session["uid"],)).fetchone()
@app.context_processor
def inject(): return {"me":user()}
def need(*roles):
 def deco(fn):
  @wraps(fn)
  def wrap(*a,**kw):
   if not session.get("uid"): return redirect(url_for("login"))
   if roles and session.get("role") not in roles: abort(403)
   return fn(*a,**kw)
  return wrap
 return deco
@app.route("/")
def index():
 with conn() as c: dishes=c.execute("SELECT d.*,u.name AS caterer FROM dishes d JOIN users u ON u.id=d.caterer_id WHERE d.available=1 ORDER BY d.id DESC LIMIT 6").fetchall()
 return render_template("index.html",dishes=dishes)
@app.route("/register",methods=["GET","POST"])
def register():
 if request.method=="POST":
  f=request.form; role=f.get("role")
  if role not in ("owner","caterer","worker") or not f.get("name","").strip() or len(f.get("password",""))<6:
   flash("Complete the fields. Password must have at least 6 characters.","danger"); return redirect(url_for("register"))
  try:
   with conn() as c:
    cur=c.execute("INSERT INTO users(name,email,password,role,phone) VALUES(?,?,?,?,?)",(f["name"].strip(),f["email"].strip().lower(),generate_password_hash(f["password"]),role,f.get("phone","")))
    if role=="worker": c.execute("INSERT INTO worker_profiles(user_id,skills,experience,area) VALUES(?,?,?,?)",(cur.lastrowid,f.get("skills",""),f.get("experience",""),f.get("area","")))
   flash("Account created. Please log in.","success"); return redirect(url_for("login"))
  except sqlite3.IntegrityError: flash("This email is already registered.","danger")
 return render_template("register.html")
@app.route("/login",methods=["GET","POST"])
def login():
 if request.method=="POST":
  with conn() as c: u=c.execute("SELECT * FROM users WHERE email=?",(request.form.get("email","").lower(),)).fetchone()
  if u and check_password_hash(u["password"],request.form.get("password","")):
   session.clear(); session.update(uid=u["id"],role=u["role"],name=u["name"]); return redirect(url_for("dashboard"))
  flash("Email or password is incorrect.","danger")
 return render_template("login.html")
@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("index"))
@app.route("/dashboard")
@need()
def dashboard():
 uid=session["uid"]; role=session["role"]
 with conn() as c:
  if role=="owner":
   rows=c.execute("SELECT * FROM events WHERE owner_id=? ORDER BY id DESC",(uid,)).fetchall()
   data=[]
   for e in rows:
    guests=c.execute("SELECT * FROM guests WHERE event_id=? ORDER BY id DESC",(e["id"],)).fetchall()
    bookings=c.execute("SELECT b.*,u.name caterer FROM bookings b JOIN users u ON u.id=b.caterer_id WHERE event_id=?",(e["id"],)).fetchall()
    data.append({"event":e,"guests":guests,"bookings":bookings})
   return render_template("owner_dashboard.html",data=data)
  if role=="caterer":
   bookings=c.execute("SELECT b.*,e.title event_title,e.event_date,e.venue,u.name owner_name FROM bookings b JOIN events e ON e.id=b.event_id JOIN users u ON u.id=e.owner_id WHERE b.caterer_id=? ORDER BY b.id DESC",(uid,)).fetchall()
   apps=c.execute("SELECT a.*,u.name worker_name,e.title event_title FROM applications a JOIN users u ON u.id=a.worker_id JOIN bookings b ON b.id=a.booking_id JOIN events e ON e.id=b.event_id WHERE b.caterer_id=? ORDER BY a.id DESC",(uid,)).fetchall()
   counts={"accepted":sum(b["status"]=="Accepted" for b in bookings),"guests":sum(b["guest_count"] for b in bookings if b["status"]=="Accepted"),"pending":sum(b["status"]=="Pending" for b in bookings)}
   return render_template("caterer_dashboard.html",bookings=bookings,apps=apps,counts=counts)
  if role=="worker":
   with conn() as c:
    apps=c.execute("SELECT a.*,e.title event_title,e.event_date,e.venue FROM applications a JOIN bookings b ON b.id=a.booking_id JOIN events e ON e.id=b.event_id WHERE a.worker_id=? ORDER BY a.id DESC",(uid,)).fetchall()
    jobs=c.execute("SELECT b.*,e.title event_title,e.event_date,e.venue,u.name caterer FROM bookings b JOIN events e ON e.id=b.event_id JOIN users u ON u.id=b.caterer_id WHERE b.status='Accepted' ORDER BY b.id DESC").fetchall()
    profile=c.execute("SELECT * FROM worker_profiles WHERE user_id=?",(uid,)).fetchone()
   return render_template("worker_dashboard.html",apps=apps,jobs=jobs,profile=profile)
  abort(403)
@app.route("/events/new",methods=["GET","POST"])
@need("owner")
def new_event():
 if request.method=="POST":
  f=request.form
  if not f.get("title") or not f.get("event_date") or not f.get("venue"): flash("Event name, date and venue are required.","danger"); return redirect(url_for("new_event"))
  with conn() as c: cur=c.execute("INSERT INTO events(owner_id,title,event_type,event_date,event_time,venue,budget,notes) VALUES(?,?,?,?,?,?,?,?)",(session["uid"],f["title"],f.get("event_type","Other"),f["event_date"],f.get("event_time",""),f["venue"],float(f.get("budget") or 0),f.get("notes",""))); eid=cur.lastrowid
  flash("Event created. Add guests and request catering.","success"); return redirect(url_for("event_detail",eid=eid))
 return render_template("event_form.html")
@app.route("/events/<int:eid>")
@need("owner")
def event_detail(eid):
 with conn() as c:
  e=c.execute("SELECT * FROM events WHERE id=? AND owner_id=?",(eid,session["uid"])).fetchone()
  if not e: abort(404)
  guests=c.execute("SELECT * FROM guests WHERE event_id=?",(eid,)).fetchall()
  bookings=c.execute("SELECT b.*,u.name caterer FROM bookings b JOIN users u ON u.id=b.caterer_id WHERE b.event_id=?",(eid,)).fetchall()
 return render_template("event_detail.html",event=e,guests=guests,bookings=bookings)
@app.route("/events/<int:eid>/guest",methods=["POST"])
@need("owner")
def add_guest(eid):
 with conn() as c:
  if not c.execute("SELECT id FROM events WHERE id=? AND owner_id=?",(eid,session["uid"])).fetchone(): abort(404)
  if not request.form.get("name","").strip(): flash("Guest name is required.","danger"); return redirect(url_for("event_detail",eid=eid))
  c.execute("INSERT INTO guests(event_id,name,email,phone,token) VALUES(?,?,?,?,?)",(eid,request.form["name"],request.form.get("email",""),request.form.get("phone",""),secrets.token_urlsafe(24)))
 flash("Guest invitation created.","success"); return redirect(url_for("event_detail",eid=eid))
@app.route("/invite/<token>")
def invite(token):
 with conn() as c: g=c.execute("SELECT g.*,e.title event_title,e.event_date,e.event_time,e.venue FROM guests g JOIN events e ON e.id=g.event_id WHERE token=?",(token,)).fetchone()
 if not g: abort(404)
 return render_template("invite.html",g=g)
@app.route("/invite/<token>/rsvp",methods=["POST"])
def rsvp(token):
 if request.form.get("response") not in ("Accepted","Declined"): abort(400)
 with conn() as c: c.execute("UPDATE guests SET response=? WHERE token=?",(request.form["response"],token))
 flash("Your RSVP has been saved.","success"); return redirect(url_for("invite",token=token))
@app.route("/guest-qr/<token>")
@need("owner")
def guest_qr(token):
 with conn() as c: g=c.execute("SELECT * FROM guests WHERE token=?",(token,)).fetchone()
 if not g: abort(404)
 try:
  import qrcode
  img=qrcode.make(url_for("invite",token=token,_external=True)); out=io.BytesIO(); img.save(out,format="PNG"); out.seek(0); return send_file(out,mimetype="image/png",download_name="guest-qr.png")
 except ImportError: flash("Install QR dependency with pip install -r requirements.txt.","danger"); return redirect(url_for("dashboard"))
@app.route("/caterers")
def caterers():
 with conn() as c: rows=c.execute("SELECT * FROM users WHERE role='caterer' ORDER BY name").fetchall()
 return render_template("caterers.html",caterers=rows)
@app.route("/caterer/menu",methods=["GET","POST"])
@need("caterer")
def menu_manage():
 if request.method=="POST":
  f=request.form
  with conn() as c: c.execute("INSERT INTO dishes(caterer_id,name,category,description,price) VALUES(?,?,?,?,?)",(session["uid"],f["name"],f.get("category","Main Course"),f.get("description",""),max(0,float(f.get("price") or 0))))
  flash("Dish added to your menu.","success"); return redirect(url_for("menu_manage"))
 with conn() as c: dishes=c.execute("SELECT * FROM dishes WHERE caterer_id=? ORDER BY id DESC",(session["uid"],)).fetchall()
 return render_template("menu_manage.html",dishes=dishes)
@app.route("/caterers/<int:cid>")
def caterer_menu(cid):
 with conn() as c:
  caterer=c.execute("SELECT * FROM users WHERE id=? AND role='caterer'",(cid,)).fetchone()
  if not caterer: abort(404)
  dishes=c.execute("SELECT * FROM dishes WHERE caterer_id=? AND available=1",(cid,)).fetchall()
  events=c.execute("SELECT * FROM events WHERE owner_id=?",(session["uid"],)).fetchall() if session.get("role")=="owner" else []
 return render_template("caterer_menu.html",caterer=caterer,dishes=dishes,events=events)
@app.route("/booking/new/<int:eid>/<int:cid>",methods=["GET","POST"])
@need("owner")
def booking_new(eid,cid):
 with conn() as c:
  event=c.execute("SELECT * FROM events WHERE id=? AND owner_id=?",(eid,session["uid"])).fetchone()
  caterer=c.execute("SELECT * FROM users WHERE id=? AND role='caterer'",(cid,)).fetchone()
  if not event or not caterer: abort(404)
  dishes=c.execute("SELECT * FROM dishes WHERE caterer_id=? AND available=1",(cid,)).fetchall()
  if request.method=="POST":
   guests=max(1,int(request.form.get("guest_count") or 1)); total=0
   cur=c.execute("INSERT INTO bookings(event_id,caterer_id,guest_count,total,notes) VALUES(?,?,?,?,?)",(eid,cid,guests,0,request.form.get("notes",""))); bid=cur.lastrowid
   for d in dishes:
    q=max(0,int(request.form.get("dish_"+str(d["id"])) or 0))
    if q: c.execute("INSERT INTO booking_items(booking_id,dish_id,quantity,unit_price) VALUES(?,?,?,?)",(bid,d["id"],q,d["price"])); total+=q*d["price"]*guests
   c.execute("UPDATE bookings SET total=? WHERE id=?",(total,bid))
   flash("Booking request sent to caterer.","success"); return redirect(url_for("event_detail",eid=eid))
 return render_template("booking_form.html",event=event,caterer=caterer,dishes=dishes)
@app.route("/booking/<int:bid>/<decision>",methods=["POST"])
@need("caterer")
def booking_decision(bid,decision):
 if decision not in ("accept","reject"): abort(400)
 with conn() as c: c.execute("UPDATE bookings SET status=? WHERE id=? AND caterer_id=?",("Accepted" if decision=="accept" else "Rejected",bid,session["uid"]))
 flash("Booking updated.","success"); return redirect(url_for("dashboard"))
@app.route("/apply/<int:bid>",methods=["POST"])
@need("worker")
def apply(bid):
 with conn() as c:
  if not c.execute("SELECT id FROM bookings WHERE id=? AND status='Accepted'",(bid,)).fetchone(): abort(404)
  if not c.execute("SELECT id FROM applications WHERE worker_id=? AND booking_id=?",(session["uid"],bid)).fetchone():
   c.execute("INSERT INTO applications(worker_id,booking_id,role_title) VALUES(?,?,?)",(session["uid"],bid,request.form.get("role_title","Catering Helper")))
 flash("Application submitted.","success"); return redirect(url_for("dashboard"))
@app.route("/application/<int:aid>/<decision>",methods=["POST"])
@need("caterer")
def app_decision(aid,decision):
 if decision not in ("accept","reject"): abort(400)
 with conn() as c:
  a=c.execute("SELECT a.id FROM applications a JOIN bookings b ON b.id=a.booking_id WHERE a.id=? AND b.caterer_id=?",(aid,session["uid"])).fetchone()
  if not a: abort(404)
  salary=max(0,float(request.form.get("salary") or 0)) if decision=="accept" else 0
  c.execute("UPDATE applications SET status=?,salary=CASE WHEN ?='Accepted' THEN ? ELSE salary END WHERE id=?",("Accepted" if decision=="accept" else "Rejected","Accepted" if decision=="accept" else "Rejected",salary,aid))
 flash("Worker application updated.","success"); return redirect(url_for("dashboard"))
@app.route("/payroll/<int:aid>",methods=["POST"])
@need("caterer")
def payroll(aid):
 f=request.form
 with conn() as c:
  c.execute("UPDATE applications SET salary=?,advance=?,hours=?,payment=? WHERE id=? AND booking_id IN (SELECT id FROM bookings WHERE caterer_id=?)",(max(0,float(f.get("salary") or 0)),max(0,float(f.get("advance") or 0)),max(0,float(f.get("hours") or 0)),f.get("payment","Unpaid"),aid,session["uid"]))
 flash("Payroll details saved.","success"); return redirect(url_for("dashboard"))
@app.route("/checkin/<token>",methods=["GET","POST"])
@need("caterer")

def checkin(token):
    with conn() as c:
        guest = c.execute(
            """
            SELECT g.*, e.title AS event_title,
                   e.event_date, e.venue
            FROM guests g
            JOIN events e ON e.id = g.event_id
            WHERE g.token = ?
            """,
            (token,)
        ).fetchone()

        if not guest:
            abort(404)

        if request.method == "POST":
            c.execute(
                "UPDATE guests SET checked_in = 1 WHERE token = ?",
                (token,)
            )
            flash("Guest arrival confirmed.", "success")
            return redirect(url_for("dashboard"))

    return render_template("checkin.html", g=guest)

@app.errorhandler(403)
def forbidden(e): return render_template("error.html",code=403,message="You do not have permission to view this page."),403
@app.errorhandler(404)
def missing(e): return render_template("error.html",code=404,message="The page or record could not be found."),404
if __name__=="__main__": app.run(debug=True)
