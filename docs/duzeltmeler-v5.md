# Düzeltmeler v5 + Çoklu Mağaza

Öncelik sırası: A (429) → B, C, D (hatalar) → E (çoklu mağaza).

---

## A. KRİTİK: Etsy 429 — hız sınırı aşılıyor

Yayınlama sırasında `429 Too Many Requests` alınıyor. Token bucket saniyede 4 istek hedefliyor ve Etsy'nin sınırı 5 QPS, yani kağıt üzerinde pay var. Demek ki bir yerden sınırsız çağrı sızıyor.

### Teşhis

1. Her Etsy çağrısını zaman damgasıyla logla: hangi endpoint, hangi iş, hangi saniyede
2. 429 alındığında o saniyede kaç çağrı yapıldığını say
3. Bucket'ı **atlayan** çağrı var mı kontrol et — CLAUDE.md'nin mimari kuralına göre OAuth token değişimi dışında hiçbir çağrı doğrudan gitmemeli

### Kontrol edilecek şüpheliler

- **Görsel yükleme döngüsü.** Bir listing 5-8 görsel yüklüyor; bunlar tek tek bucket'tan geçiyor mu, yoksa toplu mu gidiyor?
- **Yeniden denemeler.** 429 sonrası backoff yapılıyor ama yeniden deneme bucket'tan geçiyor mu? Geçmiyorsa 429 daha fazla 429 üretir.
- **Eşzamanlı worker'lar.** Birden fazla arq worker süreci varsa Lua bucket global olduğu için sorun olmamalı — ama doğrula.
- **Etsy'nin gerçek sınırı burst olabilir.** 5 QPS ortalama olsa bile ani yığılmayı reddediyor olabilir.

### Çözüm

- Sızan çağrı varsa kuyruğa al
- 429 alındığında `Retry-After` başlığına uy, yoksa exponential backoff — ve yeniden deneme de bucket'tan geçsin
- Güvenlik payını artır: hedef 4 yerine **3 req/s**. Kapasitenin %20'sini kaybetmek 429 almaktan iyi.
- Aynı işteki ardışık çağrılar arasına minimum aralık koy

Test: 50 çağrılık bir iş dizisinde hiçbir saniyede 3'ten fazla istek çıkmadığı doğrulanır.

---

## B. Comfort Colors öneki ve açıklaması gelmiyor

`title_prefix` elle doldurulduğunda çalışıyordu, şimdi tekrar gelmiyor.

### Teşhis

Her adımda değeri logla:
1. Profil kaydında `title_prefix` dolu mu?
2. Üretim çağrısına ulaşıyor mu?
3. Üretilen başlığın başına ekleniyor mu?
4. Açıklamanın başlık bloğu yeni başlıkla (önek dahil) değiştiriliyor mu?

### Olası sebep

Önek üretim anında ekleniyor. Profil yenilendiğinde veya şablon değiştiğinde değer sıfırlanıyor olabilir. `refresh_profile`'ın kullanıcının elle girdiği öneki ezmediğini doğrula — daha önce "yalnızca boşsa doldur" kuralı vardı, kümelemeye geçerken kaybolmuş olabilir.

### Beklenen davranış

- Önek profilde saklanır, yenileme onu ezmez
- Üretilen başlık önekle başlar
- Toplam uzunluk 110-140 arasında kalır (önek bütçeye dahil)
- Açıklamanın ilk bloğu önekli başlıkla değiştirilir

---

## C. "Publish all" yenilemeden görünmüyor

Taslak oluşturulduktan sonra "Publish all" butonu belirmiyor; sayfa yenilenince geliyor.

Taslak oluşturma işi tamamlandığında arayüzün durumu güncellemesi gerekiyor. İş takibi zaten var (`GET /jobs/{id}` ile yoklama) — iş bittiğinde içerik listesi yeniden çekilmeli ve butonların görünürlüğü yeniden değerlendirilmeli.

Aynı sorun tek listing için "Publish now" butonunda da olabilir, kontrol et.

---

## D. Üretim şekli profilden

Etsy'deki "How does your shop produce this item?" alanı — `who_made`, `when_made` ve `production_partner_ids` alanlarına karşılık geliyor.

Bunlar zaten referanstan kopyalanıyor olmalı (v4 §C). Taslakta doğru görünüyor mu doğrula; görünmüyorsa neden ulaşmadığını bul.

Taslak oluştuktan sonra `getListing` ile geri okunup referansla eşleştiği doğrulanır — kategori için yapılan kontrolün aynısı.

---

## E. ÇOKLU MAĞAZA

Bir hesap birden fazla Etsy mağazası bağlayabilir ve yayınlarken hedef mağazaları seçer.

### Limitler

```
MAX_SHOPS_PER_TENANT=8
MAX_SHOPS_APP_WIDE=20
```

- Kullanıcı başına tavan admin panelinden kişiye özel ayarlanabilir
- Uygulama geneli tavan sunucuda zorlanır, aşılınca net mesaj
- Kalan slot admin panelinde görünür

Not: Etsy 80 mağazaya izin verdi ama günlük kota hâlâ 5.000. 20 mağaza boşta günde ~360 çağrı harcar (senkronizasyon + profil yenileme); tavanı buna göre tuttuk. Kota artarsa yükseltilir.

### Veri modeli

- `etsy_connection` artık kiracı başına birden fazla olabilir; görünen ad ve sıra alanı eklenir
- `listing_profile` mağazaya ait olur, kiracıya değil — her mağazanın kendi referans listingleri var
- `upload_batch` kiracıya ait kalır; hedef mağazalar yayın anında seçilir
- Mevcut tek bağlantı migration ile yeni yapıya taşınır

### Arayüz

- Ray'in alt bloğu **mağaza değiştiriciye** dönüşür
- Profiller, mağaza listeleri ve kota göstergesi seçili mağazaya göre filtrelenir
- Batch'ler ve yüklemeler kiracı seviyesinde kalır (tasarımlar mağazadan bağımsız)

### Yayınlama

Review ekranında hedef mağaza seçimi:

- Bir veya birden fazla mağaza seçilebilir
- Her mağaza için o mağazanın kendi profili kullanılır (kategori, fiyat, varyasyon, beden tablosu, bölüm)
- Seçilen mağazada uygun profil yoksa o mağaza seçilemez, sebebi gösterilir
- N mağazaya yayın = N ayrı taslak, aynı üretilen içerikten
- Biri başarısız olursa diğerleri devam eder, sonuçlar mağaza bazında gösterilir

### Kota koruması — kritik

Çoklu mağaza yayını çağrı maliyetini katlıyor. Onay öncesi:

- Tahmini çağrı sayısı gösterilir: "3 mağaza × 5 listing ≈ 225 çağrı"
- Kalan günlük bütçeyle karşılaştırılır
- Aşacaksa işlem reddedilir ve kaç listing'in sığacağı söylenir
- %90 duraklama kuralı aynen geçerli

### İzolasyon

B bölümündeki kiracı izolasyonu mağaza seviyesinde de geçerli:

- Kullanıcı yalnızca kendi mağazalarını görür ve yönetir
- Bir mağazanın profili, listingleri ve önbelleği başka kiracıya sızmaz
- B6 tarzı çapraz erişim testleri mağaza bazında da yazılır

### Bağlantı kesme

- Tek mağaza kesilir, diğerleri etkilenmez
- Kesilen mağazanın Etsy kaynaklı tüm içeriği silinir (profiller, önbellek, referans verisi)
- Kullanıcının kendi yüklemeleri ve üretilen içerikleri kalır

---

## F. TESTLER

- Hiçbir saniyede hız sınırının aşılmadığı (50 çağrılık dizide)
- Yeniden denemelerin de bucket'tan geçtiği
- `title_prefix`'in profil yenilemesinde korunduğu ve başlığa eklendiği
- Taslak oluşturma bitince arayüz durumunun yenilenmeden güncellendiği
- `who_made`, `when_made`, `production_partner_ids`'in referanstan geldiği ve geri okumayla doğrulandığı
- Kiracı başına ve uygulama geneli mağaza tavanlarının zorlandığı
- Çoklu mağaza yayınında her mağazanın kendi profilini kullandığı
- Bütçeyi aşacak çoklu mağaza yayınının reddedildiği
- Mağaza bazında çapraz erişim izolasyonu
