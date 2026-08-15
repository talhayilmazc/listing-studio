# Veri Modeli ve Kuyruk Spesifikasyonu

Bu belge çalışma sırasındaki **1. adım (veri modeli + migration)** ve **3. adım (rate limit kuyruğu)** için referanstır. Etsy API bağlantısı gerektirmez.

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

İndeks: `(tenant_id, listing_id, taken_at DESC)`

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
- Global: **10.000 istek / 24 saat**, **10 istek / saniye** — uygulama bazında
- Güvenlik payı: saniyede 8, günde 9.000 hedefle

### Katmanlar

```
Servis → job kaydı (Postgres) → arq kuyruğu (Redis)
                                      ↓
                              worker: job al
                                      ↓
                       tenant günlük kota kontrolü (Redis sayaç)
                                      ↓
                       global token bucket (Redis, 8 req/s)
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
- Eşzamanlı 50 iş saniyede 8 sınırını aşmıyor mu
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
