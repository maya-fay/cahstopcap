from fastapi import Depends, FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.declarative import declarative_base
import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from models import Hat, Category, OrderItem

from database import engine, SessionLocal, Base, get_db
app = FastAPI()

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
    query = select(Hat).where(Hat.is_available == True)
    
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
    
    hats = db.execute(query).all()
    
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