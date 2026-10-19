# Deploy SITEQ dengan Docker dan HTTPS

Paket ini menjalankan satu server SITEQ, menyimpan SQLite pada Docker volume `siteq_data`, lalu menaruh Caddy di depannya untuk HTTPS otomatis. Domain harus sudah mengarah ke alamat publik server.

## Siapkan server

1. Siapkan VPS Linux dengan Docker Engine dan Docker Compose plugin.
2. Arahkan DNS `A` (dan `AAAA` jika IPv6 digunakan) domain ke VPS.
3. Izinkan koneksi masuk TCP 80 dan 443. UDP 443 opsional untuk HTTP/3.
4. Salin folder SITEQ ke VPS, lalu dari folder tersebut jalankan:

   ```sh
   cp .env.example .env
   ```

5. Edit `.env`: isi domain yang benar, username pemilik awal, dan password unik minimal 12 karakter. Jangan unggah atau kirim file `.env` ke repositori atau chat.
6. Jalankan:

   ```sh
   docker compose -f compose.yml up -d --build
   docker compose -f compose.yml logs -f siteq caddy
   ```

   Buka `https://DOMAIN`. Login melalui **Portal pengurus** dengan akun pemilik awal. Pemilik dapat membuat akun Petugas Kandang dan Dokter Hewan dari menu **Pengguna & audit**.

## Data dan backup

- Data SQLite tersimpan di Docker volume `siteq_data` dan bertahan saat container diperbarui.
- Untuk membuat salinan konsisten saat server tetap berjalan:

  ```sh
  docker compose -f compose.yml exec -T siteq python3 -c 'import sqlite3,sys; a=sqlite3.connect("/data/siteq.sqlite3"); b=sqlite3.connect("/tmp/siteq-backup.sqlite3"); a.backup(b); b.close(); a.close(); sys.stdout.buffer.write(open("/tmp/siteq-backup.sqlite3","rb").read())' > siteq-backup.sqlite3
  ```

- Simpan backup di lokasi lain yang aksesnya dibatasi. Uji pemulihan sebelum mengandalkan backup.

## Hak akses

| Peran | Akses |
|---|---|
| Pemilik | Semua data, permintaan pembeli, akun pengurus, dan riwayat audit |
| Petugas Kandang | Data ternak, kandang, dan permintaan pembeli |
| Dokter Hewan | Membaca inventaris/kandang dan memperbarui status kesehatan ternak |

Login dilindungi hash password PBKDF2, cookie sesi `HttpOnly`/`SameSite=Strict`, sesi delapan jam, pembatasan percobaan login dan pengajuan pesanan, pemeriksaan origin, serta audit perubahan. HTTPS mengaktifkan cookie `Secure` dan HSTS. Sesi akun lain dicabut saat password diganti atau akun dihapus.

Paket ini adalah penerapan satu server untuk prototipe/awal penggunaan; sebelum beban tinggi atau banyak server aplikasi, pindahkan API ke server WSGI/ASGI terkelola dan database yang sesuai.
