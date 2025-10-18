import telebot
from telebot import types
import sqlite3
import json
import datetime

# --- Load config ---
with open("config.json") as f:
    config = json.load(f)

BOT_TOKEN = config["BOT_TOKEN"]
QRIS_IMAGE = config["QRIS_IMAGE"]
ADMIN_ID = config["ADMIN_ID"]

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

pending_payment = {}   # user_id -> order detail
order_map = {}         # map msg admin -> order
last_message = {}      # simpan message id terakhir per user

# --- START ---
@bot.message_handler(commands=["start"])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username

    conn = sqlite3.connect("store.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (id, username) VALUES (?,?)", (user_id, username))
    c.execute("SELECT SUM(total_harga) FROM orders WHERE user_id=? AND status='selesai'", (user_id,))
    total_transaksi = c.fetchone()[0] or 0
    conn.commit()
    conn.close()

    waktu = datetime.datetime.now().strftime("%A, %d %B %Y %H:%M:%S")

    teks = f"""
👋 Halo {username or user_id}
{waktu}

<b>User Info :</b>
└ ID : {user_id}
└ Username : @{username or '-'}
└ Transaksi : Rp. {total_transaksi:,}
"""

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("📦 List Produk", callback_data="produk"))
    markup.row(types.InlineKeyboardButton("✨ Produk Populer", callback_data="populer"),
               types.InlineKeyboardButton("❓ Cara Order", callback_data="cara"))

    bot.send_message(message.chat.id, teks, reply_markup=markup)

# --- Hapus pesan lama (kecuali /start) ---
def safe_delete(chat_id, user_id):
    if user_id in last_message:
        try:
            bot.delete_message(chat_id, last_message[user_id])
        except:
            pass

# --- CALLBACK ---
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    uid = call.from_user.id
    if call.data == "produk":
        safe_delete(call.message.chat.id, uid)
        list_produk(call.message, uid)
    elif call.data.startswith("order_"):
        safe_delete(call.message.chat.id, uid)
        order_produk(call, uid)
    elif call.data.startswith("qty_"):
        update_qty(call, uid)
    elif call.data.startswith("confirm_"):
        safe_delete(call.message.chat.id, uid)
        confirm_order(call, uid)
    elif call.data == "batal_order":
        safe_delete(call.message.chat.id, uid)
        list_produk(call.message, uid)
    elif call.data.startswith("paid_"):
        paid_order(call)
    elif call.data == "cara":
        safe_delete(call.message.chat.id, uid)
        cara_order(call.message, uid)
    elif call.data == "populer":
        safe_delete(call.message.chat.id, uid)
        populer(call.message, uid)

# --- LIST PRODUK ---
def list_produk(message, uid):
    conn = sqlite3.connect("store.db")
    c = conn.cursor()
    c.execute("SELECT id, nama, harga, stok FROM products")
    rows = c.fetchall()
    conn.close()

    if not rows:
        sent = bot.send_message(message.chat.id, "Belum ada produk.")
        last_message[uid] = sent.message_id
        return

    teks = "📦 <b>LIST PRODUK</b>\n\n"
    for r in rows:
        teks += f"[{r[0]}] {r[1]} - Rp {r[2]:,} (stok {r[3]})\n"

    teks += "\nKlik tombol angka di bawah untuk memilih produk."

    markup = types.InlineKeyboardMarkup(row_width=6)
    buttons = [types.InlineKeyboardButton(str(r[0]), callback_data=f"order_{r[0]}") for r in rows]
    for i in range(0, len(buttons), 6):
        markup.row(*buttons[i:i+6])

    sent = bot.send_message(message.chat.id, teks, reply_markup=markup)
    last_message[uid] = sent.message_id

# --- ORDER PRODUK ---
def order_produk(call, uid):
    pid = int(call.data.split("_")[1])

    conn = sqlite3.connect("store.db")
    c = conn.cursor()
    c.execute("SELECT nama, harga, stok, deskripsi FROM products WHERE id=?", (pid,))
    produk = c.fetchone()
    conn.close()

    if not produk:
        return

    nama, harga, stok, deskripsi = produk
    teks = f"""
🛒 <b>Pesanan</b>
Produk : {nama}
Deskripsi : {deskripsi}

Harga  : Rp {harga:,}
Jumlah : 1
Total  : Rp {harga:,}

Tekan ( - ) untuk mengurangi jumlah pcs
Tekan ( + ) untuk menambah jumlah pcs
"""

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("➖", callback_data=f"qty_{pid}_-1"),
        types.InlineKeyboardButton("➕", callback_data=f"qty_{pid}_+1")
    )
    markup.row(types.InlineKeyboardButton("✅ Konfirmasi", callback_data=f"confirm_{pid}_1"))
    markup.row(types.InlineKeyboardButton("❌ Batal Order", callback_data="batal_order"))

    sent = bot.send_message(call.message.chat.id, teks, reply_markup=markup)
    last_message[uid] = sent.message_id

# --- UPDATE JUMLAH ---
def update_qty(call, uid):
    _, pid, change = call.data.split("_")
    pid = int(pid)
    change = int(change)

    conn = sqlite3.connect("store.db")
    c = conn.cursor()
    c.execute("SELECT nama, harga, deskripsi FROM products WHERE id=?", (pid,))
    produk = c.fetchone()
    conn.close()
    nama, harga, deskripsi = produk

    jumlah = 1
    if "Jumlah" in call.message.text:
        try:
            jumlah_line = [line for line in call.message.text.splitlines() if "Jumlah" in line][0]
            jumlah = int(jumlah_line.split(":")[1].strip())
        except:
            jumlah = 1

    jumlah = max(1, jumlah + change)
    total = harga * jumlah

    teks = f"""
╭────────────────────╮
🛒 <b>Pesanan</b>
Produk : {nama}
Deskripsi : {deskripsi}

Harga  : Rp {harga:,}
Jumlah : {jumlah}
Total  : Rp {total:,}
╰────────────────────╯
╭────────────────────────────────────────╮
│Tekan ( - ) untuk mengurangi jumlah pcs
│Tekan ( + ) untuk menambah jumlah pcs
╰────────────────────────────────────────╯

"""

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("➖", callback_data=f"qty_{pid}_-1"),
        types.InlineKeyboardButton("➕", callback_data=f"qty_{pid}_+1")
    )
    markup.row(types.InlineKeyboardButton("✅ Konfirmasi", callback_data=f"confirm_{pid}_{jumlah}"))
    markup.row(types.InlineKeyboardButton("❌ Batal Order", callback_data="batal_order"))

    bot.edit_message_text(teks, call.message.chat.id, call.message.message_id, reply_markup=markup)

# --- KONFIRMASI ORDER ---
def confirm_order(call, uid):
    _, pid, jumlah = call.data.split("_")
    pid = int(pid)
    jumlah = int(jumlah)

    conn = sqlite3.connect("store.db")
    c = conn.cursor()
    c.execute("SELECT nama, harga FROM products WHERE id=?", (pid,))
    produk = c.fetchone()
    conn.close()
    nama, harga = produk
    total = harga * jumlah

    now = datetime.datetime.now()
    idco = f"PINN{now.strftime('%H.%M.%S/%d/%m/%y')}"

    caption = f"""
<b>Konfirmasi Pesanan✅</b>
╭ - - - - - - - - - - - - - - - - - - - - - - - - -- - ╮
┊・IDCO : {idco}
┊・Produk : {nama}
┊・Harga  : Rp {harga:,}
┊  - - - - - - - - - - - - - - - - - - - - - - -- - - -  
┊・Jumlah : {jumlah}
┊・Total  : Rp {total:,}
╰ - - - - - - - - - - - - - - - - - - - - - - - -- - - ╯ 
╭ - - - - - - - - - - - - - - - - - - - - - - - - ╮
Silakan bayar via QRIS berikut:
╰ - - - - - - - - - - - - - - - - - -  - - -- - - ╯ 
"""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📤 Saya Sudah Bayar", callback_data=f"paid_{pid}_{jumlah}_{idco}"))

    sent = bot.send_photo(call.message.chat.id, open(QRIS_IMAGE, "rb"), caption=caption, reply_markup=markup)
    last_message[uid] = sent.message_id

    pending_payment[call.from_user.id] = {
        "produk": nama, "jumlah": jumlah, "idco": idco,
        "harga": harga, "total": total, "msg_id": sent.message_id
    }

# --- SAAT USER KLIK "SAYA SUDAH BAYAR" ---
def paid_order(call):
    user_id = call.from_user.id
    if user_id not in pending_payment:
        bot.send_message(call.message.chat.id, "Tidak ada pesanan yang menunggu pembayaran.")
        return
    bot.send_message(call.message.chat.id, "📤 Silakan upload bukti transaksi (foto/screenshot pembayaran).")

# --- HANDLE GAMBAR BUKTI PEMBAYARAN ---
@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    user_id = message.from_user.id
    if user_id not in pending_payment:
        return

    order = pending_payment[user_id]
    order["waktu"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        bot.delete_message(message.chat.id, order["msg_id"])
    except:
        pass

    teks_user = f"""
<b></b>

IDCO : {order['idco']}
Produk Item : {order['produk']}
Status : Proses Validasi ⏳

Produk akan otomatis dikirim setelah validasi pembayaran berhasil.
"""
    bot.reply_to(message, teks_user)

    caption_admin = f"""
📥 <b>PESANAN MASUK</b>
└ ID : {user_id}
└ Username : @{message.from_user.username}
└ IDCO : {order['idco']}

<b>Order :</b>
Produk : {order['produk']}
Jumlah : {order['jumlah']}
Total  : Rp {order['total']:,}
Checkout time : {order['waktu']}
"""
    sent_admin = bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=caption_admin)

    order_map[sent_admin.message_id] = {**order, "buyer": user_id}
    del pending_payment[user_id]

# --- HANDLE REPLY ADMIN ---
@bot.message_handler(func=lambda m: m.chat.id == ADMIN_ID and m.reply_to_message,
                     content_types=["text","photo","document","video","audio","voice"])
def handle_admin_reply(message):
    ref = order_map.get(message.reply_to_message.message_id)
    if not ref:
        return

    buyer = ref["buyer"]

    if message.content_type == "text":
        teks_buyer = f"""
╭ - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - ╮
<b>Terima kasih telah bertransaksi bersama kami🙏 </b>

<b>Data Produk Item Yang Anda Beli</b>
IDCO : {ref['idco']}
Produk Item : {ref['produk']}

<b>Data Orderan Anda:</b>
{message.text}

Status : Transaksi Selesai ✅
PINN STUDIO
╰ - - - - - - - - - - - - - - - -- - - - - - - - - - - - - - - - - - - - -  - - - - - ╯ 
╭ - - - - - - -  - - ╮
Order lagi - /start
╰ - - - - - - - - -- ╯ 

"""
        bot.send_message(buyer, teks_buyer)
    elif message.content_type == "document":
        bot.send_document(buyer, message.document.file_id,
                          caption=f"🎁 Produk Anda: {ref['produk']}\nIDCO: {ref['idco']}\nStatus: Transaksi Selesai ✅")
    elif message.content_type == "photo":
        bot.send_photo(buyer, message.photo[-1].file_id,
                       caption=f"🎁 Produk Anda: {ref['produk']}\nIDCO: {ref['idco']}\nStatus: Transaksi Selesai ✅")
    elif message.content_type == "video":
        bot.send_video(buyer, message.video.file_id,
                       caption=f"🎁 Produk Anda: {ref['produk']}\nIDCO: {ref['idco']}\nStatus: Transaksi Selesai ✅")
    elif message.content_type == "audio":
        bot.send_audio(buyer, message.audio.file_id,
                       caption=f"🎁 Produk Anda: {ref['produk']}\nIDCO: {ref['idco']}\nStatus: Transaksi Selesai ✅")
    elif message.content_type == "voice":
        bot.send_voice(buyer, message.voice.file_id,
                       caption=f"🎁 Produk Anda: {ref['produk']}\nIDCO: {ref['idco']}\nStatus: Transaksi Selesai ✅")

    conn = sqlite3.connect("store.db")
    c = conn.cursor()
    c.execute("UPDATE products SET stok = stok - ? WHERE nama=?", (ref["jumlah"], ref["produk"]))
    c.execute("""INSERT INTO orders (user_id, produk, jumlah, total_harga, status, idco, waktu) 
                 VALUES (?,?,?,?,?,?,?)""",
              (buyer, ref["produk"], ref["jumlah"], ref["total"], "selesai", ref["idco"], ref["waktu"]))
    c.execute("UPDATE users SET total_transaksi = total_transaksi + ? WHERE id=?", (ref["total"], buyer))
    conn.commit()
    conn.close()

    del order_map[message.reply_to_message.message_id]

# --- PRODUK POPULER ---
def populer(message, uid=None):
    conn = sqlite3.connect("store.db")
    c = conn.cursor()
    c.execute("""SELECT produk, COUNT(*) as total_jual 
                 FROM orders WHERE status='selesai' 
                 GROUP BY produk ORDER BY total_jual DESC""")
    rows = c.fetchall()
    conn.close()

    if not rows:
        teks = "Belum ada penjualan."
    else:
        teks = "✨ <b>Produk Populer</b>\n\n"
        for r in rows:
            teks += f"{r[0]} - {r[1]} penjualan\n"

    sent = bot.send_message(message.chat.id, teks)
    if uid: last_message[uid] = sent.message_id

# --- CARA ORDER ---
def cara_order(message, uid):
    teks = """
❓ <b>Cara Order</b>
1. Klik 📦 List Produk
2. Pilih produk & jumlah
3. Bayar via QRIS
4. Klik 📤 Saya Sudah Bayar
5. Upload bukti pembayaran
6. Admin validasi → produk dikirim
"""
    sent = bot.send_message(message.chat.id, teks)
    last_message[uid] = sent.message_id

# --- ADMIN COMMANDS ---
@bot.message_handler(commands=["addproduk"])
def add_produk(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        data = message.text.replace("/addproduk ", "")
        nama, harga, stok, deskripsi = [x.strip() for x in data.split("|")]
        harga, stok = int(harga), int(stok)

        conn = sqlite3.connect("store.db")
        c = conn.cursor()
        c.execute("INSERT INTO products (nama, harga, stok, deskripsi) VALUES (?,?,?,?)", (nama, harga, stok, deskripsi))
        conn.commit()
        conn.close()

        bot.reply_to(message, f"✅ Produk '{nama}' berhasil ditambahkan.")
    except Exception as e:
        bot.reply_to(message, f"Format salah. Contoh:\n/addproduk Nama | 10000 | 50 | Deskripsi\nError: {e}")

@bot.message_handler(commands=["hapusproduk"])
def hapus_produk(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        _, pid = message.text.split()
        pid = int(pid)

        conn = sqlite3.connect("store.db")
        c = conn.cursor()
        c.execute("DELETE FROM products WHERE id=?", (pid,))
        conn.commit()
        conn.close()

        bot.reply_to(message, f"✅ Produk ID {pid} berhasil dihapus.")
    except:
        bot.reply_to(message, "Format salah. Contoh: /hapusproduk 1")

@bot.message_handler(commands=["updatestok"])
def update_stok(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        _, pid, stok = message.text.split()
        pid, stok = int(pid), int(stok)

        conn = sqlite3.connect("store.db")
        c = conn.cursor()
        c.execute("UPDATE products SET stok=? WHERE id=?", (stok, pid))
        conn.commit()
        conn.close()

        bot.reply_to(message, f"✅ Stok produk ID {pid} diupdate ke {stok}")
    except:
        bot.reply_to(message, "Format salah. Contoh: /updatestok 1 50")

# --- CEK TRANSAKSI (khusus admin) ---
@bot.message_handler(commands=["cek"])
def cek_transaksi(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Kamu tidak punya akses ke perintah ini.")
        return

    conn = sqlite3.connect("store.db")
    c = conn.cursor()
    c.execute("SELECT DISTINCT user_id FROM orders WHERE status='selesai'")
    user_ids = [row[0] for row in c.fetchall()]

    if not user_ids:
        bot.reply_to(message, "Belum ada transaksi selesai.")
        conn.close()
        return

    teks = "📊 <b>Daftar Transaksi Selesai</b>\n\n"
    for uid in user_ids:
        c.execute("SELECT username FROM users WHERE id=?", (uid,))
        username = c.fetchone()
        username = username[0] if username and username[0] else "-"

        c.execute("SELECT produk, idco, total_harga FROM orders WHERE user_id=? AND status='selesai'", (uid,))
        trans = c.fetchall()

        teks += f"User Info :\n└ ID : {uid}\n└ Username : @{username}\n└ Transaksi : {len(trans)} transaksi\n"
        for t in trans:
            teks += f"- {t[0]} , {t[1]} , Rp {t[2]:,} , Selesai\n"
        teks += "\n"

    conn.close()
    bot.send_message(message.chat.id, teks)

print("🤖 Bot berjalan...")
bot.infinity_polling()
