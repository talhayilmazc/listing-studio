# Etsy Listing Assistant

Etsy satıcılarının kendi orijinal tasarımlarından uyumlu **taslak listing** hazırlamasına yardımcı olan çok kiracılı SaaS.

Aşama: kapalı beta, 5 kullanıcı. Etsy erişim seviyesi: Personal App.

> Etsy bu uygulamayı **en fazla 80 bağlı mağaza** için onayladı. Ücret almaya başlamadan önce Commercial Access başvurusu zorunludur.
>
> **Kendi tavanlarımız daha düşük ve bilerek öyle: hesap başına 8 (`MAX_SHOPS_PER_TENANT`), uygulama geneli 20 (`MAX_SHOPS_APP_WIDE`).** Bunları Etsy'nin mağaza sınırı değil, **günlük 5.000 istek bütçesi** belirler: bütçe tüm mağazalar arasında paylaşılır. Kullanılmayan bir mağaza arka planda istek harcamaz. Her gün kullanılan bir mağaza, görüntülendikçe senkronizasyon (önbellek 6 saatten eskiyse; 5 listing durumunun her biri için en az 1 istek, her 100 listing için 1 istek: küçük mağazada ~6, 3.000 listingli mağazada ~36; günde en fazla 4 kez) ve **son 14 günde kullanılan her profil için ~12 istek** (görsel linkleri 5 saatte bir 1 istek, yapısal veri 20 saatte bir 6 istek) harcar: 2 profilli aktif bir mağaza ≈ 36/gün, 20 aktif mağaza ≈ 720/gün (bütçenin ~%15'i); üstüne her taslak ~15 istek. 80 aktif mağazada (~2.900/gün) bütçenin çoğu yalnızca mağazaları güncel tutmaya giderdi. **Bu tavanlar, Etsy'den günlük kota artışı alınmadan yükseltilmez**; kota artarsa aynı hesapla birlikte yükseltilir.

---

## MUTLAK KISITLAR — asla ihlal edilmez

Bunlar Etsy API Terms of Use'dan gelir. İhlali geliştirici hesabının ve kullanıcı mağazalarının kapatılması demektir. Bir özellik bunlardan birine dokunuyorsa **yazma, önce sor**.

1. **Scraping yasak.** Etsy sitesine yalnızca resmi Open API v3 üzerinden erişilir. Selenium, Playwright, Puppeteer, tarayıcı eklentisi, HTML parse — hiçbiri kullanılmaz. Bu yasak, **veri API'de mevcut olmasa bile** geçerlidir (ToU Bölüm 9). Etsy'nin dahili/eski API'lerine veya site içi veri akışlarına erişilmez.
2. **Rakip analizi yasak.** Başka satıcıların listingleri, tagleri, görselleri veya fiyatları toplanmaz, analiz edilmez, model eğitiminde kullanılmaz. Yalnızca kimliği doğrulanmış kullanıcının **kendi** mağaza verisine erişilir. Üye izni olmadan Etsy üyeleri hakkında kişisel veri toplanamaz/işlenemez.
3. **Otomatik yayın yok.** Her listing `createDraftListing` ile taslak olarak oluşturulur. Yayın her zaman satıcının arayüzdeki açık onayını gerektirir; bu onay iki biçimde verilebilir: **şimdi yayınla**, ya da satıcının **tek tek onayladığı bir taslak için belirli bir zaman planlamak** (v6 §G). Satıcının onaylamadığı ve kendisinin zamanlamadığı hiçbir şey yayınlanmaz. Listing bazında onay olmadan otomatik yayın ("auto-publish") **yasak olmaya devam eder** ve eklenmez.
4. **Etsy Ads endpoint'i yoktur.** Reklam açma/kapama/bütçe özelliği yazılmaz.
5. **Rate limit: 5.000 istek/gün, 5 istek/saniye** — uygulama bazında, kullanıcı bazında değil (Personal App). Güvenlik payı için **3 req/s, burst olmadan** hedeflenir (4 req/s + 4 jetonluk burst, eşzamanlı işlerde bir saniyeye 7 istek sığdırıp 429 üretti — v5 §A); global günlük bütçe 5.000.
6. **Token'lar şifreli saklanır.** Asla loglanmaz, asla hata mesajında görünmez, asla frontend'e gönderilmez.
7. **Checkout akışı taklit edilemez.** Etsy'nin ödeme/checkout deneyimini kopyalayan veya devre dışı bırakan hiçbir şey yazılmaz.
8. **Trafik başka yere yönlendirilemez.** Uygulama, kullanıcıyı veya alıcıyı Etsy dışı platformlara taşımak için kullanılamaz.
9. **API erişimi satılamaz, kiralanamaz, alt lisanslanamaz.** Ürün bir hizmet aboneliğidir; hiçbir yerde "API erişimi kiralama" olarak konumlandırılmaz. Kaynak kod veya white-label satışı yapılmaz.

## Cache ve veri saklama kuralları

ToU Bölüm 1'den gelir, ihlali doğrudan sözleşme ihlalidir.

| Veri tipi | Maksimum yaş |
|---|---|
| Listing içeriği, ürün bilgisi, görseller | **6 saat** |
| Diğer Etsy içeriği | **24 saat** |

- Etsy içeriği (Member Content) yalnızca **hizmeti sunmak için makul süre** boyunca saklanabilir. Süresiz saklama yasaktır.
- `listing_snapshot` tablosu Member Content içerir. **Retention politikası zorunlu: 90 gün sonra otomatik silinir.** Bunu yapan periyodik bir temizlik job'ı olmalıdır.
- Kullanıcı bağlantısını kestiğinde (`etsy_connection` → `revoked`) o kullanıcıya ait tüm Etsy kaynaklı içerik silinir.
- **Satış ve reklam verisi (v7 §C):** `sales_daily` ham işlem değil, satıcının kendi mağazasından türetilmiş günlük toplamdır (listing başına gün başına adet, sipariş, gelir); işlem yanıtı aynı job'da okunur, toplanır ve atılır, alıcıya ait hiçbir alan yazılmaz. `ad_spend` satıcının Shop Manager'dan indirip yüklediği Etsy Ads CSV'sinden gelir; dosyanın kendisi saklanmaz, yalnızca kendi listinglerine eşleşen satırların harcama/sipariş/geliri. İkisi de **13 ay** (`SalesDaily.RETENTION_DAYS`) tutulur, mağaza bağlantısı kesilince hemen silinir. Analiz önerileri yalnızca Shop Manager'a yönlendirir; reklam kapatma vb. uygulamadan yapılmaz (kural 4).
- **Satış okuma maliyeti (`workers/sales.py`):** Etsy'nin transactions endpoint'i yalnızca `limit` (en çok 100) ve `offset` alır; tarih filtresi ve sıralama yoktur. Maliyet listing sayısına değil **satış sayısına** bağlıdır: 100 satış = 1 istek. İlk okuma öncesi tahmin (1 istek + ~log₂(toplam satış) ikili arama) satıcıya gösterilir; okuma arka planda 20 sayfalık parçalarla ilerler, her sayfanın toplamı ve konumu aynı transaction'da yazılır (kesilirse kaldığı yerden devam), mağaza başına günde en çok `SalesSync.DAILY_REQUESTS` (250) istek, aşarsa ertesi güne yayılır. Sonrası yalnızca yeni satışlar (genelde 1 istek/gün). Alıcı alanlarına dokunulmaz; imleç olarak yalnızca satışın oluşturulma zamanı ve transaction id'si saklanır. `getShopReceipts` (tarih filtresi var) alıcı adı/adresi/e-postası döndürdüğü için kullanılmaz.
- **Ödeme hesabı defteri (`workers/ledger.py`, `ledger_daily`):** mağazanın gerçek Etsy ücretleri ve reklam harcaması `getShopPaymentAccountLedgerEntries`'ten okunur (scope `transactions_r`, yeni izin gerekmez; `min_created`/`max_created` zorunlu, `limit` ≤ 100, `offset`). Kayıtlardan yalnızca **tür, tutar ve tarih** alınır ve **tür başına günlük toplam** olarak yazılır; kayıtların kendisi, açıklaması ve referansları saklanmaz. `prolist` + `offsite_ads_fee` = mağaza geneli reklam harcaması; listing bazında harcama API'de yoktur, yalnızca yüklenen Ads CSV'sinden gelir (aradaki fark arayüzde "not attributed" olarak gösterilir). Tür adları Etsy'nin şemasında listelenmediği için yalnızca bilinen türler maliyet sayılır (`pipeline/ledger.py::CATEGORIES`); diğerleri (ödeme, satış, vergi, tanınmayan) listelenir ama sayılmaz. İlk okuma son `LedgerSync.FIRST_DAYS` (90) günü kapsar (defterde sipariş başına ~5 kayıt var; 13 ayı geriye okumak pahalı), tahmin önce gösterilir, günde en çok 250 istek, kesilirse kaldığı yerden devam eder; sonra her gece yalnızca yeni kayıtlar. **13 aylık geçmiş arka planda geriye doğru doldurulur** (`backfill_ledger`; geçen yılın sezonuyla karşılaştırma için): mağazanın satış okuması ve ilk defter okuması bitince başlar, 30 günlük dilimlerle geriye gider, her sayfanın toplamı ve konumu birlikte yazılır (kesilirse kaldığı yerden; dilimler çakışmaz, çift sayım olmaz). Uygulamadaki **en düşük öncelikli** Etsy işidir: mağazanın günlük defter payını (250) gecelik güncellemeye `BACKFILL_RESERVE` (30) bırakarak paylaşır, uygulama günlük bütçenin %50'sini (`BACKFILL_GLOBAL_PERCENT`) kullandıysa o gün bekler, gerektiği kadar güne yayılır; gecelik cron devam ettirir. Geçmişin henüz ulaşmadığı dönemler satıcının oranlarıyla **tahmin** edilir ve arayüzde nedeniyle birlikte öyle etiketlenir; ilerleme (ulaşılan tarih, hedef, kalan tahmini istek) Analytics'te gösterilir. Bir okumayı yeniden başlatmak (`begin`) o mağazanın defter toplamlarını önce siler. Saklama 13 ay; bağlantı kesilince hemen silinir.
- **Analytics rakamları (`pipeline/finance.py`):** her rakam kaynağını söyler (satışlar / Etsy defteri / defterden paylaştırılmış / oranlardan tahmin / satıcının maliyetleri / Ads raporu). **Arkasında veri olmayan rakam sıfır değil boş (`None`) döner ve nedeni yanında yazar**; satış okuması bitmiş ama satış yoksa gerçek sıfırdır. Sipariş bazında döküm yoktur ve eklenmez: yalnızca günlük toplamlar tutulur (alıcı verisi yok).
- Cache yaşı arayüzde gösterilen her listing için kontrol edilir; süresi geçmişse gösterilmez, yeniden çekilir.
- **Profil yenileme (v6 §H):** son 14 günde listing yazan veya taslak oluşturan onaylı profiller arka planda, sınırları dolmadan yenilenir (görseller 5 saatte, yapı 20 saatte bir). Kullanılmayan profiller arka planda yenilenmez; Profiles sayfası açılınca, bir batch için seçilince veya onunla içerik üretilirken yenilenir (`app/etsy/refresh.py`).
- **Referans görsel baytları saklanmaz.** Beden tablosu sınıflandırması için satıcının kendi referans listing görselleri API'nin verdiği CDN URL'inden çekilir, **yalnızca bellekte** sınıflandırılır (yerel sezgisel; belirsizse vision fallback) ve baytlar hemen atılır. Yalnızca **sınıflandırma sonucu** (`size_chart`/`artwork`) ve türetilen `fixed_image_ids` saklanır. Görsel baytı diske/DB'ye yazılmadığı için 6 saatlik görsel cache yükümlülüğü doğmaz; saklanan sonuç, profilin 24 saatlik `cached_payload` yenilenmesine tabidir.

## Zorunlu uyum unsurları (arayüzde bulunacak)

- **Marka ibaresi, birebir bu metin** (değiştirilmez, kısaltılmaz):
  ```
  The term 'Etsy' is a trademark of Etsy, Inc. This application uses the Etsy API but is not endorsed or certified by Etsy, Inc.
  ```
- **Geri link zorunluluğu:** ürün bilgisi veya görseli gösterilen her yerde, Etsy'deki ilgili listing'e doğrudan link verilir. Listing kartı bileşeni bu linki her zaman içerir.
- Görünür destek e-posta adresi
- Kullanım şartları + gizlilik politikası, tıklayarak kabul
- Kalan API kotasının kullanıcıya gösterilmesi
- Etsy logosu veya markası kullanılırsa, uygulamanın kendi markasından **daha az belirgin** olmalı; hiçbir şekilde onay/ortaklık ima edilmez
- Uygulama adında ve web sitesi başlığında "Etsy" kelimesi **kullanılamaz**

## Marka filtresi ve karakter içeren tasarımlar

- Marka filtresi marka/karakter adlarını başlık, tag ve açıklamada reddeder. **Satıcı kendi Settings sayfasından** açıp kapatır (`tenant.trademark_filter_seller`, varsayılan açık); kapatmak, Etsy'nin fikri mülkiyet politikasını, toplu takedown bildirimlerini ve listing kaldırma/mağaza askıya alma riskini açıkça söyleyen bir onay ister ve sunucu onay metninin sürümünü doğrular (`TRADEMARK_RISK_VERSION`, metin değişirse ikisi birlikte). Admin hesap bazında geçersiz kılabilir (`tenant.trademark_filter`, None = satıcının seçimi) ve satıcının seçimini görür. İki yol da `user.trademark_filter_changed` olarak audit'lenir (`by`: seller/admin). Geçerli değer `compliance/trademarks.filter_on`; `TRADEMARK_FILTER=false` uygulama genelinde kapatır. Kullanım Şartları satıcının yüklediği tasarımların fikri mülkiyetinden ve Etsy politikalarına uyumdan sorumlu olduğunu söyler.
- Vision tanınabilir karakter, franchise görseli veya tema parkı görürse (`characters`) listing işaretlenir, çünkü **çizimin kendisi** başlık nasıl yazılırsa yazılsın ihlal edebilir. Filtre **açıksa bu engelleyicidir** (metin filtresi modelin markayı "mouse ears" gibi dolaylı ifadelerle yazmasına yol açmamalı; kaçış yolu değil). Filtre **kapalıysa** (satıcı riski kabul etti) yayın öncesi kartta **uyarı** gösterilir.
- İçerik prompt'u markanın etrafından dolaşmayı (dolaylı ifade) açıkça yasaklar.

## Mimari kuralı

**Her Etsy API çağrısı kuyruk üzerinden geçer.** Servis katmanından doğrudan `httpx` ile Etsy'ye istek atılmaz. Tek istisna: OAuth token değişimi.

```
Job → queue → tenant kota kontrolü → global token bucket (3 req/s, burst yok) → Etsy API
                                            ↓ 429
                  Retry-After / exponential backoff, tüm worker'lar bekler;
                  yeniden deneme yine kota + bucket'tan geçer
```

**Her job önce `app/workers/gate.py`'dan geçer.** Askıya alınmış kiracının işi çalışmaz (askıya alma kuyruktaki job'ları iptal eder). Global kullanım `GLOBAL_PAUSE_PERCENT` (%90 = 4.500) seviyesine ulaşınca veya işin tahmini maliyeti kiracının kalan kotasına sığmıyorsa, yeni işler **ilk istekten önce** 00:00 UTC'ye ertelenir; sebep job'a ve kullanıcıya gösterilen uyarıya yazılır. `GLOBAL_DAILY_LIMIT` Etsy'nin gerçek tavanı olan 5.000'de kalır. Yeni bir job türü eklenirse `JOB_COST`'a maliyet üst sınırı eklenir.

**Taslak oluşturma kaldığı yerden devam eder; yeniden deneme asla ikinci taslak üretmez (`etsy/publisher.py`, `workers/recovery.py`).** `createDraftListing` gönderilmeden önce `draft_attempt` yazılır ve Etsy listing id'yi döndürdüğü anda saklanır; publication satırı ancak her adım bitince yazılır. Sonraki deneme o listing'i okur ve devam eder: ayarlar yeniden yazılır (idempotent), yalnızca Etsy'de eksik olan görseller yüklenir (görseller sırayla yüklendiği için listing'deki görsel sayısı neyin bittiğini söyler). Yanıtı gelmeyen bir create/görsel POST'u **körlemesine yeniden gönderilmez**: önce Etsy'ye bakılır (mağazanın kendi taslakları arasında gönderilen başlık; listing'in görsel sayısı). İstemci yalnızca tekrarı zararsız istekleri (GET/PUT/PATCH/DELETE) yanıtsızlık ve 5xx'te tekrarlar; 429 her metod için tekrarlanır. Geçici nedenle bitmeyen iş **başarısız sayılmaz, bekler ve kendiliğinden yeniden çalışır** (`job.paused_reason`: `etsy_rate_limit`, `etsy_unavailable`, `interrupted`; günlük bütçe biterse reset'e kadar); `max_attempts` dolunca nedeniyle başarısız olur. Bayat profil (24 saat ya da eski `payload_version`) işin içinde **bir kez** yenilenir. Bir iş aynı anda iki kez çalışmaz (Redis `job-run:{id}` kilidi); ölen worker'ın "running" bıraktığı işler `recover_interrupted_jobs` cron'u ile yeniden kuyruğa alınır. arq ayarları açıkça verilir (`WORKER_JOB_TIMEOUT`=900 sn, `WORKER_MAX_JOBS`=10); varsayılan 300 sn, hacimde taslağı yarıda kesiyordu. Gerçek hata kartta adım ve Etsy'nin kendi tek satırlık nedeni ile gösterilir (`etsy/errors.py::explain`; yalnızca listing'in sahibine, token redaksiyonundan geçerek) ve tek tıkla yeniden denenir. `tests/test_publish_volume.py` 50 taslağı gerçek arq worker'ı, gerçek bucket ve kota üzerinden, 429/yanıtsızlık/5xx/zaman aşımı enjekte edilmiş sahte Etsy'ye karşı çalıştırır.

**Zamanlanmış yayın saat dilimi.** Her hesabın IANA saat dilimi var (`tenant.time_zone`; ilk girişte tarayıcıdan alınır, Settings'ten değişir). Zamanlar hesabın diliminde duvar saati olarak girilir/gösterilir (her zamanın yanında kısaltma: "5:00 PM CDT"), sunucuda kaydedilirken **bir kez** UTC'ye çevrilir (`app/core/timezones.py`, o tarihin kurallarıyla; yaz saati değişimi seçilen duvar saatini korur) ve çalışan, saklanan UTC anıdır. Tarayıcı saat dilimi hesaptan farklıysa arayüz bunu söyler. Günün zamanlanmış yayınlarının ihtiyaç duyduğu kota diğer işlerden ayrılır (`gate.scheduled_reserve`); aksi hâlde gün içindeki bir batch bütçeyi bitirip yayını 00:00 UTC'ye (ABD Central yaz saatiyle 19:00) itebilir.

**Ürün kullanım hakkı (allowance) ≠ Etsy API kotası.** Satıcının kullanım hakkı (`core/allowance.py`) **üretilen listing + oluşturulan taslak** sayar (yeniden üretme ve Replace images dahil), ham Etsy isteklerini değil. Miktar + dönem (günlük/haftalık/aylık) satıcı başına admin panelden, yoksa sistem varsayılanı (`app_setting`, yoksa config). Dönemler satıcının saat diliminde: gün gece yarısı, hafta Pazartesi 00:00, ay ayın 1'i. Kullanım zaman damgalı olay olarak tutulur (`allowance_use`), dönem bunun üzerinde bir penceredir; ayar değişince hemen uygulanır, kayıtlı kullanım kaybolmaz. İş başlamadan kontrol edilir (kuyruktaki taslaklar kullanılmış sayılır); dolunca 429 ve sıfırlanma tarihini söyleyen mesaj. Değişiklikler audit'lenir (`user.allowance_changed`, `app.allowance_default_changed`). Etsy API kotası (`tenant.daily_quota`) ayrıdır ve günlük kalır; arayüzde ikisi ayrı etiketlenir.

**Bir hesap birden fazla mağaza bağlayabilir (v5 §E).** Her mağaza bir `etsy_connection`; bir Etsy hesabı bir mağazadır ve bir mağaza aynı anda tek bir hesaba aittir. `listing_profile` ve `shop_listing_cache` mağazaya aittir; batch'ler ve üretilen içerik hesaba. Aynı içerikten N mağazaya yayın N taslak (`listing_publication`), her biri o mağazanın kendi profiliyle. Mağaza seçen her endpoint (`?shop=`, yayın hedefleri) mağazanın çağıranın kendi aktif mağazası olduğunu `owned_shop` ile doğrular; değilse 404. Tavanlar: hesap başına `MAX_SHOPS_PER_TENANT` (admin kişiye özel değiştirebilir), uygulama geneli `MAX_SHOPS_APP_WIDE`. Mağaza bağlantısı kesilince yalnızca o mağazanın Etsy içeriği silinir.

**Mağaza önce seçilir; profil listesi asla karışık değildir (Priority 2).** Hedef mağaza batch'in (`upload_batch.connection_id`) ve her grubun (`listing_group_setting.connection_id`) saklanan, açık seçimidir; profilin ait olduğu mağazadan dolaylı çıkarılmaz. Birden fazla mağaza varken mağaza adı verilmeden oluşturulan batch'in mağazası boştur (satıcı seçene kadar hiçbir şey bir mağazaya atanmaz); upload sayfası mağaza değiştiricideki mağazayı **önerir ve gösterir**. Bir grubun profili ve beden tablosu profili her zaman o grubun mağazasına aittir: başka mağazanın profili 422 ile reddedilir; grup başka mağazaya taşınınca profili o mağazadaki eşleşen profille (aynı ad, yoksa aynı türden tek profil) değişir, beden tablosu kendi profiline döner. Tek bir gruba yapılan seçim, satıcının elle ayarlamadığı sonraki gruplara taşınır (içeriği yazılmış gruplar hariç); `group_keys` ile seçili gruplara toplu uygulanır. `ProfilePicker` yalnızca verilen `shopId`'nin profillerini listeler. Çok mağazaya yayın bir **matris**tir (`POST /batches/{id}/publish/preview` → `columns`/`rows`): her listing × her mağaza için "oluşturulacak", "taslak/yayında var" ya da nedeniyle "olamaz"; mağaza ve toplam tahmini istek sayısı. Varsayılan olarak listing yalnızca yazıldığı mağazaya gider; başka mağazaya yalnızca satıcının işaretlediği hücreler (`pairs`) gönderilir. Listing, taslak, zamanlama ve rakam gösterilen her yerde mağaza adı görünür (`ShopBadge`).

**Yayın geçmişi batch'ten uzun yaşar.** Batch silmek yüklemeleri ve çalışma içeriğini (asset, generated_content, grup ayarları, compliance bulguları) siler; bir listing'in oluşturulduğunun ve yayınlandığının kaydını **silmez**: `listing_publication.content_id` `SET NULL` olur (eskiden CASCADE'di ve satıcı biten batch'leri temizleyince tüm yayın geçmişi, admin'deki yayın sayısı ve Analytics'in SKU/profil eşlemesi kayboluyordu). Kayıt kendi `title`, `sku` ve `published_at` alanlarını taşır. Silinen batch'in bekleyen zamanlaması iptal edilir; silme `batch.deleted` olarak audit'lenir. Job geçmişi ve `listing_snapshot` (90 gün) zaten kalır; kaybolan kayıtlar bunlardan `python -m app.cli rebuild-publications [--apply]` ile geri kurulur (Etsy'den hiçbir şey okunmaz). Publication'a bağlı yeni bir tablo eklenirse aynı kural geçerlidir: geçmiş, analiz ve sayaçların dayandığı hiçbir kayıt batch silmeyle gitmez.

**Batch adı ve görsel silme.** Batch'in adı satıcının verdiği addır (`upload_batch.name`), yoksa içeriğinden türetilir ("BR5229 + 4 more"; `pipeline/batch_names.py`); ad sunucuda hesaplanır ve batch'e atıf yapılan her yerde (Batches, batch ve review başlığı, zamanlamalar, toplu işlem özetleri) aynı gösterilir; Batches sayfası ada göre aranır. Bir gruptan görsel silmek (`DELETE /api/assets/{id}`) yüklemeyi ve önizlemelerini siler, sırayı sıkıştırır; kapak silinirse sıradaki kapak olur ve kayıtlı kapak kırpması düşer; grubun yazılmış içeriği **silinmez**, yeni kapağa taşınır (içerik tasarıma aittir, tek fotoğrafa değil). Son görsel silinirse grup ve içeriği gider (yayın kayıtları yine kalır). Etsy'deki hiçbir şey değişmez: silme yalnızca bundan sonra oluşturulacak taslakları ve "Replace images"ı etkiler; arayüz bunu onaydan önce söyler.

**Admin rolü kiracı izolasyonunu delmez.** `/api/admin/*` yalnızca hesap üst verisi (e-posta, bağlı mağaza adı, kayıt tarihi, yayın sayısı) ve kota görür; başka bir kiracının tasarımlarını, batch'lerini, profillerini veya üretilen içeriğini okuyan admin endpoint'i yazılmaz. Her admin endpoint'i `is_admin`'i sunucuda kontrol eder, admin olmayana 404 döner, her işlem `audit_log`'a yazılır. Admin yalnızca CLI ile atanır (`python -m app.cli create-admin`).

## Etsy API erişim notları (doğrulandı — Personal App aktif)

- **`x-api-key` başlığı `{keystring}:{shared_secret}` biçiminde gönderilir**, yalnızca keystring değil. Sadece keystring gönderilirse `openapi-ping` **403** döner. İki değer de `.env`'den (`ETSY_CLIENT_ID` = keystring, `ETSY_CLIENT_SECRET` = shared secret) okunur; asla loglanmaz.
- OAuth 2.0 PKCE: authorization + token endpoint'i client_id (keystring) ve `code_verifier` ile çalışır; token değişiminde `x-api-key` gerekmez. `x-api-key` yukarıdaki biçimde yalnızca **API v3 çağrılarında** kullanılır.
- Scope'lar: `listings_r listings_w shops_r shops_w transactions_r`. Redirect URI: `http://localhost:8000/api/auth/etsy/callback`.
- **`transactions_r` (2026-09-26'da eklendi) yalnızca satıcının kendi satış analizi için.** Satıştan yalnızca listing id, adet, fiyat ve tarih okunur. **Alıcı alanları (isim, adres, e-posta, mesaj) hiçbir yere yazılmaz**; ham yanıt aynı job içinde okunur, toplanır ve atılır (kural #2). Bu tarihten önce bağlanan mağazalar izni vermek için bir kez yeniden bağlanır (`missing_scopes`); o zamana kadar satış verisi gösterilmez.

## Yığın

- Backend: Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic
- Kuyruk: Redis + arq
- DB: PostgreSQL
- Frontend: Next.js (App Router), TypeScript, Tailwind
- Görsel işleme: pyvips (fallback: Pillow)
- Storage: yerel Docker volume (`LocalStorage`); S3 uyumlu depolama (R2) henüz yok
- Deploy: Docker Compose + Cloudflare Tunnel, Türkiye'de tek VPS — adımlar `docs/deploy.md`. **Güncelleme yalnızca `bash deploy/update.sh` ile yapılır** (disk kontrolü → doğrulanmış yedek → `git pull` → build cache temizliği → `up -d --build` → tüm servisler healthy → preflight; ilk hatada durur, `ALERT_WEBHOOK_URL`'e bildirir). Elle `up --build` yapılmaz: disk dolunca build yarıda kalmış ve Redis AOF'u bozulup site düşmüştü. Deploy betikleri her yerde `bash` ile çağrılır (çalıştırma biti kaybolsa da çalışsın). Redis başlangıçta bozuk AOF'u kendi onarır, onaramazsa kenara alıp boş başlar; 256 MB sınırı, `volatile-lru`

## Klasör yapısı

```
backend/
  app/
    api/          # FastAPI router'ları
    core/         # config, security, şifreleme
    db/           # modeller, session, migration
    etsy/         # API client, kuyruk, rate limiter, taksonomi
    pipeline/     # SKU parse, görsel işleme, içerik üretimi
    compliance/   # marka/duplicate/stuffing taraması
    workers/      # arq worker tanımları, retention temizliği
  tests/
frontend/
docker/
docs/             # data-model.md ve diğer spesifikasyonlar
```

## Kod kuralları

- Tüm public fonksiyonlarda type hint
- Etsy client katmanı test edilirken gerçek API'ye çağrı yapılmaz, mock kullanılır
- Migration'lar Alembic ile, elle SQL yazılmaz
- Sır/anahtar kodda tutulmaz, `.env` üzerinden okunur, `.env` gitignore'da
- Hata mesajlarında token ve başka kullanıcının verisi bulunmaz; log ve hata izleyiciye giden metin geneldir. Tek istisna: satıcının kendi listing'i için Etsy'nin verdiği neden, yalnızca o satıcının kartında gösterilir (`etsy/errors.py::explain`)
- Member Content saklayan her tabloda retention stratejisi tanımlı olmalı
- **Frontend: tarayıcı çevirisine dayanıklı metin.** Çeviri eklentileri (Chrome çevirisi vb.) metin düğümlerini kendi elemanlarıyla değiştirir; React sonra o düğümü silmeye/önüne eleman eklemeye çalışınca `removeChild` hatasıyla sayfa çöker, değişen bir değer ise eskide kalır. Bu yüzden: iki+ çocuklu bir listede **dinamik metin** kendi elemanında (`<span>`; boş olabiliyorsa `<Txt>`, `components/Txt.tsx`), koşullu bir kardeşin yanındaki **sabit metin** de bir `<span>` içinde; bitişik metin parçaları tek bir `<span>`'de toplanır (flex/grid düzeni değişmesin); `cond && <X/>` elemanlarına sabit `key`. Çalışma anında değişen durum satırları, sayaçlar ve iş ilerlemesi `translate="no"`; sabit metnin çevirisi engellenmez. `npm run check:translation` (ve `npm test`) bunu denetler; `node scripts/translation-safety.cjs --apply` düzeltir.

- **Frontend: telefondan tam kullanım (Priority 3).** Her ekran 375px'te yatay kaydırma olmadan çalışır. Dokunulan her şey en az 44px: paylaşılan `.btn-*`/`.field`/onay kutuları `globals.css`'teki `(max-width: 639px), (pointer: coarse)` kuralıyla büyür; metin görünümlü küçük kontrollere `.tap` verilir (görünüm aynı, dokunma alanı 44px; `.tap` `position: relative` atar, `absolute` elemana verilmez). Giriş alanları telefonda 16px'tir (iOS odakta yakınlaştırmasın). Yalnızca hover'da görünen ya da yalnızca sürüklemeyle çalışan kontrol yazılmaz: görsel sıralama dokunup düğmeyle (Earlier / Later / Make cover), grafikler parmakla kaydırarak okunur. Mağaza ve profil seçiciler telefonda alttan açılan sayfa olarak açılır (`components/Sheet.tsx`). Yeni bir ekran eklenince 375px'te kontrol edilir.

## Geliştirme notları

- **arq worker'ının reload'u yoktur.** `api` servisi `uvicorn --reload` ile çalıştığı için
  backend değişikliklerini anında alır; worker almaz. Job kodunu etkileyen bir değişiklikten
  sonra `docker compose restart worker` çalıştırılmalıdır — aksi halde job'lar **sessizce eski
  kodu çalıştırır** ve hata vermediği için fark edilmesi zordur. Etkilenen yerler:
  `app/workers/`, `app/pipeline/`, `app/etsy/` ve bunların kullandığı her şey.

## MVP kapsamı

**Dahil:** OAuth bağlantı, klasör yükleme, dosya adından SKU parse, görsel işleme (resize/watermark/sıra), vision ile tasarım analizi, başlık+13 tag+açıklama üretimi, taksonomi eşleme, taslak listing oluşturma, section yönetimi, toplu güncelleme, dry-run önizleme, rollback, compliance tarayıcı, retention temizliği.

**Hariç:** reklam yönetimi, rakip/tag analizi, otomatik yayın, çoklu kanal (Shopify/eBay), Etsy dışına yönlendirme.

## Ürün konumlandırma notu

ToU Bölüm 4, Etsy'nin üyelerine ücretsiz sunduğu bir işleve API üzerinden ücret koymayı yasaklar. Ürünün ücretli değeri, **API'yi entegre etmeyen** kısımlarda konumlandırılır: vision analizi, LLM içerik üretimi, görsel işleme, compliance taraması, iş akışı otomasyonu. Arayüz metinleri ve fiyatlandırma sayfası buna göre yazılır. Ücretli sürüm yalnızca Commercial Access onayından sonra açılır.

## Çalışma sırası

1. Veri modeli + migration (bkz. `docs/data-model.md`)
2. OAuth 2.0 PKCE akışı + şifreli token saklama
3. Rate limit kuyruğu ve tenant kotası
4. Etsy client (taksonomi, listing, görsel, envanter)
5. Pipeline (SKU parse → görsel → vision → içerik → taslak)
6. Dry-run + rollback + retention temizliği
7. Compliance tarayıcı
8. Frontend

Bir sonraki adıma geçmeden önce mevcut adımın testleri geçmeli.
