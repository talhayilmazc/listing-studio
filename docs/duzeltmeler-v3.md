# Düzeltmeler v3

İkinci gerçek mağaza testinden çıkan bulgular.

---

## 0. TEMEL İLKE

Listing metadata'sı **hiçbir zaman kodda sabit değildir.** Kategori, fiyat, bedenler, renkler, varyasyon yapısı, kargo profili, üretim ortağı — hepsi profilin referans listinginden kopyalanır.

Bu bölümdeki hataların çoğu bu ilkenin uygulanmamasından kaynaklanıyor.

---

## A. KRİTİK: taslak oluşturma 400 hatası

Belirti: `EtsyClientError: etsy client error (400)`. Taslak açılıyor ama içi eksik:

- Görseller yok
- SKU yok
- Varyasyonlar/fiyatlar yok
- Kategori "digital files" olmuş (referanstakinin aksine)

Bunlar muhtemelen tek bir kök sebebin sonucu — taslak oluşturma çağrısı kısmen başarısız oluyor ve sonraki adımlar (görsel yükleme, envanter) hiç çalışmıyor ya da hata yutuluyor.

### Yapılacaklar

1. **Etsy'nin 400 cevabının tam gövdesini logla.** Etsy hangi alanı reddettiğini söylüyor; şu an bu bilgi kaybediliyor.
2. Taslak oluşturma çok adımlı bir işlem: `createDraftListing` → `uploadListingImage` (n kez) → `updateListingInventory`. Her adımın başarı/başarısızlığı ayrı ayrı kaydedilmeli ve arayüzde görünmeli. Bir adım patlarsa hangisi olduğu belli olmalı.
3. Kısmi başarı durumunda kullanıcıya net mesaj: "taslak oluşturuldu ama görseller yüklenemedi, sebep: ..."

### Muhtemel sebepler (kontrol edilecek)

- `createDraftListing` zorunlu alanları eksik gönderiyor olabilir (Etsy fiziksel ürün için `shipping_profile_id`, `who_made`, `when_made`, `is_supply`, `taxonomy_id` ister)
- Varyasyonlu listinglerde `updateListingInventory` çağrısının şekli farklıdır; referanstan kopyalanan yapı doğru serileştirilmiyor olabilir
- Fiziksel ürün için `type=physical` parametresi gönderilmiyor olabilir — bu kategori sorununun kaynağı olabilir

---

## B. KATEGORİ VE ÜRÜN TİPİ

Taslak "digital files" olarak açılıyor. Bu kabul edilemez — mağazada dijital ürün yok.

- `createDraftListing` çağrısında listing tipi **her zaman fiziksel** olmalı
- `taxonomy_id` **referans listingden** gelmeli, asla yeniden seçilmemeli
- Kategori niteliklerinin de referanstan geldiği doğrulanmalı
- Bu üçü için geri okumalı test: taslak oluştuktan sonra `getListing` ile kategori ve tipin referansla aynı olduğu doğrulanır

### `digital_products` şablonunu kaldır

Artık kullanılmıyor ve yanlış davranışa yol açıyor:

- `content_template` seçeneklerinden `digital_products` çıkarılır
- İlgili prompt klasörü ve politika silinir
- Migration ile kalan kayıtlar `apparel`'e çevrilir
- Arayüzdeki seçim kutusu kaldırılır (tek seçenek kaldığı için gereksiz)

---

## C. VARYASYONLAR VE FİYATLAR

Referans listingin envanteri **birebir** kopyalanmalı: her beden, her renk, her fiyat, her adet.

- `getListingInventory` ile referansın envanteri çekilir ve `cached_payload` içinde saklanır
- Yeni listing oluşturulurken bu yapı aynen gönderilir, yalnızca SKU değişir
- **Fiyatı olmayan varyasyonlar atlanır** — referansta fiyatı boş olan satır o mağazada kapalıdır, yeni listinge de eklenmez
- Fiyat tablosu koda yazılmaz, hiçbir koşulda

Test: 300+ varyasyonlu bir referanstan kopyalanan listingde varyasyon sayısı, fiyatlar ve adetler referansla birebir eşleşmeli; fiyatsız satırlar dışarıda kalmalı.

---

## D. SKU

SKU taslağa yazılmıyor. Etsy'de SKU listing seviyesinde değil, `products[].sku` içindedir.

- Kopyalanan envanterdeki **her ürün varyasyonuna** aynı SKU yazılır
- Taslak oluştuktan sonra `getListingInventory` ile geri okunup doğrulanır

---

## E. BEDEN TABLOLARI

Yeni listing oluştururken beden tabloları eklenmiyor.

- Her listing grubu için hangi profilin beden tablolarının kullanılacağı seçilebilmeli
- Comfort Colors ve normal ürünlerin tabloları farklı, bu yüzden grup bazında seçim şart
- Tablolar yeni görsellerin **sonuna** eklenir, sıraları korunur
- Görseller Etsy'nin kendi `listing_image_id`'si ile kopyalanır (indirilmez)

---

## F. SHOP SECTION

Bölüm seçimi iki kurala göre yapılır:

1. Kullanılan profil Comfort Colors ise → mağazadaki "Comfort Colors" bölümü
2. Değilse → tasarımın temasına göre uygun bölüm (Mother's Day, 4th of July, Christmas...)

Mevcut `rules.json` anahtar kelime eşlemesi ikinci kural için kullanılır. Birinci kural profil bazlı ve deterministiktir, LLM'e sorulmaz.

`AUTO_CREATE_SECTIONS` varsayılan `false` kalır — mağazada olmayan bölüm oluşturulmaz, boş bırakılır.

---

## G. GRUP BAZINDA PROFİL SEÇİMİ

Şu an profil yalnızca batch seviyesinde seçiliyor. Ama bir yüklemede hem Comfort Colors hem normal ürün olabilir.

- Her listing grubu için ayrı profil seçimi
- Üstteki toplu seçim tüm grupları birden ayarlar (varsayılan olarak)
- Grup bazında değiştirilebilir
- Beden tablosu profili de aynı şekilde grup bazında seçilebilir

---

## H. BAŞLIK VE TAG KALİTESİ

### Başlık

Model aralığın alt sınırına yapışıyor (110-117 civarı). Hedef üst banda çekilmeli.

- Prompt'ta hedef **130-140** olarak vurgulanır, 110 yalnızca kabul edilebilir alt sınırdır
- Prompt'a doğru uzunlukta somut örnekler eklenir
- Düzeltme mesajında "yours was 117, aim for 130-140" gibi net yönlendirme

### Tag'ler

Genel, arama değeri olmayan tag'ler üretiliyor: `illustrated design`, `graphic design` gibi.

Yasak liste genişletilir:
```
illustrated design, graphic design, digital art, printed design,
custom design, unique design, trendy design, cool design
```

Kural: her tag ya **konuyu** (theme, occasion, recipient) ya **ürün tipini** ya da **stili** somut olarak belirtmeli. Jenerik tasarım sıfatları reddedilir.

---

## I. TESTLER

- Taslak oluşturma 400 aldığında Etsy'nin hata gövdesinin loglandığı
- Çok adımlı taslak akışında her adımın durumunun ayrı kaydedildiği
- Listing tipinin her zaman fiziksel olduğu
- Kategorinin referanstan geldiği, yeniden seçilmediği
- Envanterin referansla birebir eşleştiği, fiyatsız satırların atlandığı
- SKU'nun her varyasyona yazıldığı
- Beden tablolarının seçilen profilden alınıp sona eklendiği
- Comfort profili → Comfort Colors bölümü kuralının deterministik çalıştığı
- Grup bazında farklı profil seçilebildiği
- Yasak tag'lerin reddedildiği
- Etsy API tamamen mock
