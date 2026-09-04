# Scanner — Bot quét cổ phiếu Mỹ bất thường → Telegram

Bot chạy 24/7, tự bật/tắt theo lịch phiên NYSE. Trong phiên, cứ **25 giây** nó
quét toàn bộ cổ phiếu đang chạy mạnh, chấm điểm bất thường, và bắn alert vào
Telegram kèm nút bấm (biểu đồ, hồ sơ SEC, cập nhật lại, theo dõi, hỏi ChatGPT).

Mọi thứ dùng **API miễn phí**: Alpaca, Yahoo Finance, SEC EDGAR, Nasdaq Trader.

---

## 1. Ý tưởng: bot này tìm cái gì?

Không phải tìm cổ phiếu "tốt". Nó tìm cổ phiếu **đang xảy ra chuyện gì đó
bất thường ngay lúc này** — thường là small-cap bật 20–100% trong vài giờ.

Logic gồm 2 phần:

**Phần 1 — Cái gì bình thường?** (`prep.py`, chạy 1 lần/ngày)
Với mỗi mã trong ~5000 mã Mỹ, tính và lưu vào SQLite:
- `adv20` — khối lượng trung bình 20 ngày → "bình thường giao dịch bao nhiêu"
- `atr14` — biên độ trung bình 14 ngày → "bình thường dao động bao nhiêu"
- `prev_close` — giá đóng cửa hôm qua
- `cik` — mã doanh nghiệp tại SEC, để tra hồ sơ

**Phần 2 — Hôm nay lệch bao xa?** (`main.py`, chạy liên tục trong phiên)
So số liệu live với baseline → chấm điểm → vượt ngưỡng thì gửi Telegram.

---

## 2. Luồng dữ liệu

```
   ┌─────────────────────────────────────────────────────────────┐
   │  CHẠY 1 LẦN/NGÀY TRƯỚC PHIÊN                                │
   │                                                             │
   │  prep.py                                                    │
   │   ├─ Alpaca Trading API  → danh sách ~5000 mã NASDAQ/NYSE   │
   │   ├─ yfinance            → 4 tháng nến ngày                 │
   │   ├─ SEC company_tickers → map ticker → CIK                 │
   │   └─ tính adv20/atr14/prev_close ──► bảng `base`            │
   │                                                             │
   │  scripts/mark_etf.py                                        │
   │   └─ Nasdaq Trader → gắn cờ is_etf=1 cho ETF & test issue   │
   │      (BẮT BUỘC chạy sau prep.py — xem mục 4)                │
   └─────────────────────────────────────────────────────────────┘
                                │
                                ▼  state/baseline.db
   ┌─────────────────────────────────────────────────────────────┐
   │  CHẠY LIÊN TỤC — main.py: 5 vòng lặp async song song        │
   │                                                             │
   │  ① loop_clock   (20s)  clock.py — bây giờ là trạng thái gì? │
   │     PREP → PREMARKET → OPENING → LIVE → CLOSING →           │
   │     AFTERHOURS → CLOSED. Chỉ 3 trạng thái giữa mới quét.    │
   │     Cũng gửi heartbeat sáng + tổng kết cuối phiên.          │
   │                                                             │
   │  ② loop_universe (60s)  universe_live.py                    │
   │     Alpaca screener (realtime) + Yahoo screener (~15p trễ)  │
   │     → gộp lại ~200–600 mã đang chạy, ưu tiên nguồn tươi hơn │
   │                                                             │
   │  ③ loop_score   (25s)  scorer.py + vprofile.py              │
   │     Ghép universe × base → lọc → chấm điểm → xếp hạng       │
   │     Mã nào ≥ 7.0 điểm  →  edgar.py tra hồ sơ SEC            │
   │                          →  render.py dựng tin nhắn HTML    │
   │                          →  tgapi.py gửi kèm nút bấm        │
   │                          →  ghi vào bảng `alerts`           │
   │                                                             │
   │  ④ loop_track   (45s)  chỉ các mã bạn đã bấm "Theo dõi"     │
   │     Chấm điểm lại (bỏ qua bộ lọc) → sửa lại chính tin nhắn  │
   │     alert của mã đó. Không gửi tin mới.                     │
   │                                                             │
   │  ⑤ Callbacks    (long-poll)  callbacks.py                   │
   │     Nghe nút bấm → chấm điểm lại → sửa tin nhắn tại chỗ     │
   └─────────────────────────────────────────────────────────────┘
```

---

## 3. Cách chấm điểm

### Bước 1 — Lọc thô (`scorer.rank`)

Mã phải qua **hết** các cửa sau mới được chấm điểm:

| Điều kiện | Ngưỡng | Vì sao |
|---|---|---|
| Có trong `base` | — | Loại ETF, mã mới IPO, mã kém thanh khoản |
| Giá | ≥ $1.00 | Loại penny rác |
| % tăng | ≥ 5% | Dưới mức này là nhiễu |
| Có volume | — | Thiếu volume thì không tính được RVOL |
| Thanh khoản | ≥ $2M | Đủ để vào/ra được |
| RVOL | ≥ 3.0× | Điều kiện quan trọng nhất |
| Lệch % giữa 2 nguồn | ≤ 25pp | Lệch nhiều → nghi gộp/chia cổ phiếu |

### Bước 2 — RVOL chuẩn hoá theo giờ (`vprofile.py`)

Đây là chỗ dễ sai nhất. Volume lúc 10:00 sáng đương nhiên nhỏ hơn lúc 16:00,
nên không thể so trực tiếp với `adv20` cả ngày. `vprofile.py` giữ đường cong
hình chữ U của khối lượng nội phiên:

```
phút 5   →  3.5% volume cả ngày đã giao dịch
phút 30  → 12.5%
phút 195 → ~40%   (giữa phiên, chậm nhất)
phút 390 → 100%
```

```
RVOL = volume_hiện_tại / (adv20 × tỷ_lệ_kỳ_vọng_tại_phút_này)
```

Premarket dùng hằng số 3%. Ngoài phiên dùng 1.0 (volume nhận được là cả phiên
đã xong). Nửa phiên (210 phút, ví dụ áp Lễ Tạ ơn) được co giãn theo tỷ lệ.

### Bước 3 — Cộng điểm (`scorer.score_one`)

| Thành phần | Công thức | Điểm tối đa |
|---|---|---|
| RVOL | `2.2 × min(log10(rvol), 2.0)` | 4.4 (bão hoà ở 100×) |
| Biên độ vs ATR | `1.6 × min(atr_move / 2, 3.0)` | 4.8 |
| Quay vòng float | `1.4 × min(vol / float, 3.0)` | 4.2 |
| Thanh khoản USD | `0.5 × min(log10(dvol/2M), 1.5)` | 0.75 |
| Chỉ Alpaca thấy | `+1.5` cố định | 1.5 (tín hiệu sớm, Yahoo chưa kịp) |

Thang log10 nên RVOL 200× không "ăn" hết điểm — điểm cao đòi **nhiều yếu tố
cùng lúc**, chứ không phải một chỉ số cực đoan.

### Bước 4 — Trừ điểm rủi ro SEC (`edgar.py`)

Tra hồ sơ SEC 120 ngày gần nhất. Nguy hiểm nhất là công ty **đang bán cổ phiếu
ra thị trường** khi giá vừa bật — pha loãng, giá sập ngay sau đó:

| Loại hồ sơ | Rủi ro | Nghĩa là |
|---|---|---|
| `424B5`, `424B4` | +3.0 | Đang chào bán cổ phiếu ngay lúc này |
| `S-3`, `S-1`, `S-3ASR` | +1.5 | Đã đăng ký kê hàng, bán bất cứ lúc nào |
| `25-NSE` | +3.0 | Thông báo huỷ niêm yết |
| `8-K` item 1.03 | +3.0 | Phá sản |
| `8-K` item 3.02 | +2.0 | Bán cổ phiếu không đăng ký (pha loãng) |
| `SC 13D` | **−1.0** | Cổ đông lớn gom hàng (tín hiệu tốt) |

Hồ sơ mới (≤5 ngày) tính đủ trọng số, cũ hơn chỉ tính 30–50%.
**Risk ≥ 3.0 → trừ thẳng 2.0 điểm.** Nếu tụt xuống dưới 7.0 thì không gửi.

### Bước 5 — Ngưỡng gửi và chống spam

| Cơ chế | Giá trị | Tác dụng |
|---|---|---|
| `ALERT_SCORE` | 7.0 | Dưới mức này không gửi |
| `ESCALATE_DELTA` | +3.0 | Đã gửi rồi, chỉ gửi lại nếu điểm tăng thêm 3.0 |
| `COOLDOWN` | 540s (9 phút) | Mỗi mã tối đa 1 tin/9 phút |
| `MAX_ALERTS` | 45/phiên | Trần tuyệt đối |
| `MIN_MSO` | 5 phút | Bỏ 5 phút đầu phiên (số liệu chưa ổn định) |
| `loud_mode` | 09–17h giờ Đức | Giờ làm việc chỉ đổ chuông nếu ≥ 12.0 điểm |
| `junk_ticker` | hậu tố W/WS/R/RT/U/UN/PR | Loại warrant, unit, right, preferred |

### Mức độ alert hiển thị (`render.AlertView.level`)

| Mức | Điều kiện | Nhãn |
|---|---|---|
| 🟨 1 | 7.0 – 7.9 điểm | `WATCH` |
| 🟧 2 | ≥ 8.0 điểm | `STRONG MOMENTUM` |
| 🚨 3 | ≥ 12.0 điểm, **hoặc** SEC risk ≥3 + RVOL ≥50× + quay vòng ≥2× | `EXTREME EVENT` |

---

## 4. Cài đặt

### Yêu cầu

- **Python 3.11+** (dùng `zoneinfo`, cú pháp `X | None`) — repo này test trên 3.14
- Tài khoản **Alpaca** miễn phí → https://alpaca.markets (lấy API key ở phần Paper Trading)
- **Bot Telegram** → chat với [@BotFather](https://t.me/BotFather), gửi `/newbot`
- Không cần Docker, không cần database server. Tất cả nằm trong 1 file SQLite.

### Bước 1 — Clone và tạo môi trường ảo

```bash
git clone https://github.com/tuanhpham/scanner.git
cd scanner

python -m venv .venv
source .venv/Scripts/activate     # Git Bash trên Windows
# .venv\Scripts\activate          # CMD / PowerShell
# source .venv/bin/activate       # Linux / macOS

pip install -r requirements.txt
```

### Bước 2 — Tạo file `.env`

```bash
cp .env.example .env
```

Mở `.env` và điền:

```ini
# BẮT BUỘC — Telegram
TG_TOKEN=123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TG_CHAT_ID=987654321

# BẮT BUỘC — Alpaca (danh sách mã + screener realtime)
ALPACA_KEY=PKxxxxxxxxxxxxxxxx
ALPACA_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# BẮT BUỘC — SEC yêu cầu User-Agent có tên thật + email thật
SEC_UA=Ten Cua Ban email@domain.com

# Không dùng trong code hiện tại, để trống được
FINNHUB_KEY=
ANTHROPIC_API_KEY=
GROQ_KEY=
```

**Lấy `TG_CHAT_ID`:** nhắn gì đó cho bot của bạn, rồi mở
`https://api.telegram.org/bot<TG_TOKEN>/getUpdates` trên browser, tìm
`"chat":{"id":...}`.

**`SEC_UA` phải có ký tự `@`** — code kiểm tra điều này. SEC sẽ chặn IP nếu
User-Agent không hợp lệ. Đây không phải secret, chỉ là quy định của SEC.

### Bước 3 — Kiểm tra kết nối

```bash
python clock.py                      # in trạng thái phiên hiện tại
python scripts/check_calendar.py     # lịch phiên 60 ngày tới theo giờ Đức
```

Nếu `clock.py` in ra được giờ phiên → thư viện đã cài đúng.

### Bước 4 — Dựng baseline

**Bước này bắt buộc và mất thời gian.** Chạy thử nhanh trước:

```bash
python prep.py --limit 300     # ~1 phút, đủ để kiểm tra pipeline
```

Nếu chạy ổn thì làm thật:

```bash
python prep.py                 # ~5000 mã, mất 20–40 phút
```

Kết quả mong đợi: `XONG: 4xxx ma cap nhat, 4xxx ma trong DB, ...`

### Bước 5 — Gắn cờ ETF ⚠️ BẮT BUỘC

```bash
python scripts/mark_etf.py
```

**Không được bỏ bước này.** `scorer.load_baseline()` query
`... FROM base WHERE is_etf=0`, nhưng `prep.py` không tạo cột `is_etf` —
chính `mark_etf.py` mới `ALTER TABLE` thêm cột đó. Bỏ qua bước này thì
`main.py` sẽ chết ngay với lỗi `sqlite3.OperationalError: no such column: is_etf`.

Kết quả mong đợi: `ETF/test: 3xxx | co phieu thuong: 4xxx | thieu CIK: xxx`

### Bước 6 — Chạy thử 1 lần

```bash
python main.py --once
```

Lệnh này quét 1 lần, in top 5 mã, gửi mã điểm cao nhất lên Telegram, rồi thoát.
**Chạy trong giờ phiên (15:30–22:00 giờ Đức)** mới có dữ liệu thật.

Nếu tin nhắn vào được Telegram → xong, mọi thứ đã hoạt động.

---

## 5. Chạy

```bash
python main.py           # chạy thật, 24/7, tự bật/tắt theo lịch NYSE
python main.py --dry     # quét và log ra terminal, KHÔNG gửi Telegram
python main.py --once    # quét 1 lần rồi thoát
```

`--dry` là chế độ nên dùng khi tinh chỉnh ngưỡng: thấy đầy đủ log
`[DRY NEW] ABCD 8.3 L2` mà không spam Telegram.

Bot khởi động lại giữa phiên vẫn an toàn: `restore_today()` đọc lại bảng
`alerts` của hôm nay để không gửi trùng.

### Chạy nền dài hạn

**Linux (systemd)** — `/etc/systemd/system/scanner.service`:

```ini
[Unit]
Description=Stock Scanner
After=network-online.target

[Service]
WorkingDirectory=/duong/dan/scanner
ExecStart=/duong/dan/scanner/.venv/bin/python main.py
Restart=always
RestartSec=30
StandardOutput=append:/duong/dan/scanner/service.log
StandardError=append:/duong/dan/scanner/service.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now scanner
journalctl -u scanner -f
```

**Windows** — Task Scheduler, trigger "At startup", action:
`C:\...\scanner\.venv\Scripts\python.exe C:\...\scanner\main.py`

### Lịch chạy `prep.py` hàng ngày

`prev_close` và `adv20` phải mới, nếu không điểm số vô nghĩa. Chạy lúc
**08:00 ET** (14:00 giờ Đức), tức trước phiên:

```cron
0 8 * * 1-5  cd /duong/dan/scanner && .venv/bin/python prep.py >> prep.log 2>&1
0 9 * * 1-5  cd /duong/dan/scanner && .venv/bin/python scripts/mark_etf.py >> prep.log 2>&1
```

`prep.py` chạy lại an toàn (idempotent, dùng `ON CONFLICT DO UPDATE`).
Nó cũng tự bỏ nến của ngày hôm nay nếu đang chạy giữa phiên — nến chưa chốt
sẽ làm `prev_close = giá hiện tại` → `chg` và `atr_move` = 0 trên toàn DB.

---

## 6. Alert trên Telegram trông như thế nào

Muốn xem ngay không cần chạy cả bot:

```bash
python render.py     # in 3 alert mẫu (mức 1 / 2 / 3) + layout bàn phím
```

Một alert mức 3 trông như sau:

```
🔴 WETO · $10.61 · ▲ +85.5%             ← HEADER
EXTREME EVENT · Tín hiệu mới
██████████ 12.4/12
Realtime · 15:42 ET · phút 12/390

⚠️ ÁP LỰC FLOAT                         ← BADGE

SỐ LIỆU                                 ← DATA (khối <pre>, ASCII, cột thẳng)
FLOW
  RVOL          66.2x  +12.4
  $ Volume      $311M
  Turnover      3.49x  +0.30

VOLATILITY
  ATR move       4.1x  +0.5

STRUCTURE
  Float          8.4M
  Float cap    $89.1M

RỦI RO                                  ← RISK (blockquote)
PHA LOÃNG — CAO
Đang chào bán — cổ phiếu mới có thể ra thị trường bất kỳ lúc nào.

BIẾN ĐỘNG CỰC MẠNH
Biên độ 4.1 lần ATR ngày thường.

HỒ SƠ SEC                               ← SEC (blockquote thu gọn được)
424B5 · 2 ngày trước · đang chào bán cổ phiếu (shelf takedown)
8-K · 1 ngày trước · tin trọng yếu · 3 lần/120 ngày

VÌ SAO CÓ TÍN HIỆU                      ← WHY (chỉ hiện ở mức 3 / khi bấm)
· RVOL 66.2× (+4.0)
· biên độ 4.1× ATR (+3.3)
· quay vòng 3.49× (+4.2)

nguồn realtime · quét 15:42 ET · điểm trước 9.8

Dữ liệu thô, chưa kiểm chứng · Không phải lời khuyên đầu tư   ← FOOT

[Biểu đồ]   [Finviz]    [Tin]
[Cập nhật]  [Chi tiết]  [Hỏi ChatGPT]
[Hồ sơ SEC] [Theo dõi]
```

### Quy ước trình bày

- **7 khối** cố định, luôn đúng thứ tự: HEADER → BADGE → DATA → RISK → SEC →
  WHY → FOOT. Khối nào không có dữ liệu thì bỏ hẳn, không để trống.
- **Panel `<pre>` chỉ dùng ASCII** — bắt buộc, xem mục 9 để biết tại sao.
- **Cả tin chỉ 2 emoji**: đèn mức độ ở header (🟡/🟠/🔴) và ⚠️ ở dòng thẻ cảnh
  báo. Tiêu đề section và nhãn nút để chữ trơn — Telegram đã tự vẽ nền xám cho
  `<pre>`, vạch dọc cho `blockquote`, và khung cho nút, nên không cần icon.
- **Ngôn ngữ**: thuật ngữ thị trường + toàn bộ panel giữ tiếng Anh (`RVOL`,
  `ATR move`, `Float cap`, `WATCH` / `STRONG MOMENTUM` / `EXTREME EVENT`);
  phần diễn giải là tiếng Việt có dấu.
- **Không lặp số liệu**: giá và %thay đổi chỉ ở HEADER; thẻ cảnh báo chỉ ghi
  tên thẻ vì con số đã có trong panel.
- **Cột trong panel thẳng hàng tuyệt đối**: thụt lề 2, nhãn 11 ký tự, giá trị
  canh phải 8 ký tự, delta cột riêng (`W_IND`, `W_LAB`, `W_VAL`, `W_DLT`).
- **Không có dòng link chữ ở cuối** — inline keyboard đã có sẵn các nút đó.
- **Tin quá 3800 ký tự** → bỏ **cả khối** theo thứ tự ưu tiên
  (WHY → SEC → RISK → BADGE...), không bao giờ cắt giữa tag HTML.

### Các nút bấm

Xử lý bởi `callbacks.py`, sửa tin nhắn tại chỗ (không gửi tin mới):

| Nút | Việc gì xảy ra |
|---|---|
| Cập nhật | Chấm điểm lại mã đó ngay, sửa tin nhắn tại chỗ. Cooldown 8s/mã |
| Chi tiết | Mở/thu gọn khối `VÌ SAO`. Cũng chấm điểm lại, nên cùng chịu cooldown 8s |
| Theo dõi | Bật chế độ tự cập nhật cho mã đó — xem dưới. Chỉ hiện từ mức 2 |
| Hỏi ChatGPT | Mở ChatGPT với prompt đã điền sẵn số liệu của mã — xem dưới |
| Biểu đồ · Finviz · Tin · Hồ sơ SEC | Link ngoài: TradingView, Finviz, Google News, EDGAR |

Số ở cột phải panel (`+12.4`) là **delta so với lần gửi trước** — snapshot lưu
trong bảng `alert_msg`, so sánh trong `render._delta()`. Chưa có snapshot thì
cột để trống; thay đổi quá nhỏ thì hiện `=`. Cột này cũng là ASCII, vì nằm
trong `<pre>`.

### Nút Cập nhật — giới hạn cần biết

Nó gọi `main.refresh_one()` → `scorer.score_sym()` → `tgapi.edit()`, tức là
sửa chính tin nhắn cũ, không gửi tin mới. Hai điều đáng lưu ý:

- **Dữ liệu mới nhất chỉ tươi tới 60 giây.** `st.universe` do `loop_universe`
  dựng lại mỗi `UNIVERSE_SEC = 60` (Yahoo giới hạn ~1 req/60s). Bấm hai lần
  trong vòng 60s sẽ ra cùng `vol`/`px`; chỉ `frac` (và do đó RVOL) nhích lên
  vì phiên đã đi thêm được vài phút.
- Nó dùng `scorer.score_sym()` chứ **không** phải `scorer.rank()`. `rank()` áp
  bộ lọc (`MIN_RVOL`, `MIN_CHG`…) nên mã đã nguội sẽ bị loại và trả về rỗng —
  khi đó tin nhắn sẽ rơi về snapshot cũ thay vì cho bạn thấy điểm đã tụt.
  Chính "điểm tụt từ 9.8 xuống 4.1" mới là thông tin bạn cần.

Nếu mã đã rời hẳn universe (hết phiên, hoặc không còn chạy) thì `refresh_one`
trả `None` và `callbacks._rerender` dựng lại tin từ snapshot trong DB, gắn nhãn
`Trễ ~15 phút`.

### Nút Theo dõi

Bật cho một mã (chỉ hiện từ mức 2), rồi ba việc xảy ra:

1. **Tin nhắn của mã đó tự sửa lại mỗi `TRACK_SEC = 45` giây** — do
   `main.loop_track()`. Giá, RVOL, quay vòng, giờ cập nhật đều đổi ngay trong
   tin nhắn cũ; header ghi `Đang theo dõi · tự cập nhật`. Không gửi tin mới,
   nên không làm ồn.
2. **Ngưỡng gửi lại hạ từ `ESCALATE_DELTA = 3.0` xuống `TRACK_ESCALATE = 1.5`**
   — mã đang theo dõi mạnh lên thì bot báo sớm hơn.
3. **Không bị trần `MAX_ALERTS = 45` chặn** — hết quota alert cả phiên thì mã
   đang theo dõi vẫn gửi được.

Chi tiết cần biết:

- **Chỉ có hiệu lực trong phiên.** `store.prune_track()` xoá mọi dòng cũ hơn
  18 giờ, chạy lúc sang ngày mới. Dùng tuổi thay vì "xoá khi khởi động" để bot
  restart giữa phiên không mất danh sách.
- **Trần `MAX_TRACK = 10` mã.** `tgapi` có `MIN_GAP = 1.2s` giữa hai lần gọi
  API, nên 10 mã đã chiếm 12s trong mỗi vòng 45s. Vượt trần thì chỉ 10 mã mới
  nhất được cập nhật, và log ghi rõ số bị bỏ.
- Vòng tự cập nhật **không ghi lại snapshot**, nên cột delta trong panel vẫn đo
  từ lần *gửi* thật gần nhất, không bị reset về `=` sau mỗi 45 giây.
- Mã đang theo dõi được đánh dấu `(theo doi)` trong tin tổng kết cuối phiên.
- Nếu bạn đã bấm Thu gọn ở một mã mức 3, lần tự cập nhật sau sẽ mở lại khối
  `VÌ SAO` — trạng thái thu gọn không được lưu vào DB.

### Nút Hỏi ChatGPT

Là một link thường (`url` button) tới `https://chatgpt.com/?q=<prompt>`, với
prompt do `render.ask_prompt()` dựng từ số liệu của mã:

```
Cổ phiếu Mỹ WETO hôm nay tăng 85.5% lên $10.61, khối lượng gấp 66 lần bình
thường, biên độ 4.1 lần ATR, float 8.4M cp, quay vòng 3.5 lần float, giá trị
giao dịch $311M. Hồ sơ SEC gần đây: 424B5 cách 2 ngày; 8-K cách 1 ngày; S-3
cách 46 ngày. 1) Vì sao nó tăng — có tin/thông báo nào hôm nay? 2) Rủi ro pha
loãng và thanh khoản ra sao? 3) Đây là đợt tăng có cơ sở hay chỉ là bơm giá?
Trả lời ngắn bằng tiếng Việt, dẫn nguồn.
```

Đặt `CHATGPT_GPT_ID` trong `.env` để mở **một GPT riêng** thay vì ChatGPT
thường (lấy ID từ URL của GPT đó: `chatgpt.com/g/g-abc123-ten` →
`g-abc123-ten`). Khi đó URL thành `chatgpt.com/g/g-abc123-ten?q=…`.

**Bốn giới hạn, cần biết trước khi tin vào nút này:**

- Tham số `?q=` **không có trong tài liệu công khai của OpenAI**. Nó vẫn hoạt
  động, nhưng hành vi đã đổi vài lần (có lúc điền sẵn rồi tự gửi, có lúc chỉ
  điền vào ô nhập). OpenAI có thể bỏ nó bất cứ lúc nào — không có gì bảo đảm.
- **Telegram mở link trong trình duyệt nội bộ của nó**, nên mặc định bạn sẽ
  thấy ChatGPT bản web chứ không phải app. Muốn nó mở đúng app: tắt in-app
  browser trong Telegram (Settings → Data and Storage), khi đó link đi ra
  trình duyệt hệ thống và universal link/app link sẽ chuyển sang app ChatGPT
  nếu đã cài.
- Chưa đăng nhập ChatGPT thì nó ra trang login, prompt mất.
- Prompt bị cắt ở `ASK_MAX = 700` ký tự. Mỗi chữ tiếng Việt có dấu thành 9 byte
  sau khi URL-encode, nên URL thực tế dài ~1100 ký tự. `python render.py` in ra
  độ dài URL và cảnh báo nếu vượt 2000.

Prompt **cố tình không chứa điểm số hay xếp loại của bot** — đó là thang điểm
riêng của project này, ChatGPT không có cách nào hiểu `12.4/12` nghĩa là gì.

---

## 7. Bản đồ file

| File | Việc |
|---|---|
| `main.py` | Vòng lặp chính, 5 task async, ngưỡng alert, ghi bảng `alerts` |
| `prep.py` | Dựng baseline hàng ngày: adv20, atr14, prev_close, cik |
| `clock.py` | Lịch phiên NYSE → giờ Đức. Xử lý DST lệch, nửa phiên, ngày lễ |
| `universe_live.py` | Gộp Alpaca + Yahoo screener → dict các mã đang chạy |
| `scorer.py` | Lọc + chấm điểm. Cũng lấy `float_sh` lười (top 60 mã) |
| `vprofile.py` | Đường cong chữ U của volume nội phiên → RVOL chuẩn hoá |
| `edgar.py` | Tra SEC EDGAR, chấm điểm rủi ro pha loãng. Cache 30 phút trên đĩa |
| `render.py` | Dựng HTML cho Telegram. **Thuần hàm** — không network, không DB |
| `tgapi.py` | Gọi Telegram API trực tiếp: gửi kèm nút, sửa tin, trả lời nút bấm |
| `notifier.py` | Hàng đợi async + token bucket + spool khi mất mạng (fallback) |
| `callbacks.py` | Long-polling `getUpdates` → xử lý nút bấm |
| `store.py` | Bảng phụ: `alert_msg`, `watch`, `kv`. Mọi hàm bắt lỗi, không làm chết alert |
| `scripts/mark_etf.py` | Gắn cờ `is_etf` từ Nasdaq Trader. **Bắt buộc sau `prep.py`** |
| `scripts/check_calendar.py` | In lịch phiên 60 ngày tới theo giờ Đức |
| `scripts/preview_alert.py` | Gửi 1 alert mẫu (dữ liệu giả) để xem layout |

### Hai đường gửi Telegram

Có hai module gửi tin, và điều này là cố ý:

- **`tgapi.py`** — đường chính. Gửi được nút bấm, trả về `message_id` để sau
  này sửa tin. Nếu Telegram trả 400 vì tag HTML, nó **hạ cấp dần**:
  bỏ `expandable` → bỏ `blockquote` → strip hết tag (`render.degrade()`).
- **`notifier.py`** — đường dự phòng. Có hàng đợi, giới hạn 15 tin/phút, và
  **spool ra `state/spool.json`** khi mất mạng — gửi lại khi có mạng, kèm
  nhãn "trễ N phút". Không gửi được nút bấm.

`main.tg_send()` thử `tgapi` trước, thất bại thì rơi về `notifier`.

### Các bảng trong `state/baseline.db`

| Bảng | Tạo bởi | Nội dung |
|---|---|---|
| `base` | `prep.py` | 1 dòng/mã: adv20, atr14, prev_close, float_sh, cik, is_etf |
| `meta` | `prep.py` | Cặp key-value: `built`, `count`, `etf_marked` |
| `alerts` | `main.py` | Lịch sử alert đã gửi. Dùng cho `restore_today()` |
| `alert_msg` | `store.py` | `sym`+`day` → `message_id` + snapshot (để tính delta) |
| `watch` | `store.py` | Mã đang theo dõi trong phiên (`kind='track'`) |
| `kv` | `store.py` | Hiện chỉ giữ `tg_offset` của `getUpdates` |

DB bật WAL nên nhiều tiến trình đọc/ghi cùng lúc không sao.

---

## 8. Debug từng tầng

Mỗi module chạy độc lập được, tiện để khoanh vùng lỗi:

```bash
python clock.py                  # bây giờ phiên đang ở trạng thái gì?
python vprofile.py               # bảng RVOL theo phút, kiểm tra đường cong U
python universe_live.py          # top 15 mã đang chạy + nguồn nào thấy
python scorer.py                 # chấm điểm đầy đủ + LÝ DO BỊ LOẠI (rất hữu ích)
python scorer.py --force         # chạy cả khi ngoài phiên (số liệu không đáng tin)
python edgar.py AAPL TSLA        # tra hồ sơ SEC của mã cụ thể
python edgar.py                  # tra 10 mã đã alert gần nhất
python store.py                  # smoke test bảng phụ, tự dọn sau khi chạy
python render.py                 # in 3 alert mẫu L1/L2/L3 — không cần mạng
python scripts/preview_alert.py  # gửi 1 alert mẫu lên Telegram
```

`python scorer.py` là công cụ debug tốt nhất. Nó in ra bảng lý do bị loại:

```
Ly do bi loai:
  khong co baseline (ETF/moi/kem thanh khoan)      412
  rvol < 3.0                                       118
  tang < 5%                                         64
  thanh khoan < $2M                                 31
```

Nếu `khong co baseline` chiếm gần hết → `prep.py` chưa chạy hoặc chạy lỗi.

### Lỗi thường gặp

| Triệu chứng | Nguyên nhân | Cách sửa |
|---|---|---|
| `no such column: is_etf` | Chưa chạy `mark_etf.py` | `python scripts/mark_etf.py` |
| `baseline 0 ma` | `prep.py` chưa chạy / DB rỗng | `python prep.py` |
| `Thieu TG_TOKEN / TG_CHAT_ID` | `.env` trống hoặc sai đường dẫn | `.env` phải nằm cạnh `main.py` |
| `getUpdates 409` | Có tiến trình `main.py` khác đang chạy, hoặc webhook đang bật | Kill tiến trình cũ / `deleteWebhook` |
| Không có alert nào cả phiên | Bình thường. Ngưỡng 7.0 khá cao | Chạy `python scorer.py` xem điểm thực tế |
| `atr_move` = 0 trên mọi mã | `prep.py` chạy giữa phiên, `prev_close` = giá hiện tại | Chạy lại `prep.py` ngoài phiên |
| `[yahoo] trang 0 loi` | Yahoo rate-limit (~1 req/60s) | `UNIVERSE_SEC = 60` đã tính đến việc này |
| `[!] SEC_UA chua dat dung dinh dang` | `SEC_UA` thiếu `@` | Điền `Ten That email@domain.com` |

**Lưu ý:** `scripts/test_tg.py` hiện không chạy được — nó gọi `Notifier()`
không tham số và `n.raw()`, cả hai đều không còn tồn tại trong `notifier.py`.
Dùng `scripts/preview_alert.py` để test Telegram.

---

## 9. Tinh chỉnh

Đầu mỗi file là các hằng số cấu hình:

**`main.py`** — ngưỡng gửi
```python
ALERT_SCORE = 7.0       # hạ xuống 6.0 → nhiều alert hơn, nhiều nhiễu hơn
ESCALATE_DELTA = 3.0    # gửi lại khi điểm tăng thêm bao nhiêu
COOLDOWN = 540          # giây, mỗi mã
MAX_ALERTS = 45         # trần mỗi phiên
SCORE_SEC = 25          # tần số quét

TRACK_SEC = 45          # nút Theo dõi: tự sửa lại tin nhắn mỗi bao lâu
TRACK_ESCALATE = 1.5    # ngưỡng gửi lại cho mã đang theo dõi (nhạy hơn)
MAX_TRACK = 10          # trần số mã theo dõi cùng lúc, xem mục 6
```

**`scorer.py`** — trọng số và bộ lọc
```python
MIN_RVOL = 3.0          # cửa lọc quan trọng nhất
MIN_CHG = 0.05          # 5%
MIN_DOLLAR_VOL = 2_000_000
W_RVOL, W_ATR, W_ROT, W_DV, W_FRESH = 2.2, 1.6, 1.4, 0.5, 1.5
```

**`render.py`** — hiển thị
```python
T_STRONG, T_EXTREME = 8.0, 12.0        # ngưỡng mức 2 và mức 3
SCORE_MAX, BAR_CELLS = 12.0, 10        # thang của thanh điểm
W_IND, W_LAB, W_VAL, W_DLT = 2, 11, 8, 6   # 4 cột trong panel <pre>
SAFE_LEN = 3800           # vượt ngưỡng này thì bỏ bớt khối
EXPANDABLE = True         # <blockquote expandable>, cần Bot API >= 7.3
ASK_MAX = 700             # độ dài prompt của nút Hỏi ChatGPT
```

Chữ hiển thị nằm gọn trong dict `TXT` ở đầu `render.py` — sửa tên nhãn, tên
mức, câu cảnh báo ở đó, không phải lần trong code. Thứ tự bỏ khối khi tin quá
dài do các hằng `P_HEAD ... P_WHY` quyết định (số càng cao càng được giữ lại).

### Hai quy ước trong `render.py` — đừng "sửa" lại

**1. Panel `<pre>` chỉ dùng ASCII.** Font monospace của Telegram không có glyph
tiếng Việt có dấu. Chữ nào có dấu (`ố`, `ề`, `ộ`, `ữ`…) sẽ rơi sang font khác →
hiện **nhỏ hơn, lệch cỡ**, và phá vỡ căn cột. Vì vậy nhãn trong panel là thuật
ngữ tiếng Anh ASCII (`RVOL`, `$ Volume`, `Turnover`, `ATR move`, `Float`,
`Float cap`). Chữ tiếng Việt có dấu chỉ dùng **ngoài** panel, nơi Telegram dùng
font UI (đủ glyph). `python render.py` có kiểm tra tự động ở cuối output:

```
ky tu ngoai ASCII trong panel <pre>: khong co -> OK
```

Nếu dòng đó liệt kê ký tự nào, đó chính là chữ sẽ bị lệch cỡ trên điện thoại.

**2. Cả tin nhắn chỉ có 2 emoji.** Một đèn báo mức độ ở đầu header
(🟡 mức 1 · 🟠 mức 2 · 🔴 mức 3 — đỏ là mạnh nhất) làm neo để mắt quét nhanh
trong danh sách chat, và một dấu ⚠️ mở dòng thẻ cảnh báo. Tiêu đề section dùng
IN HOA + `<b>`, không emoji. Mục trong khối `RỦI RO` không dùng đèn màu — sẽ
trùng nghĩa với đèn mức độ ở header.

Nếu đổi chuỗi trong `TXT`, chạy lại `python render.py` để xem cột trong panel
còn thẳng hàng không — nhãn dài hơn `W_LAB` sẽ đẩy lệch cột giá trị.

**`main.loud_mode()`** — giờ im lặng
```python
if 9 <= h < 17:         # giờ Đức: giờ làm việc
    return score >= 12.0
```

**`vprofile._FRAC`** — nếu bạn có dữ liệu volume nội phiên thật của universe
của mình, thay mảng này bằng số liệu đo được sẽ chính xác hơn.

---

## 10. Giới hạn cần biết

- **Yahoo trễ ~15 phút.** Mã chỉ Alpaca thấy được cộng +1.5 điểm chính vì nó
  là tín hiệu sớm hơn — Yahoo chưa kịp phản ánh.
- **Yahoo rate-limit ~1 request/60s.** Đó là lý do `UNIVERSE_SEC = 60`.
  Giảm xuống sẽ bị chặn.
- **Alpaca free tier là IEX feed**, không phải full SIP — screener realtime
  nhưng chỉ ~150 mã movers + most actives.
- **`float_sh` lấy lười**, chỉ top 60 mã có RVOL cao nhất mỗi lần quét
  (`FLOAT_TOP_N`), vì `yfinance.get_info()` chậm.
- **Ngoài phiên số liệu không đồng bộ**: giá là giá đóng cửa → `atr_move` ≈ 0,
  `chg` lệch. `scorer.py` chặn điều này, cần `--force` để bỏ qua.
- **Không phải lời khuyên đầu tư.** Đây là dữ liệu thô chưa kiểm chứng —
  chính footer mỗi alert cũng nói vậy.

---

## Giấy phép

MIT — xem `LICENSE`.
