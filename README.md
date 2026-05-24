# Django ETL Pipeline

A production-grade, memory-efficient, and resumable data migration pipeline built with Django management commands. Migrates 500,000 legacy denormalized order records into a normalized relational schema using advanced Django ORM optimization techniques.

## Features

- **Memory-efficient**: Uses `iterator(chunk_size=...)` to keep memory usage constant (~18MB) regardless of dataset size
- **High-throughput**: Uses `bulk_create()` for batch inserts — ~6,757 records/sec at batch-size=1000
- **Idempotent**: Safe to re-run; already-migrated records are skipped
- **Atomic**: Each batch is wrapped in a database transaction — partial failures roll back cleanly
- **Resumable**: `--start-from` flag allows resuming from any point after an interruption
- **Dry-run support**: Preview what would happen without touching the database
- **Fully containerized**: Docker + Docker Compose with PostgreSQL health checks

---

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) installed

### 1. Clone and Configure

```bash
git clone <your-repo-url>
cd django_etl

# Copy environment variables
cp .env.example .env
# Edit .env if you want to change any defaults (optional for local dev)
```

### 2. Start Services

```bash
docker compose up --build -d
```

Wait for all services to become healthy (up to 3 minutes):

```bash
docker compose ps
# Both 'db' and 'app' should show as healthy/running
```

### 3. Run Database Migrations

```bash
docker compose exec app python manage.py migrate
```

### 4. Seed Legacy Data

Populate the database with 500,000 legacy order records:

```bash
docker compose exec app python manage.py seed_legacy_data
```

This will take 1-2 minutes. You can verify:

```bash
docker compose exec db psql -U etl_user -d etl_db -c "SELECT COUNT(*) FROM orders_legacyorder;"
# Should return 500000

docker compose exec db psql -U etl_user -d etl_db -c "SELECT COUNT(*) FROM orders_legacyorder WHERE migrated = false;"
# Should return 500000
```

### 5. Run the ETL Migration

```bash
docker compose exec app python manage.py migrate_orders
```

Verify results:

```bash
docker compose exec db psql -U etl_user -d etl_db -c "SELECT COUNT(*) FROM orders_order;"
# Should return 500000

docker compose exec db psql -U etl_user -d etl_db -c "SELECT COUNT(*) FROM orders_orderline;"
# Should return >= 500000

docker compose exec db psql -U etl_user -d etl_db -c "SELECT COUNT(*) FROM orders_legacyorder WHERE migrated = true;"
# Should return 500000
```

---

## Management Commands

### `seed_legacy_data`

Populates the `LegacyOrder` table with realistic test data.

```bash
python manage.py seed_legacy_data [options]
```

| Option         | Type    | Default | Description                            |
| -------------- | ------- | ------- | -------------------------------------- |
| `--batch-size` | integer | 5000    | Records per insert batch               |
| `--count`      | integer | 500000  | Total records to create                |
| `--clear`      | flag    | False   | Delete existing records before seeding |

**Examples:**

```bash
# Standard seed
docker compose exec app python manage.py seed_legacy_data

# Reset and re-seed
docker compose exec app python manage.py seed_legacy_data --clear

# Seed a smaller dataset for testing
docker compose exec app python manage.py seed_legacy_data --count=10000
```

---

### `migrate_orders`

The main ETL pipeline. Migrates `LegacyOrder` records into `Order` and `OrderLine` tables.

```bash
python manage.py migrate_orders [options]
```

| Option         | Type    | Default | Description                    |
| -------------- | ------- | ------- | ------------------------------ |
| `--batch-size` | integer | 1000    | Records per processing batch   |
| `--dry-run`    | flag    | False   | Preview without writing to DB  |
| `--start-from` | string  | None    | Resume from this `external_id` |

**Examples:**

```bash
# Standard migration (recommended)
docker compose exec app python manage.py migrate_orders

# Preview what would be migrated (no DB changes)
docker compose exec app python manage.py migrate_orders --dry-run

# Use a larger batch for faster processing
docker compose exec app python manage.py migrate_orders --batch-size=5000

# Resume after an interruption at record legacy-50001
docker compose exec app python manage.py migrate_orders --start-from=legacy-50001

# Verbose progress with large batches
docker compose exec app python manage.py migrate_orders --batch-size=50000
```

**Sample Output:**

```
============================================================
  Django ETL Pipeline: migrate_orders
============================================================
  Batch size    : 1,000
  Dry run       : False
  Start from    : (beginning)
============================================================
Pending records to migrate: 500,000

Successfully processed batch of 1,000 records (2,485 lines, 1000 legacy records marked migrated).
Successfully processed batch of 1,000 records (2,503 lines, 1000 legacy records marked migrated).
...

============================================================
  Migration Complete
============================================================
  Total records processed : 500,000
  Total batches           : 500
  Total time              : 74.32 seconds
  Throughput              : 6,727.4 records per second
  Peak memory usage       : 18.24 MB
============================================================
```

---

## Data Models

### `LegacyOrder` (source)

```json
{
  "external_id": "legacy-1",
  "raw_data": {
    "customer_email": "alice42@gmail.com",
    "total": "199.98",
    "items": [
      { "sku": "SKU-A1", "quantity": 2, "unit_price": "49.99" },
      { "sku": "SKU-B7", "quantity": 1, "unit_price": "99.99" }
    ]
  },
  "migrated": false
}
```

### `Order` (destination)

| Field            | Type         | Description                  |
| ---------------- | ------------ | ---------------------------- |
| `external_id`    | CharField    | Unique ID from legacy system |
| `customer_email` | EmailField   | Customer's email address     |
| `total_price`    | DecimalField | Order total                  |

### `OrderLine` (destination)

| Field        | Type                 | Description     |
| ------------ | -------------------- | --------------- |
| `order`      | ForeignKey → Order   | Parent order    |
| `sku`        | CharField            | Product SKU     |
| `quantity`   | PositiveIntegerField | Number of units |
| `unit_price` | DecimalField         | Price per unit  |

---

## Architecture

```
ETL Flow (per batch):
┌─────────────────────────────────────────────────┐
│  Query LegacyOrders WHERE migrated=False        │
│  (via iterator() — memory-efficient streaming)  │
└──────────────────────┬──────────────────────────┘
                       │ chunk_size records at a time
                       ▼
┌─────────────────────────────────────────────────┐
│  Transform raw_data → Order + OrderLine objs    │
└──────────────────────┬──────────────────────────┘
                       │ when batch is full
                       ▼
┌─────────────────────────────────────────────────┐
│  BEGIN ATOMIC TRANSACTION                       │
│  1. bulk_create(orders)                         │
│  2. Re-fetch orders by external_id → get PKs   │
│  3. Link OrderLines to parent Order PKs        │
│  4. bulk_create(order_lines)                    │
│  5. UPDATE legacy_orders SET migrated=True      │
│  COMMIT                                         │
└──────────────────────┬──────────────────────────┘
                       │ repeat until done
                       ▼
┌─────────────────────────────────────────────────┐
│  Print summary: time, throughput, memory        │
└─────────────────────────────────────────────────┘
```

---

## Environment Variables

See `.env.example` for all required variables:

| Variable            | Description                   | Default                        |
| ------------------- | ----------------------------- | ------------------------------ |
| `SECRET_KEY`        | Django secret key             | Dev key (change in production) |
| `DEBUG`             | Django debug mode             | `False`                        |
| `ALLOWED_HOSTS`     | Comma-separated allowed hosts | `localhost,127.0.0.1,0.0.0.0`  |
| `POSTGRES_DB`       | PostgreSQL database name      | `etl_db`                       |
| `POSTGRES_USER`     | PostgreSQL username           | `etl_user`                     |
| `POSTGRES_PASSWORD` | PostgreSQL password           | `etl_password`                 |
| `DATABASE_URL`      | Full database connection URL  | Constructed from above         |

---

## Performance

See [benchmark.md](./benchmark.md) for detailed benchmarking results including:

- Memory usage comparison: naive vs. optimized approach
- Execution time vs. batch size table
- Database query count comparison

**TL;DR:**

- Memory: 2,800 MB (naive) → **18 MB** (optimized) — 99.4% reduction
- Queries: ~5,501 per 1,000 records (naive) → **~5** (optimized) — 99.9% reduction
- Throughput: ~6,757 records/second at batch-size=1000

---

## Project Structure

```
django_etl/
├── docker-compose.yml          # Docker Compose configuration
├── Dockerfile                  # Application container
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variables template
├── manage.py                   # Django management entry point
├── benchmark.md                # Performance benchmarking report
├── README.md                   # This file
├── etl_project/                # Django project package
│   ├── __init__.py
│   ├── settings.py             # Django settings
│   ├── urls.py                 # URL configuration
│   └── wsgi.py                 # WSGI entry point
└── orders/                     # Main application
    ├── __init__.py
    ├── apps.py
    ├── admin.py                # Django admin registration
    ├── models.py               # LegacyOrder, Order, OrderLine models
    ├── migrations/
    │   ├── __init__.py
    │   └── 0001_initial.py     # Initial database schema
    └── management/
        └── commands/
            ├── __init__.py
            ├── seed_legacy_data.py   # Database seeder command
            └── migrate_orders.py     # Main ETL pipeline command
```
