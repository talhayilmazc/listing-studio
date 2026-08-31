# Düzeltmeler v4

Üçüncü gerçek mağaza testinden çıkan bulgular.

---

## 0. İLKE — genişletilmiş

Listing'in **her ayarı** profilin referans listinginden gelir. Kodda sabit hiçbir değer yoktur, arayüzde varsayılan seçilmez.

Her müşterinin kendi ön ayarları var; profil mekanizmasının varlık sebebi bu.

---

## A. KRİTİK: kategori hâlâ dijital

Taslak "digital files" olarak açılıyor. Olması gereken: referans listingin kategorisi, yani `T-shirt (physical item)` veya kuzeninin kullandığı hangi giyim kategorisiyse.

Bu tek hata muhtemelen B ve C bölümlerini de açıklıyor — yanlış kategoride giyim nitelikleri zorunlu sayılmıyor, o yüzden hiç gönderilmiyor, o yüzden yayın engelleniyor.

### Yapılacaklar

- `createDraftListing` çağrısında `taxonomy_id` **her zaman** referans listingden gelir
- Ürün tipi fiziksel olarak gönderilir (`type=physical`)
- Taslak oluştuktan sonra `getListing` ile geri okunup `taxonomy_id`'nin referansla birebir aynı olduğu doğrulanır
- Farklıysa iş başarısız sayılır ve sebep loglanır — sessizce devam edilmez

Test: referansın `taxonomy_id`'si ne olursa olsun aynısı gönderilir; hiçbir kod yolu kategori seçmez veya varsayılan atamaz.

---

## B. ZORUNLU GİYİM NİTELİKLERİ

Etsy'nin giyim kategorilerinde zorunlu nitelikler var. Eksikse taslak oluşur ama **yayınlanamaz** — bu, "Etsy'de önce save, sonra publish yapınca çalışıyor" davranışının sebebi.

### Kaynak: referans + görsel analizi

`getPropertiesByTaxonomyId` ile kategorinin zorunlu nitelikleri çekilir. Her biri için:

1. **Öncelik: referans listing.** Referansta o nitelik doluysa aynen kopyalanır.
2. **Görsel analizinden çıkarım:** referansta yoksa veya tasarım açıkça farklı bir giysi tipiyse, vision çıktısından belirlenir:
   - **Neckline** (crew, v-neck, scoop...)
   - **Sleeve length** (short sleeve, long sleeve, sleeveless)
   - **Clothing style** (graphic tee, sweatshirt, hoodie, tank)
3. **Hâlâ belirlenemiyorsa:** iş başarısız olur ve hangi niteliğin eksik olduğu kullanıcıya bildirilir. Rastgele değer atanmaz.

Vision şablonuna bu üç alan eklenir — mockup görselinde yaka tipi, kol uzunluğu ve giysi tipi görülebiliyor.

### Doğrulama

Taslak oluşturulduktan sonra kategorinin tüm zorunlu nitelikleri dolu mu diye kontrol edilir. Eksik varsa kullanıcıya "bu listing yayınlanamaz, şu nitelik eksik" uyarısı gösterilir.

---

## C. PROFİLDEN ÇEKİLECEK DİĞER AYARLAR

Şu ayarlar da referans listingden kopyalanır. Hiçbiri kodda sabitlenmez:

| Ayar | Etsy alanı |
|---|---|
| İşlem süresi / hazırlık (örn. "1-2 days", made to order) | `processing_min`, `processing_max`, `is_customizable`, `is_personalizable` |
| Kargo profili (tüm detaylar, fiyatlar dahil) | `shipping_profile_id` |
| İade ve değişim politikası | `return_policy_id` |
| Üretim şekli ("How does your shop produce this item") | `who_made`, `when_made`, `production_partner_ids` |
| Yenileme (otomatik) | `should_auto_renew` |
| Bölüm | `shop_section_id` (F bölümü kuralına göre, ama profil bazlı) |

Kargo ve iade politikaları Etsy'de ayrı kaynaklar; referanstan gelen id yeni listinge aynen yazılır, içerikleri kopyalanmaz.

Test: her alan için referanstan geldiği, kodda varsayılan olmadığı doğrulanır.

---

## D. YAYINLAMA HATASI

Belirti: yayınlarken Etsy `There was a problem saving your changes` hatası veriyor. Etsy arayüzünde önce "save" yapılıp sonra yayınlanınca çalışıyor.

Hipotez: A ve B'deki eksikler yüzünden listing yayına uygun değil. Etsy arayüzündeki "save" eksik alanları dolduruyor.

A ve B düzeltildikten sonra tekrar test edilir. Sorun devam ederse:

- `updateListing` ile `state=active` yapmadan önce listing'in yayına uygun olup olmadığı kontrol edilir
- Etsy'nin hata gövdesi loglanır (artık görünüyor)

---

## E. GRUP BAZINDA PROFİL SEÇİMİ

Şu an profil yalnızca batch seviyesinde seçiliyor. Ama tek yüklemede 3 Comfort Colors + 2 normal ürün olabiliyor.

- **Her listing grubu için ayrı profil seçimi** — grup kartında açılır menü
- Üstteki toplu seçim tüm grupları birden ayarlar
- Grup bazında değiştirilebilir, toplu seçim ezmez (kullanıcı manuel değiştirdiyse korunur)
- Beden tablosu profili de aynı mantıkla grup bazında seçilebilir

Bu, v3 §G'de vardı ama uygulanmadı.

---

## F. TESTLER

- `taxonomy_id`'nin referanstan geldiği, hiçbir kod yolunun kategori seçmediği
- Listing tipinin her zaman fiziksel olduğu
- Zorunlu giyim niteliklerinin referans → vision → hata sırasıyla belirlendiği
- Neckline, sleeve length ve clothing style'ın vision çıktısında bulunduğu
- İşlem süresi, kargo profili, iade politikası, üretim şekli ve yenileme ayarının referanstan geldiği
- Bu alanların hiçbirinde kodda varsayılan olmadığı
- Grup bazında farklı profil seçilebildiği, toplu seçimin manuel seçimi ezmediği
- Etsy API tamamen mock
