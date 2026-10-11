from flask import render_template, request, redirect, url_for, flash
from sqlalchemy.exc import IntegrityError
import secrets

from models import FoodItem, PickupSlot, Order, OrderStatus, Settings


def register_admin(app, get_db):
    @app.route('/admin')
    def admin_dashboard():
        db = get_db()
        try:
            foods = db.query(FoodItem).order_by(FoodItem.food_id).all()
            slots = db.query(PickupSlot).order_by(PickupSlot.slot_id).all()
            pending = (db.query(Order)
                       .filter_by(status=OrderStatus.PENDING)
                       .order_by(Order.created_at.desc())
                       .all())
            settings = db.query(Settings).first()
            auto_accept = settings.auto_accept if settings else True
            return render_template('admin.html', foods=foods, slots=slots,
                                   pending_orders=pending, auto_accept=auto_accept)
        finally:
            db.close()

    @app.route('/admin/food/add', methods=['POST'])
    def admin_add_food():
        name = request.form.get('name', '').strip()
        category = request.form.get('category', '').strip()
        try:
            price = float(request.form.get('price', 0))
            quantity = int(request.form.get('quantity', 0))
        except (TypeError, ValueError):
            flash('Enter a valid price and stock quantity.', 'error')
            return redirect(url_for('admin_dashboard'))

        if not name or price < 0 or quantity < 0:
            flash('Food name is required; price and stock cannot be negative.', 'error')
            return redirect(url_for('admin_dashboard'))

        db = get_db()
        try:
            db.add(FoodItem(name=name, price=price, category=category,
                            available_quantity=quantity, is_available=quantity > 0))
            db.commit()
            flash(f'{name} added successfully.', 'success')
        except Exception:
            db.rollback()
            flash('Could not add the food item.', 'error')
        finally:
            db.close()
        return redirect(url_for('admin_dashboard'))

    @app.route('/admin/food/<int:food_id>/edit', methods=['POST'])
    def admin_edit_food(food_id):
        db = get_db()
        try:
            item = db.query(FoodItem).filter_by(food_id=food_id).first()
            if not item:
                flash('Food item not found.', 'error')
                return redirect(url_for('admin_dashboard'))

            item.name = request.form.get('name', '').strip()
            item.category = request.form.get('category', '').strip()
            item.price = float(request.form.get('price', 0))
            item.available_quantity = int(request.form.get('quantity', 0))
            item.is_available = bool(request.form.get('is_available')) and item.available_quantity > 0

            if not item.name or item.price < 0 or item.available_quantity < 0:
                raise ValueError('Invalid food details.')

            db.commit()
            flash(f'{item.name} updated.', 'success')
        except (ValueError, TypeError):
            db.rollback()
            flash('Invalid food details.', 'error')
        except Exception:
            db.rollback()
            flash('Could not update the food item.', 'error')
        finally:
            db.close()
        return redirect(url_for('admin_dashboard'))

    @app.route('/admin/food/<int:food_id>/delete', methods=['POST'])
    def admin_delete_food(food_id):
        db = get_db()
        try:
            item = db.query(FoodItem).filter_by(food_id=food_id).first()
            if not item:
                flash('Food item not found.', 'error')
            else:
                db.delete(item)
                db.commit()
                flash(f'{item.name} deleted.', 'success')
        except IntegrityError:
            db.rollback()
            flash('This food item is used by an existing order and cannot be deleted. Mark it unavailable instead.', 'error')
        except Exception:
            db.rollback()
            flash('Could not delete the food item.', 'error')
        finally:
            db.close()
        return redirect(url_for('admin_dashboard'))

    @app.route('/admin/food/<int:food_id>/restock', methods=['POST'])
    def admin_restock_food(food_id):
        db = get_db()
        try:
            item = db.query(FoodItem).filter_by(food_id=food_id).first()
            amount = int(request.form.get('amount', 0))
            if not item or amount <= 0:
                flash('Enter a valid restock quantity.', 'error')
            else:
                item.available_quantity += amount
                item.is_available = True
                db.commit()
                flash(f'{item.name} restocked by {amount}.', 'success')
        except (ValueError, TypeError):
            db.rollback()
            flash('Restock quantity must be a positive whole number.', 'error')
        except Exception:
            db.rollback()
            flash('Could not restock the item.', 'error')
        finally:
            db.close()
        return redirect(url_for('admin_dashboard'))

    @app.route('/admin/slot/add', methods=['POST'])
    def admin_add_slot():
        start = request.form.get('start_time', '').strip()
        end = request.form.get('end_time', '').strip()
        try:
            capacity = int(request.form.get('capacity', 0))
        except (TypeError, ValueError):
            capacity = 0
        if not start or not end or capacity <= 0:
            flash('Enter valid slot times and a positive capacity.', 'error')
            return redirect(url_for('admin_dashboard'))

        db = get_db()
        try:
            db.add(PickupSlot(start_time=start, end_time=end, capacity=capacity, booked_count=0))
            db.commit()
            flash('Pickup slot added.', 'success')
        except Exception:
            db.rollback()
            flash('Could not add the pickup slot.', 'error')
        finally:
            db.close()
        return redirect(url_for('admin_dashboard'))

    @app.route('/admin/slot/<int:slot_id>/edit', methods=['POST'])
    def admin_edit_slot(slot_id):
        db = get_db()
        try:
            slot = db.query(PickupSlot).filter_by(slot_id=slot_id).first()
            if not slot:
                flash('Pickup slot not found.', 'error')
                return redirect(url_for('admin_dashboard'))
            slot.start_time = request.form.get('start_time', '').strip()
            slot.end_time = request.form.get('end_time', '').strip()
            slot.capacity = int(request.form.get('capacity', 0))
            if not slot.start_time or not slot.end_time or slot.capacity < slot.booked_count or slot.capacity <= 0:
                raise ValueError('Capacity cannot be below current bookings.')
            db.commit()
            flash('Pickup slot updated.', 'success')
        except (ValueError, TypeError):
            db.rollback()
            flash('Invalid slot details. Capacity cannot be below current bookings.', 'error')
        except Exception:
            db.rollback()
            flash('Could not update the pickup slot.', 'error')
        finally:
            db.close()
        return redirect(url_for('admin_dashboard'))

    @app.route('/admin/slot/<int:slot_id>/delete', methods=['POST'])
    def admin_delete_slot(slot_id):
        db = get_db()
        try:
            slot = db.query(PickupSlot).filter_by(slot_id=slot_id).first()
            if not slot:
                flash('Pickup slot not found.', 'error')
            elif slot.booked_count > 0:
                flash('A slot with existing bookings cannot be deleted.', 'error')
            else:
                db.delete(slot)
                db.commit()
                flash('Pickup slot deleted.', 'success')
        except IntegrityError:
            db.rollback()
            flash('This slot is linked to an order and cannot be deleted.', 'error')
        except Exception:
            db.rollback()
            flash('Could not delete the pickup slot.', 'error')
        finally:
            db.close()
        return redirect(url_for('admin_dashboard'))

    @app.route('/staff/orders/<int:order_id>/approve', methods=['POST'])
    def admin_approve_order(order_id):
        db = get_db()
        try:
            order = db.query(Order).filter_by(order_id=order_id).first()
            if not order:
                flash('Order not found.', 'error')
            elif order.status != OrderStatus.PENDING:
                flash('Only PENDING orders can be approved.', 'error')
            else:
                order.status = OrderStatus.ACCEPTED
                order.qr_token = secrets.token_hex(16)
                db.commit()
                flash(f'Order #{order_id} approved.', 'success')
        except Exception:
            db.rollback()
            flash('Could not approve the order.', 'error')
        finally:
            db.close()
        return redirect(url_for('admin_dashboard'))

    @app.route('/staff/orders/<int:order_id>/reject', methods=['POST'])
    def admin_reject_order(order_id):
        db = get_db()
        try:
            order = db.query(Order).filter_by(order_id=order_id).first()
            if not order:
                flash('Order not found.', 'error')
            elif order.status != OrderStatus.PENDING:
                flash('Only PENDING orders can be rejected.', 'error')
            else:
                # A PENDING order already reserved stock and one pickup seat
                # in order_service.py, so rejection must release both.
                for line in order.items:
                    if line.food_item:
                        line.food_item.available_quantity += line.quantity
                        line.food_item.is_available = True
                if order.pickup_slot:
                    order.pickup_slot.booked_count = max(order.pickup_slot.booked_count - 1, 0)
                order.status = OrderStatus.REJECTED
                db.commit()
                flash(f'Order #{order_id} rejected and its stock/slot reservation was released.', 'success')
        except Exception:
            db.rollback()
            flash('Could not reject the order.', 'error')
        finally:
            db.close()
        return redirect(url_for('admin_dashboard'))
