"""config.py - Moi nguong dieu chinh duoc cua phan swing, mot cho duy nhat.

Vi sao la .py chu khong phai .yaml - ba ly do cu the, khong phai thoi quen:

  - Repo nay co mot tinh chat co chu dinh: tang DO va tang LUU TRU thuan stdlib
    (bars.py, structure.py) nen selftest chay duoc tren may dev khong cai
    pandas. Them PyYAML la them dependency vao dung chuoi do, va no khong co
    trong requirements.txt.
  - backtest.py quet NHIEU bo nguong trong mot lan chay bang cach truyen
    `g=BO` (xem setups.py). Mot dict Python truyen duoc nhu tham so ham; mot
    file yaml thi phai doc lai roi ghi de bien toan cuc - va luc do hai lan
    quet trong cung process se anh huong nhau.
  - push.py day chinh cac dict duoi day len khoa `scanner:config`, nen bang
    "Config" tren dashboard hien NGUONG DANG CHAY chu khong phai mot ban copy
    co the lech. Mot file yaml doc tay cung lam duoc, nhung luc do co hai
    nguon su that.

Nguong cua setup BO/RV KHONG o day: chung nam trong dict `BO`/`RV` dau
setups.py, da dung mot cho va backtest.py sua truc tiep vao do. Chuyen chung
sang day chi de "cho gon" se lam backtest.py phai import vong.

Thuan stdlib, khong import gi -> bars.py va regime.py import duoc ma khong keo
theo thu vien nao.
"""
from __future__ import annotations

# ───────────────────────── ma chuan ─────────────────────────
# Ma do trang thai thi truong. Doi sang QQQ/RSP duoc, nhung doi thi bang
# `regime` cu va moi khong con so sanh duoc -> ghi ca `bench` vao tung dong.
BENCH = "SPY"

# 11 sector SPDR. Thu tu khong quan trong (sectors.py xep theo diem), nhung
# danh sach phai DU 11: thieu mot cai thi percentile cua 10 cai con lai lech,
# va khong co gi bao loi.
SECTOR_ETFS = ("XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU",
               "XLB", "XLRE", "XLC")

# Nhung ma PHAI co trong kho nen du prep.fetch_universe() co tra ve hay khong:
# universe cua prep.py loc theo screener co phieu, khong dam bao co ETF. Thieu
# SPY thi regime.py im lang tra None, va do la loai loi khong ai phat hien.
REGIME_SYMS = (BENCH,) + SECTOR_ETFS

# Sector phong thu. Chung len top 3 la tin hieu dong tien dang rut khoi rui ro;
# sectors.py canh bao rieng chuyen nay.
DEFENSIVE = ("XLP", "XLU")


# ───────────────────────── Stage 1: trang thai thi truong ─────────────────────────
REGIME = {
    # So phien de do do doc sma50: so sanh sma50 hom nay voi sma50 cua N phien
    # truoc.
    #
    # LUU Y: structure.metrics() dung 20 phien cho co phieu (`sma50_slope`).
    # Hai con so khac nhau la CO Y - o day do nhip cua ca thi truong nen can
    # nhay hon. Neu doi mot cai thi doc lai ca hai cho, dung gia dinh chung
    # phai bang nhau.
    "slope_win": 10,

    # Nguong phan loai do doc. +-0.5% tren 10 phien ~ +-12%/nam: du lon de
    # khong bi nhieu, du nho de bat duoc khuc chuyen huong som.
    "slope_up": 0.005,
    "slope_dn": -0.005,

    # Bien do: atr_pct cua phien cuoi so voi TRUNG BINH CUA CHINH NO tren
    # `vol_win` phien. So voi chinh no chu khong voi mot con so tuyet doi -
    # "ATR 1.2%" la cao hay thap phu thuoc vao thi truong nam do.
    "vol_win": 100,
    "vol_contract": 0.8,     # < 0.8x trung binh -> CONTRACTED
    "vol_expand": 1.3,       # > 1.3x trung binh -> EXPANDED

    # Toi thieu 210 nen: sma200 can 200, do doc can them `slope_win`, va trung
    # binh atr_pct 100 phien can 114. Duoi nguong nay measure() tra None chu
    # khong tra mot ket qua "gan dung" - mot regime tinh tren 150 nen se noi
    # sai ve sma200 ma khong co gi bao.
    "min_bars": 210,

    # So nen doc tu kho. Du du de tinh moi thu tren, va la tran tren de mot ma
    # co 3 nam lich su khong lam cham build().
    "load_n": 400,
}


# ───────────────────────── Stage 1: bang tra playbook ─────────────────────────
# (trend, vol) -> setup nao duoc phep + co vi the.
#
# `size`: 1.0 = full, 0.5 = half, 0.0 = KHONG mo vi the moi. `size == 0` la tin
# hieu may doc duoc duy nhat cho "dung ngoai" - dung no lam cong tac, khong
# doan qua `setups` rong.
#
# `setups`: ten setup trong setups.py. "SPIKE" la engine trong phien; de no o
# day de phan intraday (prompt 2) dung CUNG MOT bang nay thay vi tu nghi ra
# mot bang thu hai roi hai bang lech nhau.
#
# 12 dong, viet het ra thay vi tinh bang logic: bang tra cuu thi doc mot cai la
# biet, con ba dong if long nhau thi phai chay thu moi biet o trang thai nao
# bot lam gi.
#
# `note` la CHU CHO NGUOI DOC, khong phai comment: no di nguyen van vao tin
# nhan Telegram buoi sang va vao bang Config tren dashboard. Nen no co dau -
# giong `TXT` trong render.py. Mot ban ASCII thu hai o render_night.py se lech
# khoi ban nay dung mot lan, va luc do khong con biet ban nao dang chay.
PLAYBOOK: dict[tuple[str, str], dict] = {
    # UPTREND - 50 tren 200, gia tren 50, do doc khong giam.
    ("UPTREND", "CONTRACTED"): {
        "setups": ("BO", "LEAD", "SPIKE"), "size": 1.0,
        "note": "Trường hợp tốt nhất: nền chặt, breakout có chỗ để chạy."},
    ("UPTREND", "NORMAL"): {
        "setups": ("BO", "LEAD", "SPIKE"), "size": 1.0,
        "note": "Bình thường, đầy đủ setup."},
    ("UPTREND", "EXPANDED"): {
        "setups": ("BO", "LEAD", "SPIKE"), "size": 0.5,
        "note": "Biên độ nở rộng → stop phải rộng hơn → hạ cỡ vị thế."},

    # UPTREND_UNDER_STRESS - 50 van tren 200 nhung gia da mat 50, hoac do doc
    # dang giam. Khong mo vi the moi, chi quan ly cai dang co.
    ("UPTREND_UNDER_STRESS", "CONTRACTED"): {
        "setups": (), "size": 0.0,
        "note": "Chỉ quản lý vị thế đang có, không vào mới."},
    ("UPTREND_UNDER_STRESS", "NORMAL"): {
        "setups": (), "size": 0.0,
        "note": "Chỉ quản lý vị thế đang có, không vào mới."},
    ("UPTREND_UNDER_STRESS", "EXPANDED"): {
        "setups": (), "size": 0.0,
        "note": "Xu hướng yếu đi kèm biên độ nở rộng: đứng ngoài."},

    # RANGE - 50 duoi 200 nhung gia tren 50 va do doc dang len: dang hoi phuc,
    # chua thanh xu huong. Chi mean-reversion.
    ("RANGE", "CONTRACTED"): {
        "setups": ("RV",), "size": 0.5,
        "note": "Kênh giá hẹp: chỉ mua lại chứ không mua breakout."},
    ("RANGE", "NORMAL"): {
        "setups": ("RV",), "size": 0.5,
        "note": "Kênh giá: chỉ mean-reversion, nửa cỡ vị thế."},
    # O DAY LA O DANG TRANH LUAN NHAT CA BANG. Bien do no rong trong kenh gia
    # vua la luc mean-reversion tra tien nhieu nhat, vua la luc gap xuyen qua
    # stop. Chon dung ngoai vi mot alert bo lo re hon mot lenh bi gap.
    ("RANGE", "EXPANDED"): {
        "setups": (), "size": 0.0,
        "note": "Kênh giá + biên độ nở rộng = whipsaw hai chiều, đứng ngoài."},

    # DOWNTREND - khong co dong nao mo vi the long. Day la cong tac cung cua
    # prompt 2: mot tin nhan luc mo cua giai thich vi sao im lang, sau do chi
    # con alert stop cho vi the dang mo.
    ("DOWNTREND", "CONTRACTED"): {
        "setups": (), "size": 0.0,
        "note": "Xu hướng giảm: không mở vị thế mua, chỉ canh stop."},
    ("DOWNTREND", "NORMAL"): {
        "setups": (), "size": 0.0,
        "note": "Xu hướng giảm: không mở vị thế mua, chỉ canh stop."},
    ("DOWNTREND", "EXPANDED"): {
        "setups": (), "size": 0.0,
        "note": "Xu hướng giảm + biên độ nở rộng: tuyệt đối không mở vị thế mua."},
}

TRENDS = ("UPTREND", "UPTREND_UNDER_STRESS", "RANGE", "DOWNTREND")
VOLS = ("CONTRACTED", "NORMAL", "EXPANDED")


# ───────────────────────── Stage 2: xep hang sector ─────────────────────────
SECTORS = {
    # Ba cua so loi nhuan, tinh theo PHIEN chu khong theo ngay lich: 21 ~ 1
    # thang, 63 ~ 1 quy, 126 ~ nua nam. Diem tong = trung binh percentile cua
    # ba cua so, moi cua so mot phieu ngang nhau.
    #
    # Vi sao trung binh percentile chu khong trung binh loi nhuan: thang 4/2025
    # XLE +18% con ca ro +2% thi "trung binh loi nhuan" bien thanh cuoc do ve
    # mot ma. Percentile chi hoi "hon duoc bao nhieu ma khac", nen mot sector
    # bung no khong lam nhoe thu tu cua 10 sector con lai.
    "ret_wins": (21, 63, 126),

    # Ba co hieu xu huong. EMA21 nhanh (con dang chay hay khong), SMA50 cham
    # (xu huong trung han), do doc SMA50 cho biet xu huong dang manh len hay
    # yeu di. Ba cai nay KHONG vao diem tong - chung la bo loc de doc bang,
    # vi mot sector co the dung dau bang chi nho no giam it nhat.
    "ema_win": 21,
    "sma_win": 50,
    "slope_win": 10,         # giong REGIME["slope_win"], cung nhip do

    # Top may sector duoc phep lay co phieu (Stage 3).
    "top_n": 3,

    # So phien de doi chieu thay doi hang. 5 phien = tuan truoc, 21 = thang
    # truoc. Doc tu bang `sector_rank`, nen can co lich su -> xem --backfill.
    "change_wins": (5, 21),

    # Toi thieu 140 phien: ret126 can 127 nen, do doc SMA50 can 50 + 10 = 60.
    # Duoi nguong nay tra None chu khong bo bot cua so - mot bang xep hang
    # thieu cot 126d nhung van in ra la loai loi khong ai doc ky de phat hien.
    "min_bars": 140,
    "load_n": 400,

    # So phien lich su bieu do tren dashboard doc ve.
    "hist_days": 90,
}


# ───────────────────────── Stage 3: co phieu dan dat ─────────────────────────
# Nguong cua profile LEAD trong setups.py. De o day chu khong o setups.py (noi
# BO/RV dang o) vi mot ly do cu the: bang "Config" tren dashboard doc
# config.snapshot(), nen moi nguong o day la nguong ANH XEM DUOC. setups.py chi
# lam `LEAD = config.LEAD`, va lead_candidate(m, g=...) van nhan dict tuy y de
# backtest.py quet nhieu bo nguong nhu voi BO/RV.
LEAD: dict = {
    # San chat luong. Day la nhung con so lam Stage 3 khac han scanner trong
    # phien hien tai: co phieu $3 tang 12% khong bao gio di qua duoc muc nay.
    "min_px": 10.0,
    "min_dollar_vol": 20_000_000.0,   # adv50 (co phieu) x gia. ~$20M/phien la
                                      # nguong co to chuc tham gia.

    # Thanh khoan hom nay so voi binh thuong. >1.5 = dang co nguoi de y den no.
    "min_rvol": 1.5,

    # Bien do. Duoi 2% thi mot cu chay 3 ATR cung khong du bu phi va truot gia;
    # tren 6% thi stop hop ly rong den muc co vi the phai nho lai den vo nghia.
    # Day la khoang "du dong de kiem tien, du yen de dat stop".
    "min_atr_pct": 0.02,
    "max_atr_pct": 0.06,

    # Suc manh tuong doi so voi SPY, tinh bang loi nhuan VUOT TROI (chenh lech
    # diem phan tram), duong tren CA HAI cua so. Mot cua so co the la may; hai
    # cua so cung duong thi kho la may hon.
    "min_rs21": 0.0,
    "min_rs63": 0.0,

    # Phai o gan dinh 52 tuan. Co phieu dan dat lam dinh moi; ma cach dinh 30%
    # dang hoi phuc thi la mot cau chuyen khac (do la viec cua RV).
    "max_off_high": 0.15,

    # Chuan hoa diem RS: vuot SPY bao nhieu thi duoc diem toi da. 15pp trong 21
    # phien va 30pp trong 63 phien la muc "dan dat that", tren nua khong con
    # phan biet duoc gi nen cat tran.
    "rs_cap21": 0.15,
    "rs_cap63": 0.30,

    # Tran danh sach. 5 moi sector va 10 tong: danh sach dai hon thi khong con
    # la "co phieu dan dat", va trong phien thi khong theo noi 30 ma bang mat.
    "per_sector": 5,
    "max_total": 10,
}

# ───────────────────────── Stage 3: file thanh phan sector ─────────────────
HOLDINGS = {
    # Tran tuoi cua holdings/sector_holdings.csv. Thanh phan sector doi vai lan
    # mot nam; file cu hon nua nam nghia la dang tim co phieu dan dat trong mot
    # ro khong con dung, va khong co gi trong so lieu cho thay dieu do.
    "max_age_days": 180,
}


# ───────────────────────── Stage 4: chuoi chay + dashboard ─────────────────
NIGHTLY = {
    # Lan chay thanh cong gan nhat cu hon bao nhieu GIO thi dashboard treo bang
    # "so lieu cu". 36 chu khong phai 24: cron chay 08:00 ET moi ngay lam viec,
    # nen sang thu Hai ban ghi moi nhat la sang thu Sau - hon 48 gio - va mot
    # nguong 24 gio se bao dong moi thu Hai cho den khi khong ai doc banner nua.
    #
    # 36 gio bat dung cai can bat: BO MOT ngay lam viec. Cuoi tuan thi bo qua
    # han (xem `stale` trong push.status_payload): thu Bay va Chu Nhat khong
    # tinh la tre, vi khong co phien nao de tre.
    "stale_hours": 36,

    # So phien ve trong bieu do lich su hang nganh tren dashboard. Khac
    # SECTORS["hist_days"]: cai kia la luong du lieu GIU trong DB, cai nay la
    # luong VE ra. Ve 3 nam vao mot bieu do cao 220px thi 11 duong thanh mot
    # dam mau, va cau hoi "nganh nao dang len" khong con doc duoc nua.
    "chart_days": 90,

    # So dong toi da trong bang danh sach theo doi day len dashboard. Stage 3 da
    # chan o 10 ma; con so nay chi la chot an toan neu nguong bi ha xuong.
    "watch_top": 40,
}


# ───────────────────────── Ke hoach lenh (trigger / stop / co) ─────────────
# Bang `candidates` truoc day chi co `pivot`: mot diem VAO, khong co stop va
# khong co co vi the. Tuc la "ke hoach" chua ton tai - trong phien van phai tu
# nghi ra stop, dung luc khong nen nghi gi. plan.py tinh ba con so nay tu nen
# QUYET DINH cua dem truoc, va phan intraday chi doc chu khong tinh lai.
PLAN = {
    # Diem vao = max(pivot cua nen tich luy, dinh cua NEN QUYET DINH) x (1+buf).
    #
    # Vi sao lay ca dinh nen quyet dinh chu khong chi pivot: mot ma LEAD co the
    # khong co nen tich luy nao (pivot = None) - no di qua bo san chat luong,
    # khong qua mot hinh mau. Luc do "vuot dinh hom qua" la dinh nghia ro rang
    # va tinh duoc, thay vi mot con so bia ra.
    #
    # buf: phai VUOT han chu khong phai cham vao. 0.1% ~ mot tick tren $50, du
    # de khong bi mot lan quote nhay len dung gia dinh coi la da vuot.
    "trigger_buf": 0.001,

    # Stop = trigger - stop_atr x ATR(14). Tinh bang ATR chu khong bang % co
    # dinh: 5% duoi gia la stop rong o ma ATR 2% va la stop trong nhieu o ma
    # ATR 6%. Cung mot con so % se bi cat lien tuc o ma nay va khong bao gio
    # duoc dung o ma kia.
    #
    # 1.5 ATR: duoi 1 ATR thi mot phien bien dong binh thuong da cat; tren 2
    # ATR thi co vi the phai nho den muc mot cu chay dung huong cung khong doi
    # duoc gi. Voi LEAD (ATR 2-6%) khoang nay ra stop rong 3-9%.
    #
    # Mot lua chon khac da can nhac va bo: dat stop duoi sma20. Cau truc hon,
    # nhung khoang cach tu gia den sma20 thay doi tuy y theo tung ma nen rui ro
    # moi lenh khong con bang nhau - va luc do co vi the khong con so sanh duoc.
    "stop_atr": 1.5,

    # Rui ro moi lenh, tinh bang % VON. Day la con so duy nhat quyet dinh co vi
    # the; stop rong hon thi vi the nho hon, tu dong.
    #   size_pct = risk_pct / (khoang cach stop tinh theo %) x co-vi-the-playbook
    # 0.75%: 10 lenh cung sai mot luc la -7.5%, chua den muc phai xu ly khac di.
    "risk_pct": 0.0075,

    # Tran mot vi the, tinh bang % von. Can thiet vi cong thuc tren: stop 3%
    # cho ra 25% von vao mot ma, va luc do mot cai gap qua dem lam hong ca thang
    # du stop "chi" 3%. Stop bao ve khoi bien dong, khong bao ve khoi gap.
    #
    # ⚠️ TRAN NAY BINH THUONG XUYEN HON LA TUONG: no chan khi
    #   stop_pct < risk_pct / max_pos_pct = 0.0075 / 0.20 = 3.75%
    # tuc la voi moi ma co ATR duoi ~2.5% - va LEAD nhan ATR tu 2%. Nen mot
    # phan danh sach chay o dung 20% von voi rui ro THUC SU duoi 0.75%. Do la
    # phia an toan, nhung no co nghia: doi con so nay la doi co vi the cua ca
    # mot nhom ma cung luc, khong phai vai ma. Xem test_plan.py
    # test_ma_bien_do_thap_nhat_cua_LEAD_bi_tran_chan.
    #
    # CHUA CO: tran tong (max_total_pct). 10 ma x 20% = 200% von, tuc la bang
    # nguong o day khong mot minh ngan duoc viec vao qua nhieu. plan.py khong
    # biet portfolio nen khong the tu chan; cho dung la cong intraday, sau khi
    # doc `scanner:positions`. Ghi lai o day de khong ai tuong 20% la da du.
    "max_pos_pct": 0.20,

    # Muc tieu = trigger + rr x (trigger - stop). Khong bat buoc, chi de tin
    # nhan co du ba con so cua mot ke hoach hoan chinh.
    "rr": 2.0,
}


# ───────────────────────── Phan trong phien (prompt 2) ─────────────────────
# Phan intraday DOC lai file nay chu khong co config rieng: yeu cau cua prompt
# 2 la "same config lookup table as nightly". PLAYBOOK o tren da co "SPIKE"
# trong cac o UPTREND chinh vi ly do do - de khong sinh ra mot bang thu hai roi
# hai bang lech nhau ma khong ai biet.
INTRADAY = {
    # ─── SAN CHAT LUONG ───
    # ⚠️ DAY LA SAN, KHONG PHAI NGUONG DIEU CHINH. Ha bat ky con so nao duoi
    # day la moi lai dung van de da lam scanner cu vo dung: tin nhan toan co
    # phieu gia thap, spread rong, khong co to chuc tham gia, rui ro gap. Neu
    # mot ngay nao thay "it alert qua" thi do la thiet ke dang chay dung, khong
    # phai san dat qua cao.
    "min_px": 10.0,
    "min_dollar_vol": 20_000_000.0,   # adv50 x gia, giong LEAD
    "min_mktcap": 2_000_000_000.0,

    # Von hoa lay tu yfinance cho DUNG cac ma trong danh sach (<= 10 ma moi
    # dem), cache vao base.mktcap. KHONG dung float_sh x gia roi goi no la von
    # hoa: do la von hoa FLOAT, lech han khi noi bo giu nhieu co phan.
    # Cache cu hon TTL thi lay lai. Von hoa doi cham nen 7 ngay la du, va nguong
    # nay giu so request o muc mot chu so moi dem.
    "mktcap_ttl_days": 7,

    # San niem yet. NYSE + NASDAQ la yeu cau goc cua prompt 2; ARCA them vao
    # theo quyet dinh cua ban. Hai ly do de them ma khong lam ro ri tro lai:
    #   - Toan bo ETF niem yet o ARCA, ke ca SPY va 11 ma XL*. Loai ARCA thi
    #     Stage 1-2 phai co mot ngoai le rieng, va mot ngoai le la mot cho de
    #     bug tron vao.
    #   - Rat it cong ty van hanh niem yet CHINH o ARCA, nen no khong phai cua
    #     vao cua co phieu rac.
    # AMEX thi KHONG co trong danh sach - do moi la cho co phieu nho thanh
    # khoan thap thuc su nam, va prep.fetch_universe() hien dang nhan no.
    "exchanges": ("NYSE", "NASDAQ", "ARCA"),

    # Niem yet duoi ~12 thang thi loai: chua co lich su de biet no xu su the nao
    # khi thi truong xau. Do bang SO NEN trong kho (250 phien ~ 12 thang) vi
    # khong co nguon nao cho ngay IPO. Xem watchlist.py: neu kho nen chua du sau
    # thi ghi "khong kiem duoc" chu khong loai sach danh sach.
    "min_listed_days": 250,

    # Spread toi da, tinh bang % gia. Prompt 2 ghi "if available" - va dung la
    # thuong khong co: yfinance tra bid/ask = 0 ngoai gio. Khong co thi BO QUA
    # va dong dau vao tin nhan la chua kiem, khong bao giờ im lang coi nhu dat.
    "max_spread_pct": 0.0015,

    # ─── LUAT TIER 1 ───
    # RVol da DIEU CHINH THEO GIO (vprofile.py), khong phai vol/adv tho. Luc
    # 10:00 sang mot ma binh thuong moi chay ~15% khoi luong ngay, nen vol/adv
    # tho luc do la 0.15 va khong bao giờ vuot 1.5 - bo loc tho khong bao giờ
    # kich trong nua dau phien, va do la loi im lang.
    "min_rvol_adj": 1.5,

    # Gap mo cua vuot nguong nay -> alert Tier 1, ke ca khi ke hoach chua kich:
    # mot ma gap +4% da di qua diem vao truoc khi ban kip lam gi, va gap -4% co
    # the da xuyen stop. Ca hai deu la thong tin phai biet ngay, khong phai cuoi
    # phien.
    "gap_alert": 0.03,

    # ─── CHONG SPAM ───
    # "Tha bo lo mot alert hon la nhan 40 cai. Im lang la mac dinh."
    "cooldown_sec": 900,        # 15 phut moi ma (truoc day 540)
    "open_mute_min": 10,        # bo 10 phut dau phien (truoc day 5)
    "tier2_cap": 8,             # tran Tier 2 moi phien; Tier 1 KHONG cap
    "poll_sec": 60,

    # ─── VI THE DANG MO ───
    # Anh chup vi the do app lux-lookthrough day len khoa `scanner:positions`
    # moi lan luu portfolio. Cu hon nguong nay thi noi ro trong tin nhan mo
    # phien. 30 gio chu khong 24: luu luc chieu hom truoc van con dung sang hom
    # sau, va cuoi tuan thi khong co phien nao de canh stop.
    "pos_max_age_h": 30,
}


def playbook_rows() -> list[dict]:
    """PLAYBOOK dang JSON duoc: khoa tuple khong qua duoc json.dumps.

    Dung boi push.py (khoa `scanner:thresholds` - KHONG phai `scanner:config`,
    khoa do phia app giu; xem ghi chu truoc thresholds_payload()) va boi
    --dry-run. Thu tu theo
    TRENDS x VOLS chu khong theo thu tu dict, de bang tren dashboard on dinh.
    """
    return [{"trend": t, "vol": v,
             "setups": list(PLAYBOOK[(t, v)]["setups"]),
             "size": PLAYBOOK[(t, v)]["size"],
             "note": PLAYBOOK[(t, v)]["note"]}
            for t in TRENDS for v in VOLS]


def snapshot() -> dict:
    """Toan bo config dang JSON duoc, de day len `scanner:config`.

    Day CHINH cac dict dang chay chu khong phai mot ban mo ta viet tay: bang
    Config tren dashboard sai thi phai la loi hien thi, khong bao gio duoc la
    "file mo ta da cu".
    """
    return {
        "bench": BENCH,
        "sector_etfs": list(SECTOR_ETFS),
        "defensive": list(DEFENSIVE),
        "regime": dict(REGIME),
        "sectors": {k: list(v) if isinstance(v, tuple) else v
                    for k, v in SECTORS.items()},
        "lead": dict(LEAD),
        "holdings": dict(HOLDINGS),
        "nightly": dict(NIGHTLY),
        "plan": dict(PLAN),
        "intraday": {k: list(v) if isinstance(v, tuple) else v
                     for k, v in INTRADAY.items()},
        "playbook": playbook_rows(),
    }
