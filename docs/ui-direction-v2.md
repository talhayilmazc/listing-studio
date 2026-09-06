# Arayüz Yönü v2 — Yapısal Yeniden Kurulum

`docs/ui-redesign.md` §0 (bozulmayacaklar) aynen geçerlidir. Bu belge §1 ve §2'nin yerine geçer.

Sorun renk değil **düzen**. Şu anki sayfa ortada dar bir sütun, iki yanı boş. Referans ürünlerde her alanın bir işi var.

---

## 1. KABUK — en büyük değişiklik

Üstteki yatay gezinme kalkar, yerine **kalıcı sol ray** gelir.

```
┌────────────┬──────────────────────────────────────────────┐
│            │  Üst şerit: sayfa başlığı + birincil eylem   │
│  Sol ray   ├──────────────────────────────────────────────┤
│  240px     │                                              │
│  koyu      │  İçerik — tam genişlik, ortalanmış sütun yok │
│            │                                              │
│  · logo    │                                              │
│  · Overview│                                              │
│  · Batches │                                              │
│  · Profiles│                                              │
│  · Uploads │                                              │
│            │                                              │
│  ── alt ── │                                              │
│  mağaza    │                                              │
│  kota çubuğu│                                             │
└────────────┴──────────────────────────────────────────────┘
```

**Sol ray koyu** (`#1C1917` civarı), içerik alanı sıcak açık (`#FAFAF9`). Bu kontrast tek başına ürünü pahalı gösterir ve ürün fotoğrafları açık zeminde doğru görünür.

Ray içinde:
- Üstte logo, monogram + kelime işareti
- Gezinme öğeleri, aktif olan vurgu renginde ince bir sol çizgiyle
- Altta ayrı bir blokta: bağlı mağaza adı ve avatarı, günlük kota ince çubukla, kalan sayı

Mobilde ray gizlenir, hamburger ile açılır.

**Maksimum genişlik yok.** İçerik alanı ekranı doldurur, sadece 1800px üstünde ortalanır.

---

## 2. TİPOGRAFİ — lüks buradan gelir

İki aile:

```
Display (sayfa başlıkları, büyük sayılar):
  Instrument Serif veya Fraunces — serif, 400 ağırlık
  Sadece h1 ve büyük metrik sayılarda

Arayüz (geri kalan her şey):
  Inter — mevcut
```

Serif başlık, sans arayüz kombinasyonu editoryal ve pahalı okunur. Her yerde serif kullanma — sadece sayfa başlıkları ve öne çıkan sayılar.

Sayfa başlığı 40px serif, altında 15px sans açıklama.

---

## 3. ÜST ŞERİT — özet metrikler

Her sayfanın üstünde, içerikten önce, ince bir metrik şeridi:

```
Bu ay yayınlanan      Bekleyen taslak      Bugünkü kota      Aktif profil
     127                    14               1,998 / 2,000         2
   ↑ %23                                    ▓▓▓▓▓▓▓▓▓░           
```

Sayılar **serif ve büyük** (32px), etiketler küçük ve soluk. Kota için ince çubuk. Değişim varsa küçük bir ok ve yüzde.

Bu şerit boşluğu doldurmaz — bilgi verir. Kullanıcı sayfaya girdiğinde durumu bir bakışta görür.

---

## 4. IZGARA — içerik ekranı doldurur

Batch listesi dikey satırlar değil, **kart ızgarası**:

- Geniş ekranda 3 sütun, orta 2, dar 1
- Her kart bir görsel mozaikle başlar: kartın tam genişliği, 16:10 oran
  - Bir baskın kare + yanında 2×2 daha küçük kare
  - Görseller kartın kahramanı, 60px pul değil
- Mozaiğin altında kompakt meta: başlık (serif, 18px), profil çipleri, ilerleme durumu, göreli zaman
- Hover: kenar vurguya döner, mozaik %2 ölçeklenir, birincil eylem belirir

**Yeşil çubuk kalkar.** Yerine tek satır durum: "5 yayınlandı" vurgu tonunda. İlerleme çubuğu yalnızca iş gerçekten devam ediyorsa görünür.

---

## 5. DOKU VE DERİNLİK

Referanslardaki "pahalı" hissin kaynağı:

- **Katmanlı yüzeyler:** koyu ray → açık içerik → beyaz kart. Üç seviye, net ayrım.
- **İnce kenarlar:** 1px, düşük kontrast. Gölge neredeyse yok.
- **Cömert iç boşluk** ama **sıkı ızgara** — kartlar arası 16px, kart içi 20px.
- **Görsel kenarları yumuşak:** mozaik köşeleri 8px, kart 12px.

Yapılmayacaklar: gradyan zemin, cam efekti, büyük gölge, animasyonlu arka plan. Referans 2'deki cam görünümü kötü uygulanınca ucuzlar; referans 1 ve 3'ün disiplini hedef.

---

## 6. RENK — güncellenmiş

```
--rail            #1C1917   sol ray zemini
--rail-text       #A8A29E   ray metni
--rail-active     #FAFAF9   aktif öğe
--bg              #FAFAF9   içerik zemini
--surface         #FFFFFF   kartlar
--border          #E7E5E4
--text            #1C1917
--text-secondary  #57534E
--text-muted      #A8A29E
--accent          #4C1D95   birincil eylem, aktif durum
--accent-subtle   #F5F3FF
```

Vurgu rengi hâlâ cimrice: birincil buton, aktif gezinme, seçili kart kenarı. Başka yerde yok.

---

## 7. UYGULAMA SIRASI

1. **Kabuk** — sol ray, düzen ızgarası, üst şerit iskeleti. Sayfalar geçici olarak eski içerikleriyle yeni kabuğun içine girer.
2. **Tipografi** — serif ailesi eklenir (Google Fonts veya `next/font`), başlıklara uygulanır
3. **Metrik şeridi** — gerçek verilerle
4. **Batch ızgarası** — mozaik kartlar
5. **Profiller**
6. **Yükleme**
7. **İnceleme**

Her adımdan sonra `tsc --noEmit`, `next build` ve elle uçtan uca kontrol.

---

## 8. DEĞİŞMEYENLER

`docs/ui-redesign.md` §0 aynen geçerli:

- Backend'e dokunulmaz (onaylanan iki istisna dışında: `?w=` önizleme ve kota geçmişi)
- API sözleşmeleri, davranış, doğrulama, onay-taslak-yayın akışı aynı
- ToU unsurları: marka ibaresi, ToS/gizlilik, destek e-postası, kota göstergesi, Etsy geri linkleri
- Etsy turuncusu veya Etsy'ye benzer düzen yok
- Özellik eklenmez, kaldırılmaz
- Yeni bağımlılık yok — tek istisna serif yazı tipi (`next/font` üzerinden, paket değil)
