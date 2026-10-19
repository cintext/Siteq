PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'staff', 'vet')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    actor_username TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_logs_by_created ON audit_logs(created_at DESC);

CREATE TABLE IF NOT EXISTS login_attempts (
    remote_key TEXT PRIMARY KEY,
    failure_count INTEGER NOT NULL,
    window_started INTEGER NOT NULL,
    locked_until INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS order_rate_limits (
    remote_key TEXT PRIMARY KEY,
    request_count INTEGER NOT NULL,
    window_started INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pens (
    name TEXT PRIMARY KEY,
    category TEXT NOT NULL CHECK (category IN ('Sapi', 'Kambing')),
    capacity INTEGER NOT NULL CHECK (capacity BETWEEN 1 AND 500),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS animals (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('Sapi', 'Kambing')),
    breed TEXT NOT NULL,
    age INTEGER NOT NULL CHECK (age BETWEEN 1 AND 500),
    weight REAL NOT NULL CHECK (weight > 0 AND weight <= 2000),
    price INTEGER NOT NULL CHECK (price > 0),
    pen TEXT NOT NULL REFERENCES pens(name) ON UPDATE CASCADE ON DELETE RESTRICT,
    health TEXT NOT NULL CHECK (health IN ('Sehat', 'Perawatan', 'Karantina', 'Terjual')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    animal_id TEXT NOT NULL REFERENCES animals(id) ON DELETE RESTRICT,
    animal_name TEXT NOT NULL,
    category TEXT NOT NULL,
    buyer_name TEXT NOT NULL,
    buyer_phone TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('Menunggu konfirmasi', 'Dikonfirmasi', 'Ditolak')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_order_per_animal
    ON orders(animal_id)
    WHERE status IN ('Menunggu konfirmasi', 'Dikonfirmasi');

CREATE INDEX IF NOT EXISTS orders_by_created ON orders(created_at DESC);
