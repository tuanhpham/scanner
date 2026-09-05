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

Dòng `python -c "import ..."` đáng làm: import lỗi thì systemd sẽ
crash-loop mỗi 30 giây và bạn phải mò trong `journalctl`, trong khi chạy
import tay nó in traceback ra ngay.

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

## 11. Roadmap — các bước tiếp theo

Sắp theo thứ tự **giá trị / công sức**, không phải theo độ thú vị. Mỗi mục có
tiêu chí "xong" rõ ràng để bạn biết khi nào dừng.

Nguyên tắc xuyên suốt: **bot này là công cụ phát hiện, không phải công cụ giao
dịch.** Mọi thứ dưới đây đều nhằm làm alert *đáng tin hơn* hoặc *ít rác hơn*,
không nhằm tự động đặt lệnh.

---

### PHASE 1 — Đo chất lượng alert (làm trước mọi thứ khác)

**Vấn đề:** hiện tại `ALERT_SCORE = 7.0` và trọng số trong `scorer.py` là số
bạn *đoán*. Không có cách nào biết mã 8.3 điểm có thật sự tốt hơn mã 7.1 điểm.
Không có dữ liệu này thì mọi lần "tinh chỉnh" chỉ là đổi cảm giác.

**Việc cần làm — `outcome.py` (module mới):**

Bảng mới trong `state/baseline.db`:

```sql
CREATE TABLE IF NOT EXISTS outcome (
    sym       TEXT,
    day       TEXT,
    alert_ts  INTEGER,        -- lúc gửi alert
    score     REAL,
    level     INTEGER,
    px0       REAL,           -- giá lúc alert
    px15      REAL,           -- giá sau 15 phút
    px60      REAL,           -- giá sau 60 phút
    px_close  REAL,           -- giá đóng phiên
    hi_after  REAL,           -- đỉnh cao nhất sau alert
    lo_after  REAL,           -- đáy thấp nhất sau alert
    PRIMARY KEY (sym, alert_ts)
);
```

Một task async trong `main.py`: mỗi 60 giây, tìm các dòng `outcome` còn thiếu
`px15`/`px60`/`px_close` và đã tới hạn, rồi điền vào từ `st.universe` (giá đã
có sẵn trong vòng quét, **không cần gọi API thêm**). Sau giờ đóng, một job
cron điền `px_close`, `hi_after`, `lo_after`.

**Rồi `scripts/report_quality.py`** in ra bảng như sau:

```
BUCKET      n     win15%  med15%  win60%  med60%  medMFE%  medMAE%
7.0-8.0    142     48%    +0.4%    44%    -0.2%    +3.1%    -2.8%
8.0-9.0     67     61%    +1.9%    57%    +2.4%    +6.0%    -2.1%
9.0-10.0    23     70%    +3.8%    65%    +5.1%    +9.2%    -1.9%
10.0+        8     75%    +6.2%    75%    +8.0%   +14.1%    -2.2%
```

**Tiêu chí xong:** sau 3–4 tuần chạy, bảng này cho thấy điểm cao **thật sự**
tương quan với kết quả tốt hơn. Nếu bucket 7–8 có `win15%` khoảng 50% (tức là
ngang tung xúc xắc) thì bạn đã tìm ra câu trả lời: **nâng `ALERT_SCORE` lên
8.0** và cắt được một nửa số alert rác.

**Công sức:** ~150 dòng code. **Đây là mục có ROI cao nhất trong toàn bộ danh
sách này** — nó biến việc tinh chỉnh từ đoán thành đo.

⚠️ Đừng tự lừa mình ở bước này. `hi_after` (MFE) trông rất đẹp vì nó là đỉnh
*hoàn hảo* mà không ai bắt được. Cột đáng tin là `med15%` và `med60%`. Và nhớ
rằng số liệu này **không tính slippage, spread, hay việc bạn có kịp vào lệnh
hay không** — mã float nhỏ RVOL 60x có spread rất rộng.

---

### PHASE 2 — Feed trading halt (giá trị cao, công sức thấp)

**Vấn đề:** một mã +85% RVOL 60x rất có thể **đang bị halt**. Alert cho một mã
đang halt là alert vô dụng — bạn không mua được, và khi mở lại giá đã nhảy chỗ
khác. Ngược lại, mã vừa **resume sau halt T2** (tin đã ra) lại là tình huống
đáng chú ý nhất.

**Nguồn (đã kiểm chứng, miễn phí, không cần key):**

```
https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts
```

XML trả về mỗi `<item>` có `ndaq:IssueSymbol`, `ndaq:ReasonCode`,
`ndaq:HaltDate` + `ndaq:HaltTime`, và `ndaq:ResumptionTradeTime` (trống nghĩa
là **chưa mở lại**). Nasdaq ghi rõ: **không query quá 1 lần/phút.**

Các mã lý do cần biết:

| Code | Nghĩa | Xử lý trong alert |
|---|---|---|
| `LUDP` | Volatility pause (giá chạy ≥10% trong 5 phút) | 🔴 rất thường gặp với mã bạn quét |
| `T1` | Halt chờ tin — tin **chưa** ra | ⏸️ chờ, đừng vào |
| `T2` | Tin đã ra, vẫn đang halt | 🟡 chuẩn bị, resume sắp tới |
| `T12` | Halt chờ công ty trả lời SEC/exchange | ⚠️ xấu |
| `H10` | **SEC trading suspension** | ⛔ cực xấu, thường là nghi vấn gian lận |

**`halts.py` (module mới):** một task async poll mỗi 60 giây, parse XML, giữ
dict `{sym: {"code": ..., "since": ..., "resume": ...}}` trong bộ nhớ.

**Tích hợp:**
- `render.AlertView` thêm field `halt` → khi có halt, chèn một dòng **ngay
  đầu tin nhắn**, trên cả panel: `⏸️ HALT T1 · tu 15:42 ET · chua co gio mo lai`
- `H10` → **chặn alert hoàn toàn**, hoặc gắn cảnh báo đỏ đậm ở khối RISK
- Mã vừa resume trong 5 phút → cộng điểm hoặc gắn nhãn `🔄 vua resume`

**Tiêu chí xong:** trong một phiên có mã bị LUDP, alert phải hiện dòng halt.

**Công sức:** ~100 dòng. Không cần API key, không rate limit đáng lo. **Làm
ngay sau Phase 1.**

---

### PHASE 3 — Catalyst: mã này chạy *vì cái gì*

**Vấn đề:** alert hiện tại nói "RVOL 66x, +85%" nhưng không nói **tại sao**.
Bạn phải tự mở Finviz đi tìm. Mà "tại sao" chính là thứ quyết định nên bỏ qua
hay để ý — cùng một +85%, do FDA approval khác hoàn toàn do pump vô cớ.

**Nguồn (đã kiểm chứng, có gói free):**

```
wss://stream.data.alpaca.markets/v1beta1/news
```

Đăng ký `{"action":"subscribe","news":["*"]}` rồi nhận từng bài. Trường
`symbols` là array mã liên quan, `headline`, `source` (thường là Benzinga),
`created_at`, `url`. Bạn **đã có `ALPACA_KEY`/`ALPACA_SECRET`** nên không cần
đăng ký gì thêm.

**`news.py` (module mới):** giữ websocket, lưu vào dict cuộn
`{sym: [(ts, headline, url), ...]}` chỉ giữ 4 giờ gần nhất và bỏ mã không có
trong universe (để không phình RAM).

**Tích hợp vào `render.py`** — một khối mới đặt **trên** khối WHY, vì tin tức
quan trọng hơn giải thích điểm:

```
📰 CATALYST
   "XYZ Announces FDA Clearance for..."
   Benzinga · 14 phut truoc · [doc]
```

**Phân loại từ khoá** để gắn icon và điều chỉnh điểm (đây là nơi giá trị thật
nằm, không phải ở việc hiển thị):

| Nhóm từ khoá | Icon | Ý nghĩa |
|---|---|---|
| `fda approval`, `clearance`, `phase 3`, `topline` | 🧬 | catalyst thật, bền |
| `contract`, `award`, `partnership`, `acquisition` | 🤝 | catalyst thật |
| `offering`, `pricing of`, `registered direct`, `atm` | 💧 | **pha loãng — trừ điểm mạnh** |
| `reverse split` | ⚠️ | tăng giá giả |
| `nasdaq notification`, `deficiency`, `delisting` | ⛔ | rủi ro cao |
| không có tin | ⚪ | chạy không có lý do → cẩn thận |

Nhóm 💧 đặc biệt quan trọng: một mã +80% kèm headline "Announces Pricing of
$15M Registered Direct Offering" là bẫy điển hình — nó vừa in thêm cổ phiếu.
`edgar.py` của bạn bắt được cái này *sau* khi 424B5 lên EDGAR, nhưng news
stream bắt được **sớm hơn nhiều**.

**Tiêu chí xong:** ≥70% alert có ít nhất một dòng catalyst hoặc nhãn ⚪ rõ ràng.

**Công sức:** ~200 dòng, cộng thời gian nuôi từ điển từ khoá dần theo thực tế.

---

### PHASE 4 — Dọn nợ kỹ thuật (làm khi có 2 tiếng rảnh)

Không thêm tính năng, chỉ để những phase sau đỡ đau.

**4a. Gộp `notifier.py` và `tgapi.py`.** Hiện có hai đường gửi Telegram song
song, `tg_send()` fallback từ cái này sang cái kia. Nó chạy được, nhưng nghĩa
là logic rate-limit và degradation HTML tồn tại ở hai nơi và sẽ lệch nhau.
Chọn `tgapi.py` làm đường chính, biến `notifier.py` thành *chỉ* lớp spool
(hàng đợi lúc mạng chết), bỏ phần format khỏi nó.

**4b. Viết test cho `scorer.py` và `render.py`.** Hai file này là nơi bug
lặng lẽ nhất — sai trọng số không crash, chỉ ra số vô nghĩa.

```
tests/
  test_scorer.py     # input cố định → điểm cố định, chặn regression
  test_render.py     # panel <pre> phải thuần ASCII, len < SAFE_LEN,
                     # HTML tag phải cân, không lọt tag lạ
  test_clock.py      # premarket/regular/afterhours/closed + biên DST
  test_edgar.py      # assess() với filing giả, kiểm cờ pha loãng
```

Chạy bằng `pytest -q`. Test 4b đáng giá nhất là **`render` với dữ liệu thiếu**:
`float_sh=None`, `cik=None`, `explain=""` — đó là chỗ hay `TypeError` giữa
phiên.

**4c. GitHub Actions.** Một file `.github/workflows/ci.yml` chạy `pytest` +
`python -m compileall` mỗi lần push. Bạn deploy bằng `git pull` nên bug syntax
push lên là service crash-loop; CI bắt trước.

**4d. Log có cấu trúc.** Hiện log là chuỗi tiếng Việt cho người đọc. Thêm một
file thứ hai `state/events.jsonl` ghi mỗi alert dạng JSON một dòng. Phase 1
cần cái này để phân tích, và `grep` trên log tiếng Việt sẽ không bao giờ đủ.

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
Tuần 1-2   Phase 1 (outcome tracking)   ← chạy nền, thu số liệu
           Phase 2 (halt feed)          ← xong trong một buổi tối
Tuần 3     Phase 4 (test + CI)          ← trong lúc chờ số liệu Phase 1
Tuần 4     Đọc bảng Phase 1 → chỉnh ALERT_SCORE và trọng số
Tuần 5-6   Phase 3 (catalyst/news)
Sau đó     Phase 5, 6, 7 tuỳ chỗ nào làm bạn khó chịu nhất
Để dành    Phase 8
```

Điểm quan trọng nhất của thứ tự này: **Phase 1 và 2 xong trong khoảng một
tuần công sức, nhưng Phase 1 cần vài tuần *thời gian* để tích dữ liệu.** Nên
viết nó trước, rồi làm việc khác trong lúc nó thu số liệu. Đừng đợi.

## Giấy phép

MIT — xem `LICENSE`.
