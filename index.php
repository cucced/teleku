<?php
session_start();
$db = new SQLite3('store.db');

// --- Login sederhana ---
if (isset($_POST['login'])) {
    if ($_POST['username'] === 'xpinn') {
        $_SESSION['admin'] = true;
    }
}
if (isset($_GET['logout'])) {
    session_destroy();
    header("Location: index.php");
    exit;
}

if (!isset($_SESSION['admin'])) {
?>
<form method="post">
  <input type="text" name="username" placeholder="ID Admin">
  <button type="submit" name="login">Login</button>
</form>
<?php exit; } ?>

<h2>📦 Dashboard Admin</h2>
<a href="?logout=1">Logout</a>

<h3>Tambah Produk</h3>
<form method="post">
  <input type="text" name="nama" placeholder="Nama">
  <input type="number" name="harga" placeholder="Harga">
  <input type="number" name="stok" placeholder="Stok">
  <input type="text" name="deskripsi" placeholder="Deskripsi">
  <button type="submit" name="add">Tambah</button>
</form>

<?php
if (isset($_POST['add'])) {
    $db->exec("INSERT INTO products (nama,harga,stok,deskripsi) VALUES ('{$_POST['nama']}',{$_POST['harga']},{$_POST['stok']},'{$_POST['deskripsi']}')");
}

// Hapus produk
if (isset($_GET['del'])) {
    $db->exec("DELETE FROM products WHERE id=".(int)$_GET['del']);
}

// Daftar produk
$res = $db->query("SELECT * FROM products");
echo "<h3>Daftar Produk</h3><table border=1><tr><th>ID</th><th>Nama</th><th>Harga</th><th>Stok</th><th>Deskripsi</th><th>Aksi</th></tr>";
while ($r = $res->fetchArray(SQLITE3_ASSOC)) {
    echo "<tr>
        <td>{$r['id']}</td>
        <td>{$r['nama']}</td>
        <td>{$r['harga']}</td>
        <td>{$r['stok']}</td>
        <td>{$r['deskripsi']}</td>
        <td><a href='?del={$r['id']}'>Hapus</a></td>
    </tr>";
}
echo "</table>";

// Produk populer
$res = $db->query("SELECT produk, COUNT(*) as total FROM orders WHERE status='selesai' GROUP BY produk ORDER BY total DESC");
echo "<h3>✨ Produk Populer</h3><ul>";
while ($r = $res->fetchArray(SQLITE3_ASSOC)) {
    echo "<li>{$r['produk']} - {$r['total']} penjualan</li>";
}
echo "</ul>";
?>
