"""
SmartCanteen — Flask app (demo scaffold)
------------------------------------------
Wires the automatic order-acceptance service into HTTP routes:

  POST /order/place            -> student places an order (auto-accept flow)
  POST /staff/orders/<id>/start-preparing
  POST /staff/orders/<id>/mark-ready
  POST /staff/orders/<id>/verify-qr        (body: {"qr_token": "..."})
  POST /staff/orders/<id>/mark-collected
  GET  /staff/dashboard         -> NEW / PREPARING / READY / COLLECTED buckets
  GET  /admin/settings          -> view auto_accept
  POST /admin/settings          -> toggle auto_accept

Run with: python app.py
Uses SQLite for zero-setup local testing. Swap the SQLALCHEMY DB URL for
MySQL/PostgreSQL in production (see order_service.py for why that matters
for real row-locking under concurrent orders).
"""

import os

from flask import Flask, request, jsonify
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, User, FoodItem, PickupSlot, Order, OrderStatus, Settings
from order_service import place_order, OrderLine, OrderRejected

app = Flask(__name__)

# ---------------------------------------------------------------------------
# MySQL connection
# ---------------------------------------------------------------------------
# Format: mysql+pymysql://<user>:<password>@<host>:<port>/<database>
# Reads from an env var so you're not hardcoding a password into the file;
# falls back to a local default you should edit for your machine.
#
# Create the database first (one-time, in MySQL Workbench / mysql CLI):
#   CREATE DATABASE smartcanteen;
#
# On Windows, set the env var before running (PowerShell):
#   $env:DATABASE_URL = "mysql+pymysql://root:yourpassword@localhost:3306/smartcanteen"
# On Mac/Linux:
#   export DATABASE_URL="mysql+pymysql://root:yourpassword@localhost:3306/smartcanteen"

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    DATABASE_URL = "mysql+pymysql://root:7@localhost:3306/smartcanteen"


engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


def get_db():
    return SessionLocal()


# ---------------------------------------------------------------------------
# Seed data — only runs once, so you can hit the endpoints immediately.
# ---------------------------------------------------------------------------
def seed():
    Base.metadata.create_all(engine)
    db = get_db()
    try:
        if not db.query(Settings).first():
            db.add(Settings(auto_accept=True))

        if not db.query(User).first():
            db.add(
                User(
                    name="Test Student",
                    email="student@test.com",
                    password_hash="x",
                    role="student",
                )
            )

        if not db.query(FoodItem).first():
            db.add_all(
                [
                    FoodItem(
                        name="Vada Pav",
                        price=20,
                        category="snacks",
                        is_available=True,
                        available_quantity=50,
                    ),
                    FoodItem(
                        name="Sandwich",
                        price=40,
                        category="snacks",
                        is_available=True,
                        available_quantity=20,
                    ),
                    FoodItem(
                        name="Cold Coffee",
                        price=50,
                        category="beverages",
                        is_available=True,
                        available_quantity=0,
                    ),
                ]
            )

        if not db.query(PickupSlot).first():
            db.add_all(
                [
                    PickupSlot(
                        start_time="12:30",
                        end_time="12:45",
                        capacity=10,
                        booked_count=10,
                    ),
                    PickupSlot(
                        start_time="12:45",
                        end_time="13:00",
                        capacity=10,
                        booked_count=4,
                    ),
                    PickupSlot(
                        start_time="13:00",
                        end_time="13:15",
                        capacity=10,
                        booked_count=0,
                    ),
                ]
            )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Student: place order (this is the automatic acceptance flow)
# ---------------------------------------------------------------------------
@app.route("/order/place", methods=["POST"])
def api_place_order():
    """
    Expected JSON body:
    {
      "student_id": 1,
      "slot_id": 3,
      "items": [{"food_id": 1, "quantity": 2}, {"food_id": 2, "quantity": 1}]
    }
    """
    data = request.get_json(force=True) or {}
    student_id = data.get("student_id")
    slot_id = data.get("slot_id")
    raw_items = data.get("items", [])

    if not student_id or not slot_id or not raw_items:
        return jsonify({"error": "student_id, slot_id and items are required."}), 400

    lines = [OrderLine(food_id=i["food_id"], quantity=i["quantity"]) for i in raw_items]

    db = get_db()
    try:
        result = place_order(db, student_id=student_id, slot_id=slot_id, lines=lines)
        return (
            jsonify(
                {
                    "order_id": result.order_id,
                    "status": result.status,
                    "total_amount": result.total_amount,
                    "qr_token": result.qr_token,
                    "items": result.lines,
                }
            ),
            201,
        )
    except OrderRejected as e:
        # These are the exact user-facing messages from the spec.
        return jsonify({"error": str(e)}), 409
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Staff: status transitions (no accept/reject step for auto-accepted orders)
# ---------------------------------------------------------------------------
def _transition(order_id, expected_current, new_status):
    db = get_db()
    try:
        order = db.query(Order).filter_by(order_id=order_id).first()
        if not order:
            return jsonify({"error": "Order not found."}), 404
        if order.status != expected_current:
            return (
                jsonify(
                    {
                        "error": f"Order must be {expected_current.value} to do this "
                        f"(currently {order.status.value})."
                    }
                ),
                409,
            )
        order.status = new_status
        db.commit()
        return jsonify({"order_id": order.order_id, "status": order.status.value}), 200
    finally:
        db.close()


@app.route("/staff/orders/<int:order_id>/start-preparing", methods=["POST"])
def start_preparing(order_id):
    return _transition(order_id, OrderStatus.ACCEPTED, OrderStatus.PREPARING)


@app.route("/staff/orders/<int:order_id>/mark-ready", methods=["POST"])
def mark_ready(order_id):
    return _transition(order_id, OrderStatus.PREPARING, OrderStatus.READY)


@app.route("/staff/orders/<int:order_id>/verify-qr", methods=["POST"])
def verify_qr(order_id):
    data = request.get_json(force=True) or {}
    submitted_token = data.get("qr_token")

    db = get_db()
    try:
        order = db.query(Order).filter_by(order_id=order_id).first()
        if not order:
            return jsonify({"error": "Order not found."}), 404
        if order.status != OrderStatus.READY:
            return jsonify({"error": "Order is not marked Ready yet."}), 409
        if order.status == OrderStatus.COLLECTED:
            return jsonify({"error": "This order has already been collected."}), 409
        if submitted_token != order.qr_token:
            return jsonify({"error": "Invalid QR / order token."}), 403

        order.status = OrderStatus.COLLECTED
        db.commit()
        return jsonify({"order_id": order.order_id, "status": "COLLECTED"}), 200
    finally:
        db.close()


@app.route("/staff/dashboard", methods=["GET"])
def staff_dashboard():
    db = get_db()
    try:
        buckets = {"NEW_ORDERS": [], "PREPARING": [], "READY": [], "COLLECTED": []}
        status_map = {
            OrderStatus.ACCEPTED: "NEW_ORDERS",
            OrderStatus.PREPARING: "PREPARING",
            OrderStatus.READY: "READY",
            OrderStatus.COLLECTED: "COLLECTED",
        }
        for order in db.query(Order).all():
            key = status_map.get(order.status)
            if key:
                buckets[key].append(
                    {
                        "order_id": order.order_id,
                        "total_amount": order.total_amount,
                        "slot_id": order.slot_id,
                    }
                )
        return jsonify(buckets), 200
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Admin: auto-accept toggle
# ---------------------------------------------------------------------------
@app.route("/admin/settings", methods=["GET"])
def get_settings():
    db = get_db()
    try:
        settings = db.query(Settings).first()
        return jsonify({"auto_accept": settings.auto_accept if settings else True}), 200
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------
from flask import render_template, redirect, url_for, flash
from models import OrderItem


@app.route("/admin", methods=["GET"])
def admin_dashboard():
    db = get_db()
    try:
        foods = db.query(FoodItem).order_by(FoodItem.food_id).all()
        slots = db.query(PickupSlot).order_by(PickupSlot.slot_id).all()
        pending_orders = (
            db.query(Order)
            .filter(Order.status == OrderStatus.PENDING)
            .order_by(Order.created_at.desc())
            .all()
        )

        settings = db.query(Settings).first()
        auto_accept = settings.auto_accept if settings else True

        return render_template(
            "admin.html",
            foods=foods,
            slots=slots,
            pending_orders=pending_orders,
            auto_accept=auto_accept,
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Admin: food management
# ---------------------------------------------------------------------------
@app.route("/admin/food/add", methods=["POST"])
def admin_add_food():
    db = get_db()
    try:
        food = FoodItem(
            name=request.form["name"].strip(),
            category=request.form.get("category", "").strip(),
            price=float(request.form["price"]),
            available_quantity=int(request.form["quantity"]),
            is_available=True,
        )

        if food.available_quantity == 0:
            food.is_available = False

        db.add(food)
        db.commit()

        flash("Food item added successfully.", "success")
        return redirect(url_for("admin_dashboard"))

    except Exception:
        db.rollback()
        flash("Could not add food item.", "error")
        return redirect(url_for("admin_dashboard"))

    finally:
        db.close()


@app.route("/admin/food/<int:food_id>/edit", methods=["POST"])
def admin_edit_food(food_id):
    db = get_db()
    try:
        food = db.query(FoodItem).filter_by(food_id=food_id).first()

        if not food:
            flash("Food item not found.", "error")
            return redirect(url_for("admin_dashboard"))

        food.name = request.form["name"].strip()
        food.category = request.form.get("category", "").strip()
        food.price = float(request.form["price"])
        food.available_quantity = int(request.form["quantity"])
        food.is_available = "is_available" in request.form

        if food.available_quantity == 0:
            food.is_available = False

        db.commit()

        flash("Food item updated.", "success")
        return redirect(url_for("admin_dashboard"))

    except Exception:
        db.rollback()
        flash("Could not update food item.", "error")
        return redirect(url_for("admin_dashboard"))

    finally:
        db.close()


@app.route("/admin/food/<int:food_id>/restock", methods=["POST"])
def admin_restock_food(food_id):
    db = get_db()
    try:
        food = db.query(FoodItem).filter_by(food_id=food_id).first()

        if not food:
            flash("Food item not found.", "error")
            return redirect(url_for("admin_dashboard"))

        amount = int(request.form["amount"])

        if amount <= 0:
            flash("Restock amount must be greater than zero.", "error")
            return redirect(url_for("admin_dashboard"))

        food.available_quantity += amount
        food.is_available = True

        db.commit()

        flash(f"{food.name} restocked by {amount}.", "success")
        return redirect(url_for("admin_dashboard"))

    except Exception:
        db.rollback()
        flash("Could not restock item.", "error")
        return redirect(url_for("admin_dashboard"))

    finally:
        db.close()


@app.route("/admin/food/<int:food_id>/delete", methods=["POST"])
def admin_delete_food(food_id):
    db = get_db()
    try:
        food = db.query(FoodItem).filter_by(food_id=food_id).first()

        if not food:
            flash("Food item not found.", "error")
            return redirect(url_for("admin_dashboard"))

        db.delete(food)
        db.commit()

        flash("Food item deleted.", "success")
        return redirect(url_for("admin_dashboard"))

    except Exception:
        db.rollback()
        flash(
            "Could not delete this food item. It may be used by an existing order.",
            "error",
        )
        return redirect(url_for("admin_dashboard"))

    finally:
        db.close()


# ---------------------------------------------------------------------------
# Admin: pickup slots
# ---------------------------------------------------------------------------
@app.route("/admin/slot/add", methods=["POST"])
def admin_add_slot():
    db = get_db()
    try:
        slot = PickupSlot(
            start_time=request.form["start_time"],
            end_time=request.form["end_time"],
            capacity=int(request.form["capacity"]),
            booked_count=0,
        )

        db.add(slot)
        db.commit()

        flash("Pickup slot added.", "success")
        return redirect(url_for("admin_dashboard"))

    except Exception:
        db.rollback()
        flash("Could not add pickup slot.", "error")
        return redirect(url_for("admin_dashboard"))

    finally:
        db.close()


@app.route("/admin/slot/<int:slot_id>/edit", methods=["POST"])
def admin_edit_slot(slot_id):
    db = get_db()
    try:
        slot = db.query(PickupSlot).filter_by(slot_id=slot_id).first()

        if not slot:
            flash("Pickup slot not found.", "error")
            return redirect(url_for("admin_dashboard"))

        new_capacity = int(request.form["capacity"])

        if new_capacity < slot.booked_count:
            flash(
                f"Capacity cannot be less than current bookings ({slot.booked_count}).",
                "error",
            )
            return redirect(url_for("admin_dashboard"))

        slot.start_time = request.form["start_time"]
        slot.end_time = request.form["end_time"]
        slot.capacity = new_capacity

        db.commit()

        flash("Pickup slot updated.", "success")
        return redirect(url_for("admin_dashboard"))

    except Exception:
        db.rollback()
        flash("Could not update pickup slot.", "error")
        return redirect(url_for("admin_dashboard"))

    finally:
        db.close()


@app.route("/admin/slot/<int:slot_id>/delete", methods=["POST"])
def admin_delete_slot(slot_id):
    db = get_db()
    try:
        slot = db.query(PickupSlot).filter_by(slot_id=slot_id).first()

        if not slot:
            flash("Pickup slot not found.", "error")
            return redirect(url_for("admin_dashboard"))

        if slot.booked_count > 0:
            flash("Cannot delete a slot that already has bookings.", "error")
            return redirect(url_for("admin_dashboard"))

        db.delete(slot)
        db.commit()

        flash("Pickup slot deleted.", "success")
        return redirect(url_for("admin_dashboard"))

    except Exception:
        db.rollback()
        flash("Could not delete pickup slot.", "error")
        return redirect(url_for("admin_dashboard"))

    finally:
        db.close()


# ---------------------------------------------------------------------------
# Admin: auto-accept setting
# ---------------------------------------------------------------------------
@app.route("/admin/settings", methods=["POST"])
def admin_update_settings_page():
    db = get_db()
    try:
        settings = db.query(Settings).first()

        if not settings:
            settings = Settings(auto_accept=True)
            db.add(settings)

        settings.auto_accept = "auto_accept" in request.form

        db.commit()

        flash(
            "Automatic acceptance is now "
            + ("ON." if settings.auto_accept else "OFF."),
            "success",
        )

        return redirect(url_for("admin_dashboard"))

    except Exception:
        db.rollback()
        flash("Could not update setting.", "error")
        return redirect(url_for("admin_dashboard"))

    finally:
        db.close()


from frontend_routes import register_frontend

register_frontend(app, get_db)

from auth_routes import register_auth

register_auth(app, get_db)

if __name__ == "__main__":
    seed()
    app.run(host="0.0.0.0", debug=True, port=5000)
