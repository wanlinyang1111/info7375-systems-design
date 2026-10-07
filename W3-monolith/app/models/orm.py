"""
SQLAlchemy ORM models - all tables in one monolith DB.

Intentional design: all models share the same engine and session.
This is what creates the shared connection pool - the Break-phase bottleneck.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Integer, Float, DateTime,
    ForeignKey, Text, Boolean, Index,
)
from sqlalchemy.orm import relationship

from repository.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


class Product(Base):
    __tablename__ = "products"

    id          = Column(String(36), primary_key=True, default=new_uuid)
    name        = Column(String(255), nullable=False)
    description = Column(Text)
    price       = Column(Float, nullable=False)
    stock_count = Column(Integer, nullable=False, default=100)
    category    = Column(String(100), index=True)
    weight_kg   = Column(Float, default=1.0)    # used by WeightBased shipping strategy
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    order_items = relationship("OrderItem", back_populates="product")

    __table_args__ = (
        Index("ix_products_category_price", "category", "price"),
    )


class User(Base):
    __tablename__ = "users"

    id         = Column(String(36), primary_key=True, default=new_uuid)
    email      = Column(String(255), unique=True, nullable=False, index=True)
    name       = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    orders = relationship("Order", back_populates="user")


class Order(Base):
    __tablename__ = "orders"

    id               = Column(String(36), primary_key=True, default=new_uuid)
    #~dk
    #user_id          = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    user_id          = Column(String(36), ForeignKey("users.id"), nullable=False)
    #~dk
    status           = Column(String(50), default="pending", index=True)
    status           = Column(String(50), default="pending")
    total_amount     = Column(Float, nullable=False)
    shipping_cost    = Column(Float, nullable=False)
    payment_provider = Column(String(50))           # which PaymentStrategy was used
    shipping_method  = Column(String(50))           # which ShippingStrategy was used
    #~dk
    #created_at       = Column(DateTime, default=datetime.utcnow, index=True)
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user        = relationship("User", back_populates="orders")
    order_items = relationship("OrderItem", back_populates="order",
                               cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_orders_user_status", "user_id", "status"),
        Index("ix_orders_created_at", "created_at"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id         = Column(String(36), primary_key=True, default=new_uuid)
    order_id   = Column(String(36), ForeignKey("orders.id"), nullable=False, index=True)
    product_id = Column(String(36), ForeignKey("products.id"), nullable=False)
    quantity   = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)   # price at time of order

    order   = relationship("Order",   back_populates="order_items")
    product = relationship("Product", back_populates="order_items")
