"""
SmartCanteen — Database Models
-------------------------------
Defines the tables needed for automatic order acceptance:
Users, FoodItems, PickupSlots, Orders, OrderItems, Settings.

Notes on fields added specifically to support the auto-acceptance flow:
- FoodItem.available_quantity   -> lets us check "insufficient quantity",
                                     not just a boolean available/sold-out flag.
- PickupSlot.capacity/booked_count -> lets us check slot capacity atomically.
- Order.qr_token                -> secure random token used for QR verification.
- Settings.auto_accept          -> admin ON/OFF toggle for automatic acceptance.
"""

from datetime import datetime
import enum

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Enum, Text
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"        # only used if auto_accept is OFF
    ACCEPTED = "ACCEPTED"
    PREPARING = "PREPARING"
    READY = "READY"
    COLLECTED = "COLLECTED"
    REJECTED = "REJECTED"


class User(Base):
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    email = Column(String(120), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="student")  # student/staff/admin

    orders = relationship("Order", back_populates="student")


class FoodItem(Base):
    __tablename__ = "food_items"

    food_id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    price = Column(Float, nullable=False)
    category = Column(String(60))
    is_available = Column(Boolean, default=True)
    # Tracks actual stock so we can catch "insufficient quantity" requests,
    # not just a flat sold-out flag.
    available_quantity = Column(Integer, default=0)


class PickupSlot(Base):
    __tablename__ = "pickup_slots"

    slot_id = Column(Integer, primary_key=True)
    start_time = Column(String(20), nullable=False)   # e.g. "13:00"
    end_time = Column(String(20), nullable=False)      # e.g. "13:15"
    capacity = Column(Integer, nullable=False, default=10)
    booked_count = Column(Integer, nullable=False, default=0)

    orders = relationship("Order", back_populates="pickup_slot")


class Order(Base):
    __tablename__ = "orders"

    order_id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    slot_id = Column(Integer, ForeignKey("pickup_slots.slot_id"), nullable=False)
    total_amount = Column(Float, nullable=False)
    status = Column(Enum(OrderStatus), nullable=False, default=OrderStatus.PENDING)
    qr_token = Column(String(64), unique=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    student = relationship("User", back_populates="orders")
    pickup_slot = relationship("PickupSlot", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    order_item_id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.order_id"), nullable=False)
    food_id = Column(Integer, ForeignKey("food_items.food_id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)  # snapshot of DB price at order time

    order = relationship("Order", back_populates="items")
    food_item = relationship("FoodItem")


class Settings(Base):
    """
    Single-row table holding admin-configurable system settings.
    auto_accept = True  -> orders that pass validation are accepted automatically.
    auto_accept = False -> orders are created as PENDING and need manual staff approval.
    """
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, default=1)
    auto_accept = Column(Boolean, nullable=False, default=True)
