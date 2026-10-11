"""
Frontend routes for SmartCanteen.
Kept in a separate file so your existing app.py (with your DB password) stays untouched.

Adds:
  GET /                        student page
  GET /staff                   staff board page
  GET /api/menu                food items with stock
  GET /api/slots               pickup slots with seats left
  GET /api/orders/<id>         order status (polled by the student page)
  GET /order/<id>/qr.svg       QR image for an order
"""

import io

import segno
from flask import render_template, jsonify, Response, session

from models import FoodItem, PickupSlot, Order


def _not_yours(order):
    """Students may only see their own orders; staff/admin can see all."""
    return session.get("role") == "student" and order.user_id != session.get("user_id")


def register_frontend(app, get_db):

    @app.route("/")
    def student_page():
        return render_template("index.html", user_id=session["user_id"],
                               user_name=session.get("name", ""))

    @app.route("/staff")
    def staff_page():
        return render_template("staff.html", user_name=session.get("name", ""),
                               role=session.get("role", ""))

    @app.route("/api/menu")
    def api_menu():
        db = get_db()
        try:
            items = db.query(FoodItem).order_by(FoodItem.food_id).all()
            return jsonify([{
                "food_id": f.food_id,
                "name": f.name,
                "price": f.price,
                "category": f.category,
                "stock": f.available_quantity,
                "available": bool(f.is_available and f.available_quantity > 0),
            } for f in items])
        finally:
            db.close()

    @app.route("/api/slots")
    def api_slots():
        db = get_db()
        try:
            slots = db.query(PickupSlot).order_by(PickupSlot.slot_id).all()
            return jsonify([{
                "slot_id": s.slot_id,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "left": max(s.capacity - s.booked_count, 0),
            } for s in slots])
        finally:
            db.close()

    @app.route("/api/orders/<int:order_id>")
    def api_order(order_id):
        db = get_db()
        try:
            o = db.query(Order).filter_by(order_id=order_id).first()
            if not o or _not_yours(o):
                return jsonify({"error": "Order not found."}), 404
            return jsonify({
                "order_id": o.order_id,
                "status": o.status.value,
                "total_amount": o.total_amount,
            })
        finally:
            db.close()

    @app.route("/order/<int:order_id>/qr.svg")
    def order_qr(order_id):
        db = get_db()
        try:
            o = db.query(Order).filter_by(order_id=order_id).first()
            if not o or not o.qr_token or _not_yours(o):
                return Response("Not found", status=404)
            buf = io.BytesIO()
            segno.make(o.qr_token).save(buf, kind="svg", scale=6)
            return Response(buf.getvalue(), mimetype="image/svg+xml")
        finally:
            db.close()