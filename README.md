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

**Đang mở rộng (Phase 9).** Cách trên chỉ thấy được cú nhảy đột ngột. Một mã
vừa breakout khỏi nền tích luỹ 8 tuần thì +2~3% — nó không lọt vào screener,
nên dù có nới ngưỡng bao nhiêu bot cũng **không thể** thấy. `bars.py` +
`structure.py` (đã cài) là hai tầng đầu để sửa: giữ lại nến ngày và đo nền tích
luỹ, để danh sách theo dõi sinh ra từ tối hôm trước thay vì từ bảng top-mover.
Chi tiết ở mục 11, Phase 9.

### Ba tiến trình trong cùng một repo

Đọc các mục dưới đây nhớ để ý mình đang đọc cái nào — chúng dùng chung kho nến
và chung DB, nhưng trả lời những câu hỏi khác nhau:

| | `main.py` **ĐANG BỊ THAY THẾ** | `nightly.py` | `watchd.py` **MỚI** |
|---|---|---|---|
| Nhịp | 25 giây, 24/7 | 1 lần lúc 08:00 ET | 60 giây, chỉ trong phiên |
| Câu hỏi | "*ngay lúc này* có gì bất thường?" | "hôm nay được phép làm gì, và ở đâu?" | "một mốc giá đã định có bị chạm chưa?" |
| Nguồn mã | screener top-mover (Alpaca + Yahoo) | 11 sector ETF → thành phần sector | **đúng danh sách của `nightly.py`**, không hơn |
| Nhịp giữ | phút đến giờ | ngày đến tuần | ngày đến tuần |
| Mục | 1–11 | **mục 12** | **mục 12.9** |

`main.py` đi tìm "% tăng mạnh nhất", và cách tính đó gần như chỉ ra cổ phiếu giá
thấp — vì trên giá thấp, một bước nhảy phần trăm là chuyện tầm thường: spread
rộng, không có tổ chức tham gia, rủi ro gap. Đó là lý do prompt 2 tồn tại.

`watchd.py` là câu trả lời: **canh, không tìm.** Nó đọc danh sách + kế hoạch lệnh
mà `nightly.py` đã chốt từ tối qua (sau một sàn chất lượng $10 / $20M/phiên /
$2B vốn hoá) và không bao giờ thêm một mã nào ngoài đó. `main.py` còn chạy song
song trong tuần chuyển đổi — hai tiến trình không đụng nhau vì chỉ `main.py` gọi
`getUpdates`.

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
   │  CHẠY LIÊN TỤC — main.py: 8 vòng lặp async song song        │
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
   │  ⑤ loop_outcome (60s)  outcome.py — đo chất lượng alert     │
   │     Alert đã gửi: điền giá sau 15p/60p, đỉnh/đáy, giá đóng  │
   │     → bảng `outcome`. Không gọi API, chỉ đọc lại universe.  │
   │                                                             │
   │  ⑥ loop_halts   (60s)  halts.py — feed tạm dừng giao dịch   │
   │     RSS Nasdaq → mã nào đang halt, vì lý do gì, từ lúc nào. │
   │     Alert có dòng halt ở trên cùng; H10/T12… bị chặn hẳn.   │
   │                                                             │
   │  ⑦ loop_news    (30s)  news.py — tin tức theo mã            │
   │     Alpaca News → mã này chạy vì cái gì. Tin "Pricing of    │
   │     Offering" bị trừ điểm; alert có thêm khối CATALYST.     │
   │                                                             │
   │  ⑧ Callbacks    (long-poll)  callbacks.py                   │
   │     Nghe nút bấm → chấm điểm lại → sửa tin nhắn tại chỗ     │
   └─────────────────────────────────────────────────────────────┘
```

Đây là luồng của `main.py` — cái đang bị thay thế. Luồng mới (`nightly.py` ghi
danh sách + kế hoạch lệnh, `watchd.py` canh các mốc đó trong phiên) ở **mục
12.9**.

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

### Bước 4 — Trừ điểm rủi ro pha loãng (`edgar.py` + `news.py`)

Hai nguồn, một hình phạt. Nguy hiểm nhất là công ty **đang bán cổ phiếu ra thị
trường** khi giá vừa bật — pha loãng, giá sập ngay sau đó.

`edgar.py` tra hồ sơ SEC 120 ngày gần nhất:

| Loại hồ sơ | Rủi ro | Nghĩa là |
|---|---|---|
| `424B5`, `424B4` | +3.0 | Đang chào bán cổ phiếu ngay lúc này |
| `S-3`, `S-1`, `S-3ASR` | +1.5 | Đã đăng ký kê hàng, bán bất cứ lúc nào |
| `25-NSE` | +3.0 | Thông báo huỷ niêm yết |
| `8-K` item 1.03 | +3.0 | Phá sản |
| `8-K` item 3.02 | +2.0 | Bán cổ phiếu không đăng ký (pha loãng) |
| `SC 13D` | **−1.0** | Cổ đông lớn gom hàng (tín hiệu tốt) |

Hồ sơ mới (≤5 ngày) tính đủ trọng số, cũ hơn chỉ tính 30–50%.

`news.py` đọc bản tin và gán mỗi tiêu đề một nhóm, nhóm xấu có `risk` (bảng đầy
đủ ở mục 8 → "Tin tức catalyst"). Bản tin ra **trước** hồ sơ EDGAR vài giờ, nên
đây thường là nguồn bắt được cái bẫy sớm nhất.

**`max(sec_risk, news_risk) ≥ 3.0 → trừ thẳng 2.0 điểm.**` Nếu tụt xuống dưới
7.0 thì không gửi. Lấy `max()` chứ không cộng dồn: một bản tin "Pricing of
Offering" và hồ sơ `424B5` là **cùng một sự kiện** — trừ hai lần là phạt trùng.

Nhóm tin **tốt** (FDA, hợp đồng, kết quả kinh doanh) **không được cộng điểm**:
chưa có số liệu nào chứng minh tin tốt làm mã chạy xa hơn, và Phase 1 tồn tại
đúng để tránh chỉnh thang điểm theo cảm giác.

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

## 4. Cài đặt trên máy của bạn (chạy thử trước)

Làm bước này trước khi thuê VM. Nếu chạy được ở máy nhà thì lên server chỉ là
lặp lại y hệt, và bạn biết chắc lỗi (nếu có) là do server chứ không do code.

### Yêu cầu

- **Python 3.11+** (dùng `zoneinfo`, cú pháp `X | None`) — repo test trên 3.12 và 3.14
- Tài khoản **Alpaca** miễn phí → https://alpaca.markets (lấy key ở phần Paper Trading)
- **Bot Telegram** → chat với [@BotFather](https://t.me/BotFather), gửi `/newbot`
- Không cần Docker, không cần database server. Tất cả nằm trong 1 file SQLite.

### Bước 1 — Clone và tạo môi trường ảo

```bash
git clone https://github.com/tuanhpham/scanner.git
cd scanner

python3 -m venv .venv
source .venv/bin/activate         # Linux / macOS
# source .venv/Scripts/activate   # Git Bash trên Windows

pip install --upgrade pip
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

# TUỲ CHỌN — nút Biểu đồ và nút Hỏi ChatGPT, xem mục 6
TV_URL=https://www.tradingview.com/chart/?symbol={sym}
CHATGPT_GPT_ID=

# Không dùng trong code hiện tại, để trống được
FINNHUB_KEY=
ANTHROPIC_API_KEY=
GROQ_KEY=
```

**Lấy `TG_CHAT_ID`:** nhắn gì đó cho bot của bạn, rồi mở
`https://api.telegram.org/bot<TG_TOKEN>/getUpdates` trên browser, tìm
`"chat":{"id":...}`.

**`SEC_UA` phải có ký tự `@`** — code kiểm tra điều này. SEC chặn IP nếu
User-Agent không hợp lệ. Đây không phải secret, chỉ là quy định của SEC.

### Bước 3 — Kiểm tra kết nối

```bash
python clock.py                      # in trạng thái phiên hiện tại
python scripts/check_calendar.py     # lịch phiên 60 ngày tới theo giờ Đức
python render.py                     # in 3 alert mẫu, không cần mạng
python scripts/check_tg.py           # .env đúng chưa: gửi 1 tin vào nhóm
pytest -q                            # toàn bộ test
```

### Bước 4 — Dựng baseline

**Bước này bắt buộc và mất thời gian.** Chạy thử nhanh trước:

```bash
python prep.py --limit 300     # ~1 phút, đủ để kiểm tra pipeline
python prep.py                 # thật: ~5000 mã, 20–40 phút
```

Kết quả mong đợi: `XONG: 4xxx ma cap nhat, 4xxx ma trong DB, ...`

### Bước 5 — Gắn cờ ETF ⚠️ BẮT BUỘC

```bash
python scripts/mark_etf.py
```

**Không được bỏ bước này.** `scorer.load_baseline()` query
`... FROM base WHERE is_etf=0`, nhưng `prep.py` không tạo cột `is_etf` —
chính `mark_etf.py` mới `ALTER TABLE` thêm cột đó. Bỏ qua thì `main.py` chết
ngay với `sqlite3.OperationalError: no such column: is_etf`.

Kết quả mong đợi: `ETF/test: 3xxx | co phieu thuong: 4xxx | thieu CIK: xxx`

### Bước 6 — Chạy thử 1 lần

```bash
python main.py --dry --once    # quét 1 lần, in ra terminal, KHÔNG gửi Telegram
python main.py --once          # quét 1 lần, gửi mã điểm cao nhất lên Telegram
```

**Chạy trong giờ phiên (15:30–22:00 giờ Đức)** mới có dữ liệu thật. Nếu tin
nhắn vào được Telegram và nút bấm phản hồi → xong, sẵn sàng lên server.

### Ba chế độ chạy

```bash
python main.py           # chạy thật, 24/7, tự bật/tắt theo lịch NYSE
python main.py --dry     # quét và log ra terminal, KHÔNG gửi Telegram
python main.py --once    # quét 1 lần rồi thoát
```

`--dry` là chế độ nên dùng khi tinh chỉnh ngưỡng: thấy đầy đủ log
`[DRY NEW] ABCD 8.3 L2` mà không spam Telegram.

Bot khởi động lại giữa phiên vẫn an toàn: `restore_today()` đọc lại bảng
`alerts` của hôm nay để không gửi trùng.

⚠️ **Chỉ được có đúng một tiến trình `main.py` sống cùng lúc.** Telegram chỉ
cho một consumer gọi `getUpdates`; chạy hai cái sẽ ra `getUpdates 409` và
vòng lặp nút bấm chết. Khi service đã chạy trên VM thì **đừng** đồng thời
chạy `python main.py` trên máy nhà bằng cùng `TG_TOKEN`.

---

## 5. Chạy 24/7 trên Oracle Cloud (miễn phí)

Bot cần sống liên tục để bắt kịp phiên Mỹ, mà để laptop mở 24/7 thì không
thực tế. Oracle Cloud **Always Free** cho VM Arm dùng vĩnh viễn không mất phí,
mạnh hơn cần thiết rất nhiều.

Toàn bộ mục này mất khoảng **1 giờ** cho lần đầu, trong đó 30–40 phút là ngồi
chờ `prep.py`.

### 5.0 Bot này cần server cỡ nào?

Rất nhỏ. Nó chủ yếu ngồi chờ HTTP response:

| Tài nguyên | Cần thực tế | Always Free cho |
|---|---|---|
| CPU | ~2–5% của 1 core | 2 OCPU (Arm Ampere A1) |
| RAM | ~250–400 MB | 12 GB |
| Đĩa | ~1 GB (DB ~150 MB + log) | 47 GB boot, tổng 200 GB |
| Băng thông | vài trăm MB/tháng | 10 TB/tháng |
| Port mở vào | **không cần cái nào** | — |

Điểm cuối quan trọng: bot chỉ tạo kết nối **đi ra** (Alpaca, Yahoo, SEC,
Telegram). Không có web server, không có webhook — Telegram được gọi bằng
long-polling `getUpdates`. Nên **không cần mở port nào trong firewall**, và
đừng tự ý sửa firewall (dễ tự khoá mất SSH).

Cấu hình đề xuất: `VM.Standard.A1.Flex`, **1 OCPU / 6 GB RAM**, Ubuntu 24.04.
Chỉ lấy một nửa quota Always Free, để dành nửa còn lại cho một VM thứ hai
(ví dụ máy test, hoặc endpoint redirect ở mục 6).

### 5.1 Tạo tài khoản Oracle Cloud

1. Vào https://www.oracle.com/cloud/free/ → **Start for free**.
2. Cần **thẻ tín dụng/debit để xác minh danh tính** — Oracle giữ ~1 EUR rồi
   hoàn lại. Tài khoản Always Free **không** tự động trừ tiền khi hết credit
   dùng thử: hết 300 USD credit (30 ngày) thì tài khoản rơi về Always Free và
   chỉ những tài nguyên trong hạn mức miễn phí còn sống.
3. ⚠️ **Chọn Home Region cẩn thận — không đổi được về sau.** Máy Always Free
   bắt buộc nằm trong home region.
   - **Germany Central (Frankfurt)** nếu bạn ở Đức: SSH nhanh, log/giờ dễ đối chiếu.
   - Một region Mỹ (**US East Ashburn**, **US West Phoenix**) cho latency thấp
     hơn tới Alpaca/Yahoo/SEC.
   
   Với vòng quét 25 giây thì chênh 100 ms là vô nghĩa — chọn Frankfurt cho dễ
   quản lý. Điều thực sự khác biệt giữa các region là **còn máy A1 trống hay
   không** (xem 5.9).
4. Xác minh email, đặt mật khẩu, bật **MFA** khi được hỏi.

### 5.2 Tạo VM

Trong Console: **Menu ☰ → Compute → Instances → Create instance**.

| Trường | Chọn |
|---|---|
| Name | `scanner` |
| Compartment | để mặc định (root) |
| Placement / Availability domain | thử **AD-1**; hết máy thì đổi AD-2, AD-3 |
| Image | **Change image → Canonical Ubuntu → 24.04** (`Minimal` cũng được) |
| Shape | **Change shape → Ampere → VM.Standard.A1.Flex** → 1 OCPU, 6 GB |
| Networking | **Create new VCN** (wizard tự làm subnet + gateway) |
| Public IPv4 address | **Assign** ← bắt buộc, không có thì không SSH được |
| Boot volume | để mặc định 47 GB |
| SSH keys | **Generate a key pair for me** → **tải cả 2 file về** |

Ảnh Ubuntu trên OCI có nhãn **"Always Free-eligible"** — cứ nhìn nhãn đó để
chắc không phát sinh phí.

⚠️ File private key tải về **chỉ tải được một lần duy nhất**. Lưu nó vào
`~/.ssh/` và đổi quyền ngay, nếu không SSH sẽ từ chối:

```bash
mv ~/Downloads/ssh-key-*.key ~/.ssh/oracle_scanner
chmod 600 ~/.ssh/oracle_scanner
```

Bấm **Create**. Sau ~1 phút state chuyển **RUNNING**, copy **Public IP address**.

### 5.3 SSH vào máy

User của ảnh Ubuntu là `ubuntu` (không phải `root`, không phải `opc`):

```bash
ssh -i ~/.ssh/oracle_scanner ubuntu@<PUBLIC_IP>
```

Đỡ phải nhớ, thêm vào `~/.ssh/config` trên máy bạn:

```
Host scanner
    HostName <PUBLIC_IP>
    User ubuntu
    IdentityFile ~/.ssh/oracle_scanner
    ServerAliveInterval 60
```

Từ giờ chỉ cần `ssh scanner`.

### 5.4 Chuẩn bị Ubuntu

Chạy trên VM. **Bước timezone là quan trọng nhất, đừng bỏ.**

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-dev build-essential git sqlite3 tmux
```

`python3-dev` + `build-essential` là để dự phòng: máy Arm (aarch64) đôi khi
thiếu wheel dựng sẵn cho một thư viện nào đó và pip phải tự biên dịch.

```bash
sudo timedatectl set-timezone Europe/Berlin
timedatectl        # kiểm tra "System clock synchronized: yes"
```

Vì sao Europe/Berlin: `clock.py` quy đổi phiên NYSE sang **giờ Đức**, và
`main.loud_mode()` dùng khung 09–17h giờ Đức để quyết định có đổ chuông không.
Để VM ở UTC (mặc định) thì giờ trong log lệch 1–2 tiếng so với những gì bạn
đọc trong alert, và bất kỳ chỗ nào trong code dùng giờ local sẽ sai. Dòng
`System clock synchronized: yes` cũng cần đúng: `vprofile.py` tính RVOL theo
**phút thứ mấy của phiên**, lệch đồng hồ là lệch RVOL.

Máy 6 GB RAM không cần swap. Nếu bạn buộc phải dùng shape `E2.1.Micro`
(1 GB RAM) thì thêm 2 GB swap:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 5.5 Lấy code và cài đặt

```bash
cd ~
git clone https://github.com/tuanhpham/scanner.git
cd scanner
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Repo private thì dùng **deploy key**: `ssh-keygen -t ed25519 -C scanner-vm`
trên VM, dán `~/.ssh/id_ed25519.pub` vào GitHub → repo → Settings → Deploy keys
(chỉ cần quyền read), rồi clone bằng URL dạng `git@github.com:...`.

Tạo `.env` trên VM — **đừng commit nó vào git**, gõ lại bằng tay:

```bash
nano .env      # dán nội dung .env ở mục 4, Ctrl+O, Enter, Ctrl+X
chmod 600 .env
```

Kiểm tra nhanh:

```bash
python clock.py     # phải in ra trạng thái phiên và giờ Đức
python render.py    # phải in 3 alert mẫu
```

### 5.6 Dựng baseline lần đầu — chạy trong `tmux`

`prep.py` mất 20–40 phút. SSH đứt giữa lúc đó sẽ giết tiến trình và bạn phải
làm lại từ đầu. Dùng `tmux`:

```bash
tmux new -s prep
source .venv/bin/activate
python prep.py && python scripts/mark_etf.py
```

Bấm **Ctrl+B** rồi **D** để rời ra (tiến trình vẫn chạy). Tắt máy tính, đi ăn
cơm, quay lại `ssh scanner && tmux attach -t prep` để xem đã xong chưa.

Xong thì phải thấy:

```
XONG: 4xxx ma cap nhat, 4xxx ma trong DB, ...
ETF/test: 3xxx | co phieu thuong: 4xxx | thieu CIK: xxx
```

Rồi thử một lần thật (nếu đang trong phiên):

```bash
python main.py --dry --once     # log ra terminal, không gửi gì
python main.py --once           # gửi 1 alert lên Telegram
```

### 5.7 Chạy tự động bằng systemd

Đây là phần "tự chạy": systemd bật bot khi máy boot, và bật lại nếu bot chết.

```bash
sudo nano /etc/systemd/system/scanner.service
```

```ini
[Unit]
Description=Stock Scanner (Telegram alert bot)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/scanner
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/scanner/.venv/bin/python main.py
Restart=always
RestartSec=30
StandardOutput=append:/home/ubuntu/scanner/state/service.log
StandardError=append:/home/ubuntu/scanner/state/service.log

[Install]
WantedBy=multi-user.target
```

Từng dòng đáng chú ý:

- `User=ubuntu` — chạy dưới user thường, không cần root. Nếu để trống, service
  chạy bằng root và sẽ ghi file `state/*.db` thuộc quyền root; sau đó bạn chạy
  tay bằng user `ubuntu` sẽ bị `unable to open database file`.
- `PYTHONUNBUFFERED=1` — không có nó, Python đệm stdout và log chỉ hiện ra
  từng khối 4 KB, `tail -f` trông như bot bị treo.
- `Restart=always` + `RestartSec=30` — mất mạng, Alpaca 500, Python traceback:
  bot chết thì 30 giây sau sống lại. `restore_today()` chống gửi trùng nên
  restart giữa phiên là an toàn.
- `Wants/After=network-online.target` — chờ có mạng mới khởi động, tránh
  crash-loop lúc máy vừa boot.

Bật lên:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now scanner     # enable = tự bật khi boot
sudo systemctl status scanner           # phải thấy "active (running)"
tail -f ~/scanner/state/service.log     # Ctrl+C để thoát xem log
```

Trong log phải xuất hiện dòng `callbacks: bat dau lang nghe nut bam`. Nếu thấy
`getUpdates 409` thì đang có tiến trình `main.py` thứ hai ở đâu đó:

```bash
ps aux | grep -v grep | grep main.py    # còn tiến trình lạc nào không
```

Bốn lệnh cần nhớ:

```bash
sudo systemctl restart scanner    # sau khi git pull
sudo systemctl stop scanner       # trước khi chạy main.py bằng tay
sudo systemctl start scanner
journalctl -u scanner -n 50       # log của systemd (crash trước khi vào file)
```

### 5.8 Chạy `prep.py` hằng ngày bằng cron

`prev_close` và `adv20` phải là số của hôm qua, không thì điểm vô nghĩa. Chạy
lúc **08:00 ET**, trước phiên và sau khi Yahoo đã chốt nến ngày hôm trước.

```bash
crontab -e         # chọn nano nếu nó hỏi
```

```cron
CRON_TZ=America/New_York
0 8 * * 1-5  cd /home/ubuntu/scanner && .venv/bin/python prep.py >> state/prep.log 2>&1
0 9 * * 1-5  cd /home/ubuntu/scanner && .venv/bin/python scripts/mark_etf.py >> state/prep.log 2>&1
5 9 * * 1-5  /usr/bin/systemctl restart scanner
```

`CRON_TZ=America/New_York` là mẹo đáng giá: cron sẽ tự xử lý lệch DST. Không
có nó, bạn phải viết `0 14` (giờ Đức) và lịch sẽ trôi 1 tiếng trong hai tuần
tháng 3 và tháng 10 khi Mỹ và EU đổi giờ lệch ngày nhau — đúng những tuần
`prep.py` dễ chạy nhầm vào giữa phiên nhất.

Ba dòng, theo thứ tự: dựng baseline (08:00 ET, xong khoảng 08:40), gắn cờ ETF
(09:00 ET), rồi restart service (09:05 ET, trước giờ mở 09:30). Dòng restart
là để chắc chắn bot nạp baseline mới; nếu bạn xác nhận
`scorer.load_baseline()` tự nạp lại theo chu kỳ thì bỏ dòng đó đi. Để lại
cũng vô hại.

Nếu bạn muốn quét cả premarket sớm, đẩy `prep.py` lên `0 6` — nhưng nhớ là
nến ngày hôm trước của Yahoo phải đã chốt.

Khi dùng phễu swing buổi sáng (mục 12), dòng 08:00 đổi thành **một** dòng gọi
`nightly.py`:

```cron
CRON_TZ=America/New_York
0 8 * * 1-5  cd /home/ubuntu/scanner && .venv/bin/python nightly.py >> state/prep.log 2>&1
0 9 * * 1-5  cd /home/ubuntu/scanner && .venv/bin/python scripts/mark_etf.py >> state/prep.log 2>&1
5 9 * * 1-5  /usr/bin/systemctl restart scanner
```

Trước đó chỗ này là một chuỗi bốn lệnh nối bằng `&&`:

```cron
# ĐỪNG dùng nữa — để đây làm mốc so sánh
0 8 * * 1-5  cd /home/ubuntu/scanner && .venv/bin/python bars.py --sync >> state/prep.log 2>&1 && .venv/bin/python prep.py --from-bars >> ... && .venv/bin/python structure.py --build >> ... && .venv/bin/python setups.py --build >> ...
```

`&&` làm đúng một việc: bước lỗi thì bước sau không chạy trên dữ liệu cũ. Nó
**không** làm ba việc còn lại, và đó là lý do thay:

1. **Nó im lặng.** Bước 2 chết thì bạn không biết gì cho đến khi tự đi đọc
   `prep.log`. `nightly.py` luôn gửi Telegram, kể cả khi lỗi, và nói rõ **bước
   nào** lỗi vì **cái gì** (mục 12.5).
2. **Mã thoát vô nghĩa.** `&&` trả mã của lệnh cuối chạy được, nên cron thấy
   "OK". `nightly.py` trả 0 / 2 / 1 / 3 — bước không bắt buộc lỗi khác hẳn bước
   bắt buộc lỗi, và `MAILTO` hay một systemd `OnFailure=` mới phân biệt được.
3. **Không có chỗ nào ghi lại lần chạy.** `nightly.py` ghi bảng `night`, nên
   dashboard trả lời được "lần cuối chạy xong là khi nào" — cái mà một chuỗi
   `&&` về nguyên tắc không biết.

Chuỗi cũ vẫn chạy được nếu bạn chỉ muốn Phase 9 mà không muốn phễu swing. Cả
hai đều bỏ được **một lượt tải yfinance** so với `prep.py` trần: `prep.py` tải 4
tháng nến cho ~5000 mã rồi ném đi, chỉ giữ 3 con số. Nhưng chỉ đổi sau khi đã
đối chiếu `adv20`/`prev_close` giữa hai cách trên bản sao DB — xem cảnh báo ở
mục 9.1.

#### Cách thứ hai: systemd timer

Timer dài hơn cron ba file nhưng được ba thứ cron không có: log đi vào
`journalctl` cùng chỗ với service chính, `Persistent=true` chạy bù nếu VM tắt
qua giờ đó, và `OnFailure=` gọi được một unit khác khi thất bại.

`/etc/systemd/system/scanner-nightly.service`:

```ini
[Unit]
Description=Scanner - phieu swing buoi sang (Stage 1-4)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/scanner
ExecStart=/home/ubuntu/scanner/.venv/bin/python nightly.py
# Chuoi nay khong bao gio chay 40 phut; qua nguong nay la treo o mot lan goi
# mang, va treo thi phai chet de lan sau con chay, khong phai giu may mai.
TimeoutStartSec=2400
```

`/etc/systemd/system/scanner-nightly.timer`:

```ini
[Unit]
Description=Goi scanner-nightly luc 08:00 gio New York, ngay lam viec

[Timer]
# systemd hieu ten mui gio, nen no tu xu ly DST — giong CRON_TZ cua cron.
OnCalendar=Mon..Fri 08:00 America/New_York
# VM tat qua 08:00 thi chay ngay khi bat lai, thay vi bo ca ngay hom do.
Persistent=true
# Lech ngau nhien toi 3 phut: khong de ca the gioi goi yfinance dung giay 0.
RandomizedDelaySec=180

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now scanner-nightly.timer
systemctl list-timers scanner-nightly.timer   # lần kế tiếp là khi nào
journalctl -u scanner-nightly.service -n 60   # panel của lần chạy gần nhất
sudo systemctl start scanner-nightly.service   # chạy tay ngay bây giờ
```

⚠️ **Chọn một, đừng chạy cả hai.** Hai lịch cùng gọi `nightly.py` lúc 08:00 thì
hai tiến trình cùng ghi `bars`/`struct`, và bạn nhận hai tin nhắn mỗi sáng. Nếu
chuyển sang timer thì xoá dòng cron tương ứng bằng `crontab -e`.

Kiểm tra cron có chạy thật:

```bash
crontab -l                        # xem lịch đã lưu
grep CRON /var/log/syslog | tail  # cron có kích hoạt job không
tail -20 ~/scanner/state/prep.log # job in ra gì
```

⚠️ `crontab -e` phải chạy bằng user `ubuntu`, **không** `sudo crontab -e` —
cron của root sẽ không tìm thấy venv và tạo file thuộc quyền root trong `state/`.

### 5.9 Xoay vòng log

`service.log` chạy 24/7 sẽ phình dần. Ba dòng cấu hình cho gọn:

```bash
sudo nano /etc/logrotate.d/scanner
```

```
/home/ubuntu/scanner/state/*.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
    copytruncate
}
```

`copytruncate` là bắt buộc ở đây: bot giữ file log mở suốt, nếu logrotate đổi
tên file thì bot vẫn ghi vào inode cũ và log mới trống trơn. `copytruncate`
copy nội dung ra rồi cắt file tại chỗ, bot không cần biết gì.

```bash
sudo logrotate -d /etc/logrotate.d/scanner   # -d = chạy thử, không sửa gì
```

### 5.10 Cập nhật code về sau

```bash
ssh scanner
cd ~/scanner
git pull
source .venv/bin/activate
pip install -r requirements.txt        # chỉ khi requirements.txt đổi
python -c "import main, render, scorer, callbacks"   # bắt lỗi syntax/import trước
sudo systemctl restart scanner
tail -20 state/service.log
```
```bash
 cd ~/scanner && git pull && source .venv/bin/activate && pip install -r requirements.txt && python -m pytest -q && sudo systemctl daemon-reload && sudo systemctl restart scanner.

Dòng `python -c "import ..."` đáng làm: import lỗi thì systemd sẽ
crash-loop mỗi 30 giây và bạn phải mò trong `journalctl`, trong khi chạy
import tay nó in traceback ra ngay.
```

Sửa `render.py` thì thêm `python render.py` trước khi restart — nó in 3 alert
mẫu và tự kiểm tra panel `<pre>` còn thuần ASCII hay không (xem mục 9).

### 5.11 Sao lưu

Ít việc hơn bạn tưởng. `base` và `meta` được `prep.py` dựng lại mỗi ngày, mất
cũng chỉ tốn 40 phút chạy lại. Thứ thật sự không thay lại được là **`.env`** —
lưu nó vào password manager, xong.

Muốn giữ lịch sử alert để phân tích về sau thì backup bảng `alerts` mỗi tuần.
Dùng `.backup` của sqlite3, đừng `cp` file `.db` khi bot đang chạy (WAL đang
bật, copy thô có thể ra file hỏng):

```bash
mkdir -p ~/backup
sqlite3 ~/scanner/state/baseline.db ".backup '/home/ubuntu/backup/baseline-$(date +%F).db'"
```

### 5.12 Bốn cạm bẫy của Oracle Always Free

**"Out of host capacity" khi tạo VM.** Lỗi hay gặp nhất, và không phải lỗi của
bạn: region đó tạm hết máy A1 trống. Cách xử lý, theo thứ tự nên thử:

1. Đổi **Availability Domain** (AD-1 → AD-2 → AD-3) rồi thử lại.
2. Hạ xuống **1 OCPU / 6 GB** — dễ có chỗ hơn 2 OCPU / 12 GB.
3. Thử lại vào giờ thấp điểm của region đó (đêm theo giờ địa phương).
4. Nâng tài khoản lên **Pay As You Go**. Tài nguyên trong hạn mức Always Free
   vẫn miễn phí sau khi nâng, nhưng tài khoản PAYG được ưu tiên capacity nên
   thường tạo được ngay. Đặt **compartment quota** hoặc **budget alert** để
   chắc không vô tình tạo thêm tài nguyên có phí.
5. Bí quá thì dùng tạm hai VM `VM.Standard.E2.1.Micro` (x86, 1 GB RAM) —
   luôn có sẵn. Bot chạy được trên 1 GB nếu thêm swap (xem 5.4), chỉ là
   `prep.py` sẽ chậm hơn.

Nhiều người viết script gọi API tạo instance mỗi 30 giây cho tới khi có máy.
Nó hoạt động, nhưng đổi AD và hạ cấu hình thường giải quyết được rồi.

**Oracle có thể thu hồi VM "nhàn rỗi".** Đây là điều bạn cần biết trước.
Oracle coi một instance Always Free là idle nếu trong **7 ngày liên tục** cả
ba điều sau đúng: CPU percentile 95 dưới 20%, network dưới 20%, RAM dưới 20%
(điều kiện RAM chỉ áp cho shape A1). Bot này dùng ~3% CPU nên **đúng là ứng
viên bị thu hồi**. Oracle gửi email cảnh báo trước, và cái bị "thu hồi" là
instance bị **stop** — bạn thường start lại được, nhưng nó có thể xảy ra giữa
phiên và bạn mất alert cả hôm đó.

Cách xử lý sạch sẽ: **nâng lên Pay As You Go**. Tài nguyên Always Free không
bị tính phí sau khi nâng, và instance PAYG không bị thu hồi vì nhàn rỗi. Nếu
giữ Always Free, ít nhất hãy bật giám sát để biết khi nó bị stop — thêm vào
crontab một dòng gửi tin Telegram lúc máy boot chẳng hạn, hoặc đơn giản là để
ý heartbeat sáng của bot: sáng nào không thấy heartbeat thì vào Console xem
instance còn RUNNING không.

(Trên mạng có nhiều script "keep-alive" đốt CPU giả để vượt ngưỡng 20%. Chúng
chạy được, nhưng đó là đốt điện thật để lách một chính sách — nâng PAYG rẻ hơn
và trung thực hơn.)

**Đừng chạm vào firewall.** Ảnh Ubuntu trên OCI đến kèm sẵn rule iptables, và
lớp `Security List` / `NSG` của VCN còn chặn ở tầng trên nữa. Bot chỉ cần
kết nối đi ra nên **không phải mở gì cả**. Chạy `sudo ufw enable` mà chưa
allow 22 là mất SSH ngay lập tức, và cách vào lại duy nhất là Cloud Shell
serial console — đừng thử.

**IP nhà và IP datacenter không được đối xử như nhau.** SEC EDGAR không quan
tâm, miễn `SEC_UA` hợp lệ. Nhưng **Yahoo screener siết IP cloud mạnh hơn** —
nếu trên VM bạn thấy `[yahoo] trang 0 loi` liên tục trong khi ở máy nhà thì
không, đó là chuyện này. Bot vẫn chạy được: `universe_live.py` gộp hai nguồn
và Alpaca là nguồn realtime, chỉ là bạn mất phần đối chiếu chéo (cột "lệch %
giữa 2 nguồn" ở mục 3 và điểm `+1.5` cho mã chỉ Alpaca thấy sẽ không còn ý
nghĩa vì mọi mã đều chỉ Alpaca thấy).

### 5.13 Kiểm tra sức khoẻ hệ thống

Ba lệnh chạy sau tuần đầu để chắc mọi thứ ổn:

```bash
systemctl is-active scanner && uptime          # service sống, máy chưa reboot lạ
df -h / && free -h                             # đĩa và RAM còn thoải mái
ls -la ~/scanner/state/                        # baseline.db mới cỡ nào
sqlite3 ~/scanner/state/baseline.db \
  "SELECT k, v FROM meta; SELECT COUNT(*) FROM alerts WHERE day = date('now');"
```

`meta.built` phải là ngày hôm nay (hoặc phiên gần nhất) — nếu nó cũ vài ngày
thì cron `prep.py` đang không chạy, xem lại 5.8.

### Chạy trên máy khác

**Windows** — Task Scheduler, trigger "At startup", action
`C:\...\scanner\.venv\Scripts\python.exe C:\...\scanner\main.py`, đặt
"Start in" là thư mục scanner. Nhớ đặt cả task cho `prep.py`.

**macOS** — `launchd` với `KeepAlive`, hoặc đơn giản là `tmux` nếu bạn không
tắt máy. Nhưng laptop có sleep, và sleep giữa phiên thì bot mất phiên — VM
vẫn là lựa chọn đúng.

**Raspberry Pi** — chạy tốt, dùng y hệt phần systemd ở 5.7. Nhớ
`sudo timedatectl set-timezone Europe/Berlin` và kiểm tra thẻ SD còn khoẻ
(SQLite + WAL ghi khá nhiều).

---

## 6. Alert trên Telegram trông như thế nào

Muốn xem ngay không cần chạy cả bot:

```bash
python render.py     # in 3 alert mẫu (mức 1 / 2 / 3) + layout bàn phím
```

Một alert mức 3 trông như sau:

```
TẠM DỪNG GIAO DỊCH · CHỜ CÔNG BỐ TIN · T1     ← HALT (chỉ khi đang bị dừng)
từ 15:42 ET · chưa có giờ mở lại — Tin CHƯA ra. Không ai biết giá mở lại ở đâu.

🔴 WETO · $10.61 · ▲ +85.5%             ← HEADER
EXTREME EVENT · Tín hiệu mới
████████░░ 12.4/15
Realtime · 15:42 ET · phút 12/390

⚠️ ÁP LỰC FLOAT                         ← BADGE

CATALYST                                ← NEWS (blockquote, chỉ khi feed sống)
Weto Inc Announces Pricing of $15.0 Million Registered Direct Offering
Benzinga · 30 phút trước · còn 1 tin khác

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

- **9 khối** cố định, luôn đúng thứ tự: HALT → HEADER → BADGE → CATALYST →
  DATA → RISK → SEC → WHY → FOOT. Khối nào không có dữ liệu thì bỏ hẳn, không
  để trống. HALT chỉ xuất hiện khi feed Nasdaq nói mã đang bị dừng (hoặc vừa mở
  lại trong 5 phút) — bình thường alert bắt đầu ngay ở HEADER. CATALYST chỉ
  xuất hiện khi feed tin đang sống; xem mục 8 → "Tin tức catalyst".
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
  (FOOT → BADGE → WHY → SEC → CATALYST → RISK → SỐ LIỆU → HEADER → HALT),
  không bao giờ cắt giữa tag HTML:

  | Khối | P | Vì sao ở đó |
  |---|---|---|
  | HALT | 10 | Đang bị dừng giao dịch thì mọi số liệu còn lại là thứ yếu |
  | HEADER | 9 | Mã, giá, %, điểm, mức độ — bỏ là không còn gì để đọc |
  | SỐ LIỆU | 8 | Lý do alert tồn tại: RVOL / ATR / $ volume / float |
  | RISK | 7 | Nơi **duy nhất** kết luận về pha loãng / phá sản / hủy niêm yết |
  | CATALYST | 6 | Mất nó là mất tiêu đề tin, nhưng cảnh báo vẫn còn ở RISK |
  | SEC, WHY | 5, 4 | Cả hai là `blockquote expandable` — phải bấm mới mở |
  | BADGE | 3 | Chỉ nhắc lại một con số đã có trong panel |
  | FOOT | 2 | Câu miễn trừ cố định |

  **Ràng buộc bắt buộc: `P_DATA > P_FOOT`.** Bản trước ngược lại (`DATA = 4`,
  `FOOT = 8`) vì thang đo tính theo *giá trị / độ dài*: bỏ FOOT chỉ tiết kiệm
  ~90 ký tự nên nó "rẻ" để giữ. Nhưng hệ quả thực tế là panel số là khối **thứ
  ba** bị bỏ, còn dòng miễn trừ sống gần cuối — một alert còn header + RISK +
  miễn trừ mà không có RVOL/ATR/$ volume thì không dùng để quyết định gì được.
  Dòng miễn trừ là nghĩa vụ pháp lý, không phải nội dung. Nếu muốn giữ nó là
  dòng bắt buộc thì đặt `P_FOOT = 8, P_DATA = 9` — miễn giữ được bất đẳng thức.
  `tests/test_render.py::test_data_song_lau_hon_footer` khoá điều này lại.

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

### Nút Biểu đồ — mở app TradingView thay vì web

Mặc định URL là `https://www.tradingview.com/chart/?symbol={sym}`. Đây là một
https URL thường, và **bot không thể ép nó mở app**: trường `url` của
`InlineKeyboardButton` trong Bot API chỉ nhận "HTTP or `tg://` URL", nên đặt
`tradingview://…` vào đó sẽ bị trả `Bad Request: inline keyboard button URL is
invalid`. Nhét vào `<a href>` trong nội dung tin cũng bị lọc y hệt.

Việc mở app hay không do **universal link** (iOS) / **app link** (Android)
quyết định — tức là do TradingView khai báo đường dẫn nào trong
`apple-app-site-association` / `assetlinks.json` của họ. Hai điều kiện:

1. Telegram phải **không** dùng trình duyệt nội bộ (Settings → mục có chữ
   *browser*). Nếu còn bật, webview nuốt link và không bao giờ bàn giao cho OS.
2. Đường dẫn trong URL phải nằm trong danh sách TradingView khai báo.

Nếu ChatGPT mở được app mà TradingView không, thì điều kiện 1 đã đạt và vấn đề
là điều kiện 2. Vì vậy URL này **cấu hình được** — đặt `TV_URL` trong `.env`
với `{sym}` là chỗ điền mã, rồi thử tới khi mở được app:

```bash
TV_URL=https://www.tradingview.com/symbols/{sym}/
```

Cách tìm dạng đúng nhanh nhất, không cần chạy bot: mở app Ghi chú trên điện
thoại, dán từng URL vào rồi bấm, xem cái nào nhảy sang app TradingView.

`python render.py` in ra URL đang dùng và **cảnh báo nếu scheme không phải
http(s)/tg**, vì lỗi đó làm Telegram từ chối *cả* tin nhắn, không chỉ cái nút.

Nếu hoá ra chỉ `tradingview://` mở được app, cách duy nhất là dựng một
**endpoint chuyển hướng**: một URL https của bạn trả HTTP 302 sang
`tradingview://…`. Telegram nhận https, trình duyệt hệ thống theo redirect,
OS thấy scheme lạ và bàn giao cho app. Bạn có Oracle VM nên chạy được, nhưng
cần mở port, có tên miền và TLS — công sức thật, không phải sửa vài dòng.

Ghi chú liên quan: URL hiện chỉ ghi `?symbol=WETO`, **không có sàn**, nên
TradingView phải tự đoán và có thể mở sai mã khi ticker trùng giữa các sàn.
Alpaca đã trả về sàn ở `prep.py:70` (`a.exchange`) nhưng chỉ dùng để lọc rồi
bỏ đi. Muốn có `NASDAQ:WETO` hay `/symbols/NASDAQ-WETO/` thì phải thêm cột
`exchange` vào bảng `base` và chạy lại `prep.py`.

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
| `main.py` | Vòng lặp chính, 8 task async, ngưỡng alert, ghi bảng `alerts` |
| `prep.py` | Dựng baseline hàng ngày: adv20, atr14, prev_close, cik |
| `bars.py` | Kho nến ngày (~3 năm) trong SQLite. Lưu cả giá thô và giá đã điều chỉnh |
| `structure.py` | Đo cấu trúc giá từ nến ngày: nền tích luỹ, pivot, độ sâu cú rơi. Chỉ **đo**, không phán xét |
| `setups.py` | Hai setup BO/RV: ngưỡng nền (buổi tối) + điều kiện kích hoạt (trong phiên) |
| `plan.py` | Từ `struct` ra **kế hoạch lệnh**: điểm vào, cắt lỗ, mục tiêu, cỡ vị thế. Thuần stdlib, không import `setups.py` |
| `backtest.py` | Chạy lại BO/RV trên nến ngày quá khứ. Gọi đúng hàm của bot, có mốc so sánh "vào mù" |
| `clock.py` | Lịch phiên NYSE → giờ Đức. Xử lý DST lệch, nửa phiên, ngày lễ |
| `universe_live.py` | Gộp Alpaca + Yahoo screener → dict các mã đang chạy |
| `scorer.py` | Lọc + chấm điểm. Cũng lấy `float_sh` lười (top 60 mã) |
| `vprofile.py` | Đường cong chữ U của volume nội phiên → RVOL chuẩn hoá |
| `edgar.py` | Tra SEC EDGAR, chấm điểm rủi ro pha loãng. Cache 30 phút trên đĩa |
| `render.py` | Dựng HTML cho Telegram. **Thuần hàm** — không network, không DB |
| `tgapi.py` | **Đường gửi Telegram duy nhất**: nhịp gọi, thử lại, hạ cấp HTML |
| `notifier.py` | Chỉ là spool trên đĩa (`state/spool.json`) cho lúc mất mạng |
| `callbacks.py` | Long-polling `getUpdates` → xử lý nút bấm |
| `store.py` | Bảng phụ: `alert_msg`, `watch`, `kv`. Mọi hàm bắt lỗi, không làm chết alert |
| `outcome.py` | Đo chất lượng alert: giá sau 15p/60p/đóng phiên, đỉnh/đáy → bảng `outcome` |
| `events.py` | Log có cấu trúc `state/events.jsonl` — 1 dòng JSON/alert, để máy đọc |
| `halts.py` | Feed tạm dừng giao dịch của Nasdaq. Chỉ trong RAM, không ghi DB |
| `news.py` | Tin tức theo mã + bảng từ khoá catalyst. Sổ tay cuộn 4 giờ trong RAM |
| `push.py` | Đẩy snapshot lên Cloudflare D1 cho trang web. Mở DB ở chế độ **chỉ đọc** |
| `tests/` | Test chạy được cả bằng `pytest -q` và bằng `python tests/test_x.py` |

Phễu swing buổi sáng (mục 12) — năm file, chạy theo thứ tự đó:

| File | Việc |
|---|---|
| `config.py` | **Mọi ngưỡng của Stage 1–4, một chỗ.** Dashboard đọc đúng file này để hiện bảng Config |
| `regime.py` | Stage 1: trạng thái thị trường từ nến SPY → bảng `regime` + tra bảng playbook |
| `sectors.py` | Stage 2: xếp hạng 11 sector SPDR → bảng `sector_rank` (có lịch sử, `--backfill`) |
| `holdings.py` | Stage 3: đọc `holdings/sector_holdings.csv` — thành phần sector, **chép tay, không scrape** |
| `watchlist.py` | Cửa vào của phần **trong phiên** (mục 12.7): sàn chất lượng + cổng regime + đọc danh sách của hôm nay. Thuần stdlib — yfinance chỉ import bên trong đúng một hàm |
| `positions.py` | Vị thế đang mở, đọc từ khoá `scanner:positions` do app lux-lookthrough đẩy lên (mục 12.8). Thiếu = **không biết**, chứ không phải "không có vị thế" |
| `quotes.py` | Nguồn báo giá sau một **giao diện** (`Provider`): `YFProvider` thật, `FixtureProvider` cho test. Mọi báo giá mang mốc thời gian, độ trễ và cờ `vol_ok` |
| `watch.py` | Ba luật **Tier 1** trong phiên + chống spam ghi xuống DB (mục 12.9). `evaluate()` **đo**, `decide()` **phán xét** — cùng một cách tách như `structure`/`setups` |
| `render_watch.py` | Dựng ba tin nhắn trong phiên (mở phiên, cảnh báo, tổng kết). **Thuần hàm** |
| `watchd.py` | Tiến trình canh phiên, **riêng biệt với `main.py`**: đọc danh sách của tối qua, hỏi giá mỗi 60s, gửi Tier 1, tự bật/tắt theo lịch phiên |
| `render_night.py` | Dựng tin nhắn buổi sáng. **Thuần hàm** — giống `render.py`, không network, không DB |
| `nightly.py` | Xâu các bước lại (`nightly.NAMES`), ghi bảng `night`, luôn gửi Telegram kể cả khi lỗi, trả mã thoát cho cron |
| `holdings/sector_holdings.csv` | ~250 mã → sector. Có `as_of=` ở đầu file; quá 180 ngày thì cảnh báo |
| `scripts/demo_stage3.py` | Dựng DB giả để xem dashboard/tin nhắn mà không cần mạng |
| `.github/workflows/ci.yml` | CI: compile + selftest (không thư viện) và `pytest` (đủ thư viện) |
| `scripts/mark_etf.py` | Gắn cờ `is_etf` từ Nasdaq Trader. **Bắt buộc sau `prep.py`** |
| `scripts/check_calendar.py` | In lịch phiên 60 ngày tới theo giờ Đức |
| `scripts/check_tg.py` | Thử `.env` + kết nối Telegram bằng 1 tin nhắn trơn |
| `scripts/preview_alert.py` | Gửi 1 alert mẫu (dữ liệu giả) để xem layout, có cả nút |
| `scripts/report_quality.py` | Bảng "điểm cao có tốt hơn không" từ bảng `outcome` |

### Đường gửi Telegram

Trước Phase 4a có **hai** module gửi tin, mỗi cái có bản sao riêng của
rate-limit / 429 / hạ cấp HTML — và hai bản đã bắt đầu lệch nhau. Giờ chỉ còn
một đường, `notifier.py` không gọi mạng nữa:

- **`tgapi.py`** — mọi lần gọi Bot API. Ba lớp bảo vệ, mỗi lớp cho một kiểu thất
  bại khác nhau:
  1. **Nhịp gọi** — `MIN_GAP` 1.2s giữa hai lần gọi, cộng trần `PER_MIN` 18
     tin/phút. Trần chỉ áp cho `sendMessage`; `editMessageText` và
     `answerCallbackQuery` đi ngay, không thì người bấm nút phải chờ cả phút.
  2. **Thử lại** — mất mạng thì lùi dần; 429 thì chờ đúng `retry_after` Telegram
     trả về. Lỗi 4xx khác thì *không* thử lại: gửi lại cũng thế thôi.
  3. **Hạ cấp HTML** — Telegram từ chối tag thì bỏ `expandable` → bỏ
     `blockquote` → strip hết tag (`render.degrade()`). Cắt ở 4096 ký tự.
- **`notifier.Spool`** — khi `tgapi.send()` trả `None`, text được giữ trong
  `state/spool.json` (**trên đĩa**, để VM restart giữa lúc mất mạng không mất
  tin), tối đa 50 tin, bỏ tin *cũ* nhất khi tràn. Gửi bù đúng thứ tự, kèm nhãn
  "trễ N phút", `loud=False`. Gửi bù được kích hoạt sau mỗi alert gửi thành công
  và mỗi 20s trong `loop_clock`.

**Hạn chế có ý thức: tin gửi bù không có nút bấm.** Nút "Theo dõi" gắn với
`message_id`, mà tin gửi bù là tin mới nên snapshot cũ không còn khớp. Alert trễ
không nút vẫn hơn không có alert.

`main.tg_send()` coi "đã vào spool" là **đã gửi**. Nếu trả `False` thì
`loop_score` sẽ chấm điểm lại mã đó ở vòng sau và gửi trùng.

### Các bảng trong `state/baseline.db`

| Bảng | Tạo bởi | Nội dung |
|---|---|---|
| `base` | `prep.py` | 1 dòng/mã: adv20, atr14, prev_close, float_sh, cik, **exch**, is_etf |
| `meta` | `prep.py` | Cặp key-value: `built`, `count`, `etf_marked` |
| `bars` | `bars.py` | Nến ngày, khoá `(sym, d)`: o/h/l/c + `ac` (adj close) + v. Giữ ~800 phiên |
| `struct` | `structure.py` | 1 dòng/mã, **dựng lại toàn bộ mỗi tối**: nền tích luỹ, pivot, off_high, rs_pct |
| `candidates` | `setups.py` + `plan.py` | Danh sách theo dõi cho phiên tới, khoá `(sym, setup)`, **kèm kế hoạch lệnh** (`trigger`, `stop`, `target`, `size_pct`). Cũng dựng lại toàn bộ |
| `alerts` | `main.py` | Lịch sử alert đã gửi. Dùng cho `restore_today()` |
| `alert_msg` | `store.py` | `sym`+`day` → `message_id` + snapshot (để tính delta) |
| `watch` | `store.py` | Mã đang theo dõi trong phiên (`kind='track'`) |
| `outcome` | `outcome.py` | 1 dòng/alert: px0, px15, px60, px_close, đỉnh, đáy |
| `kv` | `store.py` | Hiện chỉ giữ `tg_offset` của `getUpdates` |
| `regime` | `regime.py` | 1 dòng/phiên: trend, vol, sma50/200, độ dốc, atr_pct. **Ghi thêm, không ghi đè** |
| `sector_rank` | `sectors.py` | 1 dòng/(phiên, sector): hạng, điểm, ret21/63/126, ba cờ xu hướng |
| `night` | `nightly.py` | 1 dòng/lần chạy: mã thoát, giây, nến quyết định, JSON của từng bước, cảnh báo |
| `watch_alert` | `watch.py` | 1 dòng/(ngày, mã, luật) — **khoá chính chính là luật chống spam**: đã có dòng thì không gửi lại, kể cả sau khi restart giữa phiên. `sym=''` giữ các tin một-lần (`open`, `summary`, `src_down`) |

Ba bảng `regime` / `sector_rank` / `night` **giữ lịch sử** thay vì dựng lại mỗi
tối như `struct`/`candidates`.
Khác biệt đó là cố ý: biểu đồ lịch sử hạng ngành trên dashboard và cột "Δ5d /
Δ21d" đọc chính lịch sử này, nên một bảng dựng lại mỗi tối sẽ không bao giờ trả
lời được "tuần trước sector này đứng thứ mấy". `night` thì bị cắt còn
`KEEP_RUNS` dòng gần nhất; hai bảng kia không cắt (11 dòng/phiên ≈ 2.800
dòng/năm — không đáng dọn).

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
python outcome.py                # smoke test bảng outcome trên DB tạm
python halts.py                  # test parser feed halt bằng mẫu — không cần mạng
python halts.py --live           # gọi thật Nasdaq, in mã nào đang bị dừng
python news.py                   # test bảng từ khoá catalyst — không cần mạng
python news.py --live            # gọi thật Alpaca, in tin 4 giờ gần nhất theo mã
python events.py                 # smoke test log có cấu trúc
python events.py --tail 20       # 20 dòng cuối của state/events.jsonl
python notifier.py               # smoke test spool: giữ tin, đúng thứ tự
python scripts/check_tg.py       # .env đúng chưa, bot gửi được vào nhóm chưa
python scripts/preview_alert.py  # gửi 1 alert mẫu lên Telegram
```

Kho nến ngày và tầng đo cấu trúc (Phase 9):

```bash
python bars.py                   # selftest: upsert, limit, upto, điều chỉnh split
python bars.py --info            # kho có bao nhiêu mã / nến, ngày mới nhất
python bars.py --info NVDA       # 10 nến cuối của một mã
python bars.py --sync --full     # lần đầu: tải 2 năm cho cả universe (dùng tmux)
python bars.py --sync            # hằng ngày: chỉ 1 tháng gần nhất
python bars.py --purge           # xoá nến cũ hơn 800 phiên
python structure.py              # selftest trên chuỗi giá tổng hợp — không cần mạng
python structure.py --build      # dựng bảng struct + in list "sắp vượt pivot"
python structure.py --show NVDA  # toàn bộ số đo của một mã
python setups.py                 # selftest ngưỡng BO/RV — không cần mạng
python setups.py --build         # struct -> candidates + bảng LÝ DO BỊ LOẠI
python setups.py --show BO       # danh sách BO đang theo dõi, kèm pivot
python backtest.py               # selftest trên chuỗi nến tổng hợp
python backtest.py --run --limit 300      # chạy lại quá khứ, 300 mã đầu
python backtest.py --run --by base_len    # nền dài bao nhiêu thì mới trả tiền
python prep.py --from-bars       # dựng base từ kho nến, không gọi mạng lần nào
```

`python setups.py --build` in bảng lý do bị loại giống `scorer.py`. Nếu
`khong co nen tich luy` chiếm gần hết thì bình thường — phần lớn thị trường
không ở nền. Nếu `con xa pivot` chiếm gần hết thì thị trường vừa rơi mạnh, và
BO đúng ra nên im lặng.

### Test

```bash
pytest -q                        # cần đủ thư viện (chỉ chạy đủ trên VM / CI)
python tests/test_render.py      # từng file chạy riêng được, không cần pytest
```

`pytest.ini` ghim `testpaths = tests`: `pytest -q` **chỉ** quét `tests/`, không
quét `scripts/`. Đừng bỏ dòng đó — xem mục "Lỗi thường gặp".

Cùng một file test chạy được ở cả hai chỗ (`tests/_util.py`). Trên máy dev thiếu
thư viện, `need()` **bỏ qua cả file và thoát 0** thay vì báo lỗi — nhờ vậy bạn
vẫn kiểm tra được `render.py`, `halts.py`, `news.py`, `outcome.py`,
`events.py`, `notifier.py`, `bars.py`, `structure.py`, `setups.py`,
`backtest.py` trước khi push, và CI chạy phần còn lại.

Cả tầng trong phiên của mục 12.9 cũng chạy đầy đủ trên máy dev: `test_watch.py`,
`test_render_watch.py`, `test_watchd.py`, `test_quotes.py`, `test_vprofile.py`.
Đó là cố ý — `watch.py`, `quotes.py`, `vprofile.py` **thuần stdlib**, nguồn báo
giá nằm sau một giao diện nên test dùng `FixtureProvider`, và `send` được tiêm
vào `tick()` nên không có test nào gọi mạng hay gọi Telegram.

**Cập nhật 2026-09-25:** máy dev hiện **cài được đủ** `requirements.txt`
(`pip` đi qua được firewall; chỉ `wrangler login` là không). Nghĩa là
`python -m pytest -q -rs` ở máy tái hiện **đúng** job `test` của CI — đừng đợi
CI để biết mình làm đỏ. Nhưng nó mở ra một kiểu lỗi mới: một test **gọi mạng
thật** trước đây vô hình (thiếu thư viện → `need()` bỏ qua cả file), giờ nó
chạy, và một test xanh nhờ mạng là một test sẽ đỏ ngẫu nhiên vào ngày mạng hỏng.
Cách phát hiện là **thời gian**, không phải kết quả:

```bash
python -m pytest -q --durations=10      # test thuần logic phải ~ms, không phải giây
```

Hai test đã bị bắt bằng đúng cách này (2.06s và 3.21s → cả hai giờ dưới 0.1s):
`test_quotes.py` thay `quotes._rows_from_yf`, và `watchlist.refresh_mktcap()`
được thêm tham số `fetch=None` làm **mối khâu** để test tiêm nguồn giả vào.
Không có mối khâu đó thì chính test kiểm *"thất bại không được đóng dấu là đã
kiểm"* lại đi ra mạng thật.

`tests/test_bars.py`, `test_structure.py`, `test_setups.py` và
`test_backtest.py` chạy **đầy đủ** trên máy dev vì bốn module đó chỉ dùng
`sqlite3` — kể cả phần đọc
DataFrame của yfinance,
được test bằng một DataFrame giả (`_DF`/`_Ser`) chỉ hỗ trợ đúng những phép
`bars._rows_of()` thật sự gọi. Đây là chỗ duy nhất trong repo mà một
lỗi *logic* (không phải cú pháp) bị bắt trước khi push.

CI có hai job vì hai mục đích khác nhau: `compile` **không cài gì** (bắt lỗi cú
pháp — bạn deploy bằng `git pull`, một lỗi syntax là service crash-loop suốt
phiên), còn `test` cài đủ `requirements.txt` rồi `pytest -q`.

`python scorer.py` là công cụ debug tốt nhất. Nó in ra bảng lý do bị loại:

```
Ly do bi loai:
  khong co baseline (ETF/moi/kem thanh khoan)      412
  rvol < 3.0                                       118
  tang < 5%                                         64
  thanh khoan < $2M                                 31
```

Nếu `khong co baseline` chiếm gần hết → `prep.py` chưa chạy hoặc chạy lỗi.

### Đo chất lượng alert — bảng `outcome`

Đây là công cụ trả lời câu hỏi **"mã 8.3 điểm có thật sự tốt hơn mã 7.1 điểm
không"**. Không có nó thì mọi lần chỉnh `ALERT_SCORE` hay trọng số trong
`scorer.py` chỉ là đổi cảm giác.

Mỗi alert được gửi → một dòng trong bảng `outcome`, rồi được điền dần:

| Cột | Nghĩa |
|---|---|
| `px0` | giá **lúc gửi alert** — chính con số bạn nhìn thấy trong tin nhắn |
| `px15` / `px60` | giá sau 15 / 60 phút |
| `px_close` | giá đóng phiên |
| `hi_after` | đỉnh cao nhất sau alert (**MFE**) |
| `lo_after` | đáy thấp nhất sau alert (**MAE** — mức lỗ phải chịu) |
| `src` | `live` = số tạm từ vòng quét · `yf` = **đã chốt** bằng nến 1 phút |

**Hai đường điền số, cố ý làm vậy:**

1. `loop_outcome` (60 giây) đọc giá có sẵn trong `st.universe` — **không gọi
   API nào thêm**. Nhược điểm: mã nguội đi và rời khỏi screener thì mất dữ
   liệu, và `hi_after`/`lo_after` chỉ là đỉnh/đáy *của các lần lấy mẫu*.
2. `outcome.backfill()` chạy lúc sang ngày mới, đọc **nến 1 phút của
   yfinance** → số chính xác, điền cả những mã đã rời universe, rồi đặt
   `src='yf'`. Chỉ số này mới đáng dùng để kết luận.

Vì vậy báo cáo có cột `cov` (coverage). **Đọc `win%`/`med%` khi `cov` còn thấp
là tự lừa mình** — phần thiếu chính là những mã đã nguội, tức là phần tệ nhất.

```bash
python scripts/report_quality.py           # 30 ngày gần nhất
python scripts/report_quality.py 90        # 90 ngày
python scripts/report_quality.py 30 --syms # kèm từng alert một
python outcome.py --backfill 2026-09-04    # chốt lại một ngày cụ thể
python outcome.py --report 30              # bảng gọn, không phần diễn giải
```

```
BUCKET       n   cov  win15   med15  win60   med60  medCls  medMFE  medMAE
--------------------------------------------------------------------------
7.0-8.0    142   96%    48%    +0.4    44%    -0.2    -1.1    +3.1    -2.8
8.0-9.0     67   99%    61%    +1.9    57%    +2.4    +1.0    +6.0    -2.1
9.0-10.0    23  100%    70%    +3.8    65%    +5.1    +3.3    +9.2    -1.9
12.0+        8  100%    75%    +6.2    75%    +8.0    +6.1   +14.1    -2.2
```

**Tiêu chí xong:** sau 3–4 tuần, nếu nhóm `7.0-8.0` có `win15` khoảng 50%
(ngang tung xúc xắc) trên ≥30 alert thì đã có câu trả lời: **nâng
`ALERT_SCORE` lên 8.0** và cắt được một nửa số alert rác.
`report_quality.py` tự in ra kết luận đó khi đủ mẫu, để bạn không đọc bảng
theo hướng mình muốn thấy.

⚠️ Ba cạm bẫy khi đọc bảng:

- **`medMFE` rất đẹp và gần như vô dụng** — đó là đỉnh *hoàn hảo* không ai bán
  đúng. Hai cột đáng tin là `med15` và `med60`.
- Bảng **không tính spread, slippage, hay việc bạn có kịp vào lệnh** — mã float
  nhỏ RVOL 60× có spread rất rộng.
- Một mã có thể có **nhiều dòng trong cùng ngày** (alert `NEW` rồi `UP`). Các
  mẫu đó tương quan với nhau, nên `n` lớn hơn số mã thực tế.

`python main.py --dry` **không** ghi vào bảng `outcome` — chỉ alert gửi thật
mới được đo.

### Log có cấu trúc — `state/events.jsonl`

`state/bot.log` là tiếng Việt cho **người** đọc lúc bot đang chạy. Còn câu hỏi
"trong 3 tuần qua, mã có RVOL > 50 *và* SEC risk cao thì thắng bao nhiêu lần"
thì `grep` trên tiếng Việt không bao giờ trả lời được. `events.py` ghi thêm một
file cho **máy** đọc: mỗi dòng một JSON độc lập.

```bash
python events.py --tail 20                 # 20 dòng cuối
python events.py --tail 50 --kind alert    # chỉ dòng alert
```

| Khoá | Nghĩa |
|---|---|
| `ts` | epoch giây (giữ nguyên dạng số để sort/join, không format) |
| `kind` | `alert` hoặc `halt_block` |
| `alert_kind` | `NEW` / `UP` — khác `kind` của dòng log |
| `level`, `score`, `rvol`, `px`, `chg`, `float_rot`, `sec_risk`, … | số liệu lúc gửi |
| `dry`, `mso`, `session`, `ts_et`, `halt` | ngữ cảnh phiên |

Ba điều cố ý:

- **Trường thiếu ghi là `null`, không bỏ khoá.** "Không đo được RVOL" và
  "RVOL = 0" là hai chuyện khác nhau; bỏ khoá thì sau này không phân biệt được.
- **Ghi thất bại không ném lỗi** — mất một dòng log còn hơn mất một alert. Mọi
  hàm trả `bool`, lỗi thì im lặng bỏ qua.
- **Ghi cả trong `--dry`** (có `dry: true`), khác với bảng `outcome`. Dry chỉ
  đọc, không gửi gì, nên thu số liệu vẫn an toàn.

Ghép với bảng `outcome` bằng khoá **`(sym, int(ts))`**: `main.py` truyền *cùng*
một `ts` cho `outcome.record()` và `events.alert()`, nhưng cột `alert_ts` là
`INTEGER` nên phần thập phân bị cắt.

File tự xoay khi vượt 5 MB (đổi tên thành `.jsonl.1`), và không nằm trong git.

### Tạm dừng giao dịch — `halts.py`

Một mã +85% với RVOL 60× rất có thể **đang bị tạm dừng giao dịch**. Alert cho
mã đang halt là alert vô dụng: không mua được, và khi mở lại giá đã nhảy sang
chỗ khác. Ngược lại, mã **vừa mở lại** sau halt T2 (tin đã ra) lại là tình
huống đáng chú ý nhất trong phiên.

Nguồn: `https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts` — miễn phí,
không cần key, nhưng **Nasdaq giới hạn 1 query/phút**, nên `HALT_SEC = 60` và
đừng giảm.

`loop_halts` chạy mỗi 60 giây, giữ toàn bộ trạng thái **trong RAM** (không ghi
DB — dữ liệu này chỉ có giá trị trong vài phút). Ba mức xử lý theo `sev`:

| `sev` | Mã lý do | Bot làm gì |
|---|---|---|
| 3 | `H10` (SEC đình chỉ), `H4`, `H9`, `H11`, `T12`, `D`, `MWC3` | **Chặn hẳn alert.** Log `CHAN <sym>` một lần |
| 2 | `T1` (chờ tin), `T6`, `R1`/`R4`/`R9`, mã lạ | Vẫn gửi alert, có dòng HALT ở trên cùng |
| 1 | `LUDP`/`LUDS` (biến động), `T2` (tin đã ra), `M` | Vẫn gửi, dòng HALT mang tính thông tin |

Điểm quan trọng nhất khi đọc feed: **`ResumptionTradeTime` trống = chưa mở
lại.** Đó là trường quyết định `active`.

Ba lựa chọn thiết kế cần biết:

- **Feed cũ hơn 5 phút (`STALE`) → `view()` trả `None`.** Mất mạng thì bot nói
  "không biết", chứ không in một dòng halt đã hết hiệu lực.
- **Halt cũ hơn 12 giờ (`MAX_AGE_H`) không còn tính là đang halt** — feed giữ
  lại bản ghi cũ, và một mã "đang halt từ 3 ngày trước" gần như luôn là bản ghi
  chưa được cập nhật resume.
- **Lỗi mạng không xoá dữ liệu cũ.** `refresh()` trả `-1` và giữ nguyên
  `by_sym`: biết trạng thái của 2 phút trước vẫn tốt hơn không biết gì.

Mục "NGUY CƠ HALT (LULD)" trong khối RỦI RO **đã bị xoá** — nó đoán từ
`chg`/`rvol`, mà giờ đã có feed thật; phần "biến động mạnh" thì mục
`BIẾN ĐỘNG CỰC MẠNH` ngay trên nó đã nói rồi.

**Tiêu chí xong:** trong một phiên có mã bị `LUDP`, alert của mã đó phải hiện
dòng halt. Kiểm nhanh giữa phiên Mỹ:

```bash
python halts.py --live        # nếu cột [DANG HALT] có mã nào, feed đang hoạt động
```

Không có mã nào đang halt là **bình thường** ngoài giờ và cả trong những phiên
yên tĩnh — feed chỉ có bản ghi khi thực sự có halt.

### Tin tức catalyst — `news.py`

Alert cũ nói **"RVOL 66×, +85%"** nhưng không nói **vì sao**. Mà cái "vì sao" mới
quyết định có nên quan tâm: một mã +85% vì phê duyệt FDA và một mã +85% vì vừa
công bố chào bán cổ phiếu là hai chuyện trái ngược nhau.

Nguồn: `https://data.alpaca.markets/v1beta1/news` — dùng lại `ALPACA_KEY` /
`ALPACA_SECRET` đã có, không cần key mới. `loop_news` gọi mỗi 30 giây, giữ một
**sổ tay cuộn 4 giờ trong RAM** (`{mã: [(ts, tiêu đề, url), …]}`), không ghi DB.

**Bảng từ khoá** — nhóm nào khớp trước thì thắng, nên **thứ tự dòng là một phần
của logic**:

| Nhãn hiện trong alert | `risk` | Ví dụ từ khoá |
|---|---|---|
| PHÁ SẢN / MẤT THANH KHOẢN | 3.0 | `chapter 11`, `chapter 7`, `receivership` |
| NGUY CƠ HỦY NIÊM YẾT | 3.0 | `deficiency letter`, `delisting`, `minimum bid price` |
| **PHA LOÃNG — TIN VỪA RA** | **3.0** | `pricing of`, `public offering`, `registered direct`, `private placement`, `at-the-market`, `atm program`, `convertible note`, `warrant inducement`, `s-3` |
| GỘP CỔ PHIẾU (REVERSE SPLIT) | 2.0 | `reverse stock split`, `1-for-` |
| DỮ LIỆU / PHÊ DUYỆT | 0 | `fda`, `phase 3`, `topline`, `clearance` |
| HỢP ĐỒNG / THƯƠNG VỤ | 0 | `contract`, `awarded`, `definitive agreement` |
| KẾT QUẢ KINH DOANH | 0 | `record revenue`, `third quarter`, `guidance` |

**Nhóm PHA LOÃNG là lý do module này tồn tại.** Cái bẫy kinh điển là +80% kèm
tiêu đề *"Announces Pricing of $15M Registered Direct Offering"*: `edgar.py` chỉ
bắt được **sau khi** 424B5 về tới EDGAR, còn bản tin ra **sớm hơn nhiều**.

Bốn lựa chọn thiết kế cần biết:

- **Tin xấu thắng tin mới.** `view()` trả về bản ghi có `risk` cao nhất, không
  phải bản ghi mới nhất. Một mã ra *"Pricing of Offering"* lúc 9:40 rồi
  *"Record Revenue"* lúc 10:05 vẫn phải hiện dòng pha loãng.
- **Nhóm xấu xếp trước nhóm tốt trong `GROUPS`.** Tiêu đề *"Announces FDA
  Clearance And Pricing Of $20M Offering"* là có thật, và nếu xét nhóm tốt trước
  thì nó được gắn nhãn tích cực — đúng cái bẫy cần chặn.
- **Nhóm tốt `risk = 0`: không cộng điểm.** Chưa có số liệu nào chứng minh
  "có tin FDA" thì alert đúng hơn. Chờ Phase 1 trả lời bằng cột `news_group`
  trong `state/events.jsonl`.
- **Bài gắn > 4 mã bị bỏ** (`MAX_SYMS`). Đó là *"10 Stocks Moving In Monday's
  Pre-Market Session"*, không phải catalyst của riêng mã nào.

Và một giao ước ba trạng thái, giống `halts.py`:

| `view()` trả về | Nghĩa | Alert hiện gì |
|---|---|---|
| `None` | **không biết** — chưa có key, hoặc feed chết > 3 phút | không có khối CATALYST |
| `{"n": 0, …}` | feed sống và **thật sự không có tin** | "Không thấy tin nào trong 4 giờ qua — chạy không rõ lý do" |
| có bản ghi | tin xấu nhất trong 4 giờ | tiêu đề (link) + nguồn · bao lâu trước; nhãn nhóm chỉ khi RỦI RO không nói (xem dưới) |

Một điểm dễ sai nếu sau này sửa `main.py`: tin pha loãng và filing 424B5 là
**cùng một sự kiện**, nên `SEC_PENALTY` bị trừ **đúng một lần** qua
`max(sec_risk, news_risk)`. Có một test khoá đúng điều này lại.

**Cảnh báo từ tin nằm ở khối RỦI RO, không ở CATALYST.** Đây là chỗ dễ mất
cảnh báo nhất nên nói rõ:

- `AlertView.dilution_risk = max(sec_risk, news_risk nếu nhóm là PHA LOÃNG)`.
  Cả `level` và `render_risk()` đọc thuộc tính này, nên tin *"Pricing of
  Offering"* tự nó đủ để lên mức 3 và tự nó đủ để in "PHA LOÃNG — CAO", kể cả
  khi 424B5 chưa về tới EDGAR (`sec_risk = 0`).
- Chỉ nhóm `DILUTION` được cộng vào đó. `news_risk` cũng là `3.0` cho
  `BANKRUPT` và `DELIST`, mà gọi phá sản là "pha loãng" thì sai hẳn — mỗi nhóm
  trong `render.RISK_GROUPS` có mục riêng trong RỦI RO, lấy nguyên
  `label` + `note` từ `news.py`.
- `RISK_GROUPS` có cả `DILUTION`, nhưng `render_risk()` chặn trùng ngay tại chỗ
  thêm mục: `dup = news_group == "DILUTION" and dilution_risk >= SEC_MID`.
  Nghĩa là **không bao giờ** có hai mục pha loãng cùng chiếm slot của
  `max_items`. Vì `dilution_risk` đã gộp `news_risk` sẵn, nhánh này chỉ mở khi
  bản tin có `risk` dưới `SEC_MID` — `news.py` hiện luôn gán `3.0` cho nhóm
  `DILUTION`, nên đó là lớp chặn phòng xa, không phải đường chạy thường ngày.
- Vì vậy CATALYST **không in lại** nhãn của nhóm xấu: RỦI RO là khối *kết
  luận*, CATALYST là khối *bằng chứng* (tiêu đề tin + nguồn · tuổi). Nhóm tốt
  không xuất hiện ở RỦI RO nên vẫn giữ nhãn ở CATALYST.
- Ưu tiên cắt: `P_RISK = 7 > P_NEWS = 6`. Tin dài thì mất tiêu đề tin trước,
  nhưng kết luận pha loãng vẫn còn.

**Hồ sơ SEC cũng có giao ước ba trạng thái** (`edgar.scan()` trả
`(danh_sách, status)`), vì trước đây cả ba lý do "không có hồ sơ" đều in
"thiếu CIK" — vu oan một mã sạch:

| `status` | Nghĩa | Khối HỒ SƠ SEC in |
|---|---|---|
| `ok` + danh sách rỗng | tra được, mã **không phát hành gì** trong 120 ngày | "Không có hồ sơ nào trong 120 ngày qua" |
| `no_cik` | chưa có CIK trong bảng `base` → không tra được | "Không tra được hồ sơ (thiếu CIK)" |
| `error` | có CIK nhưng EDGAR lỗi / rate-limit → **không biết** | "Chưa tra được EDGAR lúc này" |

`assess()` chuyển tiếp `status` và một câu `note` tương ứng; `filings()` vẫn
còn để tương thích, nó chỉ bỏ `status` đi.

**Tiêu chí xong:** ≥70% alert có ít nhất một dòng catalyst hoặc nhãn "không rõ
lý do" rõ ràng. Kiểm nhanh:

```bash
python news.py                # bảng từ khoá + sổ tay, không cần mạng
python news.py --live         # gọi thật: in tin 4 giờ gần nhất theo mã
```

`--live` trả `403` nghĩa là gói Alpaca miễn phí của bạn không có quyền đọc news
— lúc đó `view()` luôn trả `None` và bot chạy y như trước, chỉ mất khối
CATALYST.

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
| `INTERNALERROR> SystemExit` + `no tests ran` | Có file `test_*.py` **ngoài** `tests/` — pytest import nó lúc collect và code cấp module chạy thật | Đổi tên thành `check_*.py`; `pytest.ini` đã chặn bằng `testpaths = tests` |
| CI đỏ ở `test_ensure_cols_chay_lai_khong_lam_gi`: `ensure_cols()` trả về `['mktcap','mktcap_ts']` | Cột được thêm vào `prep.ADD_COLS` mà **quên `prep.DDL`** → một DB dựng từ đầu vẫn thiếu cột, nên lần chạy đầu của mọi máy mới đều là một lần migrate | Khai báo cột ở **cả hai** chỗ, cùng kiểu. `test_moi_cot_trong_ADD_COLS_deu_da_co_trong_DDL` giữ hai bản không lệch nữa |

**Lưu ý:** script thử Telegram tên là `scripts/check_tg.py`, **không** phải
`test_tg.py`. Tên cũ trùng mẫu tên của pytest nên nó bị import lúc collect,
`asyncio.run()` ở cấp module chạy ngay, `SystemExit("Thieu TG_TOKEN")` làm cả
job `pytest` chết với `INTERNALERROR` trước khi một test nào kịp chạy.

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
OUTCOME_SEC = 60        # đo kết quả alert: điền px15/px60/đỉnh/đáy
HALT_SEC = halts.POLL   # 60 — ĐỪNG giảm, Nasdaq chỉ cho 1 query/phút
NEWS_SEC = news.POLL    # 30 — nhịp lấy tin
SEC_PENALTY = 2.0       # trừ MỘT LẦN, dù cả SEC và tin cùng báo pha loãng
```

**`outcome.py`** — đo chất lượng
```python
FILL_TOL = 240          # giây, cửa sổ cho phép khi điền px15/px60
BUCKETS = ((7,8), (8,9), (9,10), (10,12), (12, ...))   # nhóm điểm trong báo cáo
```

**`halts.py`** — tạm dừng giao dịch
```python
POLL = 60               # giới hạn của Nasdaq
STALE = 300             # feed cũ hơn thế này → coi như không biết gì
MAX_AGE_H = 12          # halt cũ hơn thế này không còn tính là đang halt
JUST_RESUMED = 300      # vừa mở lại trong 5 phút → vẫn hiện dòng halt
BLOCK_SEV = 3           # sev ≥ ngưỡng này → không gửi alert
REASONS = {...}         # mã lý do → (nhãn, sev, giải thích). Thêm mã mới ở đây
```

**`news.py`** — tin tức catalyst
```python
POLL = 30               # nhịp gọi API
WINDOW_H = 4            # sổ tay chỉ giữ tin trong 4 giờ gần nhất
STALE = 180             # không lấy được tin lâu hơn thế → view() trả None
MAX_SYMS = 4            # bài gắn nhiều mã hơn = bài tổng hợp thị trường, bỏ
MAX_PER_SYM = 6         # trần số tin mỗi mã, chặn RAM phình
NEWS_RISK_MAX = 3.0     # từ mức này main.py mới trừ điểm
GROUPS = (...)          # bảng từ khoá. Thêm từ mới ở đây
```

Chỗ đáng chỉnh nhất là `GROUPS`. Ba điều phải giữ khi thêm từ khoá:

1. **Nhóm `risk > 0` phải nằm trước mọi nhóm `risk = 0`** — có test khoá lại.
2. **Dùng cụm từ, đừng dùng từ đơn.** `offering` một mình khớp cả *"Now
   Offering Free Shipping"*; `pricing of` / `public offering` thì không.
3. Từ ngắn (`s-3`, `atm`) được tự thêm ranh giới từ, nên `ATMosphere` không bị
   gắn nhãn oan. Đừng bỏ cơ chế đó.

Cách tìm từ khoá còn thiếu: `grep news_group state/events.jsonl` xem có bao
nhiêu dòng `null` trên những mã tăng mạnh.

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
SCORE_MAX, BAR_CELLS = 15.0, 10        # thang của thanh điểm
W_IND, W_LAB, W_VAL, W_DLT = 2, 11, 8, 6   # 4 cột trong panel <pre>
SAFE_LEN = 3800           # vượt ngưỡng này thì bỏ bớt khối
NEWS_HEAD_MAX = 170       # cắt tiêu đề tin dài hơn thế
RISK_GROUPS = {"BANKRUPT": 3, "DELIST": 3, "SPLIT": 2,   # nhóm tin → mục RỦI RO
               "DILUTION": 3}                           # chặn trùng bằng `dup`
EXPANDABLE = True         # <blockquote expandable>, cần Bot API >= 7.3
ASK_MAX = 700             # độ dài prompt của nút Hỏi ChatGPT
```

`SCORE_MAX` **phải lớn hơn** `T_EXTREME`. Khi hai số bằng nhau (cả hai từng là
`12.0`) thì mọi alert mức 3 đều hiện thanh đầy và tử số vượt mẫu số —
`12.4/12`, và `12.4` với `14.0` vẽ giống nhau. Trần lý thuyết của `scorer.py`
là `2.2*2.0 + 1.6*3 + 1.4*3 + 0.5*1.5 + 1.5 = 15.65`, nên `15.0` là mẫu số
hợp lý. `outcome.BUCKETS` vẫn chia nhóm theo `12.0` — đó là `T_EXTREME`
(ngưỡng mức 3), không phải mẫu số của thanh điểm.

Chữ hiển thị nằm gọn trong dict `TXT` ở đầu `render.py` — sửa tên nhãn, tên
mức, câu cảnh báo ở đó, không phải lần trong code. Thứ tự bỏ khối khi tin quá
dài do các hằng `P_HALT ... P_WHY` quyết định (số càng cao càng được giữ lại) —
bảng đầy đủ ở mục 6.

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
trùng nghĩa với đèn mức độ ở header. Dòng HALT cũng **không** có emoji, dù nó
là thứ nghiêm trọng nhất trong tin: chữ IN HOA đậm ở dòng đầu tiên đã đủ nặng,
và thêm emoji thứ ba thì cái neo ở header mất tác dụng.

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
- **Feed halt chỉ có mã Nasdaq/NYSE báo về Nasdaq Trader**, và trễ vài chục
  giây so với lúc halt thật sự xảy ra. Không có dòng halt **không** đảm bảo mã
  đang giao dịch bình thường.
- **Không phải lời khuyên đầu tư.** Đây là dữ liệu thô chưa kiểm chứng —
  chính footer mỗi alert cũng nói vậy.

---

## 11. Roadmap — các bước tiếp theo

Sắp theo thứ tự **giá trị / công sức**, không phải theo độ thú vị. Mỗi mục có
tiêu chí "xong" rõ ràng để bạn biết khi nào dừng.

Nguyên tắc xuyên suốt: **bot này là công cụ phát hiện, không phải công cụ giao
dịch.** Mọi thứ dưới đây đều nhằm làm alert *đáng tin hơn* hoặc *ít rác hơn*,
không nhằm tự động đặt lệnh.

Phase 1–8 làm engine spike sạch hơn. **Phase 9 thì khác loại**: nó thêm hai
setup mới (nền tích luỹ và bật đáy) — tức là đổi *việc bot đi tìm cái gì*, chứ
không chỉnh cái đang có. Nếu bạn chỉ đọc một phase, đọc Phase 9.

---

### PHASE 1 — Đo chất lượng alert ✅ ĐÃ XONG

Đã cài: `outcome.py`, bảng `outcome`, task `loop_outcome` trong `main.py`, và
`scripts/report_quality.py`. **Hướng dẫn đọc bảng ở mục 8** ("Đo chất lượng
alert").

Ba điểm khác với thiết kế ban đầu, đều theo hướng làm số đáng tin hơn:

- Ngoài việc điền từ `st.universe`, có thêm `outcome.backfill()` đọc **nến 1
  phút của yfinance** để chốt lại `px15`/`px60`/`px_close`/đỉnh/đáy chính xác —
  kể cả những mã đã rời khỏi screener. Chạy tự động lúc sang ngày mới, không
  cần thêm dòng cron nào.
- Báo cáo có thêm cột **`cov`** (bao nhiêu % dòng đã có số) và `medCls`, vì đọc
  `win%` khi dữ liệu còn thiếu là tự lừa mình — phần thiếu chính là mã đã nguội.
- Bảng lưu thêm `rvol` và `src`, để sau này so được "RVOL cao có tốt hơn không"
  mà không phải join sang bảng `alerts`.

**Việc còn lại của phase này không phải code, mà là chờ:** để bot chạy 3–4
tuần, rồi đọc `python scripts/report_quality.py`. Nếu nhóm `7.0-8.0` có `win15`
≈ 50% thì nâng `ALERT_SCORE` lên 8.0.

---

### PHASE 2 — Feed trading halt ✅ ĐÃ XONG

Đã cài: `halts.py`, task `loop_halts` (60 giây), khối HALT trong `render.py`,
và chặn alert với các mã lý do nghiêm trọng. Chi tiết cách hoạt động và cách
kiểm tra: **mục 8 → "Tạm dừng giao dịch"**.

Bốn chỗ khác với bản thiết kế ban đầu ở trên, đều có lý do:

- **Dòng halt không dùng emoji.** Bản thiết kế đề nghị `⏸️` và `🔄`, nhưng quy
  ước "cả tin chỉ 2 emoji" (mục 9) quan trọng hơn: emoji thứ ba làm đèn màu ở
  header mất tác dụng neo mắt. Dòng halt là chữ IN HOA đậm ở dòng đầu tiên —
  đã là thứ nặng nhất trong tin.
- **Chặn theo `sev`, không chặn riêng `H10`.** `T12`, `H4`, `H9`, `H11`, `D`
  cũng tệ ngang `H10`; gán mỗi mã lý do một mức `sev` 0–3 rồi chặn từ `sev 3`
  thì thêm mã lý do mới về sau chỉ là thêm một dòng trong bảng `REASONS`.
- **Không cộng điểm cho mã vừa resume.** Đó là thay đổi thang điểm mà chưa có
  số liệu nào chứng minh; Phase 1 tồn tại chính để tránh loại chỉnh sửa theo
  cảm giác đó. Hiện chỉ hiện nhãn "VỪA MỞ LẠI GIAO DỊCH".
- **Đã xoá mục "NGUY CƠ HALT (LULD)"** đoán từ `chg`/`rvol` trong khối RỦI RO —
  giờ đã có feed thật, giữ cả hai chỉ là nói hai lần.

**Việc còn lại:** đúng một lần kiểm tra thực tế — trong phiên Mỹ có mã bị
`LUDP`, xem alert của mã đó có dòng halt không.

---

### PHASE 3 — Catalyst: mã này chạy *vì cái gì* ✅ ĐÃ XONG

Đã cài: `news.py`, khối CATALYST trong `render.py`, task `loop_news` trong
`main.py`, hai cột `news_group`/`news_risk` trong `state/events.jsonl`.
**Bảng từ khoá và giao ước ba trạng thái ở mục 8** ("Tin tức catalyst").

Sáu điểm khác với thiết kế ban đầu:

- **Dùng REST (`GET /v1beta1/news`) thay vì websocket.** Websocket cần một máy
  trạng thái tự kết nối lại và tự phát hiện "chết im lặng" — mà tin tức là
  chuyện tính bằng phút, còn vòng quét đã là 25 giây. Đổi lại: hàm `parse()`
  thuần, test được offline, không cần mạng. Lấy tối đa 4 trang mỗi vòng với
  90 giây chồng lấn, nên lúc 9:30 tin dày cũng không rơi.
- **Không dùng emoji.** Quy ước `render.py` là tối đa 2 emoji mỗi tin nhắn
  (mục 9), nên icon 🧬/💧/⛔ được thay bằng nhãn IN HOA tiếng Việt:
  `PHA LOÃNG — TIN VỪA RA`, `DỮ LIỆU / PHÊ DUYỆT`… Nhãn ⚪ thành một dòng chữ
  đầy đủ: *"Không thấy tin nào trong 4 giờ qua — chạy không rõ lý do"*.
- **Nhóm xấu xét trước nhóm tốt.** Tiêu đề gộp cả FDA và chào bán là có thật,
  và xét nhóm tốt trước thì đúng cái bẫy này lọt lưới.
- **Chỉ trừ điểm một lần.** Tin *"Pricing of Offering"* và filing `424B5` là
  cùng một sự kiện, nên `main.py` lấy `max(sec_risk, news_risk)` rồi trừ
  `SEC_PENALTY` đúng một lần, thay vì trừ hai lần cho cùng một chuyện.
- **Nhóm tin tốt không cộng điểm** (`risk = 0`). Chưa có số liệu nào chứng minh
  "có tin FDA" thì alert đúng hơn — để Phase 1 trả lời bằng cột `news_group`,
  chứ không đoán trước.
- **Bỏ bài gắn > 4 mã** (`MAX_SYMS`) và **tin xấu thắng tin mới**: hai chi tiết
  không có trong bản thiết kế nhưng thiếu chúng thì nhãn sai thường xuyên.
- **Kết luận chuyển sang khối RỦI RO.** Bản đầu để nhãn + câu giải thích trong
  CATALYST, nhưng `level` và `render_risk()` chỉ đọc `sec_risk`, nên một tin
  chào bán ra trước 424B5 chỉ được cảnh báo bên trong CATALYST — mà CATALYST
  bị bỏ **trước** RỦI RO khi tin dài. Giờ có `AlertView.dilution_risk` và
  `render.RISK_GROUPS`; CATALYST chỉ còn tiêu đề tin. Chi tiết ở mục 8 →
  "Cảnh báo từ tin nằm ở khối RỦI RO".

**Tiêu chí xong** (≥70% alert có dòng catalyst hoặc nhãn "không rõ lý do") đạt
được về mặt cấu trúc: hễ feed còn sống thì mọi alert đều có một trong ba thứ —
tiêu đề đã phân loại, tiêu đề chưa phân loại, hoặc dòng "không thấy tin nào".

**Việc còn lại:** một lần kiểm thật trên VM giữa phiên Mỹ.

```bash
python news.py --live         # có dòng nào → feed hoạt động (403 = gói free không có news)
```

Rồi xem một alert thật có khối CATALYST không. Sau đó là việc dài hạn: nuôi
`GROUPS` dần bằng cách xem `news_group` nào còn `null` trong `events.jsonl`.

---

### PHASE 4 — Dọn nợ kỹ thuật ✅ ĐÃ XONG

Không thêm tính năng, chỉ để những phase sau đỡ đau.

- **4a. Gộp `notifier.py` vào `tgapi.py`.** `tgapi.py` giờ là đường gửi duy
  nhất; `notifier.py` chỉ còn `class Spool`. Cách hoạt động: **mục 7 → "Đường
  gửi Telegram"**.
- **4b. Test.** `tests/` — 12 file, 267 test, chạy được cả bằng `pytest -q` và
  bằng `python tests/test_x.py` trên máy thiếu thư viện. Cách chạy: **mục 8 →
  "Test"**.
- **4c. CI.** `.github/workflows/ci.yml` — hai job: `compile` (không cài gì) và
  `test` (`pytest -q`).
- **4d. Log có cấu trúc.** `events.py` → `state/events.jsonl`. Định dạng và cách
  ghép với bảng `outcome`: **mục 8 → "Log có cấu trúc"**.

Năm chỗ khác với bản thiết kế ở trên, đều có lý do:

- **Trần `PER_MIN` chỉ áp cho `sendMessage`.** Bản `notifier` cũ throttle mọi
  thứ. Nếu áp cả cho `answerCallbackQuery` thì người bấm nút có thể phải chờ hết
  60 giây mới thấy phản hồi — mà nút chỉ còn hiệu lực vài giây.
- **`_call` trả về bốn loại giá trị**, và `send()` phải kiểm `res is True` trước
  mọi phép kiểm số: `isinstance(True, int)` là `True`. Vì vậy chỉ báo lỗi 400 là
  **chuỗi** `BAD_REQ`, không phải số — nếu là số thì tin lỗi format sẽ bị nhầm
  thành `message_id` và coi như đã gửi. Hạ cấp HTML chỉ chạy khi lỗi *nội dung*;
  mất mạng thì dừng ngay vì bỏ tag không cứu được mạng.
- **`esc()` chỉ còn một bản, trong `render.py`.** Trước có hai bản giống nhau ở
  `render.py` và `notifier.py` — đúng loại trùng lặp sẽ lệch nhau.
- **`test_clock.py` không hardcode 09:30/16:00**, mà lấy `open_et`/`close_et` từ
  chính clock rồi kiểm máy trạng thái quanh đó. DST hay giờ giao dịch đổi thì
  test vẫn đúng thay vì đỏ giả. Test nửa phiên tự bỏ qua nếu
  `pandas_market_calendars` không đồng ý về ngày đó.
- **Một số test khoá cả *giao ước giữa các file*, không chỉ hành vi một hàm**:
  `test_scorer` khoá bộ trọng số (đổi trọng số phải là hành động có ý thức, vì
  nó làm số liệu Phase 1 đang tích luỹ mất khả năng so sánh);
  `test_events` khoá khoá join `(sym, int(ts))` với bảng `outcome`;
  `test_notifier`/`test_tgapi` khoá việc `notifier.py` không được gọi mạng nữa.

**Việc còn lại:** trên VM, `pytest -q` một lần với đủ thư viện — `test_clock.py`
và các test cần pandas/tzdata chỉ thật sự chạy ở đó.

---

### PHASE 5 — Bớt rác, bớt trùng

Khi bot đã chạy vài tuần bạn sẽ gặp ba kiểu rác. Chờ đến lúc *thật sự* gặp
mới sửa, đừng làm sớm.

**5a. Alert theo chùm (cluster).** Khi cả nhóm quantum/uranium/nuclear chạy
cùng lúc, bạn nhận 8 alert gần như giống nhau. Giải pháp: nếu ≥3 mã cùng
sector vượt ngưỡng trong 10 phút, gửi **một** tin gộp:

```
🌊 NHOM DANG CHAY · Uranium (4 ma)
   UUUU  +22%  8.1 diem
   UEC   +19%  7.6
   DNN   +17%  7.2
   NXE   +14%  7.0
```

Cần cột `sector` trong bảng `base` — `prep.py` lấy được từ Yahoo cùng lúc với
`prev_close`, gần như miễn phí.

**5b. Cooldown thích ứng.** `COOLDOWN = 540` cố định cho mọi mã. Nên: mã đã
alert 3 lần trong ngày → nhân đôi cooldown; mã điểm tăng ≥2.0 so với lần
trước → cho phép gửi sớm (đó là leo thang thật, đáng biết).

**5c. Nút "Bỏ qua mã này hôm nay".** Nút inline `mute|SYM` ghi vào bảng
`watch` với `kind='mute'`, `main.py` bỏ qua mã đó tới hết phiên. Đây là tính
năng **rẻ nhất** để giảm rác, vì nó dùng ngay phán đoán của bạn thay vì cố
làm scorer thông minh hơn.

---

### PHASE 6 — Lệnh chat và bảng tổng kết

Hiện tại bot chỉ nói một chiều. Thêm vài lệnh trong `callbacks.py` (bạn đã có
vòng `getUpdates` rồi, chỉ cần nhận thêm `message` ngoài `callback_query`):

| Lệnh | Việc |
|---|---|
| `/top` | 10 mã điểm cao nhất *ngay lúc này*, kể cả dưới ngưỡng |
| `/s WETO` | render alert cho một mã bất kỳ theo yêu cầu |
| `/wl` | danh sách watchlist (`store.watch_list`) kèm điểm hiện tại |
| `/stats` | bảng chất lượng của Phase 1, gửi thẳng vào chat |
| `/mute WETO` | bỏ qua mã tới hết phiên |
| `/health` | uptime, tuổi baseline, số alert hôm nay, lỗi API gần nhất |

`/health` đáng làm sớm: nó là cách nhanh nhất để biết bot còn sống mà không
cần SSH — trả lời trực tiếp cho lo ngại "Oracle thu hồi instance nhàn rỗi" ở
mục 5.12.

**Tổng kết cuối phiên** (22:05 giờ Đức, sau khi đóng cửa):

```
📊 TONG KET 04/09
   38 alert · 12 ma · diem cao nhat WETO 9.4
   Top theo dong tien: WETO $311M · CHPT $180M
   Sau 60p:  8 tang / 4 giam  (trung vi +1.9%)
   Watchlist:  UUUU +4.2%  ·  SMR -1.1%
```

Dữ liệu cho phần "sau 60p" đến từ bảng `outcome` của Phase 1 — thêm một lý do
làm Phase 1 trước.

---

### PHASE 7 — Chất lượng dữ liệu nền

Việc âm thầm nhưng ảnh hưởng tới *mọi* điểm số.

**7a. Float chính xác hơn.** `float_sh` từ Yahoo thường cũ hoặc sai với mã
micro-cap vừa phát hành thêm — mà `float_rot` là một trong những tín hiệu
mạnh nhất của bạn. Nguồn tốt hơn: `data.sec.gov` companyfacts
(`dei:EntityCommonStockSharesOutstanding`) — chính xác, miễn phí, và bạn **đã
có `SEC_UA`** hợp lệ để gọi. Đây là "shares outstanding" chứ không phải
"float", nhưng nó *mới* và đủ để phát hiện khi Yahoo lệch nghiêm trọng. Cách
dùng an toàn nhất: dùng nó để **gắn cờ số liệu đáng ngờ**, không phải để thay
thế mù quáng.

```
⚠️ float Yahoo 8.4M nhung SEC bao 31.2M shares (11/08) — float_rot co the sai
```

**7b. Short interest.** FINRA công bố 2 lần/tháng, miễn phí. Short interest
cao + float nhỏ + RVOL cao là tổ hợp squeeze kinh điển, và bạn đang bỏ qua
chiều này hoàn toàn.

**7c. Cảnh báo baseline cũ.** Nếu `meta.built` không phải hôm nay, **ghi rõ
trong mỗi alert**, đừng chỉ ghi log:

```
⚠️ baseline tu 02/09 (2 ngay truoc) — prev_close va adv20 co the lech
```

Vì `atr_move` và `chg%` đều dựa trên baseline, một `prep.py` chết âm thầm sẽ
làm toàn bộ điểm số sai mà tin nhắn trông vẫn hoàn toàn bình thường. **Đây là
kiểu lỗi nguy hiểm nhất trong cả hệ thống** và nó rẻ để phòng.

---

### PHASE 8 — Chỉ khi bạn muốn dùng lâu dài

Đừng chạm vào cho đến khi Phase 1–3 đã chạy ổn vài tháng.

**8a. Chấm điểm bằng dữ liệu.** Sau ~500 alert có kết quả trong bảng
`outcome`, bạn có thể fit một logistic regression đơn giản (chỉ cần
`scikit-learn`, chạy dư sức trên VM 6 GB) để tìm trọng số *thực nghiệm* thay
vì tay. Giữ scorer thủ công song song và so sánh — nếu model không thắng rõ
ràng thì giữ cái thủ công, vì nó giải thích được.

⚠️ Cạm bẫy: 500 mẫu là **rất ít**, và tất cả đều từ một chế độ thị trường.
Model fit trên đó sẽ overfit và sẽ thất bại khi thị trường đổi tính cách. Nếu
làm, hãy chia train/test theo **thời gian** (không random), và coi kết quả là
gợi ý chứ không phải chân lý.

**8b. Backtest ngoại tuyến.** Lưu snapshot universe mỗi 5 phút vào parquet
(~50 MB/tháng) để có thể chạy lại scorer với trọng số mới trên dữ liệu cũ mà
không phải chờ tuần này qua tuần khác. Đây là thứ biến vòng lặp tinh chỉnh từ
"vài tuần" thành "vài phút".

**8c. EDGAR real-time.** Hiện `edgar.py` chỉ tra khi có alert. Có thể poll
`https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom`
để bắt filing mới trong vòng 1 phút, hoặc dùng full-text search
`efts.sec.gov` để tìm từ khoá. Chỉ đáng làm nếu bạn thấy mình thường xuyên
biết tin muộn.

**8d. Nhiều người dùng.** Bảng `subscriber(chat_id, min_score, sectors, muted)`,
gửi theo ngưỡng riêng từng người. Chỉ làm nếu có người thật muốn dùng — nó
kéo theo rate limit Telegram (30 tin/giây toàn bot), quyền riêng tư, và trách
nhiệm mà một dự án cá nhân không cần.

---

### PHASE 9 — Hai setup mới: nền tích luỹ và bật đáy

Đây là phase **đổi hướng**, không phải chỉnh tinh. Ba phase trước làm alert
sạch hơn; phase này thay đổi *bot đi tìm cái gì*.

#### 9.a Vì sao engine hiện tại không thể thấy nền tích luỹ

Một mã vừa breakout khỏi nền 8 tuần thường +2~3% với RVOL khoảng 2×. Nó bị loại
ở **cả ba tầng**, và tầng thứ nhất là tầng chết:

| Tầng | Ngưỡng hiện tại | Breakout thật |
|---|---|---|
| `universe_live.py` | Alpaca movers + Yahoo `%chg > 4` | +2.5% → **không bao giờ xuất hiện trong danh sách** |
| `scorer.rank` | `chg ≥ 5%` | +2.5% → loại |
| `scorer.rank` | `rvol ≥ 3.0` | 2.0× → loại |

Tầng đầu là điểm quan trọng nhất: nới ngưỡng trong `scorer.py` cũng vô ích, vì
mã đó **không có trong universe** để mà chấm. Phải đảo chiều luồng dữ liệu:
danh sách theo dõi sinh ra từ tối hôm trước, phiên hôm sau chỉ canh mức pivot
của đúng những mã đó.

Và một mã rơi 70% từ đỉnh thì cần *lịch sử*, không cần realtime. Cả hai setup
mới đều là setup **theo ngày**, nên chúng có một tính chất mà setup spike không
có: **backtest được**.

#### 9.b Ba quyết định đã chốt

1. **Giữ engine spike song song**, gắn nhãn `setup` (SPIKE/BO/RV) vào bảng
   `alerts` và `outcome`. Sau vài tuần bảng `outcome` sẽ trả lời "spike có đáng
   giữ không" bằng số. Xoá bây giờ là xoá cả chuỗi dữ liệu đang tích.
2. **Nhịp**: candidates 3 phút/vòng trong phiên + một lần quét chốt ~15:40 ET
   (`mso ≈ 370`) + báo cáo sau đóng cửa. Vòng `loop_score` 25 giây giữ nguyên
   cho spike.
3. **Ngưỡng riêng từng setup** — `scorer.py` chuyển từ hằng số cấp module sang
   3 profile:

| | SPIKE (giữ nguyên) | BO | RV |
|---|---|---|---|
| Giá tối thiểu | $1 | $1.5 | $3 |
| adv20 | 200k | 200k | 500k |
| %chg kích hoạt | ≥ 5% | ≥ 1.5% trên pivot | ≥ 7% |
| RVOL | ≥ 3.0 | ≥ 1.8 | ≥ 3.0 |
| Số liệu cơ bản | không cần | không cần | **bắt buộc đủ** |

#### 9.0 Kho nến ngày — `bars.py` ✅ ĐÃ XONG

`prep.py` tải 4 tháng nến ngày cho ~5000 mã rồi **ném đi**, chỉ giữ 3 con số.
Không thể đo "nền 6 tuần chặt dần" bằng 3 con số đó.

Bảng `bars(sym, d, o, h, l, c, ac, v)`, giữ ~3 năm. Chi phí ~70–90 MB, và cập
nhật hằng ngày chỉ tải 1 tháng gần nhất nên **nhanh hơn** `prep.py` hiện tại.

Hai quyết định trong đó đáng nêu:

- **Lưu cả `c` (thô) và `ac` (adj close).** `load(adj=True)` back-adjust theo
  hệ số `ac/c` → một lần chia 1:10 không còn biến thành "rơi 90%" giả trong
  `structure.py`; volume chia ngược lại để tổng tiền không đổi. Ngược lại
  `prep.py` gọi `load(adj=False)` vì `prev_close` phải khớp quote live.
- **Tầng lưu trữ thuần stdlib** (chỉ `sqlite3`), chỉ `fetch()` mới cần
  yfinance. Nhờ vậy nó tự test được trên máy dev không có pandas — kể cả phần
  đọc DataFrame, bằng một DataFrame giả trong `tests/test_bars.py`.

`bars.partial_day()` bỏ nến của ngày đang chạy (trước 16:00 ET) — không thì
`adv20`/`atr14` của toàn DB tính trên một nến chưa chốt.

#### 9.1 Đo cấu trúc giá — `structure.py` ✅ ĐÃ XONG

Quan hệ với `setups.py` giống quan hệ `vprofile.py` ↔ `scorer.py`: file này
chỉ **đo**, không **phán xét**. Không có ngưỡng "thế nào là nền tốt" ở đây — nó
chỉ nói "nền dài 34 phiên, biên độ 12%, pivot 10.05, volume co ngót còn 0.7".
Tách thế vì khi backtest cho biết ngưỡng sai, ta muốn sửa **ngưỡng** chứ không
phải sửa lại cách đo.

Bảng `struct`, một dòng/mã, dựng lại mỗi tối:

| Nhóm | Cột |
|---|---|
| Mốc | `d` (ngày của nến cuối), `px`, `n_bars` |
| Xu hướng | `sma20/50/200`, `sma50_slope`, `hi52`, `lo52`, `off_high`, `up_from_low`, `ret63`, `rs_pct` |
| Nén biên độ | `atr14`, `atr_pct`, `atr_contract` (so với 50 phiên trước), `tight10`, `depth20` |
| Nền tích luỹ | `base_len`, `base_depth`, `base_slope`, `base_dryup`, `pivot`, `dist_pivot` |
| Thanh khoản | `adv20`, `adv50`, `dryup` |
| Nến hôm nay | `close_pos`, `gap`, `vol_ratio` |
| Cú rơi | `below20_streak`, `days_since_low` |

`dist_pivot` là **dấu ngược trực giác**: `> 0` = còn cách pivot bấy nhiêu %,
`<= 0` = đã ở trên pivot. Viết vậy để `ORDER BY dist_pivot` cho ra đúng thứ tự
"sắp vượt trước".

**Ba chỗ dễ sai mà `find_base()` phải xử lý** — cả ba đều do test bắt được, và
đều là loại lỗi sẽ không bao giờ tự lộ ra khi chạy thật:

1. **Không được lấy "đoạn dài nhất còn dưới 35%".** Biên độ không bao giờ giảm
   khi cửa sổ dài ra, nên cách đó luôn kéo về phía rộng: một đoạn phẳng 45
   phiên đi sau cú tăng chậm bị báo thành **nền 90 phiên** — nuốt cả đoạn tăng
   vào nền. Nền tích luỹ phải *phẳng*.
2. **Độ trôi phải đo theo biên độ, không phải bằng % cố định.** Với ngưỡng
   "trôi ≤ 15%", một đoạn trượt đều từ 30 xuống 26 trong 90 phiên vẫn được nhận
   là "nền 13%" — trong khi 20 phiên đầu và 20 phiên cuối của nó không còn giao
   nhau. Nền thật thì giá dao động qua lại: **biên độ >> độ trôi**. Điều kiện:
   `|trôi| ≤ max(0.5 × biên_độ, 2%)`.
3. **`pivot` phải tính từ nến TRƯỚC hôm nay.** Nếu không, cú breakout hôm nay
   tự nâng pivot của chính nó lên và điều kiện `px > pivot` **không bao giờ**
   đạt. `metrics()` gọi `find_base(bs[:-1])`.

Hai chỗ khác cùng loại:

- **`adv20` không tính nến hôm nay.** Gộp hôm nay vào mẫu số làm RVOL nhỏ lại
  đúng lúc nó cần to.
- **`dryup` so với `adv50` gần như vô dụng** khi nền dài và im lặng suốt: adv50
  cũng thấp theo, tỉ lệ tiến về 1. Nên `base_dryup` đo **co ngót ngay trong
  nền** (5 phiên cuối so với cả nền) — đó mới là dấu hiệu cạn trước khi bật. Hệ
  quả: `setups.py` không được coi `dryup` thấp là điều kiện *bắt buộc* của BO.

`structure.baseline()` là bản không-pandas của `prep.compute()`, nên
`prep.py --from-bars` dựng được bảng `base` từ kho nến **không gọi mạng lần
nào** — về sau chỉ còn một lượt tải yfinance mỗi tối thay vì hai.

```bash
python bars.py --sync --full         # lần đầu: 2 năm nến (chạy trong tmux)
python bars.py --sync                # hằng ngày: 1 tháng gần nhất
python bars.py --info NVDA           # kho có gì, 10 nến cuối của một mã
python structure.py --build          # dựng bảng struct + in list "sắp vượt pivot"
python structure.py --show NVDA
python prep.py --from-bars           # base từ kho nến, không gọi mạng
```

Cron sau khi Phase 9 chạy ổn (thay dòng `prep.py` cũ):

```cron
0 8 * * 1-5  cd /home/ubuntu/scanner && .venv/bin/python bars.py --sync >> state/prep.log 2>&1 && .venv/bin/python prep.py --from-bars >> state/prep.log 2>&1 && .venv/bin/python structure.py --build >> state/prep.log 2>&1 && .venv/bin/python setups.py --build >> state/prep.log 2>&1
```

⚠️ Đừng đổi cron trước khi đối chiếu: chạy `prep.py --from-bars` trên **bản sao
DB** rồi so `adv20`/`prev_close` với bảng `base` hiện tại. Lệch quá vài phần
trăm nghĩa là kho nến thiếu ngày, và mọi thứ dựa trên baseline sẽ sai *âm thầm*
— đúng kiểu lỗi mục 7c nói tới.

#### 9.2 Hai setup — `setups.py`, bảng `candidates` ✅ ĐÃ XONG

Tách làm **hai bước**, và đây là chỗ dễ làm sai nhất của cả phase:

| Bước | Khi nào | Đọc gì | Trả lời |
|---|---|---|---|
| `bo_candidate()` / `rv_candidate()` | buổi tối | bảng `struct` | "mã này **có nền** đáng theo dõi không" → bảng `candidates` + `pivot` |
| `trig_bo()` / `trig_rv()` | trong phiên | quote | "hôm nay nó **có kích hoạt** không" → alert |

Bước 1 không được dùng dữ liệu trong phiên, bước 2 không được tính lại nền.
Nhờ ranh giới đó, `backtest.py` (9.7) gọi **đúng hai hàm này** trên nến ngày quá
khứ mà không phải viết lại logic — nếu backtest và bot chạy hai đoạn code khác
nhau thì kết quả backtest không nói lên điều gì về bot.

Mọi ngưỡng nằm trong hai dict `BO` / `RV` ở đầu file, không rải rác trong hàm —
vì `backtest.py` sẽ sửa chính hai dict đó, và sửa một chỗ thì không có bản sao
nào bị bỏ sót. Cả hai hàm nhận tham số `g=BO` nên backtest quét được nhiều bộ
ngưỡng cùng lúc mà không cần đụng vào biến toàn cục.

**BO** — nền: `base_len ≥ 20` · `base_depth ≤ 20%` (≤ 35% cho mã dưới $10) ·
`atr_contract ≤ 0.75` · giá trên `sma50`, `sma50_slope ≥ 0` · `off_high ≤ 25%`.
Kích hoạt: `px > pivot × 1.005` · `rvol ≥ 1.8` · đóng ở nửa trên biên độ ngày ·
dollar-vol ≥ $2M.

**RV** — `off_high ≥ 50%` · `ret63 ≤ −10%` · `days_since_low ≤ 30` ·
`up_from_low ≤ 40%` · giá ≥ $3, adv20 ≥ 500k · điểm cơ bản (9.3) đạt.
Kích hoạt: +7% với `rvol ≥ 3`, `close_pos ≥ 0.75`, và **lấy lại `sma20`** sau
nhiều tuần ở dưới.

Bốn quyết định trong đó không hiển nhiên:

- **`dist_pivot` phải nằm trong `[−5%, +12%]`.** Cách pivot 30% thì hôm nay
  không thể vượt — lấy quote của nó là lãng phí; ở trên pivot quá 5% thì đã vào
  muộn. Đây cũng là cái giữ danh sách theo dõi đủ nhỏ để lấy quote theo lô.
- **BO không alert khi giá đã vượt pivot 15%.** Một mã +19% trên pivot giữa
  phiên là gap-and-go — việc của SPIKE. Để BO nhận nó thì entry đã xấu và
  `outcome` sẽ trộn hai loại setup vào một cột.
- **Gap > 8% là *cảnh báo*, không phải loại.** Nó vẫn là breakout thật, chỉ là
  entry xấu; ghi vào `warn` để panel nói ra, chứ bỏ đi thì mất luôn những cú
  breakout mạnh nhất.
- **RV thiếu số liệu cơ bản = không kích hoạt**, dù giá chạy bao nhiêu. Trạng
  thái `fund_ok` có ba giá trị: `True` / `False` / `None` (chưa biết) — và
  `None` **không bao giờ** được coi là đạt. Chưa có 9.3 nên hiện tại RV chỉ
  dựng được danh sách theo dõi, chưa alert; `--build` in ra "chờ số liệu cơ
  bản: N" để con số đó không im lặng.

Cột `quality` (0..1) **chỉ để xếp hạng** danh sách theo dõi cho khỏi trần
`MAX_CAND = 500`, không phải điểm alert — điểm alert do `scorer.py` chấm khi có
số liệu trong phiên. Dùng chung một con số cho hai việc là cách chắc chắn để sau
này không biết nên điều chỉnh nó theo cái gì.

`load_candidates()` mặc định **bỏ mọi dòng cũ hơn 5 ngày**. Chốt an toàn này
thật sự cần: cron chết thì bảng `struct` đứng yên, và không có gì báo lỗi — chỉ
có pivot cũ nằm đó, còn alert vẫn gửi như thật.

```bash
python setups.py                 # selftest — không cần mạng, không cần DB
python setups.py --build         # quét struct -> candidates + bảng lý do bị loại
python setups.py --show          # danh sách đang theo dõi
python setups.py --show BO --n 40
```

#### 9.3 Cơ bản — `fundamentals.py`, bảng `fund` ▶ CHƯA LÀM

Phần "cơ bản tốt", và cũng là phần dễ tự lừa mình nhất: mã rơi 70% mà cơ bản
"tốt" thường là bẫy pha loãng. Nên điểm cơ bản xây quanh **khả năng sống sót và
không pha loãng**, không quanh P/E:

- Tăng trưởng doanh thu YoY, biên gộp có ổn định không
- Dòng tiền hoạt động dương, **hoặc** tiền / tốc độ đốt tiền ≥ 6 quý
- Nợ / vốn chủ, tiền so với nợ ngắn hạn
- **Tăng trưởng số lượng cổ phiếu YoY** — trên 25%/năm là cờ đỏ, quan trọng
  hơn mọi chỉ số lợi nhuận với small-cap
- Lịch sử hồ sơ pha loãng 12 tháng: S-1 / S-3 / 424B5 / ATM (mở rộng `edgar.py`)

Nguồn: SEC XBRL **frames API**
(`data.sec.gov/api/xbrl/frames/us-gaap/<concept>/USD/CY2025Q2.json`) — một
request trả về giá trị của *toàn bộ* doanh nghiệp cho một chỉ tiêu một quý, nên
phủ 5000 mã bằng ~40 request/tuần. `SEC_UA` đã có sẵn.

⚠️ Frames phủ **không đầy đủ**: cùng một khái niệm có nhiều tag XBRL khác nhau
(`Revenues` vs `RevenueFromContractWithCustomerExcludingAssessedTax`...). Sẽ có
mã thiếu số liệu → trạng thái phải là **"chưa biết"** và bị loại khỏi RV, chứ
không được coi là "tốt".

#### 9.4 Đảo chiều luồng live ▶ CHƯA LÀM

- Nguồn universe thứ ba: **chính bảng `candidates`** — lấy quote theo lô cho
  100–400 mã đó. Setup theo ngày không cần realtime từng giây; trễ 15 phút của
  Yahoo là chấp nhận được, và volume Yahoo là volume **hợp nhất**.
  ⚠️ Alpaca free tier chỉ có bar/snapshot IEX (~2% volume toàn thị trường) →
  RVOL sai hoàn toàn. Không dùng được cho việc này, dù screener của họ thì SIP.
- `scorer.py` tách thành `score_spike` / `score_breakout` / `score_reversal`.
- Vòng mới `loop_setups` (180s) + mốc quét chốt phiên trong `clock.py`.

#### 9.5 Sửa cửa sổ tin 4 giờ ▶ CHƯA LÀM

`WINDOW_H = 4` đúng cho spike ("mã này chạy vì cái gì *lúc này*") nhưng vô
nghĩa cho nền tích luỹ 3 tháng — catalyst ở đó là báo cáo quý cách đây 2 tuần.

- Ghi tin xuống SQLite (bảng `news`), giữ **30 ngày**, thay vì dict cuộn trong
  RAM mất sạch sau mỗi restart.
- Cửa sổ tra theo setup: spike 4h · BO/RV 30 ngày, hiển thị kèm tuổi tin
  ("8 ngày trước: Q3 beat").
- Tách hai khái niệm đang bị trộn: **tin mới** (giải thích cú chạy hôm nay) và
  **rủi ro pha loãng** (bất kỳ hồ sơ offering trong 90 ngày — trừ điểm bất kể
  tuổi).
- Với BO, **không có tin gì là điểm cộng ngầm**: nền tích luỹ im lặng chính là
  dấu hiệu gom hàng. Đừng bắt setup này phải có catalyst.

#### 9.6 Trình bày ▶ CHƯA LÀM

Panel riêng từng setup. BO cần thấy nền dài bao lâu, pivot ở đâu, đang trên
pivot mấy %; RV cần thấy rơi bao sâu, điểm cơ bản, runway, lịch sử pha loãng.

#### 9.7 Backtest — `backtest.py` ✅ ĐÃ XONG

Phần trả lời trực tiếp cho "cách đánh giá hiện tại không ổn". Mỗi phiên, sinh
candidate **chỉ bằng nến tới phiên đó**, rồi đọc nến phiên sau để xem có kích
hoạt không, và ghi lợi nhuận sau 1/5/10/20 phiên.

Ba nguyên tắc, và cả ba đều có test riêng vì backtest sai thì **không bao giờ
báo lỗi** — nó chỉ in ra một con số đẹp hơn sự thật:

1. **Gọi đúng hàm của bot.** `bo_candidate` / `trig_bo` là cùng một đoạn code
   bot chạy thật. Nếu backtest viết lại logic thì kết quả không nói lên điều gì
   về bot. Test: đổi `setups.BO["rvol"]` thì kết quả backtest phải đổi theo.
2. **Không nhìn trước tương lai.** Cắt chuỗi nến ngay sau ngày vào lệnh thì
   lệnh vẫn phải được sinh ra với **cùng giá vào**, chỉ các cột `r5`/`r10` mới
   thành `None`.
3. **Luôn so với mốc ngẫu nhiên.** Báo cáo có dòng `ngau nhien` = lợi nhuận
   trung vị của việc vào mù mọi (mã, ngày). "55% thắng sau 10 phiên" không có
   nghĩa gì nếu vào mù cũng thắng 54%.

Bảng ra:

```
nhom              n   win1    med1  win10   med10  win20   med20     MFE     MAE
BO              412    54%   +0.3%    58%   +2.1%    55%   +3.4%  +11.2%   -6.8%
ngau nhien        -      -   +0.0%      -   +0.4%      -   +0.9%       -       -
```

`--by base_len` / `base_depth` / `rs_pct` / `rvol` / `quality` cắt bảng theo
từng biến — đây mới là cái để chỉnh ngưỡng, vì nó cho thấy **ngưỡng nào thật sự
trả tiền** thay vì chỉ ra một con số tổng.

```bash
python backtest.py                       # selftest — không cần DB
python backtest.py --run --limit 300     # chạy thử nhanh
python backtest.py --run --by base_len
python backtest.py --run --setup BO --rvol 2.5
python backtest.py --run --setup RV --rv-no-fund
```

Bốn điều phải đọc cùng kết quả, nếu không thì con số còn tệ hơn không có:

- **Vào lệnh ở giá đóng cửa**, còn bot alert *giữa* phiên. Thực tế sẽ xấu hơn —
  giữa phiên bạn vào ở giá cao hơn giá đóng cửa ở đúng những ngày đảo chiều.
- **Kho nến chỉ còn mã đang sống.** Mã huỷ niêm yết không có trong đó, nên mọi
  con số đẹp hơn thực tế. RV bị lệch nặng nhất — đừng so `win%` của RV và BO
  như hai số cùng thang đo.
- **`MAE` quan trọng ngang `med`.** MAE trung vị −12% thì dù backtest lãi 12%
  cũng không giao dịch được: bạn đã cắt lỗ trước khi nó lãi.
- **RV mặc định ra 0 lệnh** vì chưa có 9.3, đúng như bot. `--rv-no-fund` đo
  riêng phần cấu trúc và in cảnh báo — đừng dùng số đó để chỉnh ngưỡng RV.

Một chi tiết về tốc độ: `metrics()` là phần đắt nhất (O(số nến) mỗi ngày), nên
`_could_fire()` sàng trước bằng `rvol` và dollar-vol tính trong O(1) từ mảng
thô. Cả hai setup đều đòi `rvol ≥ 1.8` nên đây là **điều kiện cần**, không phải
điều kiện đủ — bỏ qua sớm những ngày không thể kích hoạt cắt khoảng 90% công
việc mà không đổi kết quả. `tests/test_backtest.py` chốt luôn tính chất đó, vì
một bộ sàng lọc quá tay sẽ làm giảm số lệnh mà chẳng ai biết.

#### 9.8 Đo kết quả ▶ CHƯA LÀM

Thêm cột `setup` vào `outcome`, và thêm mốc 1 ngày / 5 ngày / 10 ngày.
`px15`/`px60` là thang đo của day-trade; đo swing setup bằng nó sẽ luôn ra kết
luận sai.

---

### Những thứ mình khuyên KHÔNG làm

Có giá trị ngang phần trên:

**Tự động đặt lệnh.** Alpaca có API trading và cám dỗ là rõ ràng. Đừng. Bot
này chưa từng được đo lường (Phase 1 mới bắt đầu), chạy trên VM miễn phí có
thể bị thu hồi bất cứ lúc nào, và giao dịch mã float nhỏ RVOL 60x là nơi
slippage ăn sạch mọi lợi thế lý thuyết. Khoảng cách giữa "phát hiện tốt" và
"giao dịch có lãi" lớn hơn nhiều so với cảm giác.

**Thêm nguồn dữ liệu chỉ vì nó tồn tại.** Bạn đã có `FINNHUB_KEY` và
`GROQ_KEY` trong `.env` mà không dùng. Mỗi nguồn thêm vào là một điểm chết
mới, một rate limit mới, một chỗ để giá lệch nhau. Chỉ thêm khi có câu hỏi cụ
thể mà nguồn hiện tại không trả lời được.

**Cho LLM viết bình luận về mã.** Nghe hấp dẫn, nhưng nó sẽ tạo ra những câu
tự tin và vô căn cứ đặt ngay cạnh những con số có căn cứ, và bạn sẽ dần tin
chúng ngang nhau. Nếu vẫn muốn, hãy giới hạn nghiêm ngặt ở việc *tóm tắt tin
tức đã có* (Phase 3), không phải dự đoán hay khuyến nghị.

**Web dashboard.** Telegram đã là UI. Dashboard thêm một service phải bảo trì,
một port phải mở (phá vỡ ưu điểm "không cần mở port nào" ở mục 5.0), và bạn
sẽ không mở nó sau tuần đầu.

**Giao diện đẹp hơn nữa.** Bạn vừa làm xong phần này. Nó đủ rồi. Lợi ích biên
của việc chỉnh panel giờ gần bằng không so với việc biết alert nào đúng.

---

### Thứ tự đề xuất

```
✅ Phase 1 (outcome tracking)   ← ĐÃ CÀI, đang chạy nền thu số liệu
✅ Phase 2 (halt feed)          ← ĐÃ CÀI
✅ Phase 4 (test + CI)          ← ĐÃ CÀI
✅ Phase 3 (catalyst/news)      ← ĐÃ CÀI, còn chờ một lần kiểm live
✅ Phase 9.0 (bars.py)          ← ĐÃ CÀI, chờ chạy --sync --full trên VM
✅ Phase 9.1 (structure.py)     ← ĐÃ CÀI, chờ đối chiếu --from-bars
✅ Phase 9.2 (setups.py)        ← ĐÃ CÀI, RV chờ 9.3 mới alert được
✅ Phase 9.7 (backtest.py)      ← ĐÃ CÀI, chờ kho nến trên VM để có số thật
▶  Chạy backtest trên VM → chỉnh dict BO trong setups.py bằng SỐ
   Rồi 9.4 (đảo chiều luồng live) → có alert BO thật
   Sau đó 9.3 (fundamentals) → mở khoá setup RV
   Song song, không phụ thuộc: 9.8 (cột setup) — làm sớm thì số liệu tích sớm
   Cuối: 9.5 (cửa sổ tin), 9.6 (panel)
   Phase 5-8 lùi lại: chúng làm engine spike sạch hơn, nhưng 9.x mới đổi
   được việc bot đi tìm cái gì
```

Hai điểm quan trọng của thứ tự này:

**9.7 phải đi liền sau 9.2, không để dành.** Định nghĩa nền trong
`structure.py` là một *giả thuyết* — nó đã qua test về mặt "đo đúng cái mình
định đo", nhưng chưa có gì chứng minh nền phẳng 30 phiên với volume cạn thật sự
báo trước một cú tăng. Chỉnh ngưỡng trong `setups.py` bằng cảm giác rồi chạy
live 3 tháng để biết kết quả là đúng cái sai mà Phase 9 đang sửa. Cả hai setup
đều theo nến ngày nên **backtest được** — đừng bỏ lợi thế đó.

**9.8 làm được ngay và nên làm sớm**, vì nó chỉ là một cột `setup` cộng thêm
mốc đo 1/5/10 ngày. Cột càng có sớm thì tới lúc đọc bảng `outcome` càng có mẫu
để so ba setup với nhau. Cùng logic với Phase 1: viết mất một buổi, nhưng cần
*thời gian* mới có số.

Giờ đã có test và CI thì mọi chỉnh sửa sau này đều rẻ hơn: sai trọng số hay lệch
layout bị bắt trước khi `git pull` lên VM. Với Phase 9 thì tầng lưu trữ và tầng
đo là **thuần stdlib** nên còn chạy được cả trên máy dev — 4 lỗi thiết kế của
`find_base()` bị bắt ở đó, trước khi đẩy lên VM.

---

## 12. Phễu swing buổi sáng (Stage 1–4)

Một lần chạy lúc **08:00 ET** mỗi ngày làm việc, trả lời hai câu theo thứ tự:

1. **Hôm nay được phép làm gì?** — trạng thái thị trường quyết định setup nào
   được bật và cỡ vị thế bao nhiêu. Có ngày câu trả lời là "không gì cả".
2. **Nếu được phép, thì ở đâu?** — ba nhóm ngành mạnh nhất, rồi cổ phiếu dẫn dắt
   *trong* ba nhóm đó.

Thứ tự này là cả thiết kế. Bắt đầu từ cổ phiếu rồi mới hỏi bối cảnh thì lúc nào
cũng tìm được vài mã đang chạy — kể cả giữa một đợt bán tháo, ngay trước lúc
chúng quay đầu.

```
   08:00 ET, cron hoặc systemd timer gọi nightly.py
        │
        ├─ ① bars      kho nến ngày (yfinance) — nến của ngày đang chạy bị bỏ
        ├─ ② prep      adv20/atr14/prev_close đọc lại từ kho nến
        │
        ├─ ③ regime    STAGE 1  nến SPY → UPTREND / RANGE / DOWNTREND / …
        │              + biên độ CONTRACTED / NORMAL / EXPANDED
        │              → tra config.PLAYBOOK → setup được phép + cỡ vị thế
        │                                            ↓ bảng `regime`
        ├─ ④ sectors   STAGE 2  11 sector SPDR → hạng 1..11
        │              điểm = trung bình percentile của ret21/63/126
        │                                            ↓ bảng `sector_rank`
        ├─ ⑤ structure          đo nền tích luỹ / pivot / off_high từng mã
        ├─ ⑥ setups    STAGE 3  thành phần của 3 sector đầu bảng
        │              → sàn chất lượng → RS vs SPY → gần đỉnh 52 tuần
        │              → top 5/sector, tối đa 10  ↓ bảng `candidates`
        │
        ├─ ⑦ push      STAGE 4  4 snapshot → Cloudflare D1 → trang web
        └─ ⑧ telegram  STAGE 4  một tin nhắn, LUÔN gửi kể cả khi có bước lỗi
                                            ↓ bảng `night` (lần chạy này)
```

### 12.1 Bốn stage, bằng lời

**Stage 1 — `regime.py`.** Đọc ~400 nến SPY. Giá so với SMA50/SMA200; độ dốc
SMA50 so với 10 phiên trước (`>+0.5%` lên, `<-0.5%` xuống, còn lại phẳng) →
`UPTREND` / `UPTREND_UNDER_STRESS` / `RANGE` / `DOWNTREND`. Riêng biên độ:
`ATR(14)/giá` của phiên cuối so với **trung bình của chính nó** 100 phiên →
`CONTRACTED` / `NORMAL` / `EXPANDED`. So với chính nó chứ không với một con số
tuyệt đối, vì "ATR 1.2%" là cao hay thấp còn tuỳ thị trường năm đó.

Cặp `(trend, vol)` tra vào `config.PLAYBOOK` — **12 dòng viết hết ra**, không
phải ba câu `if` lồng nhau. Bảng tra thì đọc một cái là biết bot làm gì ở mọi
trạng thái; `if` lồng nhau thì phải chạy thử mới biết. Mỗi dòng cho `setups` nào
được phép, `size` (1.0 / 0.5 / **0.0 = không mở vị thế mới**), và một `note`
tiếng Việt đi nguyên văn vào tin nhắn lẫn dashboard.

`size == 0.0` là cờ máy đọc được duy nhất cho "đứng ngoài". Đừng suy ra từ
`setups` rỗng — hai thứ đó có thể lệch nhau.

**Stage 2 — `sectors.py`.** Lợi nhuận 21 / 63 / 126 **phiên** (không phải ngày
lịch) cho 11 sector SPDR. Điểm tổng = trung bình ba **hạng percentile**, không
phải trung bình ba lợi nhuận: tháng 4/2025 XLE +18% trong khi cả rổ +2% thì
"trung bình lợi nhuận" biến bảng xếp hạng thành cuộc đo về một mã, còn percentile
chỉ hỏi "hơn được bao nhiêu sector khác".

Ba cờ phụ — trên SMA50, trên EMA21, SMA50 đang dốc lên — **không** vào điểm
tổng. Chúng là bộ lọc để đọc bảng, vì một sector có thể đứng đầu chỉ nhờ giảm ít
nhất.

Mỗi phiên ghi thêm 11 dòng vào `sector_rank`, nên có lịch sử để so hạng với **5
và 21 phiên trước** (↑n / ↓n / —) và để vẽ biểu đồ 90 phiên trên dashboard.
`XLP` hoặc `XLU` (nhóm phòng thủ) vào top 3 thì cảnh báo riêng: đó là dấu hiệu
tiền đang rút khỏi rủi ro, chứ không phải một cơ hội.

**Stage 3 — `holdings.py` + profile `LEAD` trong `setups.py`.** Chỉ xét mã thuộc
3 sector đầu bảng. Bảy bộ lọc, mọi ngưỡng nằm trong `config.LEAD`:

| Lọc | Ngưỡng | Vì sao |
|---|---|---|
| Giá | `> $10` | dưới mức này phần trăm to là chuyện tầm thường |
| Thanh khoản | `adv50 × giá > $20M` | mốc "có tổ chức tham gia" |
| RVol | `> 1.5` | hôm nay đang có người để ý đến nó |
| Biên độ | `2% ≤ ATR/giá ≤ 6%` | dưới 2% không bù nổi phí; trên 6% stop hợp lý rộng đến mức vị thế thành vô nghĩa |
| RS vs SPY | dương ở **cả** 21d **và** 63d | một cửa sổ có thể là may; hai cửa sổ cùng dương thì khó là may hơn |
| Cách đỉnh 52 tuần | `≤ 15%` | cổ phiếu dẫn dắt làm đỉnh mới |
| Trần danh sách | 5/sector, 10 tổng | dài hơn thì không còn là "dẫn dắt", và trong phiên không theo nổi bằng mắt |

Xếp phần còn lại theo điểm RS. **Không ngưỡng nào hardcode trong code** —
`setups.py` chỉ làm `LEAD = config.LEAD`, và `lead_candidate(m, g=...)` vẫn nhận
dict bất kỳ để `backtest.py` quét nhiều bộ ngưỡng trong một lượt.

**Stage 3b — `plan.py`: kế hoạch lệnh.** Stage 3 trả lời "mã này có đáng theo
dõi không". Nó **không** trả lời "vào ở đâu, cắt ở đâu, bao nhiêu" — và trước
đây bảng `candidates` chỉ có `pivot`, không có stop và không có cỡ vị thế, nên
cái gọi là "kế hoạch đã chốt từ tối qua" thực ra chưa bao giờ tồn tại. `plan.py`
là nửa còn lại, và mọi ngưỡng nằm trong `config.PLAN`:

| Con số | Cách tính | Vì sao thế |
|---|---|---|
| `trigger` | `max(pivot, hi1) × (1 + 0.1%)` | vượt **mốc cao nhất** đã biết; `hi1` = đỉnh của chính nến quyết định, để mã không có nền tích luỹ vẫn có mốc |
| `stop` | `trigger − 1.5 × ATR(14)` | theo biên độ của **chính mã đó**, không phải một con số % cố định cho cả bảng |
| `target` | `trigger + 2 × (trigger − stop)` | R:R = 2 |
| `size_pct` | `0.75% ÷ (khoảng cách stop tính theo %)`, chặn trần `20%`, rồi **× hệ số playbook** | rủi ro cố định mỗi lệnh, còn cỡ vị thế thì thay đổi theo mã |

Bốn con số này tính từ **nến đã chốt** và **không đổi trong phiên**. Phần trong
phiên chỉ so giá với chúng, không tính lại — đó là cả điểm của thiết kế.

⚠️ **Trần `20%` chặn thường xuyên hơn là tưởng.** Nó bắt đầu chặn khi khoảng
cách stop nhỏ hơn `0.75% ÷ 20% = 3.75%`, tức là với mọi mã có ATR dưới ~2.5%
— và `config.LEAD` nhận ATR từ 2%. Nên một phần danh sách chạy ở đúng 20% vốn
với rủi ro **thực sự** dưới 0.75%. Đó là cố ý (rủi ro thấp hơn dự tính thì không
sao), nhưng đừng đọc `risk_pct` như một hằng số.

⚠️ **Chưa có trần tổng.** 10 mã × 20% = 200% vốn. `plan.py` không biết portfolio
nên không thể tự chặn; chỗ đúng để chặn là cổng trong phiên, sau khi đọc vị thế
đang mở. Ghi ở đây để không ai tưởng trần 20% là đã đủ.

`size` và `size_pct` là **hai con số khác nhau**: `size` là hệ số của ô playbook
(0.0 / 0.5 / 1.0 — "hôm nay được đánh bao nhiêu phần"), `size_pct` là cỡ vị thế
**cuối cùng** và đã nhân hệ số đó rồi. Nhân lại lần nữa ở dashboard hay ở phần
trong phiên là tự giảm vị thế xuống một nửa mà không ai thấy.

Mặc định khi **không biết** trạng thái thị trường (`regime` chưa có dòng nào,
hoặc `regime.py` không import được) là `size = 0.0` — đứng ngoài. Một Stage 1
chết không được sinh ra một ngày full size mà không ai được báo.

Không đo được ATR hoặc không có mốc giá → `plan.make()` trả `None`, dòng đó
không có kế hoạch, và tin nhắn buổi sáng **nói ra** là những mã đó không vào
lệnh hôm nay. Thiếu thì thiếu, không bịa.

**Stage 4 — `push.py` + `render_night.py` + dashboard.** Xem 12.4 và 12.5.

Bảng `DANH SÁCH THEO DÕI` trong tin nhắn buổi sáng in `VAO / STOP / MUCTIEU /
CACH / CO` — kế hoạch, không phải chỉ số. Bốn cột `RS21 / RS63 / OFF-HI / ATR%`
đã chuyển sang dashboard: chúng trả lời "vì sao mã này có trong danh sách", một
câu hỏi của tối qua. Lúc 8h sáng câu hỏi là "vào ở đâu, cắt ở đâu, bao nhiêu".
Bề rộng panel giữ ở **47 ký tự** vì `<pre>` của Telegram trên điện thoại vỡ cột
khi rộng hơn, và `watch_top = 40` nên **không** thêm được panel thứ hai (2 × 40
dòng sẽ đẩy tin nhắn quá `SAFE_LEN` và `fit()` sẽ bỏ mất bảng xếp hạng ngành —
tức là bỏ mất nguồn của chính danh sách đó).

`push.py` chọn cột bằng `_pick()` — giao của cột nó muốn và cột bảng **thật sự
có**. Lý do: `_rows()` nuốt `sqlite3.Error` và trả `[]`, nên một câu `SELECT` có
cột thiếu không báo lỗi, nó làm **cả bảng watchlist biến mất** khỏi dashboard và
đọc giống hệt một đêm không có mã nào đạt. `push.py` chạy như một tiến trình
riêng với `setups.py` nên hai bên có thể lệch phiên bản trong vài giờ sau deploy.

### 12.2 Chạy tay

Mọi module đều chạy độc lập được, và `--dry-run` nghĩa là **tính hết, in ra,
không ghi gì**:

```bash
cd ~/scanner && source .venv/bin/activate

# cả chuỗi, xem tin nhắn trước khi nó đến điện thoại
python nightly.py --dry-run

# một stage
python regime.py  --dry-run          # trạng thái hôm nay + dòng playbook
python sectors.py --dry-run          # bảng 11 sector
python setups.py  --dry-run          # danh sách theo dõi
python watchlist.py --show           # cổng regime + danh sách đã ép sàn chất lượng

# chạy lại vài bước sau khi sửa ngưỡng, không tải lại nến
python nightly.py --only regime,sectors,structure,setups

# xem lịch sử đã lưu
python regime.py  --show 20          # 20 phiên gần nhất
python sectors.py --show 10
python setups.py  --show LEAD

# in ngưỡng đang chạy — đúng cái dashboard hiện
python regime.py --config
python sectors.py --config

# lần chạy gần nhất ra sao, không chạy lại gì
python nightly.py --status
```

`--dry-run` của `nightly.py` **không gọi mạng**: nó bỏ `bars` và `prep`. Không
phải để nhanh — `bars.sync` *ghi* vào kho nến, và một lần chạy thử không được
sửa kho nến. Nghĩa là nó báo cáo trên kho nến đang có; muốn kho mới thì chạy thật.

Không muốn đụng DB thật thì trỏ sang bản khác:

```bash
cp state/baseline.db /tmp/thu.db
python nightly.py --db /tmp/thu.db --only regime,sectors
```

Chưa có dữ liệu mà muốn xem tin nhắn/dashboard trông thế nào:

```bash
python scripts/demo_stage3.py        # dựng DB giả, không cần mạng
```

### 12.3 Dựng lại lịch sử (`--backfill`)

Cột "Δ5d / Δ21d" và biểu đồ 90 phiên đọc bảng `sector_rank`. Chạy ngày đầu thì
bảng có **một** phiên, nên cả hai đều trống — không phải lỗi, chỉ là chưa có gì
để so. Dựng lại từ kho nến:

```bash
python sectors.py --backfill 120     # lặp lại 120 phiên gần nhất
python sectors.py --show 10          # kiểm tra
```

Backfill tính lại từ nến đã lưu, nên nó **không** gọi mạng và chạy lại bao nhiêu
lần cũng ra cùng kết quả. Cần khoảng `140 + N` nến trong kho
(`SECTORS["min_bars"] = 140`: `ret126` cần 127 nến, độ dốc SMA50 cần 50 + 10);
thiếu thì phiên đó bị bỏ qua chứ **không** bỏ bớt cửa sổ — một bảng xếp hạng
thiếu cột 126d mà vẫn in ra là loại lỗi không ai đọc kỹ để phát hiện.

Bảng `regime` cũng ghi thêm theo phiên nhưng **không có** `--backfill`, vì
dashboard chỉ dùng dòng mới nhất. Muốn lịch sử regime thì gọi
`regime.build(db, d=...)` trong một vòng lặp — hoặc cứ để nó tự đầy lên.

⚠️ **Một hạn chế cần biết.** `sectors.changes()` đếm theo **các phiên có trong
bảng**, không theo ngày lịch. VM tắt một tuần thì "5 phiên trước" ở đây là 5 **bản
ghi** trước — tức là hơn hai tuần thực tế, và không có gì trên màn hình nói ra
điều đó. `--backfill` lấp lại lịch sử liên tục là cách sửa; hàm không tự phát
hiện lỗ hổng.

### 12.4 Làm mới file thành phần sector

`holdings/sector_holdings.csv` là **file tĩnh chép tay, không scrape**, có dòng
`# as_of=YYYY-MM-DD` ở đầu.

**Vì sao không scrape.** Trang holdings của `ssga.com` không có API ổn định.
Scrape thì có ngày nó đổi layout, parser trả về rỗng, và Stage 3 sẽ báo "không có
mã nào đạt" — một câu **hợp lý về mặt cú pháp** cho một ngày thị trường tăng, nên
nó lọt qua mắt. File tĩnh không có kiểu thất bại đó: hoặc nó ở đây, hoặc
`holdings.load()` báo lỗi ngay.

**Đánh đổi:** thành phần sector đổi vài lần một năm (S&P đảo rổ, công ty đổi
nhóm, M&A), nên file **sẽ** cũ dần. `config.HOLDINGS["max_age_days"] = 180` là
mốc cảnh báo, và cảnh báo đó lên cả tin nhắn lẫn dashboard:

```
!! file holdings cũ 452 ngày (as_of 2025-06-30, trần 180)
   → làm mới từ file chính thức của SPDR, xem README
```

**Cách làm mới** (khoảng 15 phút, hai lần một năm là đủ):

1. Với mỗi ETF trong `config.SECTOR_ETFS`, mở
   `https://www.ssga.com/us/en/intermediary/etfs/funds/the-select-sector-spdr-trust-<mã>`
   → *Holdings* → **Daily Holdings (XLS)**.
2. Lấy cột ticker. Bỏ tiền và cân nặng — file này chỉ cần `mã → sector`.
3. Ghép vào `holdings/sector_holdings.csv` theo đúng dạng `SECTOR,TICKER`, một
   dòng một mã.
4. Sửa `# as_of=` thành ngày của file bạn vừa tải. **Đây là bước hay quên**, và
   quên thì cảnh báo tuổi file thành vô nghĩa.
5. Ticker phải là dạng Yahoo: `BRK.B` → `BRK-B`. `--check` bắt lỗi này.

```bash
python holdings.py --check           # định dạng + đối chiếu với kho nến
python holdings.py --show XLK        # xem một sector
```

⚠️ `--check` kiểm **định dạng**, không kiểm **tính đúng đắn**. Một công ty đã đổi
sector vẫn qua hết mọi phép kiểm.

### 12.5 Trang web lấy dữ liệu từ đâu

**Không có HTTP endpoint nào trên VM.** Spec ban đầu yêu cầu bốn endpoint
`/api/regime`, `/api/sectors`, `/api/watchlist`, `/api/status`. VM **không mở
cổng vào nào** — đó là một quyết định an ninh có chủ ý (mục 5.12), và mở cổng
80/443 cho một dashboard đọc-là-chính thì lật ngược nó. Thay vào đó luồng chảy
một chiều, VM luôn là bên gọi:

```
   nightly.py bước ⑦
        │  push.py  →  POST /api/scanner  (token của writer)
        ▼
   Cloudflare Pages Function  →  D1, bảng `scanner_kv`
        ▲
        │  scannerPull(since)  (token của reader)
   Trình duyệt — tab "Máy quét" của screener-ts
```

Bốn "endpoint" thành **bốn khoá D1**, và `scannerPull(since)` chính là cái API:

| Khoá | Nội dung | Thay endpoint |
|---|---|---|
| `scanner:regime` | trend, vol, playbook, cỡ vị thế, nến quyết định | `/api/regime` |
| `scanner:sectors` | 11 dòng + lịch sử 90 phiên cho biểu đồ | `/api/sectors` |
| `scanner:watchlist` | danh sách theo dõi + cỡ vị thế từng mã | `/api/watchlist` |
| `scanner:status` | lần chạy cuối, bước nào lỗi, nguồn dữ liệu, cờ "cũ quá" | `/api/status` |
| `scanner:thresholds` | `config.snapshot()` — bảng Config chỉ đọc | — |

⚠️ **Tên khoá là `scanner:thresholds`, không phải `scanner:config`.** Function
phía Cloudflare giữ riêng `scanner:config` và `scanner:commands` cho chiều
**app → VM** (`APP_KEYS` trong `functions/api/scanner/[[path]].ts`), nên token
writer của VM bị trả 403 nếu ghi vào đó. Đổi tên khoá này sẽ làm bảng Config
trống mà không báo lỗi gì.

Ba việc `push.py` làm mà đáng nhớ:

- **Mở DB ở chế độ chỉ đọc** (`mode=ro` qua URI). Một lỗi trong file này không
  thể làm hỏng 3 năm nến.
- **So digest trước khi gửi**, và `ts`/`age_sec` bị loại khỏi digest — không thì
  mọi lần chạy đều "có thay đổi" chỉ vì đồng hồ nhích.
- **Không có spool.** Mất mạng thì bỏ vòng này. Một alert Telegram bỏ lỡ là mất
  hẳn; một snapshot thì vô giá trị ngay khi có bản mới.

Trên dashboard, **banner "số liệu cũ"** bật khi lần chạy thành công gần nhất cũ
hơn `NIGHTLY["stale_hours"] = 36` — tính bằng **giờ làm việc**, thứ Bảy và Chủ
Nhật không tính (`push._biz_hours`). 36 chứ không phải 24 vì cron chạy ngày làm
việc: sáng thứ Hai, bản ghi mới nhất là sáng thứ Sáu, hơn 48 giờ thực — một
ngưỡng 24 giờ sẽ báo động **mọi thứ Hai** cho đến khi không ai đọc banner nữa, và
lúc đó nó không còn báo được sự cố thật.

"Lỗi" và "cũ" là **hai dòng riêng**, vì một lần chạy thất bại một giờ trước thì
vừa mới vừa hỏng — và chỉ dòng "lỗi" mới nói ra bước nào cần đi xem.

Link `#scanner` trong tin nhắn buổi sáng mở thẳng tab đó; URL suy ra từ
`SCANNER_PUSH_URL` nên hai bên không thể trỏ vào hai domain khác nhau.

### 12.6 Giới hạn của nguồn dữ liệu

Bảy điều đã biết. Không cái nào là bug; biết trước thì không mất buổi nào đi tìm.

1. **yfinance là dữ liệu trễ, không phải realtime**, và không có SLA. Với chuỗi
   08:00 ET thì không sao — nó đọc nến *hôm qua*, đã chốt từ lâu. Nhưng dòng
   "Nguồn dữ liệu" trên dashboard nói thẳng điều này, vì cùng một nguồn mà dùng
   trong phiên thì lại không ổn.
2. **Nến điều chỉnh bị viết lại về quá khứ.** Chia cổ tức hay split hôm nay làm
   đổi cả chuỗi `ac` phía sau. Nghĩa là `--backfill` chạy hôm nay có thể ra hạng
   hơi khác với hạng đã ghi ngày đó — số cũ không "sai", chúng là ảnh chụp
   của dữ liệu lúc bấy giờ.
3. **Nến ngày đang chạy bị bỏ, ba lớp.** `bars.sync(drop_partial=True)`, rồi
   `regime._closed()` / `sectors._closed()`, rồi `nightly._check_bar()` so nến
   quyết định với ngày ET hiện tại và **báo động nếu hai cái trùng nhau**. Lớp 3
   không thay hai lớp trên; nó là cái báo rằng hai lớp trên đã hỏng. Đây là kiểu
   lỗi mà nếu không có ai báo thì nó chỉ hiện ra dưới dạng "kết quả backtest đẹp
   một cách khả nghi".
4. **Thiếu SPY là thất bại im lặng nếu không phòng.** `universe` của `prep.py`
   lọc theo screener cổ phiếu, không đảm bảo có ETF. Nên `config.REGIME_SYMS`
   buộc SPY + 11 sector luôn có trong kho; thiếu SPY thì `regime.py` trả `None`
   và cả chuỗi mất ý nghĩa.
5. **`sectors.changes()` đếm bản ghi, không đếm ngày lịch** — xem 12.3.
6. **File holdings cũ dần** — xem 12.4. Cổ phiếu đã đổi sector vẫn nằm ở nhóm cũ
   cho đến lần làm mới tiếp theo.
7. **Không có bid-ask, không có vốn hoá.** Sàn chất lượng của Stage 3 dùng giá và
   giá-trị-giao-dịch thay thế. Hai cái đó lọc được gần hết cùng một tập mã, nhưng
   không phải tất cả: một ADR thanh khoản mỏng với spread rộng có thể lọt qua.
   Prompt 2 thêm tiêu chí vốn hoá và sàn niêm yết cho phần trong phiên.

### 12.7 `watchlist.py` — cửa vào của phần trong phiên

Phần trong phiên đang chuyển từ *đi tìm* sang *theo dõi*. `main.py` cũ tự tìm mã
bằng screener top-mover, và đó chính là nguồn của toàn bộ rác giá thấp: phần trăm
biến động là một con số **rẻ** với cổ phiếu $3, nên top-mover gần như luôn là cổ
phiếu $3 — spread rộng, không có tổ chức tham gia, gap qua đêm không đỡ được.

`watchlist.py` là cửa duy nhất đi vào phần trong phiên. Ba việc, không việc nào
gửi tin nhắn hay gọi mạng (trừ một hàm cache vốn hóa):

#### Sàn chất lượng — `check(m, g, ref_bars)`

Trả về **hai** danh sách: `(bị loại, không đo được)`.

| Sàn | Ngưỡng (`config.INTRADAY`) | Thiếu dữ liệu thì |
|---|---|---|
| Giá | `> $10` | **loại** — không có giá thì không có gì để nói |
| Thanh khoản | `adv50 × giá > $20M/ngày` | **loại** — mã qua được LEAD thì bắt buộc đã có `adv50`; thiếu nghĩa là `struct` và `candidates` lệch phiên |
| Vốn hóa | `> $2B` | không đo được |
| Sàn niêm yết | NYSE · NASDAQ · **ARCA** | không đo được |
| Tuổi niêm yết | `≥ 250` nến trong kho | không đo được (xem dưới) |
| Spread | `≤ 0.15%` giá | không đo được |
| Kế hoạch lệnh | có `trigger` **và** `stop` | **loại** — không có kế hoạch thì trong phiên lại phải ứng biến |

> ⚠️ **Đây là sàn, không phải tham số để nới.** Hạ giá xuống $5 hay hạ thanh
> khoản xuống $5M là mời lại đúng đám rác mà cả thiết kế này dùng để chặn. Chúng
> nằm trong `config.py` để đọc được, không phải để tinh chỉnh.

> ⚠️ **Thiếu dữ liệu không bao giờ là "đạt".** Một tiêu chí không đo được đi
> nguyên văn vào tin nhắn (*"chưa biết vốn hóa"*, *"chưa kiểm được spread"*).
> Làm tròn nó thành dấu tích thì sàn $2B trở thành một câu trong config chứ không
> phải một cái sàn — và không có cách nào phát hiện từ bên ngoài.

**ARCA được nhận.** Sàn niêm yết chỉ áp cho đường cổ phiếu. Bỏ ARCA là bỏ SPY và
cả 11 mã XL\*, tức là phá Stage 1–2; `config.REGIME_SYMS` không đi qua sàn này.

**Tuổi niêm yết chỉ đo được gián tiếp** bằng số nến trong kho, nên nó có ba
nhánh, không phải hai: ít nến mà kho sâu (SPY ≥ 250 nến) → mã mới thật, **loại**;
ít nến mà cả kho đều nông → *không kiểm được*, vì kho vừa backfill thì **mọi** mã
đều "mới" và loại sạch danh sách là biến một vấn đề hạ tầng thành một phiên im
lặng.

**Vốn hóa là số thật, không phải vốn hóa float.** `base.float_sh × giá` là vốn
hóa *float*, lệch hẳn khi nội bộ giữ nhiều cổ phần. Bước `mktcap` của `nightly.py`
gọi yfinance lấy `marketCap` cho **đúng** các mã trong danh sách (≤ 10 mã/đêm),
cache vào `base.mktcap` với TTL 7 ngày → thực tế là một chữ số request mỗi đêm,
thường là 0 vì cache còn hạn. Bước này **không bắt buộc**: một lần yfinance hỏng
không được huỷ cả chuỗi chạy đêm, mã thiếu vốn hóa đi vào phiên kèm ghi chú.
Lấy không được thì **không ghi gì cả** — không ghi `0` (sẽ loại mã vĩnh viễn) và
không ghi `mktcap_ts` (sẽ thành "đã kiểm rồi" và không thử lại trong 7 ngày).

#### Cổng regime — `gate(db)`

Cổng **cứng, xét một lần lúc khởi động**: nó quyết định loại alert nào *tồn tại*
trong phiên, chứ không lọc từng alert một.

| Chế độ | Nghĩa | Sinh ra khi |
|---|---|---|
| `full` | đầy đủ — cả vào mới và quản lý vị thế | còn lại |
| `revert` | chỉ hồi về trung bình | playbook cho đúng `("RV",)` |
| `manage` | chỉ quản lý vị thế đang có | playbook không cho setup nào, hoặc cỡ = 0 |
| `stop_only` | chỉ báo cắt lỗ cho vị thế đang mở | `DOWNTREND` |

Bốn chế độ này **suy ra từ `config.PLAYBOOK`**, không phải một bảng `if` thứ hai:
một bảng thứ hai sẽ lệch với tin nhắn buổi sáng mà không ai thấy.

> ⚠️ **Không biết thì đứng ngoài.** Thiếu bảng `regime`, không mở được DB, hay
> cặp trend/vol vô nghĩa → `stop_only`. Mặc định ngược lại (`full`) nghĩa là một
> Stage 1 chết sẽ cho ra một phiên đánh full size mà không ai được báo.

#### Đọc danh sách — `load(db, day=...)`

Đọc các dòng `setup='LEAD'` của `candidates` (một phiên duy nhất, đã chốt), ép sàn
chất lượng, trả về `rows` / `drop` / `day` / `stale` / `note` / `manual`.

- **Kế hoạch hết hạn bị gọi tên.** Nến quyết định cách hôm nay quá 3 **ngày làm
  việc** → `stale=True` kèm câu giải thích. Đếm ngày làm việc, không ngày lịch:
  một ngưỡng theo ngày lịch sẽ báo động **mọi** thứ Hai, và một báo động báo mọi
  thứ Hai là một báo động bị tắt. Ba ngày chứ không một: sáng thứ Ba sau một thứ
  Hai nghỉ lễ, nến thứ Sáu đã cách 2 ngày làm việc.
- **Danh sách rỗng là một kết quả có câu giải thích**, không phải sự im lặng.
- **Thêm tay** qua bảng `watch` với `kind='manual'`: vẫn phải có dòng trong
  `candidates` (tức vẫn phải có kế hoạch lệnh), vẫn ép sàn chất lượng y nguyên,
  và được đánh dấu `_manual` để tin nhắn nói rõ mã này do mình thêm. Mã thêm tay
  được gộp vào **trước** khi xét "danh sách rỗng" — trường hợp dùng nhất của nó
  chính là đêm qua không mã nào đạt.

```bash
python watchlist.py --show                 # cổng regime + danh sách + lý do bị loại
python watchlist.py --mktcap               # làm mới cache vốn hóa (có gọi mạng)
python watchlist.py                        # selftest, không mạng, không DB
```

### 12.8 `positions.py` — vị thế đang mở, và vì sao `stop_only` cần nó

Cổng regime có chế độ `stop_only`: DOWNTREND thì **im lặng toàn bộ** canh báo mua,
chỉ còn canh cắt lỗ cho vị thế đang mở. Chế độ đó vô nghĩa nếu scanner không biết
mình đang giữ gì — nó sẽ "im lặng toàn bộ" thật, tức là đúng vào những ngày một
mức cắt lỗ quan trọng nhất.

Nguồn sự thật là sổ trong app **lux-lookthrough**, không phải một file thứ hai ở
đây. Mỗi lần lưu portfolio, app dựng một **bản rút gọn** và PUT lên
`scanner:positions`; VM đọc về.

```
lux-lookthrough                                 scanner (VM)
saveAccounts()
  └─ buildPositionsDigest()   ← core, có vitest
       └─ publishPositions()  → PUT /api/scanner/kv/scanner:positions
                                      └─ positions.load() → parse()
```

**Cái gì đi qua:** mã, số cổ phiếu đang mở, giá vốn bình quân, **mọi** mức cắt lỗ,
tiền tệ đã nhập giá, tên tài khoản. **Cái gì không:** tiền, vốn, lãi/lỗ, id tài
khoản, id lô, ngày mua. Lý do phải kể ra: endpoint `/api/scanner` chỉ chặn
**ghi** theo vai, còn **GET thì cả hai vai đều đọc được** (VM buộc phải đọc được
`scanner:config`). Nên mọi thứ app đăng lên đây là thứ ai giữ token của VM cũng
đọc được, và bản rút gọn này là chỗ duy nhất được phép hẹp lại như vậy.

> ⚠️ **Thiếu không phải là "không có vị thế".** Chưa cấu hình push, mạng chết,
> khoá chưa tồn tại, JSON lạ, `rows` không phải list → `known=False`. Hai trường
> hợp "không canh gì cả" trông giống nhau từ bên ngoài nhưng một cái hợp lệ và một
> cái là hệ thống đang hỏng, nên `note` của chúng **không bao giờ** được giống
> nhau. `n=0` với một bản đọc được vẫn là một **câu trả lời**: "không có vị thế
> nào đang mở".

> ⚠️ **Cũ không phải là sai.** Khoá này được ghi theo **sự kiện** (lúc lưu
> portfolio), không định kỳ: không giao dịch một tuần thì ảnh chụp cũ một tuần và
> vẫn đúng từng chữ. Nên `pos_stale_h` (30 giờ) chỉ bật `old=True` để tin nhắn mở
> phiên **nói ra** — nó không bao giờ bỏ dữ liệu đi. Nếu bỏ thì mọi sáng thứ Hai
> canh cắt lỗ sẽ tắt, đúng ở chỗ nguy hiểm nhất.

Tuổi tính theo **`updatedAt` của Cloudflare**, không theo `ts` mà trình duyệt ghi:
một máy chạy lệch vài phút sẽ cho ra "luôn còn mới" hoặc một tuổi âm. Không có
`updatedAt` thì hiện `ts` để xem, nhưng **không đoán tuổi** từ nó.

#### Ba cạm bẫy ở phía sổ — và cách xử lý

| Cạm bẫy | Vì sao nó âm thầm | Xử lý |
|---|---|---|
| Nhiều lô cùng một mã, mỗi lô một stop | Bảng portfolio hiện stop của lô **cũ nhất** làm số đại diện. Giá giảm thì xuyên mức **cao nhất** trước → lấy số của bảng là canh muộn, hoặc không bao giờ canh | Đẩy **mọi** mức, `stops` xếp giảm dần; `stop_hit()` trả mức cao nhất bị xuyên |
| Stop đặt bằng **lệnh chờ** `STOP_LOSS` | Không nằm trong `lot.stop`, mà với người dùng thì hai thứ đó là một thứ | Gộp lệnh chờ `pending` vào, chỉ cho mã còn mở; `TAKE_PROFIT` thì **không** |
| Giá nhập bằng **EUR**, báo giá bằng **USD** | `metrics.ts` ở phía app đang so trực tiếp hai thứ đó. Sao y sang đây sẽ cho ra alert ở mức lệch ~12% mà lại còn tự tin | **Không quy đổi, không đoán tỷ giá.** Mã đó vào `unchecked()` kèm lý do, và phải xuất hiện ở tin nhắn mở phiên |

Điều cuối cùng là chỗ khác biệt giữa **đứng ngoài** và **im lặng**: một mã scanner
không canh được thì mình phải tự canh, và mình chỉ tự canh được nếu **biết**.
Thiếu `cur` cũng vào đó luôn — "không biết" thì không phải là USD.

```python
d = positions.load()                    # {known, n, rows, warn, ts, age_h, old, note}
positions.watched(d)                    # các mã canh được stop thật
positions.unchecked(d)                  # [(mã, lý do tiếng Việt)] → tin nhắn mở phiên
positions.stop_hit(d["rows"]["NVDA"], px)   # {hit, n, ok, why}; bằng nhau TÍNH LÀ xuyên
```

```bash
python positions.py                              # selftest, không mạng
python positions.py --show                        # đọc thật từ cloud
python positions.py --show --from snapshot.json   # đọc từ file, không mạng
```

### 12.9 `watchd.py` — cảnh báo Tier 1 trong phiên

Đây là phần thay thế cho hệ thống "top % tăng" cũ. Nó **không đi tìm** gì cả: nó
canh đúng các mốc giá đã được chốt từ tối qua.

```
  TỐI QUA (nightly.py, 08:00 ET)          TRONG PHIÊN (watchd.py, mỗi 60s)
  ──────────────────────────────          ────────────────────────────────
  regime → sectors → structure            watchlist.load()  ← danh sách + KẾ HOẠCH
  → setups → plan → bảng `candidates`     watchlist.gate()  ← chế độ phiên
                    │                     positions.load()  ← vị thế đang mở
                    └────────────────────→ quotes.Provider   ← báo giá (+ độ trễ)
                                                │
                                          watch.evaluate()   LUẬT: có gì xảy ra
                                                │
                                          watch.decide()     CHỐNG SPAM: gửi được không
                                                │
                                          render_watch       tin nhắn tiếng Việt
                                                │
                                          bảng `watch_alert` ghi SAU khi gửi được
```

Hai tiến trình, **một nguồn sự thật**: `nightly.py` ghi, `watchd.py` đọc. Trong
phiên không có con số nào được tính lại — điểm vào, cắt lỗ, mục tiêu, cỡ vị thế
đều là số của tối qua. Đó là cả mục đích: *không ứng biến giữa phiên, chỉ thực
hiện một quyết định đã làm từ tối hôm trước.*

⚠️ **Không bao giờ thêm một mã nào ngoài danh sách.** Thêm tay thì đi qua đúng
cửa của mục 12.7 — `INSERT INTO watch (sym, kind) VALUES ('XYZ','manual')`, và mã
đó **vẫn phải có dòng trong `candidates`** (tức vẫn phải có kế hoạch lệnh) và vẫn
bị sàn chất lượng ép y nguyên. Vòng lặp đọc lại danh sách mỗi 5 phút nên không
cần restart.

Vị thế đang mở được lấy báo giá **chỉ để canh cắt lỗ**, không có luật vào lệnh
nào chạy trên chúng.

#### Ba luật Tier 1 — `watch.evaluate()`

| Luật | Kích khi | Không kích khi |
|---|---|---|
| `stop` | giá ≤ mức cắt lỗ **cao nhất** của một vị thế đang mở | mã nhập bằng EUR, chưa có stop, chưa có báo giá (→ `unchecked`, và nói ra) |
| `trigger` | giá ≥ `trigger` **và** RVol đã chuẩn hoá ≥ 1.5× | giá **mở cửa** đã ở trên `trigger` (đó là gap, không phải "chạm điểm vào"), hoặc không tính được RVol |
| `gap` | \|giá mở / nến quyết định − 1\| ≥ 3% | thiếu giá mở hoặc thiếu nến quyết định |

"Chạm" được đo bằng **giá mở cửa của hôm nay**, không bằng vòng quét trước. Một
biến trong RAM sẽ mất khi tiến trình chết — và mất theo đúng cái cách không ai
thấy.

#### ⚠️ RVol phải chuẩn hoá theo giờ — dùng đường cong nào, và vì sao

Mẫu số là **đường cong chữ U của cả thị trường** trong `vprofile.py` (`_MIN` /
`_FRAC`: 20 điểm mốc từ phút 0 đến phút 390, nội suy tuyến tính; nửa phiên 210
phút được co giãn theo tỷ lệ).

Lý do phải có nó: lúc 11:00 (phút 90) một mã bình thường mới chạy **~25%** khối
lượng cả ngày. Nếu mẫu số là `adv50` thô thì `400k / 1M = 0.4` và ngưỡng 1.5
**không bao giờ** đạt trong nửa đầu phiên — cùng con số đó chia cho kỳ vọng đúng
giờ là **1.6×** và luật kích. Một bộ lọc không bao giờ kích thì **không báo lỗi,
nó chỉ im lặng**. Đây là chi tiết phân biệt cảnh báo khối lượng có ích với cảnh
báo khối lượng vô dụng.

Vì sao là đường cong **thị trường** chứ không phải đường cong riêng từng mã: một
đường riêng cần 20 phiên nến phút của từng mã, tức là một kho dữ liệu mới và một
đường ống mới để nuôi nó. Đường thị trường sai vài phần trăm với một mã cụ thể;
`adv50` thô sai **gấp 4 lần** lúc giữa phiên. Đường riêng từng mã là bước sau —
đến lúc đó `vprofile._FRAC` trở thành đường **dự phòng** chứ không phải đường duy
nhất, và mục này sẽ nói rõ mã nào đang dùng đường nào.

⚠️ **Khối lượng phải là khối lượng hợp nhất (consolidated).** `adv50` trong bảng
`base` là khối lượng của cả thị trường. Nếu nguồn báo giá chỉ trả khối lượng của
một sàn (feed IEX miễn phí của Alpaca ≈ 2% khối lượng) thì RVol tính ra luôn
~0.02 và ngưỡng không bao giờ kích. Nên mỗi báo giá mang cờ `vol_ok`: False nghĩa
là **không biết** RVol, và "không biết" thì không cảnh — nhưng phải nói ra.

#### Cổng regime — `watch.RULES_BY_MODE`

Cùng bảng `config.PLAYBOOK` với `nightly.py`, không có bảng thứ hai.

| Chế độ | `stop` | `trigger` | `gap` |
|---|---|---|---|
| `full` (UPTREND) | ✅ | ✅ | ✅ |
| `revert` (RANGE) | ✅ | ✅ | ✅ |
| `manage` (UPTREND_UNDER_STRESS) | ✅ | ❌ | chỉ mã **đang giữ** |
| `stop_only` (DOWNTREND, hoặc **không đọc được** regime) | ✅ | ❌ | ❌ |

`stop` có trong **mọi** chế độ, kể cả DOWNTREND: đó là cả điều khoản của
`stop_only`. Không đọc được regime cũng rơi về `stop_only` — mặc định khi không
biết là đứng ngoài.

#### Chống spam — *"thà bỏ lỡ một alert hơn là nhận 40 cái"*

| Ngưỡng | Giá trị | Ghi chú |
|---|---|---|
| mỗi mã mỗi luật | **1 lần/phiên** | do khoá chính `(d, sym, rule)` của SQLite bảo đảm, không phải một `set` trong RAM |
| 10 phút đầu phiên | bỏ hết | giá mở cửa hay là một cái răng cưa; `stop` **không** được miễn — nếu thật đã xuyên thì 10 phút nữa vẫn xuyên |
| nghỉ giữa hai tin cùng mã | 15 phút | **không áp dụng cho `stop`**: một cảnh báo cắt lỗ bị một cảnh báo gap của 10 phút trước chặn lại là kiểu lỗi không thể biện minh |
| trần Tier 2 | 8/phiên | Tier 1 **không có trần** |
| báo giá quá cũ | > 20 phút | nguồn trễ ~15 phút nên hạ xuống 15 sẽ làm mọi báo giá bình thường bị coi là quá cũ, tức là cả phiên im lặng |

**Restart giữa phiên không bắn lại.** Trạng thái nằm trong bảng `watch_alert`,
đọc lại từ DB mỗi vòng. Và ghi **sau** khi gửi được, không phải trước: một tin
nhắn không đến mà đã ghi "đã cảnh báo" thì mất hẳn, còn gửi hai lần chỉ là một
tin trùng.

#### Im lặng phải là một lựa chọn, không bao giờ là một triệu chứng

Ba trường hợp đều có **một** tin nhắn rồi im:

* **DOWNTREND / danh sách rỗng** → tin mở phiên nói rõ "sau tin này sẽ im lặng
  trừ khi một vị thế đang mở xuyên mức cắt lỗ".
* **Nguồn báo giá chết** → sau 5 vòng liên tiếp không lấy được giá, một tin
  `src_down`, rồi lui nhịp. Một API chết trông y hệt một phiên không có gì xảy ra
  nếu không có tin đó.
* **Tiến trình chết / không khởi động được** → một tin ⛔ kèm loại lỗi, rồi mã
  thoát ≠ 0. Mã thoát chỉ đến được cron; tin nhắn đến được người.

Mọi mã **không kiểm được** (báo giá cũ, thiếu `adv50`, nguồn không cho khối lượng
hợp nhất, mở cửa đã trên điểm vào) đi vào `skip` và hiện ở tin mở phiên kèm lý do
tiếng Việt. Một mã bị bỏ qua mà không ai biết là cách dễ nhất để một bộ lọc hỏng
nằm im cả tháng.

Mỗi cảnh báo mang **dấu mốc báo giá + độ trễ + tên nguồn** ("báo giá 15:42 ET ·
trễ 16 phút · nguồn yf"). Nguồn miễn phí trễ ~15 phút; bot không xoá được độ trễ,
nó chỉ nói thật về độ trễ.

#### Chạy

```bash
python watchd.py --selftest                   # luật + vòng quét, không mạng
python watchd.py --once --dry-run             # một vòng, in ra stdout
QUOTE_SRC=fixture:q.json python watchd.py --once --dry-run   # không mạng
python watchd.py                              # chạy thật, tự bật/tắt theo phiên
python watch.py --show                        # các cảnh báo đã gửi hôm nay
```

Lịch phiên lấy từ `clock.SessionClock` (ngày lễ, nửa phiên, **DST cả hai phía**
bằng `zoneinfo` — không bao giờ là một offset cố định). Ngoài phiên nó không gọi
mạng, không gửi gì. Trên VM thì chạy bằng systemd, giống mục 5.7 nhưng **không**
đụng vào unit của `main.py`:

```ini
# /etc/systemd/system/watchd.service
[Unit]
Description=Scanner intraday Tier 1 watcher
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/scanner
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
ExecStart=/home/ubuntu/scanner/.venv/bin/python watchd.py --quiet
Restart=always
RestartSec=30
StandardError=append:/home/ubuntu/scanner/state/watchd.service.log

[Install]
WantedBy=multi-user.target
```

`--quiet` vì log của chính nó đã xoay vòng trong `state/watchd.log`; nhưng
`StandardError` vẫn phải đi đâu đó — một traceback xảy ra **trước** khi logger
dựng xong thì chỉ còn đường đó.

`Restart=always` an toàn ở đây đúng vì bảng `watch_alert`: restart giữa phiên
không bắn lại tin đã gửi.

`watchd.py` **không** gọi `getUpdates`, nên nó chạy song song với `main.py` được —
đó là điều kiện để bỏ `main.py` mà không có ngày nào không ai canh phiên. Ngược
lại, hai tiến trình `main.py` cùng lúc vẫn là 409 Conflict như cũ.

### Chưa làm của prompt 2

* **Tier 2** (tiếp cận vùng trong 0.5×ATR, RVol > 2.5 kèm giá > VWAP, đảo chiều ở
  hỗ trợ đã định) — cơ chế trần 8 tin/phiên đã có và đã được test, chỉ chưa có
  luật nào. Theo đúng thứ tự đã thống nhất: **chạy thật một phiên với Tier 1
  trước.**
* Đường cong khối lượng **riêng từng mã** (20 phiên gần nhất) — xem lý do ở trên.
* `--replay [ngày]` — `quotes.FixtureProvider` đã là một nửa của nó.
* Dashboard: bảng danh sách đang canh, log cảnh báo hôm nay, thêm/bớt mã bằng tay
  (sàn chất lượng phải kiểm ở **phía server**), sức khoẻ tiến trình.
* Bỏ `universe_live.py` / `scorer.py` khỏi đường chạy mặc định, sau một tuần chạy
  song song.

---

## Giấy phép

MIT — xem `LICENSE`.
