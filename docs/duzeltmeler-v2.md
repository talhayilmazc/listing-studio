# Düzeltmeler ve Yeni Özellikler — v2

Gerçek mağazada yapılan ilk testten çıkan bulgular.

---

## A. HATALAR (öncelikli)

### A1. Taslağa yalnızca 1 tag ekleniyor

Üretilen 13 tag'in tamamı `createDraftListing` çağrısına gitmiyor. Etsy API'si tag'leri belirli bir formatta bekliyor (tekrarlanan form alanı veya virgülle ayrılmış dize). Mevcut serileştirme yanlış.

- `createDraftListing` ve `updateListing` çağrılarında tag serileştirmesini düzelt
- Taslak oluşturulduktan sonra `getListing` ile geri okuyup 13 tag'in de yazıldığını doğrulayan test ekle

### A2. SKU taslağa yazılmıyor

Arayüzde SKU görünüyor ama Etsy taslağında yok. Etsy'de SKU listing seviyesinde değil, `products[].sku` içinde tutulur — `updateListingInventory` çağrısında yanlış yere yerleşiyor.

- SKU'nun her ürün varyasyonuna yazıldığını düzelt
- Geri okuma testi ekle

### A3. Kategori değiştiriliyor

Taslak dijital ürün kategorisiyle açılıyor. Kategori mağazadaki mevcut listinglerden alınmalı, yeniden seçilmemeli. Çözüm B1'de.

### A4. Taslak linki yanlış yere gidiyor

Taslaklar herkese açık listing URL'sinden görüntülenemez ("Sorry this item is unavailable").

- Taslak durumundaki listingler için **Shop Manager düzenleme sayfasına** link ver
- Yayınlanmış listingler için herkese açık URL kalır (ToU geri link zorunluluğu)

---

## B. MAĞAZADAN VERİ ÇEKME (yeni temel mekanizma)

Bu bölüm çoğu sorunun kök çözümü. Uygulama artık listing verisini sıfırdan uydurmuyor; satıcının **kendi mevcut listinglerini** şablon olarak kullanıyor.

Yalnızca kimliği doğrulanmış satıcının kendi mağaza verisi kullanılır — başka satıcıların verisine dokunulmaz (CLAUDE.md kısıt #2).

### B1. Referans listing profilleri

Mağaza bağlandığında `getListingsByShop` ile mevcut aktif listingler çekilir. Kullanıcı bunlardan **ürün tipi profilleri** tanımlar:

```
Profil: "Standard Tee"    → referans listing #1234567890
Profil: "Comfort Colors"  → referans listing #1234567891
```

Her profil referans listingden şunları kopyalar ve sabitler:

| Alan | Kaynak |
|---|---|
| `taxonomy_id` (kategori) | referans listing |
| Kategori nitelikleri | referans listing |
| Fiyat | referans listing |
| `shipping_profile_id` | referans listing |
| `production_partner_ids` | referans listing |
| `who_made`, `when_made`, `is_supply` | referans listing |
| Varyasyon yapısı (bedenler, renkler, fiyat farkları) | referans listing envanteri |
| Beden tablosu görselleri | referans listing görselleri |
| Açıklama gövdesi | referans listing açıklaması |
| İşlem süresi | referans listing |

Yeni tablo: `listing_profile` (tenant_id, name, reference_listing_id, cached_payload jsonb, updated_at).

Önbellek 24 saatlik cache kuralına tabidir (ToU); süresi geçince referans yeniden çekilir.

### B2. Açıklama referanstan gelir

`prompts/description/default.md` şablonu kaldırılır.

- Açıklama, seçilen profilin referans listingindeki açıklamadan alınır
- **Yalnızca ilk satır** yeni üretilen başlıkla değiştirilir
- Geri kalan metin (beden ölçüleri, kargo, iade, bakım) aynen korunur
- İlk satır sayısı ayarlanabilir olsun (bazı listinglerde başlık iki satır olabilir)

### B3. Beden tabloları

Referans listingin görsellerinden hangilerinin sabit kalacağını kullanıcı arayüzden işaretler. Bu görseller her yeni listinge aynı sırada eklenir.

### B4. Ana sayfada mevcut listingler

Mağaza bağlandıktan sonra ana sayfada mevcut listingler listelenir:

- Küçük resim, başlık, SKU, bölüm, durum (aktif/taslak)
- **"Yeni sürüm oluştur"**: bu listingi profil olarak kullanıp yeni görsellerle yeni taslak açar
- **"Görselleri değiştir"**: eski görseller silinir, yenileri yüklenir, yeni görsellere göre içerik yeniden üretilir
- Her kartta Etsy linki (taslaksa Shop Manager, aktifse herkese açık URL)

---

## C. İÇERİK ÜRETİMİ

### C1. Ürün tipi şablonları

Mevcut `digital_products` şablonu tişört için yanlış dil üretiyor: "SVG", "PNG", "printable", "digital download", "instant download".

Yeni şablon: `prompts/content/apparel/`

- Başlıkta ürün tipi açıkça geçmeli: "Shirt", "T-Shirt", "Tee", "Sweatshirt", "Hoodie"
- **Yasak kelimeler** (validator'a eklenir): `SVG`, `PNG`, `PDF`, `printable`, `digital download`, `instant download`, `cut file`, `sublimation`, `clipart`
  → Başlıkta veya tag'de geçerse doğrulama başarısız, düzeltme mesajıyla yeniden üretilir
- Doğru: `Patriotic 4th of July Shirt`, `American Flag Tee`
- Yanlış: `Patriotic SVG`, `4th of July Digital Download`

Şablon seçimi profil bazında yapılır (B1).

### C2. Başlık uzunluğu

Geçerli aralık **110-140 karakter**. Hedef 130-140, alt sınır 110.

Validator, düzeltme mesajı, prompt ve testler güncellenir.

### C3. Tag kuralları

Mevcut 20 karakter sınırı korunur. Ek olarak:
- Dosya formatı adı geçen tag yasak (C1 listesi)
- Ürün tipi tag'lerinden en az biri bulunmalı (`shirt`, `tee`, `tshirt` vb.)

---

## D. KLASÖR YAPISI VE TOPLU İŞLEM

### D1. Klasör = listing

- Bir klasör = bir listing
- Klasördeki tüm görseller o listingin görselleri, alfabetik sırayla `rank`
- 10 klasör atılırsa 10 ayrı listing
- Kök dizindeki klasörsüz dosyalar tek bir listing sayılır

Veri modeli: `asset.group_key` alanı veya ayrı `listing_group` tablosu — hangisi daha temizse.

### D2. SKU klasör adından

SKU artık dosya adından değil klasör adından okunur.

- Klasör adı doğrudan SKU (örn. `BR5475/` → `BR5475`)
- Ek metin varsa regex ile ayıklanır: `([A-Za-z]{2,4}\d{3,6})`
- Dosya adı kuralı kalır ama önceliği düşer

### D3. Toplu aksiyonlar

- Her listing grubu için **Generate content**
- En üstte **Generate content for all** — tüm gruplar kuyruğa girer
- Her grup için **Create draft**
- En üstte **Create drafts for all**
- İlerleme göstergesi: tamamlanan / kalan / başarısız
- Bir grup başarısız olursa diğerleri devam eder

Toplu işlemler mevcut kuyruk ve kota mekanizmasını kullanır; kota dolduğunda işler ertelenir ve kullanıcıya bildirilir.

---

## E. YAYINLAMA AKIŞI

**Taslak her zaman önce oluşturulur.** CLAUDE.md kısıt #3 ve Etsy'ye verilen beyan bu yönde: *"Every listing is created as a draft and needs explicit seller approval before publishing."*

### Akış

1. Kullanıcı Review ekranında içeriği inceler ve onaylar
2. **"Create draft"** → `createDraftListing`, taslak oluşur
3. Aynı ekranda **"Publish now"** butonu belirir
4. Tıklandığında `updateListing` ile `state=active`

Böylece kullanıcı Etsy'ye gidip taslağı aramak zorunda kalmaz, ama yayın hâlâ ayrı ve açık bir eylem.

### Kurallar

- Yayınlama asla otomatik değil, asla üretimin yan etkisi değil
- **"Publish all"** yalnızca kullanıcının tek tek onayladığı listingleri yayınlar
- `blocking` compliance bulgusu varsa yayınlama tamamen engellenir
- Yayın sonrası listing durumu ve herkese açık URL arayüzde gösterilir

### Reklam

Etsy Open API'de reklam endpoint'i **yok**. Tarayıcı otomasyonu ToU Bölüm 9 ihlali olur ve yapılmaz.

Bunun yerine yayın sonrası ekranda bilgilendirme ve Shop Manager → Marketing → Etsy Ads sayfasına link gösterilir.

---

## F. ARAYÜZ

Mevcut arayüz işlevsel ama akış belirsiz ve kurumsal görünmüyor.

### Bilgi mimarisi

```
Dashboard
  ├── Mağaza durumu (bağlı mağaza, kota, son işlemler)
  ├── Mevcut listingler (ızgara, arama, filtre)
  └── Ürün tipi profilleri

Yeni yükleme
  ├── Klasör bırakma alanı
  ├── Tespit edilen gruplar (klasör → SKU → görsel sayısı)
  ├── Profil seçimi (grup bazında veya toplu)
  └── Generate all / grup bazında generate

İnceleme
  ├── Listing kartları (görsel + başlık + tag + açıklama önizleme)
  ├── Satır içi düzenleme, karakter sayacı, canlı doğrulama
  ├── Onayla / onayı kaldır
  ├── Create draft / Create drafts for all
  └── Publish now / Publish all (yalnızca taslak oluşmuş ve onaylanmışlar için)

Ayarlar
  ├── Profiller
  ├── Bölüm eşleme kuralları
  └── Hesap, kota, destek
```

### Tasarım ilkeleri

- Net birincil akış: **Yükle → Üret → İncele → Taslak → Yayınla**, her ekranda hangi adımda olunduğu belli
- Her listing grubunun durumu tek bakışta görünür
- Hata mesajları eylem önerir
- Toplu işlemlerde ilerleme çubuğu ve iptal
- Etsy turuncusu kullanılmaz (ToU Bölüm 2)
- Zorunlu ToU unsurları korunur: marka ibaresi, ToS/gizlilik linkleri, destek e-postası, kota göstergesi, listing geri linkleri

---

## G. TESTLER

- 13 tag'in tamamının taslağa yazıldığı (geri okuma ile)
- SKU'nun ürün varyasyonlarına yazıldığı
- Kategorinin referans listingden alındığı, yeniden seçilmediği
- Klasör başına bir listing grubu oluştuğu, 10 klasör → 10 grup
- SKU'nun klasör adından okunduğu
- Yasak kelime içeren başlık/tag'in reddedildiği
- Başlık 109 reddedilir, 110 kabul edilir
- Açıklamanın referanstan alınıp yalnızca ilk satırının değiştiği
- Yayınlamanın yalnızca taslak oluştuktan ve onaylandıktan sonra mümkün olduğu
- `blocking` bulguda yayınlamanın engellendiği
- Taslak linkinin Shop Manager'a, aktif listing linkinin herkese açık URL'e gittiği
- Etsy API tamamen mock — gerçek çağrı yok
