# Etsy Client ve Taslak Oluşturma — Spec

Çalışma sırası adım 4 + adım 5'in Etsy'ye bağlı yarısı.

---

## 1. Etsy API Client

Placeholder `EtsyClient` yerine gerçek implementasyon.

### Endpoint'ler

| İşlev | Endpoint |
|---|---|
| Taksonomi ağacı | `getSellerTaxonomyNodes` |
| Kategori nitelikleri | `getPropertiesByTaxonomyId` |
| Mağaza bilgisi | `getShop` |
| Listing okuma | `getListingsByShop`, `getListing` |
| Taslak oluşturma | `createDraftListing` |
| Görsel yükleme | `uploadListingImage` |
| Envanter/varyasyon | `updateListingInventory` |
| Bölüm | `getShopSections`, `createShopSection` |
| Listing güncelleme | `updateListing` |

### Kurallar

- **Her çağrı kuyruk üzerinden** (CLAUDE.md mimari kuralı)
- Başlık formatı: `x-api-key: {keystring}:{shared_secret}` + `Authorization: Bearer {access_token}`
- Limitler: 5.000/gün, 4 req/s
- Taksonomi yanıtı önbelleğe alınır (nadiren değişir), ama Member Content değil — 24 saat cache kuralı geçerli
- Testlerde gerçek API çağrısı yok

---

## 2. Görsel işleme — thumbnail hazırlığı

**Not:** Etsy API'sinde thumbnail kırpma/yakınlaştırma parametresi yok. `uploadListingImage` yalnızca `image`, `rank`, `overwrite`, `alt_text`, `is_watermarked` alıyor. Bu yüzden kırpmayı yüklemeden önce biz yapıyoruz.

`app/pipeline/images.py` içine ekle:

- **`prepare_thumbnail(image)`**: tasarımın sınırlayıcı kutusunu tespit et (boş/şeffaf kenarları kırp), etrafına yapılandırılabilir bir pay bırak (varsayılan %8), kareye tamamla, merkeze hizala, 2000×2000 çıktı ver
- Şeffaf PNG'lerde alfa kanalından, düz zeminli görsellerde kenar rengi farkından sınır tespiti
- Sonuç 1. sıraya (`rank=1`) yüklenir
- Diğer görseller orijinal en-boy oranıyla kalır

Yapılandırma: `THUMBNAIL_PADDING_PCT`, `THUMBNAIL_SIZE`.

---

## 3. Başlık kuralı

`prompts/content/*/system.md` içinde:

- Hedef uzunluk **130-140 karakter**. 130'un altı reddedilir.
- Doğrulama katmanına ekle: `130 <= len(title) <= 140`. Dışındaysa yeniden üret.
- En güçlü arama terimleri başa.

Mevcut doğrulama sadece üst sınıra bakıyor; alt sınır eklenmeli.

---

## 4. Sabit açıklama şablonu

Açıklama artık LLM'den serbest üretilmiyor. Şablon dosyadan okunuyor:

```
app/pipeline/prompts/description/default.md
```

Şablon örneği:

```
{title}

[buradan sonrası her listingde aynı sabit metin]
```

- `{title}` üretilen başlıkla değiştirilir
- Şablon dosyadan okunur, kod değişikliği gerektirmez
- `DESCRIPTION_TEMPLATE` env değişkeniyle farklı şablon seçilebilir

### Opsiyonel: hafif kişiselleştirme

`DESCRIPTION_VARIABLES=true` ise şablonda ek yuvalar doldurulur:
`{theme}`, `{occasion}`, `{audience}` — vision çıktısından gelir.

Gerekçe: birebir aynı açıklamalar arama sinyali üretmez ve duplicate tespitine takılabilir. Varsayılan `false`, istendiğinde açılır.

---

## 5. SKU parse kuralı

Mevcut `SkuParser`'a kural ekle: **SKU dosya adının sonunda**, harf öneki + rakam.

Örnekler:
```
tasarim_BR5475.png     → BR5475
mockup-front-AB1234.jpg → AB1234
something_BR5475_2.png  → BR5475
```

Regex: `([A-Za-z]{2,4}\d{3,6})(?:[_-]\d+)?$` (uzantıdan önce)

- Kural listesinin **başına** eklenir (ilk eşleşen kazanır)
- Aynı SKU'ya sahip birden fazla dosya varsa aynı listing'in görselleri olarak gruplanır, `rank` dosya adındaki son eke veya alfabetik sıraya göre atanır

---

## 6. Shop section otomatik atama

Vision çıktısındaki `theme` ve `occasion` bilgisinden mağaza bölümü seçilir.

### Akış

1. `getShopSections` ile mevcut bölümler çekilir
2. Vision çıktısı + mevcut bölüm isimleri LLM'e verilir, en uygun bölüm seçtirilir
3. Eşleşme yoksa: yapılandırmaya göre ya yeni bölüm oluşturulur (`createShopSection`) ya da boş bırakılır

### Kural dosyası

`app/pipeline/prompts/sections/rules.json` — anahtar kelime → bölüm eşlemesi:

```json
{
  "4th of July": ["independence day", "july 4", "patriotic", "usa", "america", "250th"],
  "Christmas": ["christmas", "xmas", "santa", "holiday"],
  "Halloween": ["halloween", "spooky", "pumpkin"]
}
```

Önce bu kural dosyasına bakılır (deterministik ve ücretsiz), eşleşme yoksa LLM'e sorulur.

Yapılandırma: `AUTO_CREATE_SECTIONS` (varsayılan `false` — kullanıcının mağazasında izinsiz bölüm açma).

---

## 7. Beden ölçüleri

Her listing'de beden bilgisi bulunacak. İki katman:

### a) Varyasyonlar

`updateListingInventory` ile beden varyasyonları eklenir. Beden seti yapılandırmadan gelir:

```
app/pipeline/prompts/sizes/default.json
```

```json
{
  "property": "Size",
  "values": ["S", "M", "L", "XL", "2XL", "3XL"],
  "price_offsets": {"2XL": 2.00, "3XL": 3.00}
}
```

- Taksonomi nitelikleri `getPropertiesByTaxonomyId` ile doğrulanır — kategori beden desteklemiyorsa atlanır
- Fiyat farkları opsiyonel

### b) Beden tablosu görseli

`SIZE_CHART_IMAGE` yolundaki görsel her listing'e son sıradan bir önceki `rank` ile eklenir. Tanımlı değilse atlanır.

### c) Açıklamada ölçüler

Açıklama şablonuna sabit ölçü metni konur (madde 4'teki şablonun parçası).

---

## 8. Taslak oluşturma akışı

`POST /batches/{id}/publish` (onaylanmış içerikler için):

1. **Snapshot al** — yazma öncesi `listing_snapshot` kaydı
2. `createDraftListing`: başlık, açıklama, 13 tag, fiyat, adet, taksonomi, nitelikler, SKU, section
3. Görselleri sırayla yükle: hazırlanmış thumbnail `rank=1`, sonra diğerleri, beden tablosu sondan bir önceki
4. `updateListingInventory` ile beden varyasyonları ve SKU
5. Sonuçtaki Etsy listing URL'ini kaydet ve arayüzde göster

### Katı kurallar

- **Yalnızca taslak.** `state` asla `active` yapılmaz. Yayınlama kullanıcının Etsy arayüzünde kendi işi.
- Compliance taramasında `blocking` bulgu varsa gönderilmez
- Her adım kuyruk üzerinden, hata durumunda `job` kaydı `failed` ve sebep görünür
- Kısmi başarı: görsel yüklemesi patlarsa listing taslak olarak kalır, hangi adımda kaldığı kaydedilir

---

## 9. Arayüz

- Review ekranındaki **"Publish to Etsy"** butonu aktifleşir (taslak oluşturur)
- Oluşan listing için **Etsy linki** gösterilir — ToU geri link zorunluluğu
- İlerleme: hangi asset hangi adımda
- Hata durumunda sebep görünür

---

## 10. Yapılandırma özeti

`.env`'e eklenecekler:

```
THUMBNAIL_PADDING_PCT=8
THUMBNAIL_SIZE=2000
DESCRIPTION_TEMPLATE=default
DESCRIPTION_VARIABLES=false
AUTO_CREATE_SECTIONS=false
SIZE_CHART_IMAGE=
DEFAULT_PRICE=
DEFAULT_QUANTITY=999
```

---

## 11. Testler

- Etsy API tamamen mock — gerçek çağrı yok
- Thumbnail hazırlama: şeffaf PNG, düz zemin, zaten kare olan görsel
- SKU parse: `BR5475` deseni ve çok dosyalı gruplama
- Section eşleme: kural dosyası isabet, isabetsizlik, otomatik oluşturma kapalıyken davranış
- Başlık doğrulama: 129 karakter reddedilir, 135 kabul edilir
- Taslak akışı: snapshot alınıyor mu, `state` asla `active` olmuyor mu, blocking bulguda duruyor mu
