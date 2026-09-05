# Arayüz Yeniden Tasarımı

Amaç: uygulamanın görünümünü üst segment bir SaaS ürünü seviyesine çıkarmak. **Davranışı değiştirmeden.**

---

## 0. BOZULMAYACAKLAR — en önemli bölüm

Bu bir **sunum katmanı** çalışmasıdır. Aşağıdakilerin hiçbirine dokunulmaz:

- Backend'de tek satır değişiklik yok. API sözleşmeleri, endpoint'ler, şemalar aynı kalır.
- Frontend'in veri akışı aynı kalır: aynı çağrılar, aynı sırayla, aynı payload'larla
- Durum mantığı aynı kalır: profil seçimi, grup atamaları, `manual` bayrağı, iş kuyruğu takibi, ilerleme sayımı
- Doğrulama mantığı aynı kalır: başlık 110-140, 13 tag, 20 karakter, yasak kelimeler
- Onay ve yayın akışı aynı kalır: **taslak önce, sonra açık kullanıcı eylemiyle yayın**
- Hiçbir buton kaldırılmaz, hiçbir yeni buton eklenmez

### ToU zorunlulukları — birebir korunacak

- Marka ibaresi, tam metin: `The term 'Etsy' is a trademark of Etsy, Inc. This application uses the Etsy API but is not endorsed or certified by Etsy, Inc.`
- Kullanım şartları ve gizlilik politikası linkleri
- Görünür destek e-posta adresi
- API kota göstergesi
- Her listing kartında Etsy geri linki (taslaksa Shop Manager, aktifse herkese açık URL)
- **Etsy turuncusu veya Etsy'nin düzenine benzer bir yerleşim kullanılmaz** (ToU Bölüm 2)

### Doğrulama

Her ekran değişikliğinden sonra:
- `tsc --noEmit` temiz
- `next build` başarılı
- Backend test suite'i çalıştırılır (değişmemeli, ama regresyon olmadığı doğrulanır)
- Uçtan uca akış elle denenir: bağlan → profil → yükle → üret → incele → taslak → yayınla

---

## 1. GÖRSEL YÖN

Hedef: sessiz lüks. Gösterişli değil, pahalı görünen. Linear, Vercel ve Stripe'ın paylaştığı disiplin.

### Renk

Sıcak nötr bir taban, tek bir sofistike vurgu. Etsy turuncusundan uzak.

```
--bg              #FAFAF9   sayfa zemini (sıcak beyaz, saf beyaz değil)
--surface         #FFFFFF   kartlar
--surface-sunken  #F5F5F4   girintili alanlar, tablo başlıkları
--border          #E7E5E4   ince ayrımlar
--border-strong   #D6D3D1   input kenarları
--text            #1C1917   birincil metin
--text-secondary  #57534E   ikincil
--text-muted      #A8A29E   yardımcı, meta
--accent          #4C1D95   derin mor — birincil eylemler
--accent-hover    #5B21B6
--accent-subtle   #F5F3FF   vurgu zeminleri
--success         #166534
--warning         #A16207
--danger          #991B1B
```

Vurgu rengi **cimrice** kullanılır: birincil buton, aktif sekme, seçili durum. Başka hiçbir yerde.

Koyu tema opsiyonel, ilk turda gerekmez.

### Tipografi

```
Başlıklar:  Inter veya Geist, 600 ağırlık, sıkı harf aralığı (-0.02em)
Gövde:      aynı aile, 400
Sayısal:    tabular-nums (kota, maliyet, karakter sayacı hizalı dursun)
```

Ölçek: 32 / 24 / 18 / 15 / 13. Gövde 15px — 14 ucuz, 16 kaba durur.

Satır yüksekliği: başlıklarda 1.2, gövdede 1.6.

### Boşluk

8px ızgara. Cömert kullan — lüks hissi boşluktan gelir, süslemeden değil.

- Kart iç boşluğu: 24px
- Bölümler arası: 48px
- Sayfa kenar boşluğu: 32px, maksimum genişlik 1280px

### Yüzeyler

- Kart: `border-radius: 12px`, `1px solid var(--border)`, gölge yok veya çok hafif (`0 1px 2px rgba(0,0,0,0.04)`)
- Büyük gölge, gradyan, cam efekti **yok**. Ucuzlatır.
- Hover'da yalnızca kenar rengi koyulaşır, kart zıplamaz

### Hareket

- Geçişler 150ms, `cubic-bezier(0.4, 0, 0.2, 1)`
- Yalnızca opaklık ve hafif transform. Boyut animasyonu yok.
- Yükleme durumları: iskelet (skeleton), dönen spinner değil

---

## 2. EKRAN EKRAN

### Genel çatı

**Üst bar:** logo, ana gezinme (Dashboard / Listings / Profiles / Uploads), sağda kota göstergesi ve mağaza durumu.

Kota göstergesi bir rozet değil, ince bir ilerleme çubuğu + sayı olsun. Kalan kota kritik bir bilgi, göze çarpsın ama bağırmasın.

**Alt bilgi:** marka ibaresi, ToS, gizlilik, destek e-postası. Küçük, sessiz, ama okunabilir.

### Dashboard

Şu an yok, eklenmeli. Kullanıcının giriş noktası:

- Mağaza durumu kartı: bağlı mağaza adı, listing sayısı, son senkronizasyon
- Kota kartı: bugün kullanılan / kalan, aylık eğilim
- Son işlemler: son 5 batch, durumları, kaç listing üretildi
- Hızlı eylem: "Yeni yükleme"

### Profiller

Mevcut sayfa iyi çalışıyor ama sıkışık.

- Her profil kartı: ad (satır içi düzenlenebilir), şablon, önek, durum rozeti
- Referans görselleri daha büyük, beden tablosu olanlar net biçimde işaretli — mevcut mor çerçeve iyi, ama etiket daha okunaklı olsun
- Onaylanmamış profiller ayrı bölümde, kehribar tonlu ince bir kenarla
- "Your listings" ızgarası: kart başına küçük resim, başlık, durum, SKU; hover'da aksiyonlar

### Yükleme

- Sürükle-bırak alanı büyük ve net, boş durumda ne beklendiğini anlatan bir çizim veya ikon
- Tespit edilen gruplar tablo halinde: klasör → SKU → görsel sayısı → profil seçimi → durum
- Toplu seçiciler tablonun üstünde, ayrı bir şeritte
- Elle ayarlanmış gruplar görsel olarak işaretli

### İnceleme

Ürünün kalbi, en çok özen buraya.

- İki sütun: solda görsel şeridi, sağda düzenlenebilir alanlar
- Başlık alanı: canlı karakter sayacı, aralık dışındaysa kenar rengi değişir (kırmızı değil, kehribar — hata değil uyarı)
- Tag'ler: her biri ayrı çip, karakter sayısı çipin içinde küçük, geçersizse çip kenarı uyarır
- Açıklama: katlanabilir, ilk 3 satır görünür
- Doğrulama hataları alanın altında, kırmızı kutu içinde değil — ince kırmızı metin yeterli
- Aksiyon çubuğu sabit altta: Onayla / Taslak oluştur / Yayınla, durum göstergesiyle

### İlerleme

Toplu işlemlerde: üstte ince bir ilerleme çubuğu, altında "12 / 30 tamamlandı · 1 başarısız". Modal değil, sayfa içi.

---

## 3. UYGULAMA SIRASI

Tek seferde her şeyi değiştirme. Sırayla:

1. **Tasarım token'ları** — CSS değişkenleri, Tailwind config, tipografi. Hiçbir bileşen değişmez, sadece renkler ve boşluklar oturur.
2. **Paylaşılan bileşenler** — Button, Input, Select, Card, Badge, ProgressBar. Mevcut kullanım yerleri aynı prop'larla çalışmaya devam eder.
3. **Çatı** — Nav, Footer, sayfa düzeni
4. **Profiller sayfası**
5. **Yükleme sayfası**
6. **İnceleme sayfası**
7. **Dashboard** (yeni)

Her adımdan sonra build alınır ve akış elle denenir. Bir adım bozarsa geri alınır, sonrakine geçilmez.

---

## 4. YAPILMAYACAKLAR

- Yeni bağımlılık eklenmez (bileşen kütüphanesi, animasyon kütüphanesi, ikon seti değişikliği)
- Mevcut ikon seti (lucide) korunur
- Sayfa yolları değişmez
- Bileşen prop'ları değişmez — yalnızca içleri
- Backend'e dokunulmaz
- Özellik eklenmez veya kaldırılmaz
