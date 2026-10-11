# SmartCanteen — Automatic Order Acceptance (Demo Scaffold)

## Files
- `models.py` — SQLAlchemy tables (Users, FoodItems, PickupSlots, Orders, OrderItems, Settings)
- `order_service.py` — the 12-step transactional acceptance logic (the core of this module)
- `app.py` — Flask routes wiring it all together, plus seed data
- `requirements.txt` — `pip install -r requirements.txt --break-system-packages`

## Run it (MySQL)

1. Create the database once, in MySQL Workbench or the `mysql` CLI:
   ```sql
   CREATE DATABASE smartcanteen;
   ```
2. Set your connection string as an environment variable (edit user/password/host as needed):
   ```bash
   # Windows PowerShell
   $env:DATABASE_URL = "mysql+pymysql://root:yourpassword@localhost:3306/smartcanteen"

   # Mac/Linux
   export DATABASE_URL="mysql+pymysql://root:yourpassword@localhost:3306/smartcanteen"
   ```
   If you skip this, `app.py` falls back to `mysql+pymysql://root:password@localhost:3306/smartcanteen` —
   edit that default directly in `app.py` if you'd rather not use an env var.
3. Install dependencies and run:
   ```bash
   pip install -r requirements.txt
   python app.py
   ```

Runs on `http://localhost:5000`, seeds the MySQL DB with:
- 1 test student (`user_id=1`)
- 3 food items: Vada Pav (`food_id=1`, in stock), Sandwich (`food_id=2`, in stock), Cold Coffee (`food_id=3`, **sold out**)
- 3 pickup slots: 12:30 (`slot_id=1`, **full**), 12:45 (`slot_id=2`, 6 left), 1:00 (`slot_id=3`, empty)

## Try the three rejection cases from the spec

**Unavailable item** (Cold Coffee is sold out):
```bash
curl -X POST localhost:5000/order/place -H "Content-Type: application/json" \
  -d '{"student_id":1,"slot_id":3,"items":[{"food_id":3,"quantity":1}]}'
# → "One or more selected items are currently unavailable."
```

**Full slot** (slot 1 is 10/10):
```bash
curl -X POST localhost:5000/order/place -H "Content-Type: application/json" \
  -d '{"student_id":1,"slot_id":1,"items":[{"food_id":1,"quantity":1}]}'
# → "This pickup slot is full. Please select another slot."
```

**Insufficient quantity** (ask for more Sandwiches than the 20 in stock):
```bash
curl -X POST localhost:5000/order/place -H "Content-Type: application/json" \
  -d '{"student_id":1,"slot_id":3,"items":[{"food_id":2,"quantity":999}]}'
# → "Some items are no longer available in the requested quantity."
```

**Successful order** → auto-accepted, QR token generated:
```bash
curl -X POST localhost:5000/order/place -H "Content-Type: application/json" \
  -d '{"student_id":1,"slot_id":3,"items":[{"food_id":1,"quantity":2},{"food_id":2,"quantity":1}]}'
```

## Staff flow (no Accept/Reject step)
```bash
curl -X POST localhost:5000/staff/orders/1/start-preparing
curl -X POST localhost:5000/staff/orders/1/mark-ready
curl -X POST localhost:5000/staff/orders/1/verify-qr -H "Content-Type: application/json" \
  -d '{"qr_token":"<the token returned when the order was placed>"}'
curl localhost:5000/staff/dashboard
```

## Admin: turning auto-accept off
```bash
curl -X POST localhost:5000/admin/settings -H "Content-Type: application/json" \
  -d '{"auto_accept": false}'
```
With this OFF, new orders are created with status `PENDING` instead of `ACCEPTED`
(you'd add a manual `/staff/orders/<id>/accept` route to approve them — not
included here since the spec's default path is auto-accept ON).

## Concurrency note
`order_service.py` uses `with_for_update()` to lock the FoodItem and
PickupSlot rows during the check-and-reserve steps, so two students booking
the last slot/item at the same moment can't both succeed — the second
request blocks until the first commits or rolls back, then re-checks fresh
numbers. This works for real under MySQL (make sure your tables use the
**InnoDB** engine, which is the default for `CREATE TABLE` in modern MySQL —
`MyISAM` does not support row locks or transactions at all).

## Troubleshooting MySQL connection issues
- `Access denied for user` → check the username/password in `DATABASE_URL`.
- `Can't connect to MySQL server` → make sure MySQL is actually running
  (check the MySQL service in Services on Windows, or `mysql.server start`
  on Mac) and that the port (default `3306`) matches.
- `Unknown database 'smartcanteen'` → you skipped step 1 above; run
  `CREATE DATABASE smartcanteen;` first.
- Auth plugin errors with MySQL 8 (`caching_sha2_password`) → the
  `cryptography` package in `requirements.txt` handles this; make sure it
  installed successfully.
