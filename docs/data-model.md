# Veri Modeli ve Kuyruk Spesifikasyonu

Bu belge çalışma sırasındaki **1. adım (veri modeli + migration)** ve **3. adım (rate limit kuyruğu)** için referanstır. Etsy API bağlantısı gerektirmez.

> **Öncelik notu:** Retention/veri saklama kurallarında `CLAUDE.md` bu belgeyi **geçersiz kılar**. Çelişki olursa CLAUDE.md geçerlidir (bkz. §5).

---

## 1. Tablolar

### `tenant`
Sistemdeki kullanıcı hesabı. Bir tenant birden fazla Etsy mağazası bağlayabilir (ileride).

| Alan | Tip | Not |
|---|---|---|
| id | UUID PK | |
| email | text unique | |
| password_hash | text | argon2 |
| status | enum | `active`, `suspended` |
| daily_quota | int | Varsayılan 2000, tenant başına ayarlanabilir |
| created_at | timestamptz | |
| updated_at | timestamptz | |

### `etsy_connection`
Bağlanmış Etsy mağazası ve token'ları.

| Alan | Tip | Not |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK → tenant | |
| etsy_user_id | bigint | |
| shop_id | bigint | |
| shop_name | text | |
| access_token_enc | bytea | **Şifreli.** Fernet / AES-GCM |
| refresh_token_enc | bytea | **Şifreli** |
| token_expires_at | timestamptz | |
| scopes | text[] | |
| status | enum | `active`, `expired`, `revoked` |
| connected_at | timestamptz | |

**Kural:** token alanları hiçbir log satırında, hata mesajında, API cevabında veya `__repr__` çıktısında görünmez. Model sınıfında `__repr__` açıkça override edilir.

**Kural (revocation):** `status` → `revoked` olduğunda, bu bağlantıya ait tüm Etsy kaynaklı içerik (özellikle `listing_snapshot`) silinir (CLAUDE.md). Şemada tenant'a bağlı FK'ler `ON DELETE CASCADE`'dir; bağlantı bazlı silme uygulama katmanında yürütülür.

### `job`
Kuyruğa giren her iş.

| Alan | Tip | Not |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK | |
| connection_id | UUID FK → etsy_connection | |
| type | enum | `create_draft`, `update_listing`, `upload_image`, `sync_listings`, `update_inventory` |
| payload | jsonb | İşin girdisi |
| status | enum | `queued`, `running`, `succeeded`, `failed`, `cancelled` |
| attempts | int | Varsayılan 0 |
| max_attempts | int | Varsayılan 5 |
| last_error | text | Token içermez |
| batch_id | UUID | Toplu işlemleri gruplamak için, nullable |
| scheduled_at | timestamptz | Backoff sonrası yeniden deneme zamanı |
| created_at / started_at / finished_at | timestamptz | |

İndeks: `(tenant_id, status)`, `(status, scheduled_at)`, `(batch_id)`

### `api_usage`
Günlük kota takibi. Redis'te sayaç tutulur, buraya kalıcı olarak yazılır.

| Alan | Tip | Not |
|---|---|---|
| tenant_id | UUID FK | |
| usage_date | date | UTC gün |
| request_count | int | |
| PK | (tenant_id, usage_date) | |

### `listing_snapshot`
Rollback için. Bir listing'e dokunmadan **önce** mevcut hali kaydedilir.

| Alan | Tip | Not |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK | |
| listing_id | bigint | Etsy listing id |
| job_id | UUID FK → job | Hangi iş bu değişikliği yaptı |
| payload | jsonb | Değişiklikten önceki tam hal |
| taken_at | timestamptz | |

İndeks: `(tenant_id, listing_id, taken_at DESC)`, `(taken_at)` — retention temizliği için.

> **⚠️ Retention (CLAUDE.md gereği — bu belgedeki her şeyi geçersiz kılar):**
> `listing_snapshot` **Member Content** içerir. `taken_at`'tan **90 gün** sonra periyodik bir
> temizlik job'ı ile **otomatik silinir**. Süresiz saklama yasaktır. Bu kural CLAUDE.md
> "Cache ve veri saklama kuralları" bölümünden gelir. Sabit kodda `ListingSnapshot.RETENTION_DAYS = 90`
> olarak tanımlıdır; temizlik job'ı 6. adımda (`workers/`) yazılır.

### `upload_batch`
Kullanıcının yüklediği klasör/dosya grubu.

| Alan | Tip | Not |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK | |
| status | enum | `uploading`, `processing`, `ready`, `applied`, `failed` |
| file_count | int | |
| created_at | timestamptz | |

### `asset`
Yüklenen tek dosya.

| Alan | Tip | Not |
|---|---|---|
| id | UUID PK | |
| batch_id | UUID FK → upload_batch | |
| tenant_id | UUID FK | |
| original_filename | text | |
| parsed_sku | text | Dosya adından çıkarılan, nullable |
| storage_key | text | R2/S3 anahtarı |
| mime_type | text | |
| width / height | int | |
| processed_key | text | İşlenmiş görselin anahtarı, nullable |
| rank | int | Batch içindeki 1-tabanlı görsel sırası (Etsy image rank), nullable. 5. adımda eklendi (migration `0002_asset_rank`) |
| status | enum | `uploaded`, `processed`, `failed` |

### `generated_content`
Vision + LLM çıktısı. Kullanıcı onaylamadan Etsy'ye gitmez.

| Alan | Tip | Not |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK | |
| batch_id | UUID FK | |
| asset_id | UUID FK | |
| title | text | ≤140 karakter |
| tags | text[] | ≤13 eleman |
| description | text | |
| taxonomy_id | bigint | |
| attributes | jsonb | |
| model_used | text | Maliyet takibi için |
| input_tokens / output_tokens | int | **Listing başı maliyeti ölçmek için zorunlu** |
| approved | bool | Varsayılan false |
| created_at | timestamptz | |

### `compliance_finding`
Yayın öncesi tarama sonucu.

| Alan | Tip | Not |
|---|---|---|
| id | UUID PK | |
| tenant_id | UUID FK | |
| generated_content_id | UUID FK | |
| severity | enum | `blocking`, `warning`, `info` |
| rule | text | `trademark`, `duplicate`, `tag_stuffing`, `missing_attribute` |
| detail | text | |

**Kural:** `blocking` bulgusu olan içerik Etsy'ye gönderilemez.

---

## 2. Rate limit kuyruğu

### Kısıt
- Global: **5.000 istek / 24 saat**, **5 istek / saniye** — uygulama bazında (Personal App)
- Güvenlik payı: saniyede 4 hedefle, günlük bütçe 5.000

### Katmanlar

```
Servis → job kaydı (Postgres) → arq kuyruğu (Redis)
                                      ↓
                              worker: job al
                                      ↓
                       tenant günlük kota kontrolü (Redis sayaç)
                                      ↓
                       global token bucket (Redis, 4 req/s)
                                      ↓
                                 Etsy API
                                      ↓
                        429 / 5xx → backoff + requeue
```

### Redis anahtarları

| Anahtar | Amaç | TTL |
|---|---|---|
| `quota:global:{YYYY-MM-DD}` | Günlük global sayaç | 48s |
| `quota:tenant:{tenant_id}:{YYYY-MM-DD}` | Tenant günlük sayaç | 48s |
| `bucket:global` | Saniyelik token bucket | — |

### Kurallar

1. **Servis katmanından doğrudan Etsy'ye istek atılmaz.** Tek istisna OAuth token değişimi.
2. Kota aşılırsa iş `queued` kalır, `scheduled_at` ertesi güne ayarlanır, kullanıcıya arayüzde bildirilir.
3. `429` alınırsa: `Retry-After` başlığı varsa ona uy, yoksa exponential backoff (2s, 4s, 8s, 16s, 32s), `attempts` artır.
4. `max_attempts` aşılırsa iş `failed` olur ve kullanıcıya görünür hale gelir.
5. `5xx` yeniden denenir, `4xx` (429 hariç) denenmez — kalıcı hatadır.
6. Global token bucket Redis üzerinde atomik olmalı (Lua script veya `INCR` + `EXPIRE` deseni).
7. Her başarılı çağrıdan sonra `api_usage` tablosuna yazma **toplu** yapılır (her istekte bir DB yazması olmaz).

### Test edilecekler

- Tenant kotası dolduğunda iş bekletiliyor mu
- 429'da backoff süresi doğru hesaplanıyor mu
- Eşzamanlı 50 iş saniyede 4 sınırını aşmıyor mu
- `max_attempts` sonrası iş `failed` oluyor mu
- Etsy client testlerinde gerçek API'ye çağrı yapılmıyor (mock)

---

## 3. Şifreleme

- Token'lar `cryptography` kütüphanesi ile şifrelenir (Fernet yeterli)
- Anahtar `.env` içindeki `ENCRYPTION_KEY`'den okunur, kodda tutulmaz
- Şifreleme/çözme tek bir modülde toplanır: `app/core/crypto.py`
- Model katmanı düz metin token'ı asla saklamaz; property üzerinden çözülür

---

## 4. Bu adımın çıktısı

- Alembic ile ilk migration
- Model sınıfları ve enum tanımları
- `app/etsy/rate_limiter.py` — token bucket ve kota kontrolü
- `app/workers/` — arq worker, job alma/işleme döngüsü
- Testler: kota, backoff, eşzamanlılık

Etsy client, OAuth ve pipeline **bu adımda yazılmaz.**

---

## 5. Retention ve silme (CLAUDE.md kaynaklı — önceliklidir)

Bu bölüm CLAUDE.md "Cache ve veri saklama kuralları"nı özetler; yukarıdaki tablo notlarıyla çelişirse bu bölüm ve CLAUDE.md geçerlidir.

| Tablo | İçerik | Retention | Uygulama |
|---|---|---|---|
| `listing_snapshot` | Member Content | **`taken_at` + 90 gün → otomatik sil** | Periyodik temizlik job'ı (`workers/`, 6. adım) |
| diğer cache verisi | Listing içeriği 6s / diğer Etsy içeriği 24s | Arayüzde yaş kontrolü; süresi geçmişse gösterilmez, yeniden çekilir | Servis/cache katmanı |

Ek kurallar:
- **Member Content saklayan her tabloda retention stratejisi tanımlı olmalı** (kod kuralı). Yeni tablo eklenirken bu bölüm güncellenir.
- Kullanıcı bağlantısını kestiğinde (`etsy_connection` → `revoked`) o kullanıcıya/bağlantıya ait tüm Etsy kaynaklı içerik silinir.
- Retention job'ının kendisi bu adımda **yazılmaz**; model/şema onu destekler (`taken_at` indeksi, `RETENTION_DAYS` sabiti). Job 6. adımda gelir.
