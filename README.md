# SITEQ backend

API lokal berbasis Python standard library dan SQLite. Skema ada di [`schema.sql`](schema.sql); data contoh hanya dimasukkan satu kali untuk database baru.

## Menjalankan lokal

Dari folder `outputs`, siapkan akun pemilik pertama lalu mulai server:

```sh
SITEQ_ADMIN_USERNAME=pemilik SITEQ_ADMIN_PASSWORD='password-unik-minimal-12-karakter' python3 backend/server.py
```

Buka <http://127.0.0.1:8765>, lalu pilih **Pengurus → Login pengurus**. Jangan buka halaman sebagai `file://` saat memakai API. Hentikan server dengan Ctrl+C.

Akun awal hanya dibuat bila tabel pengguna masih kosong; variabel bootstrap diabaikan setelah ada pengguna. Password disimpan dengan PBKDF2-SHA256. Database default berada di `backend/data/siteq.sqlite3`; atur `SITEQ_DATABASE` untuk mengganti lokasi.

## Hak akses

- **Pemilik:** semua data, permintaan pembeli, pengelolaan akun, dan audit.
- **Petugas Kandang:** data ternak, kandang, dan permintaan pembeli.
- **Dokter Hewan:** membaca ternak/kandang dan memperbarui status kesehatan.

Pemilik membuat akun melalui menu **Pengguna & audit**. Setiap akun dapat mengganti password dari **Profil & keamanan**. Mengganti password mencabut sesi lain; menghapus akun mencabut seluruh sesinya.

## Perlindungan

Sesi acak disimpan sebagai hash di database, memakai cookie `HttpOnly` dan `SameSite=Strict`, dan berakhir setelah delapan jam. Login dibatasi setelah delapan kegagalan per alamat dalam jendela 15 menit; pengajuan pesanan dibatasi lima kali per sepuluh menit. Request perubahan harus berasal dari origin yang sama. Server menambahkan CSP, proteksi frame, dan header keamanan, serta menyimpan audit perubahan.

Secara lokal server hanya bind ke `127.0.0.1`. Untuk Docker/Caddy, set `SITEQ_HOST=0.0.0.0`, `SITEQ_COOKIE_SECURE=1`, dan `SITEQ_TRUST_PROXY=1`; konfigurasi siap pakai tersedia di [`../DEPLOY.md`](../DEPLOY.md). Jangan aktifkan kepercayaan proxy jika server menerima koneksi internet langsung.

## API

- Publik: `GET /api/health`, `GET /api/animals`, `GET /api/pens`, `POST /api/orders`.
- Sesi: `POST /api/admin/login`, `GET /api/admin/session`, `POST /api/admin/logout`, `PATCH /api/admin/profile`.
- Pemilik/Petugas: `GET /api/admin/orders`, perubahan ternak dan kandang, perubahan status permintaan.
- Pemilik: `GET /api/admin/users`, `POST /api/admin/users`, `DELETE /api/admin/users/{id}`, `GET /api/admin/audit`.
- Dokter hewan/Pemilik: `PATCH /api/vet/animals/{id}/health`.

Foto ternak dan object storage cloud belum diterapkan.
