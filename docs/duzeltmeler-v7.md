# Düzeltmeler v7

---

## A. BAŞLIK VE TAG KALİTESİ — en yüksek öncelik

### A1. Çoklu tema tespiti

Mevcut sorun: bir sweatshirt tasarımında hem Christmas hem nurse teması var; model yalnızca Christmas yazıyor. İkinci tema da gerçek arama hacmi taşıyor ve satış getirir.

Vision analizi ve içerik üretimi **birden fazla temayı** taşımalı:

- Vision çıktısı `themes: []` döndürür (tekil `theme` değil), baskınlık sırasıyla
- Ayrıca `occasion`, `recipient`, `profession`, `season`, `humor_type` alanları
- Başlık birincil temayı öne alır, **ikincil temayı da içerir**
- Tag'ler her iki temayı da kapsar

Örnek: hemşire temalı Noel tasarımı
- Yanlış: `Christmas Sweatshirt, Holiday Graphic Tee, ...`
- Doğru: `Christmas Nurse Sweatshirt, Funny Nurse Holiday Tee, Nurse Christmas Gift, ...`

### A2. Model seçimi

Mevcut model `claude-haiku-4-5`. Çoklu tema çıkarımı ve arama niyeti kavrama daha güçlü model ister.

- `LLM_MODEL` ile Sonnet denenir, çıktı Haiku ile yan yana karşılaştırılır
- Maliyet farkı listing başına yaklaşık iki kat (~$0,0035 → ~$0,007), ama retry oranı düşerse fark kapanır
- Vision ve metin üretimi için **farklı model** kullanılabilmeli: `VISION_MODEL` ve `CONTENT_MODEL` ayrı ayarlar
- Karşılaştırma sonucu bir belgeye yazılır, karar veriyle verilir

### A3. 141 karakter hatası

`item title exceeds 140 characters (yours was 141)` ile üretim başarısız oluyor. Bir karakter için tüm işi çöpe atmak yanlış.

- Başlık 140'ı aşıyorsa **son öbek atılır** (başlıklar virgülle ayrılmış öbeklerden oluşuyor)
- Öbek atınca 110'un altına düşerse, son öbek kelime kelime kısaltılır
- Yalnızca hiçbir şekilde aralığa sığmıyorsa başarısız sayılır
- Aynı mantık önek eklenince aşma durumunda da çalışır

### A4. Markalar

`TRADEMARK_FILTER` zaten yapılandırılabilir. Kapatmak istersen `.env`'de `TRADEMARK_FILTER=off`.

Kapalıyken model marka adlarını kullanabilir. Risk kullanıcıya ait: Etsy'nin fikri mülkiyet politikası izinsiz marka kullanımını yasaklıyor ve hak sahipleri toplu kaldırma talebi gönderiyor.

Admin panelinden kullanıcı bazında açılıp kapanabilmeli — bir beta kullanıcısı bu riski almak istemeyebilir.

---

## B. KENDİ EN ÇOK SATANLARINDAN ÜRETİM

Müşterinin istediği: başarılı bir listingin başlık ve tag yapısını yeni tasarıma uyarlamak.

**Kaynak: satıcının kendi mağazası.** Başka satıcıların listingleri kullanılamaz (ToU Bölüm 1 ve 5).

### Akış

1. Satıcı kendi listinglerinden birini seçer (arama ve filtreleme ile) veya "en çok satanlarım" listesinden alır
2. Sistem o listingin başlık yapısını, tag setini ve tema kalıbını çıkarır
3. Yeni tasarımın vision analizi ile birleştirilir: kalıp korunur, konu yeni tasarıma göre değişir
4. Sonuç normal doğrulamadan geçer

### Neden bu daha iyi

- Satıcının kendi kitlesinde çalışmış gerçek sinyal
- Mağaza içi tutarlılık
- Kural ihlali yok

### Admin kontrolü

Bu özellik admin panelinden kullanıcı bazında açılır/kapanır (`feature_flag` tablosu veya `tenant.features` jsonb).

### Satış verisi

"En çok satanlarım" için `transactions_r` kapsamı gerekiyor. OAuth kapsamına eklenmeli; mevcut kullanıcıların yeniden yetkilendirmesi gerekir.

---

## C. ANALİZ VE KÂR SİSTEMİ

### C1. Veri kaynakları

| Veri | Kaynak |
|---|---|
| Satışlar, tutarlar, tarihler | Etsy API (`transactions_r`) |
| Listing bazında satış adedi | Etsy API |
| Ücretler (listing, işlem, ödeme) | Kullanıcı girer veya API'den hesaplanır |
| Ürün maliyeti | Kullanıcı girer |
| Reklam harcaması | **Kullanıcı CSV yükler** (API'de yok) |

Etsy Ads verisi Open API'de bulunmuyor. Kullanıcı Shop Manager → Marketing → Etsy Ads'den CSV indirir, uygulamaya yükler. Tarayıcı otomasyonu ToU Bölüm 9 ihlali olur ve yapılmaz.

### C2. Maliyet girişi

Ayarlar ekranında:
- Listing ücreti (varsayılan $0,20)
- İşlem komisyonu (%)
- Ödeme işlem ücreti (% + sabit)
- Ürün maliyeti: profil bazında veya SKU bazında
- Kargo maliyeti
- Diğer sabit giderler (aylık)

Ücret oranları değişebildiği için hepsi düzenlenebilir olmalı.

### C3. Metrikler

Listing bazında:
- Brüt gelir, net kâr, kâr marjı
- Satış adedi, ortalama sipariş değeri
- Reklam harcaması (CSV'den), reklam kaynaklı satış, ACOS
- Zaman içindeki eğilim: son 7/30/90 gün, önceki döneme göre değişim

Mağaza bazında:
- Toplam gelir, gider, net kâr
- En kârlı ve en zararlı listingler
- Reklam verimliliği

### C4. Sınıflandırma ve öneriler

Her listing puanlanır ve kategorilere ayrılır:

| Sınıf | Tanım | Öneri |
|---|---|---|
| **Winner** | Yüksek kâr, yüksek satış | Reklamı artır, benzer tasarım üret |
| **Steady** | Düşük hacim, pozitif marj | Dokunma |
| **Fading** | Eskiden satıyordu, son dönemde düştü | Başlık/tag tazele, mevsimsellik kontrol et |
| **Ad sink** | Reklam harcaması var, satış yok veya zararına | **Reklamı kapat** |
| **Loser** | Uzun süredir satış yok | Yenile veya kaldır |

Öneriler **eyleme dönük ve gerekçeli** olmalı: "Bu listing 30 günde $47 reklam harcadı, 1 satış yaptı, net -$31. Shop Manager'dan reklamını kapatmayı düşünün." Yanında Shop Manager linki.

3.000 listingi olan satıcı hepsine bakamaz — sistem dikkatini doğru yere yönlendirmeli.

### C5. Arayüz

- Dashboard: kâr özeti, dönem karşılaştırması, en iyi/en kötü beşli
- Listing tablosu: sıralanabilir, filtrelenebilir, sınıf rozetleri
- Listing detayı: zaman grafiği, maliyet dökümü, öneriler
- Reklam CSV yükleme ve eşleştirme ekranı

---

## D. PROFİLLER

### D1. Arama

Profil sayısı arttı, seçim zorlaştı. Profiller sayfasına ve profil seçicilere arama kutusu: isme, şablona ve referans listing başlığına göre filtreler.

### D2. Manuel yenileme

Kullanıcı Etsy'de elle değişiklik yapıyor (listing yayınlama, deactive→active). Profil kartında **"Refresh"** butonu olmalı — otomatik yenileme zaten var ama elle tetikleme de gerekli.

Mağaza listesi için de aynı: "Sync shop listings" butonu.

### D3. Kota görünürlüğü

Profil çekimi kullanıcının günlük kotasından düşüyor ve bu şaşırtıcı geliyor.

- Etsy her çağrıyı sayıyor, bunu değiştiremeyiz
- Ama **kullanıcı kotasından düşmesin**: profil yenileme ve mağaza senkronizasyonu uygulama geneli bütçeden düşer, kullanıcının günlük tavanına yazılmaz
- Çağrı sayısı da azaltılmalı: gereksiz yenileme yapılmıyor mu kontrol et

### D4. Personalization

Beden tablosu seçiminin yanına **personalization** ayarı:
- Referans listingden kopyalanır (`is_personalizable`, `personalization_instructions`, `personalization_char_count_max`)
- Profil kartında görünür ve düzenlenebilir

---

## E. DÜZELTMELER

### E1. "How does your shop produce this item" seçilmiyor

Bu alan daha önce API'de olmadığı tespit edildi ve manuel adım olarak işaretlendi. Ama `who_made`, `when_made` ve `production_partner_ids` API'de var ve referanstan kopyalanmalı.

Taslakta boş çıkıyorsa neden ulaşmadığını bul. Geri okuma kontrolü ekle.

### E2. Yayın sonrası onay

Publish tamamlandığında net bir başarı bildirimi: kaç listing yayınlandı, Etsy linkleri, varsa başarısızlar ve sebepleri.

### E3. Batch silme

Batches sayfasında tekli ve toplu silme. Silinen batch'in yüklenen dosyaları ve üretilen içeriği de silinir. Etsy'deki taslaklara dokunulmaz — kullanıcıya bu açıkça söylenir.

### E4. Virgülle tag girişi

Tag alanına `nurse, winter, sweatshirt` yazıldığında üç ayrı tag olarak algılanır. Yapıştırma da aynı şekilde çalışır. Her tag doğrulamadan geçer.

---

## F. TESTLER

- Vision çıktısının birden fazla tema döndürdüğü
- Başlığın ikincil temayı da içerdiği
- 141 karakterlik başlığın öbek atılarak 140'a indirildiği, başarısız sayılmadığı
- Kendi listingden kalıp çıkarmanın yalnızca kendi mağaza verisini kullandığı
- Başka kiracının listinginden kalıp çıkarılamadığı
- Kâr hesabının girilen ücretlerle doğru sonuç verdiği
- Reklam CSV'sinin listinglerle doğru eşleştiği
- Sınıflandırmanın eşik değerlerinde doğru karar verdiği
- Profil aramasının isim, şablon ve referans başlığına göre filtrelediği
- Profil yenilemenin kullanıcı kotasından düşmediği
- Personalization ayarının referanstan kopyalandığı
- Virgülle ayrılmış tag girişinin doğru ayrıştığı
- Batch silmenin Etsy taslaklarına dokunmadığı
