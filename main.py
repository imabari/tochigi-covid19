import datetime
import re
import argparse
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

import simplejson as json

# プログラム引数解析
ap = argparse.ArgumentParser()
ap.add_argument("--output", "-o", default="./data/data.json")
ap.add_argument('--deaths', "-d", type=int, default=0)
args = ap.parse_args()

JST = datetime.timezone(datetime.timedelta(hours=+9), "JST")

dt_now = datetime.datetime.now(JST)
dt_update = dt_now.strftime("%Y/%m/%d %H:%M")

data = {"lastUpdate": dt_update}

# データラングリング

url = "http://www.pref.tochigi.lg.jp/e04/welfare/hoken-eisei/kansen/hp/coronakensahasseijyoukyou.html"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64; Trident/7.0; rv:11.0) like Gecko",
}

r = requests.get(url, headers=headers)

r.raise_for_status()

soup = BeautifulSoup(r.content, "html.parser")

# 新型コロナウイルス感染症検査件数

# inspections_summary
tag_kensa = soup.find("a", text=re.compile("^新型コロナウイルス感染症検査件数.+エクセル"))

link_kensa = urljoin(url, tag_kensa.get("href"))

df_kensa = pd.read_excel(link_kensa, header=[1, 2])
df_kensa.columns = df_kensa.columns.to_flat_index()
df_kensa.rename(columns={("検査日", "Unnamed: 0_level_1"): "検査日"}, inplace=True)

flg_is_serial = df_kensa["検査日"].astype('str').str.isdigit()

# 日付のシリアルと文字対応
if flg_is_serial.sum():

    fromSerial = pd.to_datetime(df_kensa.loc[flg_is_serial, "検査日"].astype(float), unit="D", origin=pd.Timestamp("1899/12/30"))
    fromString = pd.to_datetime(df_kensa.loc[~flg_is_serial, "検査日"])

    df_kensa["検査日"] = pd.concat([fromString, fromSerial])

df_kensa.set_index("検査日", inplace=True)

df_kensa = df_kensa.astype("Int64").fillna(0)

df_kensa.sort_index(inplace=True)

df_kensa["日付"] = df_kensa.index.strftime("%Y-%m-%d")

# 委託分を合算
df_kensa[("検査件数", "栃木県")] += df_kensa[("検査件数", "県委託分")]
df_kensa[("検査件数", "宇都宮市")] += df_kensa[("検査件数", "市委託分")]

df_insp_sum = df_kensa.loc[:, ["日付", ("検査件数", "栃木県"), ("検査件数", "宇都宮市")]]

data["inspections_summary"] = {
    "data": df_insp_sum.values.tolist(),
    "date": dt_update,
}

# 栃木県における新型コロナウイルス感染症の発生状況一覧

tag_kanja = soup.find("a", text=re.compile("^栃木県における新型コロナウイルス感染症の発生状況一覧.+エクセル"))

link_kanja = urljoin(url, tag_kanja.get("href"))

df_kanja = pd.read_excel(link_kanja, header=1, skipfooter=2)

df_kanja.loc[:, ["番号", "年代", "性別", "居住地"]] = df_kanja.loc[
    :, ["番号", "年代", "性別", "居住地"]
].fillna(method="ffill")
df_kanja["番号"] = df_kanja["番号"].astype(int)

df_kanja.rename(columns={"退院･退所日": "退院日"}, inplace=True)

# 備考内に削除がある場合は除外
df_kanja["備考"] = df_kanja["備考"].fillna("").astype(str)
df_kanja = df_kanja[~df_kanja["備考"].str.contains("削除")]

# 再陽性を削除
df_kanja.drop_duplicates(subset="番号", keep="first", inplace=True)

df_kanja["陽性確認日"] = df_kanja["陽性確認日"].apply(
    lambda date: pd.to_datetime(date, unit="D", origin=pd.Timestamp("1899/12/30"))
)
df_kanja["退院日"] = pd.to_numeric(df_kanja["退院日"], errors="coerce")
df_kanja["退院日"] = df_kanja["退院日"].apply(
    lambda date: pd.to_datetime(date, unit="D", origin=pd.Timestamp("1899/12/30"))
)
df_kanja["退院"] = df_kanja["退院日"].dt.strftime("%Y-%m-%d")
df_kanja["状態"] = "入院中"
df_kanja["状態"] = df_kanja["状態"].where(df_kanja["退院日"].isnull(), "退院")

## main_summary

sr_situ = df_kanja["状態"].value_counts()
sr_situ = sr_situ.reindex(["入院中", "退院", "死亡"], fill_value=0)

data["main_summary"] = {
    "attr": "検査実施人数",
    "value": int(df_kensa[("累積検査件数", "合計")].tail(1)),
    "children": [
        {
            "attr": "陽性患者数",
            "value": len(df_kanja),
            "children": [
                {"attr": "入院中", "value": int(sr_situ["入院中"])},
                {"attr": "退院", "value": int(sr_situ["退院"]) - args.deaths},
                {"attr": "死亡", "value": int(sr_situ["死亡"]) + args.deaths},
            ],
        }
    ],
}

## patients

df_kanja["リリース日"] = df_kanja["陽性確認日"].dt.strftime("%Y-%m-%d")

df_patients = df_kanja.loc[:, ["番号", "リリース日", "居住地", "年代", "性別", "退院"]]

data["patients"] = {
    "data": df_patients.to_dict(orient="records"),
    "date": dt_update,
}

## patients_summary

ser_patients_sum = df_kanja["陽性確認日"].value_counts().sort_index()

if df_kensa.index[-1] > ser_patients_sum.index[-1]:
    ser_patients_sum[df_kensa.index[-1]] = 0

ser_patients_sum.sort_index(inplace=True)

df_patients_sum = pd.DataFrame({"小計": ser_patients_sum.asfreq("D", fill_value=0)})
df_patients_sum["日付"] = df_patients_sum.index.strftime("%Y-%m-%d")

data["patients_summary"] = {
    "data": df_patients_sum.loc[:, ["日付", "小計"]].values.tolist(),
    "date": dt_update,
}

with open(args.output, "w", encoding="utf-8") as fw:
    json.dump(data, fw, ignore_nan=True, ensure_ascii=False, indent=4)
