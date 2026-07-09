from sqlalchemy import Column, Integer, String, Float, Text, Boolean, DateTime, ForeignKey, DECIMAL
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()

class Hat(Base):
    __tablename__ = "hats"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    brand = Column(String(100))
    price = Column(Float, nullable=False)
    description = Column(Text)
    category = Column(String(50))
    size = Column(String(20))
    color = Column(String(50))
    material = Column(String(100))
    image_url = Column(String(255))
    stock_quantity = Column(Integer, default=0)
    is_available = Column(Boolean, default=True)

    order_items = relationship("OrderItem", back_populates="hat")

class Category(Base):
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True)  # "snapback", "fedoras", etc.
    description = Column(Text)

class Order(Base):
    __tablename__ = "orders"
    
    id = Column(Integer, primary_key=True)
    customer_name = Column(String(100), nullable=False)
    customer_email = Column(String(255))
    customer_phone = Column(String(30))
    instagram_handle = Column(String(100))
    fulfillment_type = Column(String(20), nullable=False, default='pickup')
    delivery_address = Column(Text)
    total_price = Column(DECIMAL(10, 2), nullable=False)
    status = Column(String(20), default='placed')
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship to items (ONE order has MANY items)
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

class OrderItem(Base):
    __tablename__ = "order_items"
    
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('orders.id', ondelete='CASCADE'), nullable=False)
    hat_id = Column(Integer, ForeignKey('hats.id', ondelete='CASCADE'), nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    price = Column(DECIMAL(10, 2), nullable=False)
    
    # Relationship to order (EACH item belongs to ONE order)
    order = relationship("Order", back_populates="items")
    
    # Relationship to hat (optional - if you want to access hat details)
    hat = relationship("Hat", back_populates="order_items")
