import os
import secrets
import smtplib
import base64
import uuid
import urllib.request
import urllib.parse
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

from fastapi import BackgroundTasks, Depends, FastAPI, File, Request, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.declarative import declarative_base
from starlette.middleware.sessions import SessionMiddleware
import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from models import Hat, Category, Order, OrderItem, CarouselSlide

from database import engine, SessionLocal, Base, get_db
app = FastAPI()

# Session cookie (used for the owner dashboard login)
SESSION_SECRET = os.getenv("SESSION_SECRET_KEY") or secrets.token_hex(32)
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD")

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=os.getenv("ENVIRONMENT") == "production",
)

ORDER_STATUSES = ["placed", "processing", "out_for_delivery", "completed"]
STATUS_LABELS = {
    "placed": "Placed",
    "processing": "Processing",
    "out_for_delivery": "Out for Delivery",
    "completed": "Completed",
}
FULFILLMENT_TYPES = ["pickup", "delivery"]
PAYMENT_METHODS = ["bank_transfer", "cash_on_delivery"]

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "cahstopcap@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")

BUSINESS_INSTAGRAM_HANDLE = "@cahstopcap"
LOGO_PATH = os.path.join(os.path.dirname(__file__), "static", "logo-email.png")

# TODO: replace with the real bank details.
BANK_TRANSFER_DETAILS = (
    "Bank: [NCB]\n"
    "Account Name: [Mario Hudson]\n"
    "Account Number: []\n"
    "Account Type: [ACCOUNT TYPE]\n"
    "Branch: [BRANCH]"
)


def build_bank_details_message(customer_name: str) -> str:
    return (
        f"Hi {customer_name}, to complete your order, send payment to the following bank details:\n\n"
        f"{BANK_TRANSFER_DETAILS}\n\n"
        "Reply to this message with proof of payment.\n\n"
        "Thanks for shopping!"
    )


def send_status_email(to_email: str, customer_name: str, order_id: int, status: str):
    if not to_email:
        return

    if not GMAIL_APP_PASSWORD:
        print(f"[email] GMAIL_APP_PASSWORD not set — skipping email for order {order_id}")
        return

    label = STATUS_LABELS.get(status, status)
    msg = MIMEText(
        f"Hi {customer_name},\n\n"
        f"Your CahStopCap order #{order_id} is now: {label}.\n\n"
        f"Thanks for shopping with us!\n"
    )
    msg["Subject"] = f"CahStopCap Order #{order_id} — {label}"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_email

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, [to_email], msg.as_string())
    except Exception as e:
        print(f"[email] Failed to send status email for order {order_id}: {e}")


def send_bank_details_email(to_email: str, order: Order):
    if not GMAIL_APP_PASSWORD:
        print(f"[email] GMAIL_APP_PASSWORD not set — skipping bank details email for order {order.id}")
        return

    item_lines = []
    item_rows_html = []
    for item in order.items:
        name = item.hat.name if item.hat else "Item"
        line_total = float(item.price) * item.quantity
        item_lines.append(f"- {name} x{item.quantity} — ${line_total:,.2f}")
        item_rows_html.append(
            f"<tr><td style='padding:4px 8px;'>{name}</td>"
            f"<td style='padding:4px 8px;text-align:center;'>{item.quantity}</td>"
            f"<td style='padding:4px 8px;text-align:right;'>${line_total:,.2f}</td></tr>"
        )
    total = float(order.total_price)

    text_body = (
        f"Hi {order.customer_name},\n\n"
        "Thanks for your order! Here are your order details:\n\n"
        + "\n".join(item_lines)
        + f"\nTotal: ${total:,.2f}\n\n"
        "To complete your order, send payment to the following bank details:\n\n"
        f"{BANK_TRANSFER_DETAILS}\n\n"
        "Reply to this message with proof of payment.\n\n"
        "Questions? Contact us:\n"
        f"Email: {GMAIL_ADDRESS}\n"
        f"Instagram: {BUSINESS_INSTAGRAM_HANDLE}\n\n"
        "Thanks for shopping with CahStopCap!"
    )

    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;color:#222;">
      <img src="cid:cahstopcap_logo" alt="CahStopCap" style="width:100px;display:block;margin:0 auto 16px;" />
      <p>Hi {order.customer_name},</p>
      <p>Thanks for your order! Here are your order details:</p>
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        {''.join(item_rows_html)}
        <tr>
          <td colspan="2" style="padding:8px 8px 4px;font-weight:bold;border-top:1px solid #ccc;">Total</td>
          <td style="padding:8px 8px 4px;text-align:right;font-weight:bold;border-top:1px solid #ccc;">${total:,.2f}</td>
        </tr>
      </table>
      <p>To complete your order, send payment to the following bank details:</p>
      <pre style="font-family:inherit;background:#f5f5f5;padding:10px;border-radius:6px;white-space:pre-wrap;">{BANK_TRANSFER_DETAILS}</pre>
      <p>Reply to this message with proof of payment.</p>
      <p>Questions? Contact us:<br/>
         Email: {GMAIL_ADDRESS}<br/>
         Instagram: {BUSINESS_INSTAGRAM_HANDLE}</p>
      <p>Thanks for shopping with CahStopCap!</p>
    </div>
    """

    msg = MIMEMultipart("related")
    msg["Subject"] = "CahStopCap — Bank Transfer Details"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_email

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text_body, "plain"))
    alt.attach(MIMEText(html_body, "html"))
    msg.attach(alt)

    try:
        with open(LOGO_PATH, "rb") as f:
            logo = MIMEImage(f.read())
        logo.add_header("Content-ID", "<cahstopcap_logo>")
        logo.add_header("Content-Disposition", "inline", filename="logo.png")
        msg.attach(logo)
    except OSError as e:
        print(f"[email] Could not attach logo for order {order.id}: {e}")

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, [to_email], msg.as_string())


def send_bank_details_sms(to_phone: str, customer_name: str, order_id: int):
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER):
        print(f"[sms] TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/TWILIO_FROM_NUMBER not set — skipping bank details text for order {order_id}")
        return

    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    data = urllib.parse.urlencode({
        "From": TWILIO_FROM_NUMBER,
        "To": to_phone,
        "Body": build_bank_details_message(customer_name),
    }).encode()

    auth = base64.b64encode(f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
    req = urllib.request.Request(url, data=data, headers={"Authorization": f"Basic {auth}"})
    urllib.request.urlopen(req, timeout=10)

# Mount static files (CSS, JS, images)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Jinja2 templates
templates = Jinja2Templates(directory="templates")

UPLOAD_DIR = Path(__file__).parent / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_UPLOAD_SIZE = 8 * 1024 * 1024  # 8MB

Base.metadata.create_all(bind=engine)


def migrate_carousel_slides():
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE carousel_slides ADD COLUMN IF NOT EXISTS show_caption BOOLEAN NOT NULL DEFAULT TRUE"
        ))


migrate_carousel_slides()

DEFAULT_CAROUSEL_SLIDES = [
    {"image_url": "static/assets/james-cahstop-trucker.jpeg", "title": "CAHSTOPCAP", "button_text": "Shop Now", "button_link": "/showroom", "sort_order": 0},
    {"image_url": "static/assets/gennow.jpg", "title": "GENNOW", "button_text": "Playlist", "button_link": "/showroom", "sort_order": 1},
    {"image_url": "static/assets/cahstop-bucket-carni.JPG", "title": "Never Miss RRWNZDZ", "button_text": "Check It Out", "button_link": "/showroom", "sort_order": 2},
    {"image_url": "static/assets/ydys1.jpg", "title": "YDYS", "button_text": "Shop Now", "button_link": "/showroom", "sort_order": 3},
    {"image_url": "static/assets/collabs.jpg", "title": "COLLABS", "button_text": "Shop Now", "button_link": "/showroom", "sort_order": 4},
    {"image_url": "static/assets/caps-on-sti.jpg", "title": "CAHSTOPCAP", "button_text": "Shop Now", "button_link": "/showroom", "sort_order": 5},
]


def seed_carousel_slides():
    db = SessionLocal()
    try:
        if db.execute(select(CarouselSlide)).first() is None:
            for slide in DEFAULT_CAROUSEL_SLIDES:
                db.add(CarouselSlide(**slide))
            db.commit()
    finally:
        db.close()


seed_carousel_slides()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    slides = db.execute(
        select(CarouselSlide).where(CarouselSlide.is_active == True).order_by(CarouselSlide.sort_order)
    ).scalars().all()
    return templates.TemplateResponse("cahstopcap.html", {"request": request, "slides": slides})


# Database dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_owner(request: Request):
    if not request.session.get("is_owner"):
        raise HTTPException(status_code=401, detail="Not authenticated")


# =========================================================
# OWNER DASHBOARD — image uploads
# =========================================================

@app.post("/api/admin/upload-image")
async def admin_upload_image(file: UploadFile = File(...), _owner=Depends(require_owner)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image type. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}",
        )

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="Image is too large (max 8MB)")

    filename = f"{uuid.uuid4().hex}{ext}"
    destination = UPLOAD_DIR / filename
    destination.write_bytes(contents)

    return {"url": f"static/uploads/{filename}"}


# GET all hats with optional filters
@app.get("/api/hats")
def get_hats(
    category: str = None,
    min_price: float = None,
    max_price: float = None,
    color: str = None,
    brand: str = None,
    size: str = None,
    db: Session = Depends(get_db)
):
    query = select(Hat).where(Hat.is_available == True).order_by(Hat.id)

    # Apply filters
    if category and category != "all":
        query = query.where(Hat.category == category)

    if min_price:
        query = query.where(Hat.price >= min_price)

    if max_price:
        query = query.where(Hat.price <= max_price)

    if color:
        query = query.where(Hat.color == color)

    if brand:
        query = query.where(Hat.brand == brand)

    if size:
        query = query.where(Hat.size == size)

    hats = db.execute(query).scalars().all()

    return [
        {
            "id": hat.id,
            "name": hat.name,
            "brand": hat.brand,
            "price": hat.price,
            "description": hat.description,
            "category": hat.category,
            "size": hat.size,
            "color": hat.color,
            "material": hat.material,
            "image_url": hat.image_url,
            "stock_quantity": hat.stock_quantity,
            "is_available": hat.is_available
        }
        for hat in hats
    ]


# GET a single hat's details
@app.get("/api/hats/{hat_id}")
def get_hat(hat_id: int, db: Session = Depends(get_db)):
    hat = db.get(Hat, hat_id)
    if not hat or not hat.is_available:
        raise HTTPException(status_code=404, detail="Hat not found")

    return {
        "id": hat.id,
        "name": hat.name,
        "brand": hat.brand,
        "price": hat.price,
        "description": hat.description,
        "category": hat.category,
        "size": hat.size,
        "color": hat.color,
        "material": hat.material,
        "image_url": hat.image_url,
        "stock_quantity": hat.stock_quantity,
        "is_available": hat.is_available
    }


# GET showroom page
@app.get("/showroom", response_class=HTMLResponse)
def get_showroom_page(request: Request):
    return templates.TemplateResponse("showroom.html", {"request": request})


# GET hat detail page
@app.get("/hat/{hat_id}", response_class=HTMLResponse)
def get_hat_detail_page(hat_id: int, request: Request):
    return templates.TemplateResponse("hat_detail.html", {"request": request})


# =========================================================
# CART / CHECKOUT
# =========================================================

@app.get("/cart", response_class=HTMLResponse)
def cart_page(request: Request):
    return templates.TemplateResponse("cart.html", {"request": request})


class OrderItemIn(BaseModel):
    hat_id: int
    quantity: int = 1


class OrderIn(BaseModel):
    customer_name: str
    customer_email: str
    customer_phone: str
    instagram_handle: str | None = None
    fulfillment_type: str
    delivery_address: str | None = None
    payment_method: str
    items: list[OrderItemIn]


# POST place an order (simulated checkout — no real payment is processed)
@app.post("/api/orders")
def create_order(order_in: OrderIn, db: Session = Depends(get_db)):
    if not order_in.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    customer_name = order_in.customer_name.strip()
    if not customer_name:
        raise HTTPException(status_code=400, detail="Name is required")

    customer_email = order_in.customer_email.strip()
    if not customer_email or "@" not in customer_email:
        raise HTTPException(status_code=400, detail="A valid email is required")

    customer_phone = order_in.customer_phone.strip()
    if not customer_phone:
        raise HTTPException(status_code=400, detail="Phone number is required")

    instagram_handle = (order_in.instagram_handle or "").strip() or None

    fulfillment_type = order_in.fulfillment_type.strip().lower()
    if fulfillment_type not in FULFILLMENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Fulfillment type must be one of {FULFILLMENT_TYPES}")

    delivery_address = (order_in.delivery_address or "").strip()
    if fulfillment_type == "delivery" and not delivery_address:
        raise HTTPException(status_code=400, detail="Delivery address is required for delivery orders")
    if fulfillment_type != "delivery":
        delivery_address = None

    payment_method = order_in.payment_method.strip().lower()
    if payment_method not in PAYMENT_METHODS:
        raise HTTPException(status_code=400, detail=f"Payment method must be one of {PAYMENT_METHODS}")

    total_price = 0
    order_items = []

    for item in order_in.items:
        if item.quantity < 1:
            raise HTTPException(status_code=400, detail="Quantity must be at least 1")

        hat = db.execute(
            select(Hat).where(Hat.id == item.hat_id).with_for_update()
        ).scalar_one_or_none()

        if not hat or not hat.is_available:
            raise HTTPException(status_code=404, detail=f"Hat {item.hat_id} not found")

        if hat.stock_quantity < item.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Only {hat.stock_quantity} left of '{hat.name}'"
            )

        hat.stock_quantity -= item.quantity
        order_items.append(OrderItem(hat_id=hat.id, quantity=item.quantity, price=hat.price))
        total_price += hat.price * item.quantity

    order = Order(
        customer_name=customer_name,
        customer_email=customer_email,
        customer_phone=customer_phone,
        instagram_handle=instagram_handle,
        fulfillment_type=fulfillment_type,
        delivery_address=delivery_address,
        payment_method=payment_method,
        total_price=total_price,
        status="placed",
        items=order_items,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    return {
        "id": order.id,
        "customer_name": order.customer_name,
        "customer_email": order.customer_email,
        "customer_phone": order.customer_phone,
        "fulfillment_type": order.fulfillment_type,
        "payment_method": order.payment_method,
        "total_price": float(order.total_price),
        "status": order.status,
        "created_at": order.created_at.isoformat(),
        "items": [
            {
                "hat_id": i.hat_id,
                "hat_name": i.hat.name if i.hat else "Deleted hat",
                "hat_image": i.hat.image_url if i.hat else None,
                "quantity": i.quantity,
                "price": float(i.price),
            }
            for i in order.items
        ],
    }


class BankDetailsRequest(BaseModel):
    channel: str
    contact: str


# POST send bank transfer details to the customer by email or text
@app.post("/api/orders/{order_id}/send-bank-details")
def send_bank_details(order_id: int, body: BankDetailsRequest, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.payment_method != "bank_transfer":
        raise HTTPException(status_code=400, detail="This order is not paying by bank transfer")

    channel = body.channel.strip().lower()
    if channel not in ("email", "phone"):
        raise HTTPException(status_code=400, detail="Channel must be 'email' or 'phone'")

    contact = body.contact.strip()
    if not contact:
        raise HTTPException(status_code=400, detail="Please provide a phone number or email")

    try:
        if channel == "email":
            if "@" not in contact:
                raise HTTPException(status_code=400, detail="Please enter a valid email")
            send_bank_details_email(contact, order)
        else:
            send_bank_details_sms(contact, order.customer_name, order.id)
    except HTTPException:
        raise
    except Exception as e:
        print(f"[bank-details] Failed to send via {channel} for order {order_id}: {e}")
        raise HTTPException(status_code=502, detail="Could not send bank details right now. Please try again.")

    return {"success": True, "channel": channel}


# =========================================================
# OWNER DASHBOARD — auth
# =========================================================

@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    if request.session.get("is_owner"):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": None})


@app.post("/admin/login", response_class=HTMLResponse)
async def admin_login_submit(request: Request):
    form = await request.form()
    password = form.get("password", "")

    if OWNER_PASSWORD and secrets.compare_digest(password, OWNER_PASSWORD):
        request.session["is_owner"] = True
        return RedirectResponse("/admin", status_code=303)

    return templates.TemplateResponse(
        "admin_login.html",
        {"request": request, "error": "Incorrect password"},
        status_code=401,
    )


@app.get("/admin/logout")
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    if not request.session.get("is_owner"):
        return RedirectResponse("/admin/login", status_code=303)
    return templates.TemplateResponse("admin.html", {"request": request})


# =========================================================
# OWNER DASHBOARD — orders API
# =========================================================

class OrderStatusUpdate(BaseModel):
    status: str


@app.get("/api/admin/orders")
def admin_list_orders(
    status: str = None,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    query = select(Order).order_by(Order.created_at.desc())
    if status:
        query = query.where(Order.status == status)

    orders = db.execute(query).scalars().all()

    return [
        {
            "id": o.id,
            "customer_name": o.customer_name,
            "customer_email": o.customer_email,
            "customer_phone": o.customer_phone,
            "instagram_handle": o.instagram_handle,
            "fulfillment_type": o.fulfillment_type,
            "delivery_address": o.delivery_address,
            "total_price": float(o.total_price),
            "status": o.status,
            "created_at": o.created_at.isoformat(),
            "items": [
                {
                    "hat_id": i.hat_id,
                    "hat_name": i.hat.name if i.hat else "Deleted hat",
                    "quantity": i.quantity,
                    "price": float(i.price),
                }
                for i in o.items
            ],
        }
        for o in orders
    ]


@app.patch("/api/admin/orders/{order_id}")
def admin_update_order_status(
    order_id: int,
    body: OrderStatusUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    if body.status not in ORDER_STATUSES:
        raise HTTPException(status_code=400, detail=f"Status must be one of {ORDER_STATUSES}")

    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.status = body.status
    db.commit()

    background_tasks.add_task(
        send_status_email, order.customer_email, order.customer_name, order.id, order.status
    )

    return {"id": order.id, "status": order.status}


# =========================================================
# OWNER DASHBOARD — hats API
# =========================================================

class HatCreate(BaseModel):
    name: str
    price: float
    brand: str | None = None
    description: str | None = None
    category: str | None = None
    size: str | None = None
    color: str | None = None
    material: str | None = None
    image_url: str | None = None
    stock_quantity: int = 0
    is_available: bool = True


class HatUpdate(BaseModel):
    name: str | None = None
    price: float | None = None
    stock_quantity: int | None = None
    is_available: bool | None = None


@app.get("/api/admin/hats")
def admin_list_hats(db: Session = Depends(get_db), _owner=Depends(require_owner)):
    hats = db.execute(select(Hat).order_by(Hat.id)).scalars().all()

    return [
        {
            "id": h.id,
            "name": h.name,
            "brand": h.brand,
            "price": h.price,
            "category": h.category,
            "stock_quantity": h.stock_quantity,
            "is_available": h.is_available,
            "image_url": h.image_url,
        }
        for h in hats
    ]


@app.post("/api/admin/hats")
def admin_create_hat(
    body: HatCreate,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    if body.price < 0:
        raise HTTPException(status_code=400, detail="Price cannot be negative")

    if body.stock_quantity < 0:
        raise HTTPException(status_code=400, detail="Stock cannot be negative")

    hat = Hat(
        name=name,
        price=body.price,
        brand=(body.brand or "").strip() or None,
        description=(body.description or "").strip() or None,
        category=(body.category or "").strip() or None,
        size=(body.size or "").strip() or None,
        color=(body.color or "").strip() or None,
        material=(body.material or "").strip() or None,
        image_url=(body.image_url or "").strip() or None,
        stock_quantity=body.stock_quantity,
        is_available=body.is_available,
    )
    db.add(hat)
    db.commit()
    db.refresh(hat)

    return {
        "id": hat.id,
        "name": hat.name,
        "price": hat.price,
        "stock_quantity": hat.stock_quantity,
        "is_available": hat.is_available,
    }


@app.patch("/api/admin/hats/{hat_id}")
def admin_update_hat(
    hat_id: int,
    body: HatUpdate,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    hat = db.get(Hat, hat_id)
    if not hat:
        raise HTTPException(status_code=404, detail="Hat not found")

    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(status_code=400, detail="Name cannot be empty")
        hat.name = body.name.strip()

    if body.price is not None:
        if body.price < 0:
            raise HTTPException(status_code=400, detail="Price cannot be negative")
        hat.price = body.price

    if body.stock_quantity is not None:
        if body.stock_quantity < 0:
            raise HTTPException(status_code=400, detail="Stock cannot be negative")
        hat.stock_quantity = body.stock_quantity

    if body.is_available is not None:
        hat.is_available = body.is_available

    db.commit()

    return {
        "id": hat.id,
        "name": hat.name,
        "price": hat.price,
        "stock_quantity": hat.stock_quantity,
        "is_available": hat.is_available,
    }


# =========================================================
# OWNER DASHBOARD — carousel API
# =========================================================

class CarouselSlideCreate(BaseModel):
    image_url: str
    title: str | None = None
    button_text: str | None = None
    button_link: str | None = None
    show_caption: bool = True
    sort_order: int = 0
    is_active: bool = True


class CarouselSlideUpdate(BaseModel):
    image_url: str | None = None
    title: str | None = None
    button_text: str | None = None
    button_link: str | None = None
    show_caption: bool | None = None
    sort_order: int | None = None
    is_active: bool | None = None


def normalize_link(link: str | None) -> str:
    """Turn 'open.spotify.com/...' into 'https://open.spotify.com/...' so it
    isn't treated as a page on this site. Site paths ('/showroom'), anchors and
    full URLs are left alone."""
    link = (link or "").strip()
    if not link or link.startswith(("/", "#", "http://", "https://", "mailto:", "tel:")):
        return link
    return "https://" + link


def serialize_slide(slide: CarouselSlide):
    return {
        "id": slide.id,
        "image_url": slide.image_url,
        "title": slide.title,
        "button_text": slide.button_text,
        "button_link": slide.button_link,
        "show_caption": slide.show_caption,
        "sort_order": slide.sort_order,
        "is_active": slide.is_active,
    }


@app.get("/api/admin/carousel")
def admin_list_carousel_slides(db: Session = Depends(get_db), _owner=Depends(require_owner)):
    slides = db.execute(select(CarouselSlide).order_by(CarouselSlide.sort_order)).scalars().all()
    return [serialize_slide(s) for s in slides]


@app.post("/api/admin/carousel")
def admin_create_carousel_slide(
    body: CarouselSlideCreate,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    image_url = body.image_url.strip()
    if not image_url:
        raise HTTPException(status_code=400, detail="Image URL is required")

    title = (body.title or "").strip()
    if body.show_caption and not title:
        raise HTTPException(status_code=400, detail="Title is required when the slide includes text")

    slide = CarouselSlide(
        image_url=image_url,
        title=title,
        button_text=(body.button_text or "").strip() or ("Shop Now" if body.show_caption else ""),
        button_link=normalize_link(body.button_link) or ("/showroom" if body.show_caption else ""),
        show_caption=body.show_caption,
        sort_order=body.sort_order,
        is_active=body.is_active,
    )
    db.add(slide)
    db.commit()
    db.refresh(slide)

    return serialize_slide(slide)


@app.patch("/api/admin/carousel/{slide_id}")
def admin_update_carousel_slide(
    slide_id: int,
    body: CarouselSlideUpdate,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    slide = db.get(CarouselSlide, slide_id)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    if body.image_url is not None:
        if not body.image_url.strip():
            raise HTTPException(status_code=400, detail="Image URL cannot be empty")
        slide.image_url = body.image_url.strip()

    if body.title is not None:
        slide.title = body.title.strip()

    if body.button_text is not None:
        slide.button_text = body.button_text.strip()

    if body.button_link is not None:
        slide.button_link = normalize_link(body.button_link)

    if body.show_caption is not None:
        slide.show_caption = body.show_caption

    if body.sort_order is not None:
        slide.sort_order = body.sort_order

    if body.is_active is not None:
        slide.is_active = body.is_active

    if slide.show_caption and not slide.title.strip():
        raise HTTPException(status_code=400, detail="Title is required when the slide includes text")

    db.commit()

    return serialize_slide(slide)


@app.delete("/api/admin/carousel/{slide_id}")
def admin_delete_carousel_slide(
    slide_id: int,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    slide = db.get(CarouselSlide, slide_id)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    db.delete(slide)
    db.commit()

    return {"success": True}
