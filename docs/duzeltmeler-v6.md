# Düzeltmeler v6

---

## A. HATALAR

### A1. "Publish all" başarılı olmasına rağmen hata veriyor

Toplu yayında listingler Etsy'ye gidiyor ama arayüz `failed` gösteriyor. İş durumu yanlış okunuyor veya kısmi başarı hatalı işaretleniyor.

Gerçek sonucu geri okuyup doğrula: listing Etsy'de aktifse iş başarılı sayılmalı.

### A2. Manuel alan uyarısı toplu yayını engelliyor

"Before publish all, set these in Shop Manager" kutusu çıkıyor ve toplu yayın yapılamıyor.

- Uyarı bilgilendirme olarak kalsın, **engelleyici olmasın**
- Kullanıcı isterse yine de toplu yayınlayabilsin
- Manuel alan listesi boşsa kutu hiç görünmesin

### A3. "Generate for all" harcama sayacını düşürmüyor

Toplu içerik üretiminde kullanıcının harcama/kota göstergesi değişmiyor.

Hangi sayacın kastedildiğini belirle: Etsy kotası üretimde kullanılmıyor (doğru davranış), ama LLM maliyeti veya kullanıcı başına üretim sayacı varsa o güncellenmelidir. Arayüzde hangi sayaç gösteriliyorsa doğru kaynaktan beslenmeli.

### A4. Overview sayıları yanlış

Aktif/taslak sayıları mağazadakiyle uyuşmuyor. Nereden sayıldığını ve süresi geçmiş önbellek satırlarının tutarsız dahil/hariç tutulup tutulmadığını kontrol et.

---

## B. BAŞLIK VE TAG KALİTESİ — en yüksek öncelik

Mevcut çıktı tasarımdaki yazıyı kopyalıyor, tasarımı **tanımlamıyor**.

Gerçek hatalı çıktılar:
- `Comfort Colors® Deliver, Labor And Delivery`
- `So Is the Flu Wash Your Hands`

Bunlar anlamsız. Alıcı Etsy'de "wash your hands shirt" diye aramıyor.

### Doğru yaklaşım

Başlık ve tag'ler tasarımın **temasından** türetilir: konu, kullanım durumu, alıcı profili, stil, mizah türü.

Tasarımdaki metin bağlamdır — şakayı veya mesajı anlamak için kullanılır, başlığa kopyalanmaz.

Örnekler:

| Tasarımdaki metin | Yanlış başlık | Doğru yaklaşım |
|---|---|---|
| "So is the flu — wash your hands" | `So Is the Flu Wash Your Hands` | Komik hemşire / sağlık çalışanı mizahı |
| "Deliver, Labor and Delivery" | `Deliver, Labor And Delivery` | Doğum hemşiresi hediyesi, L&D nurse |

### Prompt değişiklikleri

- Başlık tasarımın ne hakkında olduğunu anlatır, üzerinde ne yazdığını değil
- Vision çıktısındaki `embedded_text` yalnızca temayı anlamak için kullanılır
- Yukarıdaki gerçek hatalı çıktılar prompt'a karşı örnek olarak eklenir, doğru yazımlarıyla birlikte
- Alıcının arayacağı ifadeler öncelikli: meslek, durum, ilişki, mizah türü, mevsim, hediye bağlamı

### Yasaklı doldurma ifadeleri

Mevcut jenerik listeye ekle: `hand drawn`, `hand-drawn`, `handdrawn`, `illustration`, `artwork`, `design tee`, `graphic print`.

Bunlar arama değeri taşımıyor ve slot harcıyor.

### Marka adları

Yapılandırılabilir yasaklı marka listesi: `TRADEMARK_FILTER=on|off`, varsayılan `on`.

Açıkken Disney, Mickey, Marvel, Nintendo, Pokémon, Star Wars, Harry Potter, Barbie, Nike, Adidas gibi terimler başlık, tag ve açıklamada reddedilir ve düzeltme mesajıyla yeniden üretilir.

Liste bir yapılandırma dosyasında tutulur, kod değişikliği gerektirmeden genişletilebilir.

Gerekçe: ToU Bölüm 1, Etsy politikalarını ihlal etmek için kullanılabilecek uygulama oluşturmayı yasaklıyor.

---

## C. BAŞLIK ÖNEKİ — virgül sorunu

`Comfort Colors®, Funny Nurse Shirt` yerine `Comfort Colors® Funny Nurse Shirt` olmalı.

Önek ilk öbeğin parçasıdır, ayrı bir öbek değil.

---

## D. TOPLU ONAY

Review sayfasında toplu onay yok; kullanıcı tek tek işaretliyor.

- **"Approve all"** butonu, mevcut toplu aksiyonların yanında
- Yalnızca doğrulamadan geçen listingleri onaylar
- Kaç tanesinin onaylandığı ve kaç tanesinin hangi sebeple atlandığı gösterilir
- Sayfanın en çok kullanılan aksiyonu, en görünür yerde olmalı

---

## E. GÖRSEL SIRALAMA VE KAPAK SEÇİMİ

Yükleme sırasında:

- Her grup için görseller küçük resim olarak gösterilir
- Biri **kapak** olarak varsayılan seçilir (alfabetik ilk)
- Kullanıcı başka bir görseli kapak yapabilir
- Görseller sürükleyerek yeniden sıralanabilir
- Seçilen sıra `rank` olarak kaydedilir ve Etsy'ye aynı sırayla yüklenir

---

## F. ZIP YÜKLEME

Klasör yüklemesine ek olarak ZIP desteği:

- Kullanıcı bir veya birden fazla ZIP dosyası yükleyebilir
- ZIP sunucuda açılır, içindeki klasör yapısı aynen korunur (bir klasör = bir listing grubu)
- Kök dizindeki dosyalar tek grup sayılır
- Desteklenmeyen dosyalar sessizce atlanır, kullanıcıya kaç tanesinin atlandığı bildirilir
- Güvenlik: zip slip (yol aşımı) koruması, açılmış boyut sınırı, dosya sayısı sınırı, iç içe ZIP açılmaz
- Görsel doğrulaması mevcut kurallarla aynı (içerikten tip tespiti)

---

## G. ZAMANLANMIŞ YAYIN

Kullanıcı birden fazla günün işini önceden hazırlar, her listing için yayın zamanı belirler, sistem otomatik yayınlar.

### Akış

1. Kullanıcı tasarımları yükler, üretir, **inceler ve onaylar**
2. Taslaklar oluşturulur
3. Her listing veya grup için yayın tarihi ve saati seçilir
4. Zamanı gelince sistem otomatik `state=active` yapar

### Kurallar

- **Taslak-önce ilkesi korunur.** Zamanlama yalnızca kullanıcının tek tek onayladığı taslaklar için yapılabilir. Onaysız listing zamanlanamaz.
- Zamanlama kullanıcının kendi saat diliminde gösterilir, UTC saklanır
- Zamanlanmış iş kuyruğa girer, kota kurallarına tabidir; kota doluysa ertelenir ve kullanıcı bilgilendirilir
- Yayından önce `blocking` compliance bulgusu kontrol edilir
- Kullanıcı zamanlamayı iptal edebilir veya değiştirebilir
- Zamanlanmış listingler ayrı bir görünümde listelenir: ne zaman, hangi mağaza, hangi listing

### Arayüz

- Review ekranında "Publish now" yanında **"Schedule"**
- Toplu zamanlama: seçili listingler için tek seferde tarih/saat, isteğe bağlı aralıklı dağıtım (örn. günde 5 tane)
- Takvim benzeri bir özet görünüm

---

## H. REFERANS YENİLEME — sürtüşmeyi kaldır, kuralı koru

Kullanıcı elle "Refresh reference" basmak zorunda kalıyor. Bu kaldırılacak, ama **saklama süreleri korunacak** (ToU Bölüm 1, 6 saat / 24 saat).

### Çözüm: arka planda otomatik yenileme

- Periyodik bir iş, süresi dolmak üzere olan referansları **kendiliğinden yeniler**
- Görseller için 5 saatte bir, yapısal veri için 20 saatte bir — süre dolmadan önce
- Kullanıcı hiçbir şey yapmaz, profil her zaman taze görünür
- Elle "Refresh reference" butonu kalır ama gerekmez
- Yenileme başarısız olursa (mağaza bağlantısı koptu vs.) kullanıcıya bildirilir

### Kota etkisi

Mağaza başına günde ~18 çağrı — mevcut hesaba zaten dahil. Otomatik yenileme bunu artırmaz, sadece zamanlamasını değiştirir.

---

## I. TESTLER

- Toplu yayında Etsy'de aktif olan listingin `failed` işaretlenmediği
- Manuel alan uyarısının toplu yayını engellemediği
- Başlığın tasarım metnini kopyalamadığı (gerçek hatalı örneklerle)
- Yasaklı marka terimlerinin reddedildiği, filtre kapalıyken reddedilmediği
- Önekten sonra virgül gelmediği
- Toplu onayın yalnızca geçerli listingleri onayladığı
- Kapak seçimi ve sıralamanın Etsy'ye doğru yansıdığı
- ZIP'in klasör yapısını koruduğu, zip slip saldırısının engellendiği
- Zamanlanmış yayının yalnızca onaylanmış taslaklar için yapılabildiği
- Zamanı gelen işin çalıştığı, kota doluysa ertelendiği
- Otomatik yenilemenin süre dolmadan çalıştığı ve saklama sınırlarının korunduğu
