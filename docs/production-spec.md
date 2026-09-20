# Üretim Ortamı — Çok Kullanıcılı Beta

Hedef: 4 beta kullanıcısı, her biri kendi Etsy mağazasıyla, birbirlerinin verisini göremeden.

> **Kapasite:** Personal App en fazla 5 mağazaya bağlanabilir. 4 beta kullanıcısı + kuzenin = 5. Bu tavan. Altıncı kullanıcı için Commercial Access şart.

> **Ücret alınmayacak.** ToU Bölüm 4 gereği Commercial Access onayı olmadan ücretlendirme yapılamaz. Bu beta ücretsizdir.

---

## A. KİMLİK DOĞRULAMA

### A1. Kayıt — yalnızca davetle

Herkese açık kayıt yok. Sen davet ediyorsun.

- `invite_code` tablosu: kod, oluşturan, kullanan (nullable), oluşturma ve kullanım tarihi, son kullanma tarihi
- Kayıt formu davet kodu ister; geçersiz veya kullanılmış kod reddedilir
- Bir kod bir kez kullanılır
- Kod üretimi: CLI komutu veya basit bir yönetici endpoint'i (`ADMIN_TOKEN` ile korumalı)

E-posta servisi kurmuyoruz — kodu kullanıcıya sen elden veriyorsun.

### A2. Giriş

- E-posta + şifre
- Şifre hash'i **argon2id** (`argon2-cffi`), mevcut `tenant.password_hash` alanı kullanılır
- Minimum şifre uzunluğu 12 karakter, yaygın şifre listesi kontrolü
- Başarısız giriş denemeleri: IP ve e-posta bazında oran sınırı (5 deneme / 15 dakika), aşılınca 15 dakika kilit

### A3. Oturum

- Sunucu tarafı oturum, Redis'te saklanır (`session:{token}` → tenant_id, oluşturma, son erişim)
- Çerez: `HttpOnly`, `Secure`, `SameSite=Lax`, 30 gün ömür, her istekte kayan yenileme
- Çıkış oturumu Redis'ten siler
- Şifre değişikliğinde o kullanıcının **tüm** oturumları geçersizleşir

JWT kullanma — sunucu tarafı oturum iptal edilebilir, JWT edilemez.

### A4. Şifre sıfırlama

E-posta servisi olmadığı için: yönetici CLI komutuyla geçici şifre üretir, kullanıcı ilk girişte değiştirmek zorunda kalır (`must_change_password` bayrağı).

---

## B. KİRACI İZOLASYONU — en kritik bölüm

Şu an sistemde sabit bir `dev@localhost` kiracısı var ve tüm istekler ona düşüyor. Kaldırılacak.

### B1. Her istekte kiracı oturumdan gelir

- `get_current_tenant` bağımlılığı: oturum çerezinden tenant_id çözer, yoksa 401
- Hiçbir endpoint sabit veya varsayılan kiracı kullanmaz
- Dev tenant get-or-create kodu **tamamen silinir**

### B2. Her sorgu kiracıya göre filtrelenir

Aşağıdaki tabloların **hepsinde** `tenant_id` filtresi zorunlu:

`etsy_connection`, `job`, `api_usage`, `listing_snapshot`, `upload_batch`, `asset`, `generated_content`, `compliance_finding`, `listing_profile`, `shop_listing_cache`, `listing_group_setting`

### B3. Sahiplik kontrolü — sessiz sızıntı riski

Kaynak id'siyle erişilen her endpoint, kaynağın istekte bulunan kiracıya ait olduğunu doğrular. Ait değilse **404** döner (403 değil — 403 kaynağın varlığını sızdırır).

Özellikle kontrol edilecekler:
- `GET /api/assets/{id}/image` — şu an muhtemelen kontrol etmiyor, tasarım sızıntısı riski
- `GET /api/batches/{id}` ve alt yolları
- `GET /api/content/{id}` ve alt yolları
- `GET /api/jobs/{id}`
- `PATCH /api/profiles/{id}` ve tüm profil endpoint'leri
- `POST /api/shop/listings/{id}/replace-images`

### B4. Depolama izolasyonu

Dosya yolları kiracı id'si ile öneklenir: `/data/storage/{tenant_id}/...`. Mevcut veri taşınır veya beta öncesi temizlenir.

### B5. Kuyruk izolasyonu

Her iş `tenant_id` taşır; worker işi çalıştırmadan önce kaynağın o kiracıya ait olduğunu doğrular.

### B6. Testler — bu bölüm için zorunlu

İki kiracı oluşturup **her** endpoint için çapraz erişim denenir:
- A kiracısı B'nin batch'ini göremez (404)
- A kiracısı B'nin görselini indiremez (404)
- A kiracısı B'nin profilini düzenleyemez (404)
- A kiracısının kotası B'ninkinden bağımsız
- Oturumsuz istek her endpoint'te 401

Bu testler geçmeden üretime çıkılmaz.

---

## C. ETSY BAĞLANTISI

- OAuth akışı giriş yapmış kullanıcıya bağlanır; `state` değeri oturumla ilişkilendirilir
- Her kiracı yalnızca kendi `etsy_connection` kaydını görür ve yönetir
- Bir kiracı birden fazla mağaza bağlayamaz (beta kısıtı, basitlik için)
- Bağlantı kesildiğinde o kiracıya ait Etsy kaynaklı tüm içerik silinir (CLAUDE.md cache kuralı)

### Global kota paylaşımı

Günlük 5.000 çağrı tüm kiracılar arasında paylaşılıyor. Mevcut `DailyQuota` bunu zaten yapıyor ama beta için:

- Kiracı başına günlük tavan: 1.000 (5 kullanıcı × 1.000 = 5.000)
- Global kota %90'a ulaştığında yeni işler ertelenir ve kullanıcıya bildirilir
- Arayüzde hem kiracı hem global kota görünür (zaten var)

---

## D. GÜVENLİK SERTLEŞTİRME

- **CSRF:** durum değiştiren tüm isteklerde çift gönderim çerezi veya `Origin` başlığı doğrulaması
- **Güvenlik başlıkları:** `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, temel bir `Content-Security-Policy`
- **CORS:** yalnızca `https://listyro.com`, joker yok
- **Oran sınırı:** kimlik endpoint'lerinde sıkı, diğerlerinde makul bir tavan
- **Yükleme doğrulaması:** dosya tipi içerikten doğrulanır (uzantıya güvenilmez), dosya başına ve batch başına boyut sınırı
- **Loglar:** token, şifre, oturum çerezi, e-posta adresi loglanmaz
- **Hata mesajları:** iç detay sızdırmaz; 500 hataları kullanıcıya genel mesaj, loga tam iz

---

## E. ZORUNLU İÇERİK

ToU gereği ve beta kullanıcıları gerçek kişiler olduğu için artık placeholder kabul edilemez.

### E1. Kullanım şartları

Gerçek metin yazılacak. En az şunları içermeli: hizmetin ne olduğu, beta statüsü ve garanti verilmediği, kullanıcının kendi Etsy hesabından sorumlu olduğu, verinin nasıl işlendiği, hizmetin durdurulabileceği, uyuşmazlık ve uygulanacak hukuk.

ToU'nun istediği garanti reddi metni aynen yer alacak.

### E2. Gizlilik politikası

Hangi veri toplanıyor (e-posta, Etsy token'ları, yüklenen görseller, üretilen içerik), nerede saklanıyor, ne kadar süre, kimlerle paylaşılıyor (Anthropic API'si — yalnızca kullanıcının kendi tasarım görselleri), kullanıcının hakları, silme talebi nasıl yapılır.

**Anthropic'e görsel gönderildiği açıkça belirtilecek.** Kullanıcı bunu bilmeli.

### E3. Beta bildirimi

Giriş ve kayıt ekranında: bu ürün beta aşamasındadır, hata içerebilir, ücretsizdir, veri kaybı ihtimaline karşı kullanıcının kendi yedeğini tutması önerilir.

---

## F. DAĞITIM

Kod artık geliştirme makinesinde değil, sabit sunucuda çalışacak.

### F1. Sunucu

- Ubuntu 24.04, Docker + Docker Compose
- `ufw`: yalnızca 22 açık (Cloudflare Tunnel kullanıldığı için 80/443'e bile gerek yok)
- SSH: anahtar ile giriş, şifreli giriş kapalı, root girişi kapalı
- Otomatik güvenlik güncellemeleri (`unattended-upgrades`)

### F2. Cloudflare Tunnel

- `cloudflared` sunucuda servis olarak çalışır
- `listyro.com` → frontend, `api.listyro.com` → backend
- Geliştirme makinesindeki tünel durdurulur

### F3. Production `.env`

Geliştirme ortamından **farklı** olacaklar:
- `POSTGRES_PASSWORD` — yeni, güçlü
- `SECRET_KEY` — yeni
- `ENCRYPTION_KEY` — yeni üretilir ve **yedeklenir**; kaybedilirse tüm Etsy token'ları okunamaz
- `SESSION_SECRET` — yeni
- `ADMIN_TOKEN` — davet kodu üretimi için
- `APP_ENV=production`
- `CORS_ORIGINS=https://listyro.com`
- `ETSY_REDIRECT_URI=https://api.listyro.com/api/auth/etsy/callback`
- `FRONTEND_URL=https://listyro.com`

`uvicorn --reload` kapatılır.

### F4. Etsy Developer Portal

Production callback URL'inin kayıtlı olduğu doğrulanır.

### F5. Yedekleme

- Günlük `pg_dump`, cron ile, 14 gün saklanır
- Depolama dizini haftalık arşivlenir
- Yedekler sunucu dışına kopyalanır (geliştirme makinesine veya bir bulut deposuna)
- **Geri yükleme bir kez denenir** — denenmemiş yedek yedek değildir

### F6. İzleme

- Konteyner sağlık kontrolleri ve otomatik yeniden başlatma (`restart: unless-stopped`)
- Sentry veya benzeri bir hata takibi (ücretsiz katman yeter)
- Disk doluluk uyarısı — yüklenen görseller birikir
- Basit bir uptime kontrolü

---

## G. VERİ TEMİZLİĞİ

Mevcut geliştirme verisi üretime taşınmaz:

- Yeni, boş bir veritabanı
- Kuzeninin hesabı da davet koduyla yeniden oluşturulur, mağazasını yeniden bağlar
- Geliştirme ortamındaki batch'ler, profiller ve içerikler taşınmaz

Alternatif: mevcut veriyi kuzeninin yeni kiracısına taşıyan bir migration — ama temiz başlangıç daha az risk.

---

## H. ÇIKIŞ ÖNCESİ KONTROL LİSTESİ

- [ ] B6'daki tüm izolasyon testleri geçiyor
- [ ] Oturumsuz hiçbir endpoint'e erişilemiyor
- [ ] Dev tenant kodu tamamen silinmiş
- [ ] ToS ve gizlilik politikası gerçek metinlerle dolu
- [ ] Marka ibaresi, destek e-postası, kota göstergesi, Etsy geri linkleri yerinde
- [ ] `ENCRYPTION_KEY` yedeklenmiş
- [ ] Yedekten geri yükleme bir kez denenmiş
- [ ] 4 davet kodu üretilmiş
- [ ] Uçtan uca akış üretim ortamında bir kez test edilmiş
- [ ] Geliştirme makinesindeki tünel kapatılmış

---

## I. UYGULAMA SIRASI

1. Kimlik doğrulama altyapısı (A) — model, oturum, giriş/kayıt endpoint'leri
2. Kiracı izolasyonu (B) — dev tenant kaldırma, sahiplik kontrolleri, testler
3. Arayüz — giriş, kayıt, çıkış ekranları; korumalı yollar
4. Güvenlik sertleştirme (D)
5. Zorunlu içerik (E)
6. Dağıtım (F) ve temizlik (G)
7. Kontrol listesi (H)

Her adımdan sonra tam test suite'i ve uçtan uca akış kontrolü.
