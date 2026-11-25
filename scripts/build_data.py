import sys, os, numpy as np, pandas as pd
src, out = sys.argv[1], sys.argv[2]; os.makedirs(out, exist_ok=True)
df = pd.read_csv(src)
MM = {m:i for i,m in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"],1)}
df["arrival_date"] = pd.to_datetime(dict(year=df.arrival_date_year, month=df.arrival_date_month.map(MM), day=df.arrival_date_day_of_month))
df["nights"] = df.stays_in_weekend_nights + df.stays_in_week_nights
df["booking_date"] = df.arrival_date - pd.to_timedelta(df.lead_time, unit="D")
df["status_date"] = pd.to_datetime(df.reservation_status_date)
n0 = len(df)
df = df[(df.market_segment != "Undefined") & (df.distribution_channel != "Undefined") & (df.market_segment != "Complementary")]
df = df[(df.adr >= 0) & (df.adr <= 1000) & (df.nights > 0)].copy()
print("rows kept", len(df), "of", n0)
df["booking_id"] = np.arange(1, len(df)+1)

START, MID, END = pd.Timestamp("2015-09-01"), pd.Timestamp("2016-09-01"), pd.Timestamp("2017-08-31")
P1, P2 = "Year 1 (Sep 2015 to Aug 2016)", "Year 2 (Sep 2016 to Aug 2017)"
def period(d): return np.where(d < MID, P1, P2)
CAP = {"City Hotel": 226, "Resort Hotel": 187}
COMM = {"Online TA": .18, "Offline TA/TO": .20, "Groups": .10, "Corporate": .05, "Direct": .03, "Aviation": .00}
SEG_GROUP = {"Online TA": "Online travel agency", "Offline TA/TO": "Tour operator", "Groups": "Groups",
             "Corporate": "Corporate", "Direct": "Direct", "Aviation": "Corporate"}

# ---------- nightly stays (actualized, not canceled) ----------
nc = df[df.is_canceled == 0]
st = nc.loc[nc.index.repeat(nc.nights)].copy()
st["stay_date"] = st.arrival_date + pd.to_timedelta(st.groupby(level=0).cumcount(), unit="D")
st = st[(st.stay_date >= START) & (st.stay_date <= END)]

seg = st.groupby(["hotel","stay_date","market_segment"]).agg(rooms_sold=("adr","size"), room_revenue=("adr","sum")).reset_index()
seg["channel"] = seg.market_segment.map(SEG_GROUP)
seg["commission_rate"] = seg.market_segment.map(COMM)
seg["commission_cost"] = (seg.room_revenue * seg.commission_rate).round(2)
seg["net_revenue"] = (seg.room_revenue - seg.commission_cost).round(2)
seg["room_revenue"] = seg.room_revenue.round(2)
seg["period"] = period(seg.stay_date)
seg = seg[["hotel","stay_date","period","channel","market_segment","rooms_sold","room_revenue","commission_rate","commission_cost","net_revenue"]]

# ---------- daily KPIs (hotel x date, with capacity) ----------
dates = pd.date_range(START, END, freq="D")
grid = pd.MultiIndex.from_product([list(CAP), dates], names=["hotel","stay_date"]).to_frame(index=False)
d = seg.groupby(["hotel","stay_date"]).agg(rooms_sold=("rooms_sold","sum"), room_revenue=("room_revenue","sum"), net_revenue=("net_revenue","sum")).reset_index()
daily = grid.merge(d, how="left").fillna({"rooms_sold":0,"room_revenue":0,"net_revenue":0})
daily["rooms_available"] = daily.hotel.map(CAP)
daily["period"] = period(daily.stay_date)
daily["period_month_no"] = ((daily.stay_date.dt.month - 9) % 12) + 1   # Sep = 1 ... Aug = 12
daily["month_name"] = daily.stay_date.dt.strftime("%b")
daily["day_of_week"] = daily.stay_date.dt.strftime("%a")
daily = daily[["hotel","stay_date","period","period_month_no","month_name","day_of_week","rooms_available","rooms_sold","room_revenue","net_revenue"]]
daily["rooms_sold"] = daily.rooms_sold.astype(int)

# ---------- bookings (incl. canceled) ----------
b = df[(df.arrival_date >= START) & (df.arrival_date <= END)].copy()
b["period"] = period(b.arrival_date)
b["channel"] = b.market_segment.map(SEG_GROUP)
bins = [-1,0,7,30,90,180,365,10_000]; labels = ["Same day","1-7 days","8-30 days","31-90 days","91-180 days","181-365 days","Over 1 year"]
b["lead_time_band"] = pd.cut(b.lead_time, bins=bins, labels=labels).astype(str)
b["lead_time_band_order"] = pd.cut(b.lead_time, bins=bins, labels=range(1,8)).astype(int)
b["cancel_date"] = np.where(b.is_canceled == 1, b.status_date.dt.strftime("%Y-%m-%d"), "")
b["room_nights"] = b.nights
b["booked_revenue"] = (b.adr * b.nights).round(2)
bk = b[["booking_id","hotel","period","arrival_date","booking_date","lead_time","lead_time_band","lead_time_band_order","room_nights","adr","booked_revenue",
        "channel","market_segment","distribution_channel","deposit_type","customer_type","is_repeated_guest","country","is_canceled","cancel_date"]].copy()
bk["adr"] = bk.adr.round(2)
for c in ["arrival_date","booking_date"]: bk[c] = bk[c].dt.strftime("%Y-%m-%d")

# ---------- booking pace (room nights on the books by days before arrival) ----------
MAXD = 365
pace_rows = []
bb = b.copy()
cd = pd.to_datetime(bb.cancel_date.replace("", np.nan))
bb["cancel_dba"] = (bb.arrival_date - cd).dt.days   # days before arrival when canceled
groups = list(bb.groupby(["hotel","period"])) + [(("All hotels", per), g) for per, g in bb.groupby("period")]
for (h, p), g in groups:
    arr = np.zeros(MAXD + 2)
    lt = np.minimum(g.lead_time.values, MAXD)
    hi = lt                                          # on books from dba=lead_time down to...
    lo = np.where(g.is_canceled.values == 1, np.clip(g.cancel_dba.fillna(0).values + 1, 0, MAXD + 1), 0)
    w = g.nights.values
    for l, h_, n in zip(lo, hi, w):
        if l <= h_: arr[int(l)] += n; arr[int(h_) + 1] -= n
    on = np.cumsum(arr)[:MAXD+1]
    final = g.loc[g.is_canceled == 0, "nights"].sum()
    for dba in range(0, MAXD + 1):
        pace_rows.append((h, p, dba, int(round(on[dba])), round(on[dba] / final, 4)))
pace = pd.DataFrame(pace_rows, columns=["hotel","period","days_before_arrival","room_nights_on_books","pct_of_final"])

# ---------- RevPAR bridge (Year 1 -> Year 2) ----------
def kpi(x):
    occ = x.rooms_sold.sum() / x.rooms_available.sum(); adr = x.room_revenue.sum() / x.rooms_sold.sum()
    return occ, adr, occ * adr
steps = []
for h, g in [("All hotels", daily)] + list(daily.groupby("hotel")):
    o1, a1, r1 = kpi(g[g.period == P1]); o2, a2, r2 = kpi(g[g.period == P2])
    occ_eff = (o2 - o1) * a1; rate_eff = (a2 - a1) * o2
    s = [("Year 1 RevPAR", r1, 0, r1, "total"), ("Occupancy effect", occ_eff, r1, r1 + occ_eff, "change"),
         ("Rate effect", rate_eff, r1 + occ_eff, r1 + occ_eff + rate_eff, "change"), ("Year 2 RevPAR", r2, 0, r2, "total")]
    for i, (name, val, start, end, kind) in enumerate(s, 1):
        steps.append((h, i, name, round(val, 2), round(start, 2), round(end, 2), kind))
bridge = pd.DataFrame(steps, columns=["hotel","step_order","step","value","bar_start","bar_end","step_type"])

comm = pd.DataFrame([(k, SEG_GROUP[k], v) for k, v in COMM.items()], columns=["market_segment","channel","assumed_commission_rate"])
cap = pd.DataFrame([(k, v) for k, v in CAP.items()], columns=["hotel","rooms"])

daily["stay_date"] = daily.stay_date.dt.strftime("%Y-%m-%d"); seg["stay_date"] = seg.stay_date.dt.strftime("%Y-%m-%d")
for name, t in [("daily_kpis", daily), ("channel_nights", seg), ("bookings", bk), ("booking_pace", pace), ("revpar_bridge", bridge),
                ("commission_rates", comm), ("hotel_rooms", cap)]:
    t.to_csv(f"{out}/{name}.csv", index=False); print(name, t.shape)
