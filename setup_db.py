import sqlite3

conn = sqlite3.connect("store.db")
c = conn.cursor()

# --- Tabel Users ---
c.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    total_transaksi INTEGER DEFAULT 0
)
""")

# --- Tabel Products ---
c.execute("""
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nama TEXT,
    harga INTEGER,
    stok INTEGER,
    deskripsi TEXT
)
""")

# --- Tabel Orders ---
c.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    produk TEXT,            -- nama produk, bukan ID
    jumlah INTEGER,
    total_harga INTEGER,
    status TEXT DEFAULT 'pending',
    idco TEXT,
    waktu TEXT
)
""")

# --- Insert produk awal (jika belum ada) ---
produk_awal = [
    ("Capcut Pro", 14000, 100, "Akun private"),
    ("Email Workspace", 10000, 37, "20 akun"),
    ("Canva User", 10000, 76, "Canva Pro user")
]

for p in produk_awal:
    c.execute("SELECT COUNT(*) FROM products WHERE nama=?", (p[0],))
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO products (nama, harga, stok, deskripsi) VALUES (?,?,?,?)", p)

conn.commit()
conn.close()
print("✅ Database siap dengan 3 produk awal!")
