import tkinter as tk
from tkinter import messagebox, ttk
import sqlite3

DB_NAME = "store.db"


# -----------------------------
# Fungsi Database
# -----------------------------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def fetch_data():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT * FROM products")
    rows = cur.fetchall()
    conn.close()
    return rows


def insert_data(name, price, stock):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT INTO products (name, price, stock) VALUES (?, ?, ?)", (name, price, stock))
    conn.commit()
    conn.close()


def update_data(pid, name, price, stock):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE products SET name=?, price=?, stock=? WHERE id=?", (name, price, stock, pid))
    conn.commit()
    conn.close()


def delete_data(pid):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM products WHERE id=?", (pid,))
    conn.commit()
    conn.close()


# -----------------------------
# GUI
# -----------------------------
class ProductApp:
    def __init__(self, root):
        self.root = root
        self.root.title("🛒 CRUD Produk - SQLite GUI")
        self.root.geometry("700x500")

        # Frame input
        frame_input = tk.Frame(root)
        frame_input.pack(pady=10)

        tk.Label(frame_input, text="Nama Produk").grid(row=0, column=0, padx=5, pady=5)
        tk.Label(frame_input, text="Harga").grid(row=1, column=0, padx=5, pady=5)
        tk.Label(frame_input, text="Stok").grid(row=2, column=0, padx=5, pady=5)

        self.name_var = tk.StringVar()
        self.price_var = tk.StringVar()
        self.stock_var = tk.StringVar()

        tk.Entry(frame_input, textvariable=self.name_var).grid(row=0, column=1, padx=5)
        tk.Entry(frame_input, textvariable=self.price_var).grid(row=1, column=1, padx=5)
        tk.Entry(frame_input, textvariable=self.stock_var).grid(row=2, column=1, padx=5)

        # Tombol aksi
        frame_button = tk.Frame(root)
        frame_button.pack(pady=5)

        tk.Button(frame_button, text="Tambah", command=self.add_product, width=10, bg="#28a745", fg="white").grid(row=0, column=0, padx=5)
        tk.Button(frame_button, text="Update", command=self.update_product, width=10, bg="#007bff", fg="white").grid(row=0, column=1, padx=5)
        tk.Button(frame_button, text="Hapus", command=self.delete_product, width=10, bg="#dc3545", fg="white").grid(row=0, column=2, padx=5)
        tk.Button(frame_button, text="Refresh", command=self.load_data, width=10).grid(row=0, column=3, padx=5)

        # Tabel data
        self.tree = ttk.Treeview(root, columns=("id", "name", "price", "stock"), show="headings")
        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="Nama")
        self.tree.heading("price", text="Harga")
        self.tree.heading("stock", text="Stok")
        self.tree.pack(pady=10, fill=tk.BOTH, expand=True)

        self.tree.bind("<ButtonRelease-1>", self.select_row)

        self.load_data()

    # -----------------------------
    # Fungsi CRUD di GUI
    # -----------------------------
    def load_data(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for row in fetch_data():
            self.tree.insert("", tk.END, values=row)

    def add_product(self):
        name = self.name_var.get()
        price = self.price_var.get()
        stock = self.stock_var.get()

        if not name or not price or not stock:
            messagebox.showwarning("Input Salah", "Semua kolom harus diisi!")
            return

        try:
            insert_data(name, float(price), int(stock))
            self.load_data()
            self.clear_input()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def select_row(self, event):
        selected = self.tree.focus()
        if not selected:
            return
        values = self.tree.item(selected, "values")
        self.clear_input()
        self.name_var.set(values[1])
        self.price_var.set(values[2])
        self.stock_var.set(values[3])

    def update_product(self):
        selected = self.tree.focus()
        if not selected:
            messagebox.showwarning("Pilih Data", "Pilih produk yang akan diupdate.")
            return

        values = self.tree.item(selected, "values")
        pid = values[0]
        name = self.name_var.get()
        price = self.price_var.get()
        stock = self.stock_var.get()

        try:
            update_data(pid, name, float(price), int(stock))
            self.load_data()
            self.clear_input()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def delete_product(self):
        selected = self.tree.focus()
        if not selected:
            messagebox.showwarning("Pilih Data", "Pilih produk yang akan dihapus.")
            return

        values = self.tree.item(selected, "values")
        pid = values[0]
        confirm = messagebox.askyesno("Konfirmasi", "Yakin ingin menghapus produk ini?")
        if confirm:
            delete_data(pid)
            self.load_data()
            self.clear_input()

    def clear_input(self):
        self.name_var.set("")
        self.price_var.set("")
        self.stock_var.set("")


# -----------------------------
# Jalankan Program
# -----------------------------
if __name__ == "__main__":
    init_db()
    root = tk.Tk()
    app = ProductApp(root)
    root.mainloop()
