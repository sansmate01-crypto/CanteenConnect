"""
SmartCanteen — Automatic Order Acceptance Service
--------------------------------------------------
Implements the 12-step acceptance flow from the spec:

 1. Validate authenticated student
 2. Validate food items
 3. Validate quantities
 4. Check current availability
 5. Check pickup slot capacity
 6. Calculate total using database prices
 7. Create order
 8. Create order items
 9. Reserve pickup slot
10. Generate QR token
11. Set order status to ACCEPTED
12. Commit the transaction

If ANY step fails, the whole transaction is rolled back so that:
  - No incomplete order is created
  - Pickup capacity is not incorrectly consumed
  - Inventory is not incorrectly changed

Concurrency note
-----------------
Two students can try to book the last pickup slot (or the last unit of an
item) at the same instant. To prevent overselling, this code uses
`with_for_update()` on the PickupSlot and FoodItem rows, which issues a
`SELECT ... FOR UPDATE` under PostgreSQL/MySQL (InnoDB) and locks those rows
for the duration of the transaction — the second request has to wait until
the first commits or rolls back, then re-checks fresh numbers.

SQLite (used in the demo `app.py` for convenience) does NOT support real
row-level locking, so under SQLite this protection is effectively a no-op.
For a real deployment, switch to MySQL or PostgreSQL as the project outline
already suggests.
"""

import secrets
from dataclasses import dataclass, field
from typing import List, Dict

from sqlalchemy.orm import Session

from models import User, FoodItem, PickupSlot, Order, OrderItem, OrderStatus, Settings


class OrderRejected(Exception):
    """Raised whenever the order cannot be accepted; message is user-facing."""
    pass


@dataclass
class OrderLine:
    food_id: int
    quantity: int


@dataclass
class OrderResult:
    order_id: int
    status: str
    total_amount: float
    qr_token: str
    lines: List[Dict] = field(default_factory=list)


def place_order(db: Session, student_id: int, slot_id: int, lines: List[OrderLine]) -> OrderResult:
    """
    Attempts to place and automatically accept an order.
    Raises OrderRejected with a user-facing message on any validation failure.
    On success, returns an OrderResult and the transaction has already been committed.
    """

    if not lines:
        raise OrderRejected("Your cart is empty.")

    try:
        # ---- 1. Validate authenticated student -----------------------------
        student = db.query(User).filter_by(user_id=student_id, role="student").first()
        if not student:
            raise OrderRejected("Student account not found or not authorized.")

        # Read the auto-accept setting (default ON if no row exists yet).
        settings = db.query(Settings).first()
        auto_accept = settings.auto_accept if settings else True

        # ---- 2 & 3. Validate food items + quantities ------------------------
        # Lock the food rows so concurrent orders can't both "see" the same
        # stock as available before either one has decremented it.
        food_ids = [line.food_id for line in lines]
        food_items = (
            db.query(FoodItem)
            .filter(FoodItem.food_id.in_(food_ids))
            .with_for_update()
            .all()
        )
        food_by_id = {f.food_id: f for f in food_items}

        for line in lines:
            if line.quantity <= 0:
                raise OrderRejected("Invalid quantity requested for one or more items.")
            if line.food_id not in food_by_id:
                raise OrderRejected("One or more selected items are currently unavailable.")

        # ---- 4. Check current availability ----------------------------------
        for line in lines:
            item = food_by_id[line.food_id]
            if not item.is_available:
                raise OrderRejected("One or more selected items are currently unavailable.")

        # ---- 5. Check pickup slot capacity ------------------------------------
        slot = (
            db.query(PickupSlot)
            .filter_by(slot_id=slot_id)
            .with_for_update()
            .first()
        )
        if not slot:
            raise OrderRejected("Selected pickup slot does not exist.")
        if slot.booked_count >= slot.capacity:
            raise OrderRejected("This pickup slot is full. Please select another slot.")

        # ---- Insufficient quantity check (separate from is_available) --------
        for line in lines:
            item = food_by_id[line.food_id]
            if item.available_quantity < line.quantity:
                raise OrderRejected(
                    "Some items are no longer available in the requested quantity."
                )

        # ---- 6. Calculate total using DATABASE prices (never trust client) ---
        total_amount = 0.0
        order_lines_snapshot = []
        for line in lines:
            item = food_by_id[line.food_id]
            line_total = item.price * line.quantity
            total_amount += line_total
            order_lines_snapshot.append({
                "food_id": item.food_id,
                "name": item.name,
                "quantity": line.quantity,
                "unit_price": item.price,
                "line_total": line_total,
            })

        # ---- 7. Create order (status decided by auto_accept setting) ---------
        initial_status = OrderStatus.ACCEPTED if auto_accept else OrderStatus.PENDING
        new_order = Order(
            user_id=student.user_id,
            slot_id=slot.slot_id,
            total_amount=total_amount,
            status=initial_status,
        )
        db.add(new_order)
        db.flush()  # assigns new_order.order_id without committing yet
        # ---- 8. Create order items ---------------------------------------------
        for line in lines:
            item = food_by_id[line.food_id]
            db.add(OrderItem(
                order_id=new_order.order_id,
                food_id=item.food_id,
                quantity=line.quantity,
                unit_price=item.price,
            ))
            # Decrement stock now that the order is confirmed to be created.
            item.available_quantity -= line.quantity
            if item.available_quantity <= 0:
                item.is_available = False

        # ---- 9. Reserve pickup slot ---------------------------------------------
        slot.booked_count += 1

        # ---- 10. Generate QR token -----------------------------------------------
        # Only accepted orders get a scannable QR; a PENDING order gets one
        # once staff manually approve it (not covered here).
        qr_token = secrets.token_hex(16) if initial_status == OrderStatus.ACCEPTED else None
        new_order.qr_token = qr_token

        # ---- 11. Status already set above --------------------------------------

        # ---- 12. Commit the transaction ------------------------------------------
        db.commit()

    except OrderRejected:
        db.rollback()
        raise
    except Exception:
        # Any unexpected error (DB error, constraint violation, etc.) must
        # also roll back so nothing partial gets left behind.
        db.rollback()
        raise OrderRejected("Something went wrong while placing your order. Please try again.")

    return OrderResult(
        order_id=new_order.order_id,
        status=new_order.status.value,
        total_amount=new_order.total_amount,
        qr_token=new_order.qr_token or "",
        lines=order_lines_snapshot,
    )
