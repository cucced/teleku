import telebot
from telebot import types
import sqlite3
import json
import datetime
import requests
import io
from zoneinfo import ZoneInfo


# --- Load config ---
with open("config.json") as f:
    config = json.load(f)

BOT_TOKEN = config["BOT_TOKEN"]
QRIS_IMAGE = config["QRIS_IMAGE"]
ADMIN_ID = config["ADMIN_ID"]

# === QRIS GENERATOR HELPERS ===
BASE_PAYLOAD = "00020101021126650013ID.CO.BCA.WWW011893600014000316121002150008850031612100303UMI51440014ID.CO.QRIS.WWW0215ID10254306223950303UMI52044814530336054040.005802ID5910PINN STORE6007JAKARTA61051353062070703A01630424C7"

def tlv(tag, value):
    return f"{tag}{len(value):02d}{value}"

def parse_tlv_top_level(s):
    out, i = [], 0
    while i + 4 <= len(s):
        tag = s[i:i+2]
        length = int(s[i+2:i+4])
        value = s[i+4:i+4+length]
        out.append((tag, value))
        i += 4 + length
        if tag == "63":
            break
    return out

def crc16_ccitt_false(data):
    crc = 0xFFFF
    for c in data:
        crc ^= ord(c) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"

def inject_amount_and_recalc(base_payload, amount):
    pairs = parse_tlv_top_level(base_payload)
    tlv_map = {tag: val for tag, val in pairs if tag not in ("54", "63")}
    tlv_map["54"] = f"{float(amount):.2f}"
    body = "".join([tlv(tag, tlv_map[tag]) for tag in sorted(tlv_map.keys(), key=int)])
    crc = crc16_ccitt_false(body + "6304")
    return body + "6304" + crc

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
    c.execute("""INSERT OR IGNORE INTO users (id, username, total_transaksi, saldo)
                 VALUES (?,?,?,?)""", (user_id, username, 0, 0))
    c.execute("SELECT SUM(total_harga) FROM orders WHERE user_id=? AND status='selesai'", (user_id,))
    total_transaksi = c.fetchone()[0] or 0
    c.execute("SELECT saldo FROM users WHERE id=?", (user_id,))
    saldo = c.fetchone()[0] or 0
    conn.commit(); conn.close()

    waktu = format_wib()

    teks = f"""
👋 Halo {username or user_id}
{waktu}

<b>User Info :</b>
└ ID : {user_id}
└ Username : @{username or '-'}
└ Saldo : Rp. {saldo:,}
└ Transaksi : Rp. {total_transaksi:,}
"""

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("📦 List Produk", callback_data="produk"))
    markup.row(types.InlineKeyboardButton("✨ Produk Populer", callback_data="populer"),
               types.InlineKeyboardButton("❓ Cara Order", callback_data="cara"))
    markup.row(types.InlineKeyboardButton("💰 Isi Saldo", callback_data="topup"))
    markup.row(types.InlineKeyboardButton("🎁 Klaim Saldo", callback_data="claim"))


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

    elif call.data.startswith("saldo_"):
        _, pid, jumlah, idco = call.data.split("_", 3)
        pay_saldo(call, uid, int(pid), int(jumlah), idco)

    elif call.data.startswith("qris_"):
        _, pid, jumlah, idco = call.data.split("_", 3)
        pay_qris(call, uid, int(pid), int(jumlah), idco)

    elif call.data.startswith("paid_topup_"):
        # Format callback: paid_topup_<nominal>_<idco>
        parts = call.data.split("_", 3)
        if len(parts) < 4:
            bot.answer_callback_query(call.id, "❌ Callback error.", show_alert=True)
            return
        _, _, nominal, idco = parts
        handle_topup_paid(call, uid, int(nominal), idco)

    elif call.data.startswith("paid_"):
        paid_order(call)

    elif call.data == "cara":
        safe_delete(call.message.chat.id, uid)
        cara_order(call.message, uid)

    elif call.data == "populer":
        safe_delete(call.message.chat.id, uid)
        populer(call.message, uid)

    elif call.data == "topup":
        safe_delete(call.message.chat.id, uid)
        topup_saldo(call.message, uid)

    elif call.data == "claim":
        # Minta user input kode voucher
        prompt = bot.send_message(
            call.message.chat.id,
            "🎁 Masukkan kode klaim saldo (1x klaim per akun):"
        )
        bot.register_next_step_handler(prompt, process_claim_code, uid)


# --- LIST PRODUK ---
def list_produk(message, uid):
    conn = sqlite3.connect("store.db"); c = conn.cursor()
    c.execute("SELECT id, nama, harga, stok FROM products")
    rows = c.fetchall(); conn.close()

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

    if not produk: return
    nama, harga, stok, deskripsi = produk

    if stok <= 0:
        bot.answer_callback_query(call.id, "❌ Mohon maaf, stok tidak tersedia.", show_alert=True)
        return

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
    pid, change = int(pid), int(change)

    conn = sqlite3.connect("store.db")
    c = conn.cursor()
    c.execute("SELECT nama, harga, stok, deskripsi FROM products WHERE id=?", (pid,))
    produk = c.fetchone()
    conn.close()

    if not produk: return
    nama, harga, stok, deskripsi = produk

    jumlah = 1
    if "Jumlah" in call.message.text:
        try:
            jumlah_line = [line for line in call.message.text.splitlines() if "Jumlah" in line][0]
            jumlah = int(jumlah_line.split(":")[1].strip())
        except: jumlah = 1

    jumlah = max(1, jumlah + change)
    if jumlah > stok:
        bot.answer_callback_query(call.id, "❌ Jumlah melebihi stok yang tersedia.", show_alert=True)
        jumlah = stok

    total = harga * jumlah

    teks = f"""
🛒 <b>Pesanan</b>
Produk : {nama}
Deskripsi : {deskripsi}

Harga  : Rp {harga:,}
Jumlah : {jumlah}
Total  : Rp {total:,}

Tekan ( - ) untuk mengurangi jumlah pcs
Tekan ( + ) untuk menambah jumlah pcs
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
    pid, jumlah = int(pid), int(jumlah)

    conn = sqlite3.connect("store.db")
    c = conn.cursor()
    c.execute("SELECT nama, harga FROM products WHERE id=?", (pid,))
    produk = c.fetchone(); conn.close()
    if not produk: return
    nama, harga = produk

    total = harga * jumlah
    now = wib_now()
    idco = f"PINN{now.strftime('%H.%M.%S/%d/%m/%y')}"


    caption = f"""
<b>Konfirmasi Pesanan ✅</b>
Produk : {nama}
Jumlah : {jumlah}
Total  : Rp {total:,}
IDCO   : {idco}

Pilih metode pembayaran:
"""
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("💰 Bayar dengan Saldo", callback_data=f"saldo_{pid}_{jumlah}_{idco}"),
        types.InlineKeyboardButton("📷 Bayar dengan QRIS", callback_data=f"qris_{pid}_{jumlah}_{idco}")
    )

    sent = bot.send_message(call.message.chat.id, caption, reply_markup=markup)
    last_message[uid] = sent.message_id


# --- BAYAR DENGAN SALDO ---
def pay_saldo(call, uid, pid, jumlah, idco):
    conn = sqlite3.connect("store.db"); c = conn.cursor()
    c.execute("SELECT nama,harga FROM products WHERE id=?", (pid,))
    produk = c.fetchone()
    if not produk: conn.close(); return
    nama, harga = produk
    total = harga * jumlah

    c.execute("SELECT saldo FROM users WHERE id=?", (uid,))
    row = c.fetchone(); saldo = row[0] if row else 0

    if saldo < total:
        bot.send_message(call.message.chat.id, "❌ Saldo anda tidak cukup. Silakan gunakan metode pembayaran lainnya.")
        conn.close(); return

    c.execute("UPDATE users SET saldo = saldo - ? WHERE id=?", (total, uid))
    conn.commit(); conn.close()

    waktu = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    caption_admin = f"""
📦 <b>Dibayar dengan saldo</b>
User: {uid}
Produk: {nama}
Jumlah: {jumlah}
Total: Rp {total:,}
IDCO: {idco}
"""
    sent_admin = bot.send_message(ADMIN_ID, caption_admin)
    order_map[sent_admin.message_id] = {
        "buyer": uid, "produk": nama, "jumlah": jumlah,
        "harga": harga, "total": total, "idco": idco, "waktu": waktu, "type": "saldo"
    }

    teks_user = f"""
🙏 <b>Pembayaran dengan saldo berhasil!</b>

IDCO: {idco}
Produk: {nama}
Jumlah: {jumlah}
Total: Rp {total:,}

Status: Menunggu validasi admin ⏳
"""
    bot.send_message(uid, teks_user)


# --- BAYAR DENGAN QRIS ---
def pay_qris(call, uid, pid, jumlah, idco):
    conn = sqlite3.connect("store.db"); c = conn.cursor()
    c.execute("SELECT nama, harga FROM products WHERE id=?", (pid,))
    produk = c.fetchone(); conn.close()
    if not produk: return
    nama, harga = produk; total = harga * jumlah

    # === Generate QRIS Dinamis ===
    payload = inject_amount_and_recalc(BASE_PAYLOAD, total)
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={payload}"
    img_data = requests.get(qr_url).content

    caption = f"""
<b>Konfirmasi Pesanan ✅</b>
Produk : {nama}
Jumlah : {jumlah}
Total  : Rp {total:,}
IDCO   : {idco}

Silakan bayar dengan scan QRIS berikut ⬇️
"""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📤 Saya Sudah Bayar", callback_data=f"paid_{pid}_{jumlah}_{idco}"))

    sent = bot.send_photo(call.message.chat.id, io.BytesIO(img_data), caption=caption, reply_markup=markup)
    last_message[uid] = sent.message_id

    pending_payment[uid] = {
        "produk": nama, "jumlah": jumlah, "idco": idco,
        "harga": harga, "total": total, "msg_id": sent.message_id, "type": "qris"
    }


# --- SAAT USER KLIK "SAYA SUDAH BAYAR" ---
def paid_order(call):
    user_id = call.from_user.id
    if user_id not in pending_payment:
        bot.send_message(call.message.chat.id, "Tidak ada pesanan yang menunggu pembayaran.")
        return
    bot.send_message(call.message.chat.id, "📤 Silakan upload bukti transaksi (foto/screenshot pembayaran).")


# --- TOPUP SALDO ---
def topup_saldo(message, uid):
    teks = "💰 Masukkan nominal saldo yang ingin diisi:"
    sent = bot.send_message(message.chat.id, teks)
    last_message[uid] = sent.message_id
    bot.register_next_step_handler(sent, process_topup_nominal, uid)

def process_topup_nominal(message, uid):
    try:
        nominal = int(message.text)
    except:
        bot.send_message(message.chat.id, "❌ Nominal tidak valid.")
        return

    now = datetime.datetime.now()
    idco = f"TOPUP{now.strftime('%H.%M.%S/%d/%m/%y')}"

    # === Generate QRIS Dinamis ===
    payload = inject_amount_and_recalc(BASE_PAYLOAD, nominal)
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={payload}"
    img_data = requests.get(qr_url).content

    caption = f"""
<b>Topup Saldo</b>
IDCO : {idco}
Nominal : Rp {nominal:,}

Silakan bayar dengan scan QRIS berikut ⬇️
"""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📤 Saya Sudah Bayar", callback_data=f"paid_topup_{nominal}_{idco}"))

    sent = bot.send_photo(message.chat.id, io.BytesIO(img_data), caption=caption, reply_markup=markup)
    pending_payment[uid] = {
        "type": "topup", "nominal": nominal, "idco": idco,
        "msg_id": sent.message_id, "waktu": now.strftime("%Y-%m-%d %H:%M:%S")
    }


def handle_topup_paid(call, uid, nominal, idco):
    teks = f"""
📤 Silakan upload bukti pembayaran untuk topup saldo sebesar Rp {nominal:,}
IDCO : {idco}
"""
    bot.send_message(call.message.chat.id, teks)

    pending_payment[uid] = {
        "type": "topup", "nominal": nominal, "idco": idco,
        "msg_id": call.message.message_id,
        "waktu": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


# --- HANDLE GAMBAR BUKTI PEMBAYARAN (produk & topup) ---
@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    user_id = message.from_user.id
    if user_id not in pending_payment:
        return

    order = pending_payment[user_id]
    order["waktu"] = wib_now().strftime("%Y-%m-%d %H:%M:%S")


    try:
        bot.delete_message(message.chat.id, order["msg_id"])
    except: pass

    if order["type"] == "topup":
        teks_user = f"""
🙏 <b>Terima kasih!</b>
Topup saldo sebesar Rp {order['nominal']:,} sedang menunggu validasi admin.
IDCO : {order['idco']}
Status : Proses Validasi ⏳
"""
        bot.reply_to(message, teks_user)

        caption_admin = f"""
📥 <b>TOPUP MASUK</b>
ID : {user_id}
Username : @{message.from_user.username}
IDCO : {order['idco']}

Nominal : Rp {order['nominal']:,}
Checkout time : {order['waktu']}
"""
        sent_admin = bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=caption_admin)
        order_map[sent_admin.message_id] = {**order, "buyer": user_id, "type": "topup"}

    else:
        teks_user = f"""
🙏 <b>Terima kasih telah melakukan pembayaran!</b>

IDCO : {order['idco']}
Produk Item : {order['produk']}
Status : Proses Validasi ⏳

Produk akan otomatis dikirim setelah validasi admin berhasil.
"""
        bot.reply_to(message, teks_user)

        caption_admin = f"""
📥 <b>PESANAN MASUK</b>
ID : {user_id}
Username : @{message.from_user.username}
IDCO : {order['idco']}

Produk : {order['produk']}
Jumlah : {order['jumlah']}
Total  : Rp {order['total']:,}
Checkout time : {order['waktu']}
"""
        sent_admin = bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=caption_admin)
        order_map[sent_admin.message_id] = {**order, "buyer": user_id, "type": "qris"}

    del pending_payment[user_id]


# --- HANDLE REPLY ADMIN (produk & topup) ---
@bot.message_handler(func=lambda m: m.chat.id == ADMIN_ID and m.reply_to_message,
                     content_types=["text","photo","document","video","audio","voice"])
def handle_admin_reply(message):
    ref = order_map.get(message.reply_to_message.message_id)
    if not ref: return
    buyer = ref["buyer"]

    if ref["type"] == "topup":
        # --- ADMIN ACC SALDO ---
        if message.content_type == "text" and message.text.lower().startswith("acc"):
            try:
                nominal_acc = int(message.text.split()[1])
            except:
                bot.reply_to(message, "❌ Format salah. Gunakan: acc 5000")
                return

            conn = sqlite3.connect("store.db"); c = conn.cursor()
            c.execute("UPDATE users SET saldo = saldo + ? WHERE id=?", (nominal_acc, buyer))
            conn.commit(); conn.close()

            bot.send_message(buyer, f"✅ Saldo anda telah ditambahkan Rp {nominal_acc:,}\nIDCO: {ref['idco']}\nStatus: Topup Selesai")
            bot.reply_to(message, f"✅ Topup Rp {nominal_acc:,} berhasil di-ACC.")
        else:
            bot.reply_to(message, "❌ Untuk validasi topup gunakan format: acc 5000")

    else:
        # --- PRODUK ORDER ---
        header = f"""
✅ <b>Transaksi selesai!</b>
Produk: {ref['produk']}
IDCO: {ref['idco']}

<b>Data Produk Item :</b>
"""
        footer = "\n\nTerima kasih telah bertransaksi bersama kami\nPINNSTUDIO"

        if message.content_type == "text":
            bot.send_message(buyer, header + "\n" + message.text + footer)
        elif message.content_type == "document":
            bot.send_document(buyer, message.document.file_id, caption=header+footer)
        elif message.content_type == "photo":
            bot.send_photo(buyer, message.photo[-1].file_id, caption=header+footer)
        elif message.content_type == "video":
            bot.send_video(buyer, message.video.file_id, caption=header+footer)
        elif message.content_type == "audio":
            bot.send_audio(buyer, message.audio.file_id, caption=header+footer)
        elif message.content_type == "voice":
            bot.send_voice(buyer, message.voice.file_id, caption=header+footer)

        # Catat transaksi produk
        conn = sqlite3.connect("store.db"); c = conn.cursor()
        c.execute("UPDATE products SET stok = stok - ? WHERE nama=?", (ref["jumlah"], ref["produk"]))
        c.execute("""INSERT INTO orders (user_id, produk, jumlah, total_harga, status, idco, waktu)
                     VALUES (?,?,?,?,?,?,?)""",
                  (buyer, ref["produk"], ref["jumlah"], ref["total"], "selesai", ref["idco"], ref["waktu"]))
        c.execute("UPDATE users SET total_transaksi = total_transaksi + ? WHERE id=?", (ref["total"], buyer))
        conn.commit(); conn.close()

    del order_map[message.reply_to_message.message_id]

@bot.message_handler(commands=["addproduk"])
def add_produk(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        data = message.text.replace("/addproduk ", "")
        parts = [x.strip() for x in data.split("|", 3)]  # <- cuma pecah jadi 4 bagian maksimal
        if len(parts) < 4:
            bot.reply_to(message, "❌ Format salah. Contoh:\n/addproduk Nama | 10000 | 50 | Deskripsi")
            return

        nama, harga, stok, deskripsi = parts
        harga, stok = int(harga), int(stok)

        conn = sqlite3.connect("store.db"); c = conn.cursor()
        c.execute("INSERT INTO products (nama, harga, stok, deskripsi) VALUES (?,?,?,?)",
                  (nama, harga, stok, deskripsi))
        conn.commit(); conn.close()

        bot.reply_to(message, f"✅ Produk '{nama}' berhasil ditambahkan.\nHarga: {harga}\nStok: {stok}")
    except Exception as e:
        bot.reply_to(message, f"❌ Gagal menambahkan produk.\nError: {e}")

@bot.message_handler(commands=["updateharga"])
def update_harga(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        _, pid, harga = message.text.split()
        pid, harga = int(pid), int(harga)

        conn = sqlite3.connect("store.db"); c = conn.cursor()
        c.execute("UPDATE products SET harga=? WHERE id=?", (harga, pid))
        conn.commit(); conn.close()

        bot.reply_to(message, f"✅ Harga produk ID {pid} berhasil diupdate menjadi Rp {harga:,}")
    except:
        bot.reply_to(message, "❌ Format salah. Contoh: /updateharga 1 20000")


@bot.message_handler(commands=["updatestok"])
def update_stok(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        _, pid, stok = message.text.split()
        pid, stok = int(pid), int(stok)

        conn = sqlite3.connect("store.db"); c = conn.cursor()
        c.execute("UPDATE products SET stok=? WHERE id=?", (stok, pid))
        conn.commit(); conn.close()

        bot.reply_to(message, f"✅ Stok produk ID {pid} berhasil diupdate menjadi {stok}")
    except:
        bot.reply_to(message, "❌ Format salah. Contoh: /updatestok 1 50")

@bot.message_handler(commands=["bc"])
def broadcast(message):
    if message.from_user.id != ADMIN_ID:
        return

    text = message.text.replace("/bc", "").strip()
    if not text:
        bot.reply_to(message, "❌ Isi pesan broadcast kosong.")
        return

    # --- Deteksi parse mode otomatis ---
    if any(tag in text for tag in ["<b>", "<i>", "<u>", "</b>", "</i>", "</u>"]):
        parse_mode = "HTML"
    elif "**" in text or "__" in text:
        parse_mode = "Markdown"
    else:
        parse_mode = None

    # --- Ambil semua user dari DB ---
    conn = sqlite3.connect("store.db"); c = conn.cursor()
    c.execute("SELECT id FROM users")
    users = [row[0] for row in c.fetchall()]
    conn.close()

    sent_count = 0
    for uid in users:
        try:
            if parse_mode:
                bot.send_message(uid, f"📢 Broadcast:\n\n{text}", parse_mode=parse_mode)
            else:
                bot.send_message(uid, f"📢 Broadcast:\n\n{text}")
            sent_count += 1
        except Exception as e:
            print(f"Gagal kirim ke {uid}: {e}")

    bot.reply_to(message, f"✅ Broadcast terkirim ke {sent_count} user.")

# --- editproduk ---
@bot.message_handler(commands=["editproduk"])
def edit_produk(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        # Ambil teks setelah command
        data = message.text.replace("/editproduk ", "")
        pid, nama, deskripsi = [x.strip() for x in data.split("|")]

        pid = int(pid)

        conn = sqlite3.connect("store.db")
        c = conn.cursor()
        c.execute("UPDATE products SET nama=?, deskripsi=? WHERE id=?", (nama, deskripsi, pid))
        conn.commit(); conn.close()

        bot.reply_to(message, f"✅ Produk ID {pid} berhasil diupdate.\nNama: {nama}\nDeskripsi: {deskripsi}")
    except Exception as e:
        bot.reply_to(message, f"❌ Format salah.\nGunakan:\n/editproduk <id> | <nama> | <deskripsi>\nError: {e}")


# --- CARA ORDER ---
def cara_order(message, uid):
    teks = """
❓ <b>Cara Order</b>
1. Klik 📦 List Produk
2. Pilih produk & jumlah
3. Pilih metode pembayaran (Saldo / QRIS)
4. Jika QRIS → upload bukti bayar
5. Admin validasi → produk dikirim
"""
    sent = bot.send_message(message.chat.id, teks)
    last_message[uid] = sent.message_id


# --- PRODUK POPULER ---
def populer(message, uid=None):
    conn = sqlite3.connect("store.db"); c = conn.cursor()
    c.execute("""SELECT produk, COUNT(*) as total_jual
                 FROM orders WHERE status='selesai'
                 GROUP BY produk ORDER BY total_jual DESC""")
    rows = c.fetchall(); conn.close()

    if not rows:
        teks = "Belum ada penjualan."
    else:
        teks = "✨ <b>Produk Populer</b>\n\n"
        for r in rows:
            teks += f"{r[0]} - {r[1]} penjualan\n"

    sent = bot.send_message(message.chat.id, teks)
    if uid:
        last_message[uid] = sent.message_id

# --- Waktu WIB + format Indonesia ---
def wib_now():
    return datetime.datetime.now(ZoneInfo("Asia/Jakarta"))

def format_wib(dt=None):
    if dt is None:
        dt = wib_now()
    hari = ["Senin","Selasa","Rabu","Kamis","Jumat","Sabtu","Minggu"][dt.weekday()]
    bulan = ["Januari","Februari","Maret","April","Mei","Juni","Juli",
             "Agustus","September","Oktober","November","Desember"][dt.month-1]
    return f"{hari} , {dt.day:02d} {bulan} {dt.year} 🕒 {dt.strftime('%H:%M:%S')}"

def ensure_schema():
    conn = sqlite3.connect("store.db")
    c = conn.cursor()
    # tambahkan kolom claimed_voucher kalau belum ada
    try:
        c.execute("ALTER TABLE users ADD COLUMN claimed_voucher INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # kolom sudah ada
    conn.commit()
    conn.close()

# panggil ini sebelum bot.infinity_polling()
ensure_schema()

def process_claim_code(message, uid):
    kode = (message.text or "").strip().upper()
    KODE_RESmi = "CLAIM5KPSBO"
    NOMINAL = 5000

    if kode != KODE_RESmi:
        bot.reply_to(message, "❌ Kode voucher tidak valid.")
        return

    # transaksi aman 1x klaim
    conn = sqlite3.connect("store.db")
    c = conn.cursor()
    # pastikan user ada
    c.execute("INSERT OR IGNORE INTO users (id, username, total_transaksi, saldo) VALUES (?,?,?,?)",
              (uid, message.from_user.username, 0, 0))
    # update hanya jika belum pernah klaim
    c.execute("""
        UPDATE users
           SET saldo = saldo + ?, claimed_voucher = 1
         WHERE id = ? AND (claimed_voucher IS NULL OR claimed_voucher = 0)
    """, (NOMINAL, uid))
    changed = c.rowcount
    # ambil saldo terkini
    c.execute("SELECT saldo FROM users WHERE id=?", (uid,))
    saldo_now = (c.fetchone() or [0])[0]
    conn.commit()
    conn.close()

    if changed == 0:
        bot.reply_to(message, "⚠️ Kamu sudah pernah klaim voucher ini. Satu akun hanya bisa 1x ya.")
        return

    # sukses klaim
    waktu_klaim = format_wib()
    bot.reply_to(message,
                 f"✅ Klaim saldo berhasil!\n"
                 f"Saldo bertambah Rp {NOMINAL:,}.\n"
                 f"Saldo sekarang: Rp {saldo_now:,}\n"
                 f"Waktu: {waktu_klaim}")

    # notifikasi ke admin
    uname = f"@{message.from_user.username}" if message.from_user.username else "@-"
    notif = (
        "📥 <b>KLAIM VOUCHER BERHASIL</b>\n"
        f"Username : {uname}\n"
        f"ID : {uid}\n"
        f"Telah Claim Saldo 5k dengan kode {KODE_RESmi}\n"
        f"Waktu Klaim : {waktu_klaim}"
    )
    try:
        bot.send_message(ADMIN_ID, notif)
    except:
        pass

# --- ADMIN COMMAND: LIHAT USER ---
@bot.message_handler(commands=["user"])
def list_users(message):
    if message.from_user.id != ADMIN_ID:
        return  # hanya admin

    conn = sqlite3.connect("store.db"); c = conn.cursor()
    c.execute("SELECT id, username FROM users")
    rows = c.fetchall()
    conn.close()

    if not rows:
        bot.reply_to(message, "❌ Belum ada pengguna yang terdaftar.")
        return

    total = len(rows)
    teks = f"👥 <b>{total} pengguna bot</b>\n\n"
    for uid, uname in rows:
        teks += f"| {uid} | @{uname or '-'} |\n"

    bot.send_message(message.chat.id, teks)

# --- ADMIN COMMAND: CEK TRANSAKSI ---
@bot.message_handler(commands=["cek"])
def cek_transaksi(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Kamu tidak punya akses ke perintah ini.")
        return

    conn = sqlite3.connect("store.db"); c = conn.cursor()
    c.execute("SELECT DISTINCT user_id FROM orders WHERE status='selesai'")
    user_ids = [row[0] for row in c.fetchall()]

    if not user_ids:
        bot.reply_to(message, "Belum ada transaksi selesai.")
        conn.close()
        return

    teks = "📊 Daftar Transaksi Selesai\n\n"
    for uid in user_ids:
        c.execute("SELECT username FROM users WHERE id=?", (uid,))
        username = c.fetchone(); username = username[0] if username and username[0] else "-"
        c.execute("SELECT produk, idco, total_harga FROM orders WHERE user_id=? AND status='selesai'", (uid,))
        trans = c.fetchall()
        teks += f"User Info:\n- ID: {uid}\n- Username: @{username}\n- Transaksi: {len(trans)}\n"
        for t in trans:
            teks += f"  • {t[0]} | {t[1]} | Rp {t[2]:,} | Selesai\n"
        teks += "\n"

    conn.close()

    # --- Jika teks terlalu panjang, kirim file ---
    if len(teks) > 3000:  # batas aman
        filename = "laporan_cek.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(teks)

        with open(filename, "rb") as f:
            bot.send_document(message.chat.id, f, caption="📂 Laporan transaksi (file karena teks terlalu panjang)")
    else:
        bot.send_message(message.chat.id, teks)

# --- ADMIN COMMAND: HAPUS PRODUK ---
@bot.message_handler(commands=["hapus"])
def hapus_produk(message):
    if message.from_user.id != ADMIN_ID:
        return  # hanya admin

    try:
        # format: /hapus <id_produk>
        _, pid = message.text.split()
        pid = int(pid)

        conn = sqlite3.connect("store.db")
        c = conn.cursor()

        # cek apakah produk ada
        c.execute("SELECT nama FROM products WHERE id=?", (pid,))
        row = c.fetchone()
        if not row:
            bot.reply_to(message, f"❌ Produk dengan ID {pid} tidak ditemukan.")
            conn.close()
            return

        nama = row[0]
        c.execute("DELETE FROM products WHERE id=?", (pid,))
        conn.commit(); conn.close()

        bot.reply_to(message, f"🗑️ Produk '{nama}' (ID {pid}) berhasil dihapus.")
    except Exception as e:
        bot.reply_to(message, f"❌ Format salah. Gunakan:\n/hapus <id_produk>\nError: {e}")


print("🤖 Bot berjalan...")
bot.infinity_polling()
