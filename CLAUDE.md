# Etsy Listing Assistant

Etsy satıcılarının kendi orijinal tasarımlarından uyumlu **taslak listing** hazırlamasına yardımcı olan çok kiracılı SaaS.

Aşama: kapalı beta, 5 kullanıcı. Etsy erişim seviyesi: Personal App.

> Personal access **en fazla 5 mağazaya** bağlanabilir. 6. kullanıcıya geçmeden veya ücret almaya başlamadan önce Commercial Access başvurusu zorunludur.

---

## MUTLAK KISITLAR — asla ihlal edilmez

Bunlar Etsy API Terms of Use'dan gelir. İhlali geliştirici hesabının ve kullanıcı mağazalarının kapatılması demektir. Bir özellik bunlardan birine dokunuyorsa **yazma, önce sor**.

1. **Scraping yasak.** Etsy sitesine yalnızca resmi Open API v3 üzerinden erişilir. Selenium, Playwright, Puppeteer, tarayıcı eklentisi, HTML parse — hiçbiri kullanılmaz. Bu yasak, **veri API'de mevcut olmasa bile** geçerlidir (ToU Bölüm 9). Etsy'nin dahili/eski API'lerine veya site içi veri akışlarına erişilmez.
2. **Rakip analizi yasak.** Başka satıcıların listingleri, tagleri, görselleri veya fiyatları toplanmaz, analiz edilmez, model eğitiminde kullanılmaz. Yalnızca kimliği doğrulanmış kullanıcının **kendi** mağaza verisine erişilir. Üye izni olmadan Etsy üyeleri hakkında kişisel veri toplanamaz/işlenemez.
3. **Otomatik yayın yok.** Her listing `createDraftListing` ile taslak olarak oluşturulur. Yayın yalnızca kullanıcının arayüzde açık onayıyla yapılır. "Auto-publish" özelliği eklenmez.
4. **Etsy Ads endpoint'i yoktur.** Reklam açma/kapama/bütçe özelliği yazılmaz.
5. **Rate limit: 5.000 istek/gün, 5 istek/saniye** — uygulama bazında, kullanıcı bazında değil (Personal App). Güvenlik payı için 4 req/s hedeflenir; global günlük bütçe 5.000.
6. **Token'lar şifreli saklanır.** Asla loglanmaz, asla hata mesajında görünmez, asla frontend'e gönderilmez.
7. **Checkout akışı taklit edilemez.** Etsy'nin ödeme/checkout deneyimini kopyalayan veya devre dışı bırakan hiçbir şey yazılmaz.
8. **Trafik başka yere yönlendirilemez.** Uygulama, kullanıcıyı veya alıcıyı Etsy dışı platformlara taşımak için kullanılamaz.
9. **API erişimi satılamaz, kiralanamaz, alt lisanslanamaz.** Ürün bir hizmet aboneliğidir; hiçbir yerde "API erişimi kiralama" olarak konumlandırılmaz. Kaynak kod veya white-label satışı yapılmaz.

## Cache ve veri saklama kuralları

ToU Bölüm 1'den gelir, ihlali doğrudan sözleşme ihlalidir.

| Veri tipi | Maksimum yaş |
|---|---|
| Listing içeriği, ürün bilgisi, görseller | **6 saat** |
| Diğer Etsy içeriği | **24 saat** |

- Etsy içeriği (Member Content) yalnızca **hizmeti sunmak için makul süre** boyunca saklanabilir. Süresiz saklama yasaktır.
- `listing_snapshot` tablosu Member Content içerir. **Retention politikası zorunlu: 90 gün sonra otomatik silinir.** Bunu yapan periyodik bir temizlik job'ı olmalıdır.
- Kullanıcı bağlantısını kestiğinde (`etsy_connection` → `revoked`) o kullanıcıya ait tüm Etsy kaynaklı içerik silinir.
- Cache yaşı arayüzde gösterilen her listing için kontrol edilir; süresi geçmişse gösterilmez, yeniden çekilir.
- **Referans görsel baytları saklanmaz.** Beden tablosu sınıflandırması için satıcının kendi referans listing görselleri API'nin verdiği CDN URL'inden çekilir, **yalnızca bellekte** sınıflandırılır (yerel sezgisel; belirsizse vision fallback) ve baytlar hemen atılır. Yalnızca **sınıflandırma sonucu** (`size_chart`/`artwork`) ve türetilen `fixed_image_ids` saklanır. Görsel baytı diske/DB'ye yazılmadığı için 6 saatlik görsel cache yükümlülüğü doğmaz; saklanan sonuç, profilin 24 saatlik `cached_payload` yenilenmesine tabidir.

## Zorunlu uyum unsurları (arayüzde bulunacak)

- **Marka ibaresi, birebir bu metin** (değiştirilmez, kısaltılmaz):
  ```
  The term 'Etsy' is a trademark of Etsy, Inc. This application uses the Etsy API but is not endorsed or certified by Etsy, Inc.
  ```
- **Geri link zorunluluğu:** ürün bilgisi veya görseli gösterilen her yerde, Etsy'deki ilgili listing'e doğrudan link verilir. Listing kartı bileşeni bu linki her zaman içerir.
- Görünür destek e-posta adresi
- Kullanım şartları + gizlilik politikası, tıklayarak kabul
- Kalan API kotasının kullanıcıya gösterilmesi
- Etsy logosu veya markası kullanılırsa, uygulamanın kendi markasından **daha az belirgin** olmalı; hiçbir şekilde onay/ortaklık ima edilmez
- Uygulama adında ve web sitesi başlığında "Etsy" kelimesi **kullanılamaz**

## Mimari kuralı

**Her Etsy API çağrısı kuyruk üzerinden geçer.** Servis katmanından doğrudan `httpx` ile Etsy'ye istek atılmaz. Tek istisna: OAuth token değişimi.

```
Job → queue → tenant kota kontrolü → global token bucket (4 req/s) → Etsy API
                                            ↓ 429
                                  exponential backoff + requeue
```

**Admin rolü kiracı izolasyonunu delmez.** `/api/admin/*` yalnızca hesap üst verisi (e-posta, bağlı mağaza adı, kayıt tarihi, yayın sayısı) ve kota görür; başka bir kiracının tasarımlarını, batch'lerini, profillerini veya üretilen içeriğini okuyan admin endpoint'i yazılmaz. Her admin endpoint'i `is_admin`'i sunucuda kontrol eder, admin olmayana 404 döner, her işlem `audit_log`'a yazılır. Admin yalnızca CLI ile atanır (`python -m app.cli create-admin`).

## Etsy API erişim notları (doğrulandı — Personal App aktif)

- **`x-api-key` başlığı `{keystring}:{shared_secret}` biçiminde gönderilir**, yalnızca keystring değil. Sadece keystring gönderilirse `openapi-ping` **403** döner. İki değer de `.env`'den (`ETSY_CLIENT_ID` = keystring, `ETSY_CLIENT_SECRET` = shared secret) okunur; asla loglanmaz.
- OAuth 2.0 PKCE: authorization + token endpoint'i client_id (keystring) ve `code_verifier` ile çalışır; token değişiminde `x-api-key` gerekmez. `x-api-key` yukarıdaki biçimde yalnızca **API v3 çağrılarında** kullanılır.
- Scope'lar: `listings_r listings_w shops_r shops_w`. Redirect URI: `http://localhost:8000/api/auth/etsy/callback`.

## Yığın

- Backend: Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic
- Kuyruk: Redis + arq
- DB: PostgreSQL
- Frontend: Next.js (App Router), TypeScript, Tailwind
- Görsel işleme: pyvips (fallback: Pillow)
- Storage: yerel Docker volume (`LocalStorage`); S3 uyumlu depolama (R2) henüz yok
- Deploy: Docker Compose + Cloudflare Tunnel, Türkiye'de tek VPS — adımlar `docs/deploy.md`

## Klasör yapısı

```
backend/
  app/
    api/          # FastAPI router'ları
    core/         # config, security, şifreleme
    db/           # modeller, session, migration
    etsy/         # API client, kuyruk, rate limiter, taksonomi
    pipeline/     # SKU parse, görsel işleme, içerik üretimi
    compliance/   # marka/duplicate/stuffing taraması
    workers/      # arq worker tanımları, retention temizliği
  tests/
frontend/
docker/
docs/             # data-model.md ve diğer spesifikasyonlar
```

## Kod kuralları

- Tüm public fonksiyonlarda type hint
- Etsy client katmanı test edilirken gerçek API'ye çağrı yapılmaz, mock kullanılır
- Migration'lar Alembic ile, elle SQL yazılmaz
- Sır/anahtar kodda tutulmaz, `.env` üzerinden okunur, `.env` gitignore'da
- Hata mesajlarında kullanıcı verisi veya token bulunmaz
- Member Content saklayan her tabloda retention stratejisi tanımlı olmalı

## Geliştirme notları

- **arq worker'ının reload'u yoktur.** `api` servisi `uvicorn --reload` ile çalıştığı için
  backend değişikliklerini anında alır; worker almaz. Job kodunu etkileyen bir değişiklikten
  sonra `docker compose restart worker` çalıştırılmalıdır — aksi halde job'lar **sessizce eski
  kodu çalıştırır** ve hata vermediği için fark edilmesi zordur. Etkilenen yerler:
  `app/workers/`, `app/pipeline/`, `app/etsy/` ve bunların kullandığı her şey.

## MVP kapsamı

**Dahil:** OAuth bağlantı, klasör yükleme, dosya adından SKU parse, görsel işleme (resize/watermark/sıra), vision ile tasarım analizi, başlık+13 tag+açıklama üretimi, taksonomi eşleme, taslak listing oluşturma, section yönetimi, toplu güncelleme, dry-run önizleme, rollback, compliance tarayıcı, retention temizliği.

**Hariç:** reklam yönetimi, rakip/tag analizi, otomatik yayın, çoklu kanal (Shopify/eBay), Etsy dışına yönlendirme.

## Ürün konumlandırma notu

ToU Bölüm 4, Etsy'nin üyelerine ücretsiz sunduğu bir işleve API üzerinden ücret koymayı yasaklar. Ürünün ücretli değeri, **API'yi entegre etmeyen** kısımlarda konumlandırılır: vision analizi, LLM içerik üretimi, görsel işleme, compliance taraması, iş akışı otomasyonu. Arayüz metinleri ve fiyatlandırma sayfası buna göre yazılır. Ücretli sürüm yalnızca Commercial Access onayından sonra açılır.

## Çalışma sırası

1. Veri modeli + migration (bkz. `docs/data-model.md`)
2. OAuth 2.0 PKCE akışı + şifreli token saklama
3. Rate limit kuyruğu ve tenant kotası
4. Etsy client (taksonomi, listing, görsel, envanter)
5. Pipeline (SKU parse → görsel → vision → içerik → taslak)
6. Dry-run + rollback + retention temizliği
7. Compliance tarayıcı
8. Frontend

Bir sonraki adıma geçmeden önce mevcut adımın testleri geçmeli.
