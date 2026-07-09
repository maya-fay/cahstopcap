import os
import secrets
import smtplib
from email.mime.text import MIMEText

from fastapi import BackgroundTasks, Depends, FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.declarative import declarative_base
from starlette.middleware.sessions import SessionMiddleware
import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from models import Hat, Category, Order, OrderItem

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

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")


def send_status_email(to_email: str, customer_name: str, order_id: int, status: str):
    if not to_email:
        return

    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print(f"[email] GMAIL_ADDRESS/GMAIL_APP_PASSWORD not set — skipping email for order {order_id}")
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

# Mount static files (CSS, JS, images)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Jinja2 templates
templates = Jinja2Templates(directory="templates")

Base.metadata.create_all(bind=engine)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("cahstopcap.html", {"request": request})


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


# GET showroom page
@app.get("/showroom", response_class=HTMLResponse)
def get_showroom_page(request: Request):
    return templates.TemplateResponse("showroom.html", {"request": request})


# POST add hat to database
@app.post("/api/hats")
def create_hat(hat_data: dict, db: Session = Depends(get_db)):
    hat = Hat(**hat_data)
    db.add(hat)
    db.commit()
    db.refresh(hat)

    return {
        "message": "Hat created successfully",
        "hat_id": hat.id
    }


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
        "total_price": float(order.total_price),
        "status": order.status,
        "created_at": order.created_at.isoformat(),
    }


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
