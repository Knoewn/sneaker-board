#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
球鞋资讯看板 —— 每日数据抓取脚本

产出两个文件（内容相同，格式不同）：
  data.js   —— 挂载到 window.SNEAKER_DATA，供 file:// 直接双击打开 HTML 时使用
  data.json —— 标准 JSON，供后续换成 http(s) 服务后由 fetch() 读取

可用数据源（已实测可抓，无需代理/无 Cloudflare 拦截）：
  发售日历  Sole Retriever  列表页 JSON-LD    鞋名/品牌/SKU/发售日/发售价/鞋图/评分
  新闻资讯  Hypebeast 中文  RSS
  新闻资讯  Hypebeast 英文  RSS
  新闻资讯  Nice Kicks      RSS
  新闻资讯  Highsnobiety    RSS

抓不到的字段及其原因（写入「—」，不编造）：
  市场价      StockX / GOAT 403，需要官方 API key 或住宅代理
  尺码范围    列表页 JSON-LD 无此字段，详情页被 Cloudflare 挡（403）
  购买渠道    同上，需要渲染详情页
  得物/识货   接口需签名，无法直连

依赖：pip install requests feedparser
"""

import json
import re
import html as html_lib
import os
import sys
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    sys.exit("缺少依赖，请先执行： pip install requests feedparser")

import xml.etree.ElementTree as ET

# ---------------------------------------------------------------- 配置

HERE = os.path.dirname(os.path.abspath(__file__))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"}

RELEASE_PAGES = [
    "https://www.soleretriever.com/sneaker-release-dates",
    "https://www.soleretriever.com/sneaker-release-dates?page=2",
    "https://www.soleretriever.com/sneaker-release-dates?page=3",
]

NEWS_FEEDS = [
    {"name": "Hypebeast 中文", "url": "https://hypebeast.com/zh/footwear/feed", "lang": "zh"},
    {"name": "Hypebeast", "url": "https://hypebeast.com/footwear/feed", "lang": "en"},
    {"name": "Nice Kicks", "url": "https://www.nicekicks.com/feed/", "lang": "en"},
    {"name": "Highsnobiety", "url": "https://www.highsnobiety.com/feeds/rss", "lang": "en"},
]

# 球鞋相关性关键词：命中其一才保留，过滤掉泛时尚/无关资讯
SNEAKER_KW = [
    "sneaker", "sneakers", "shoe", "shoes", "footwear", "runner", "runners",
    "running", "trainer", "trainers", "dunk", "yeezy", "jordan", "air jordan",
    "air max", "air force", "air zoom", "blazer", "cortez", "kobe", "lebron",
    "pegasus", "gt cut", "new balance", "adidas", "nike", "asics", "puma",
    "reebok", "converse", "vans", "crocs", "hoka", "saucony", "salomon",
    "onitsuka", "on running", "air force 1", "af1", "sb dunk", "retro og",
    "球鞋", "鞋款", "跑鞋", "板鞋", "运动鞋", "复刻", "配色", "联名", "发售",
    "aj", "dunk", "yeezy", "jordan", "nike", "adidas", "new balance", "asics",
    "converse", "vans", "reebok", "crocs", "puma", "hoka", "saucony", "salomon",
]


def is_sneaker(title: str, body: str) -> bool:
    text = (title + " " + body).lower()
    return any(kw in text for kw in SNEAKER_KW)


def detect_brands(title: str, body: str):
    """从新闻标题/正文里识别涉及到的球鞋品牌（用于品牌筛选联动）"""
    text = (title + " " + body).lower()
    found = set()
    for kw, disp in BRAND_ALIAS.items():
        if kw in text:
            found.add(disp)
    return sorted(found)

KEEP_PAST_DAYS = 21      # 已发售鞋款最多往前保留多少天
NEWS_PER_FEED = 12       # 每个源取多少条
TIMEOUT = 30

# 品牌显示名归一化（统一成英文大写，和看板侧栏一致）
BRAND_ALIAS = {
    "jordan": "JORDAN", "jordan brand": "JORDAN", "air jordan": "JORDAN",
    "nike": "NIKE", "nike sb": "NIKE", "nike sportswear": "NIKE",
    "adidas": "ADIDAS", "adidas originals": "ADIDAS", "yeezy": "ADIDAS",
    "new balance": "NEW BALANCE", "asics": "ASICS", "puma": "PUMA",
    "reebok": "REEBOK", "converse": "CONVERSE", "vans": "VANS",
    "salomon": "SALOMON", "hoka": "HOKA", "on running": "ON RUNNING",
    "crocs": "CROCS", "timberland": "TIMBERLAND",
    "under armour": "UNDER ARMOUR", "karhu": "KARHU", "saucony": "SAUCONY",
    "mizuno": "MIZUNO", "birkenstock": "BIRKENSTOCK", "ugg": "UGG",
    "skechers": "SKECHERS", "clarks": "CLARKS", "autry": "AUTRY",
    "maison margiela": "MAISON MARGIELA", "celine": "CELINE",
    "louis vuitton": "LOUIS VUITTON", "dior": "DIOR", "balenciaga": "BALENCIAGA",
    "gucci": "GUCCI", "prada": "PRADA", "amiri": "AMIRI", "fear of god": "FEAR OF GOD",
}


def norm_brand(raw: str) -> str:
    if not raw:
        return "OTHER"
    key = raw.strip().lower()
    return BRAND_ALIAS.get(key, raw.strip().upper())


def today_local():
    return datetime.now().date()


def get(url: str, timeout: int = TIMEOUT) -> str:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text


# ---------------------------------------------------------------- 发售日历

LD_JSON_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S)


def find_item_list(graph):
    """在 JSON-LD @graph 里找出 ItemList -> Product 列表"""
    if isinstance(graph, dict):
        nodes = graph.get("@graph", [graph])
    else:
        nodes = graph
    for node in nodes:
        if not isinstance(node, dict):
            continue
        main = node.get("mainEntity")
        if isinstance(main, dict) and main.get("itemListElement"):
            return main["itemListElement"]
        if node.get("itemListElement"):
            return node["itemListElement"]
    return []


def strip_tags(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_lib.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_releases():
    """抓 Sole Retriever 列表页 JSON-LD"""
    rows, seen = [], set()
    today = today_local()
    cutoff = today - timedelta(days=KEEP_PAST_DAYS)
    missing = {"marketPrice", "size", "collab", "channels"}

    for url in RELEASE_PAGES:
        try:
            raw = get(url)
        except Exception as e:
            print(f"  [跳过] 发售源抓取失败: {url} -> {e}")
            continue

        blocks = LD_JSON_RE.findall(raw)
        found = 0
        for block in blocks:
            try:
                data = json.loads(html_lib.unescape(block))
            except Exception:
                continue
            for entry in find_item_list(data):
                item = entry.get("item", entry) if isinstance(entry, dict) else entry
                if not isinstance(item, dict) or item.get("@type") != "Product":
                    continue

                sku = (item.get("sku") or item.get("mpn")
                       or item.get("productID") or "").strip()
                date_str = (item.get("releaseDate") or "")[:10]
                try:
                    rdate = datetime.strptime(date_str, "%Y-%m-%d").date()
                except Exception:
                    continue
                if rdate < cutoff:
                    continue

                key = sku or item.get("name", "")
                if key in seen:
                    continue
                seen.add(key)
                found += 1

                brand_obj = item.get("brand") or {}
                offer = item.get("offers") or {}
                image = item.get("image") or {}
                rating = item.get("aggregateRating") or {}
                price = offer.get("price")
                currency = offer.get("priceCurrency", "USD")

                rows.append({
                    "id": len(rows) + 1,
                    "brand": norm_brand(brand_obj.get("name", "")),
                    "name": (item.get("name") or "").strip(),
                    "sku": sku or "—",
                    "releaseDate": date_str,
                    "price": f"{currency} {price}" if isinstance(price, (int, float)) else "—",
                    "marketPrice": "—",
                    "size": "—",
                    "collab": "—",
                    "channels": [],
                    "image": image.get("url", ""),
                    "url": offer.get("url") or item.get("@id", ""),
                    "rating": float(rating.get("ratingValue") or 0),
                    "reviewCount": int(rating.get("reviewCount") or 0),
                    "hot": 0,
                    "buzz": 0,
                })
        print(f"  [发售] {url.split('/')[-1] or 'page1'} -> {found} 条")

    missing.discard("marketPrice")
    return rows


# ---------------------------------------------------------------- 新闻

def parse_feed(source):
    """用标准库解析 RSS/Atom，避免额外依赖"""
    try:
        raw = get(source["url"])
    except Exception as e:
        print(f"  [跳过] 新闻源抓取失败: {source['name']} -> {e}")
        return []

    try:
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"  [跳过] {source['name']} XML 解析失败 -> {e}")
        return []

    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    items = root.findall(".//item") or root.findall(
        ".//{http://www.w3.org/2005/Atom}entry")

    def text_of(node, *names):
        for name in names:
            el = node.find(name)
            if el is not None and (el.text or "").strip():
                return (el.text or "").strip()
        return ""

    out = []
    for node in items[:NEWS_PER_FEED]:
        title = strip_tags(text_of(node, "title",
                                   "{http://www.w3.org/2005/Atom}title"))
        link = text_of(node, "link", "{http://www.w3.org/2005/Atom}link")
        date_raw = text_of(node, "pubDate", "published", "updated",
                           "dc:date") or ""
        desc_raw = text_of(node, "description", "summary",
                           "{http://purl.org/rss/1.0/modules/content/}encoded")
        body = strip_tags(desc_raw)

        img = ""
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_raw)
        if m:
            img = m.group(1)
        if not img:
            for tag in ("{http://search.yahoo.com/mrss/}content",
                        "enclosure"):
                el = node.find(tag)
                if el is not None and el.get("url"):
                    img = el.get("url")
                    break

        date_str = ""
        if date_raw:
            for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                        "%Y-%m-%d"):
                try:
                    d = datetime.strptime(date_raw.strip(), fmt)
                    date_str = d.date().isoformat()
                    break
                except Exception:
                    continue
        if not date_str:
            date_str = today_local().isoformat()

        if not title:
            continue
        if not is_sneaker(title, body):
            continue
        out.append({
            "id": f"{source['name']}-{len(out)}",
            "title": title,
            "source": source["name"],
            "date": date_str,
            "content": body[:900],
            "image": img,
            "link": link,
            "lang": source["lang"],
            "relatedBrands": detect_brands(title, body),
        })
    print(f"  [新闻] {source['name']} -> {len(out)} 条")
    return out


# ---------------------------------------------------------------- 热度计算

STOPWORDS = {"the", "and", "for", "low", "high", "og", "retro", "white",
             "black", "new", "nike", "adidas", "of", "x"}


def key_tokens(name: str):
    """从鞋款全名里提取用于匹配新闻的关键词"""
    name = re.sub(r"[()\[\]'\"]", " ", name.lower())
    return {t for t in re.split(r"[\s\-/]+", name)
            if len(t) > 3 and t not in STOPWORDS}


def build_scores(releases, news):
    """热度指数 + 讨论度（均 based on 抓到的真实字段，公式透明）"""
    today = today_local()
    corpus = [((n["title"] or "") + " " + (n["content"] or "")).lower()
              for n in news]

    for item in releases:
        try:
            rdate = datetime.strptime(item["releaseDate"], "%Y-%m-%d").date()
        except Exception:
            rdate = today
        gap = (rdate - today).days
        # 越临近发售热度越高；已发售的按衰减处理
        if gap >= 0:
            timing = max(0, 45 - gap)
        else:
            timing = max(0, 20 + gap * 0.6)

        tokens = key_tokens(item["name"])
        mentions = 0
        if tokens:
            for text in corpus:
                hit = sum(1 for t in tokens if t in text)
                if hit >= 2:
                    mentions += 1

        hot = (item["reviewCount"] * 14
               + item["rating"] * 180
               + timing * 22
               + mentions * 420)
        item["hot"] = int(round(hot))
        item["buzz"] = int(round(mentions * 100
                                 + item["reviewCount"] * 6
                                 + max(0, timing) * 9))
        item["newsMentions"] = mentions
    return releases


# ---------------------------------------------------------------- 主流程

def main():
    print("开始抓取球鞋数据 ...")
    releases = parse_releases()
    news = []
    for src in NEWS_FEEDS:
        news.extend(parse_feed(src))

    releases = build_scores(releases, news)
    releases.sort(key=lambda x: (-x["hot"], x["releaseDate"]))

    news.sort(key=lambda x: x["date"], reverse=True)
    for i, n in enumerate(news, 1):
        n["id"] = i

    brands = sorted({r["brand"] for r in releases if r["brand"] != "OTHER"}
                 | {b for n in news for b in n.get("relatedBrands", [])})

    payload = {
        "updatedAt": datetime.now(timezone(timedelta(hours=8)))
                             .strftime("%Y-%m-%d %H:%M"),
        "sources": {
            "releases": "Sole Retriever (JSON-LD)",
            "news": [s["name"] for s in NEWS_FEEDS],
        },
        "unavailableFields": [
            "marketPrice 市场价：StockX/GOAT 需 API key，脚本直连被 403",
            "size 尺码范围 / channels 购买渠道：详情页被 Cloudflare 拦截",
            "得物、识货：接口需签名，无法直连",
        ],
        "brands": brands,
        "releases": releases,
        "news": news,
    }

    js_path = os.path.join(HERE, "data.js")
    json_path = os.path.join(HERE, "data.json")
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("window.SNEAKER_DATA = ")
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write(";\n")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(f"\n完成：发售 {len(releases)} 条 / 新闻 {len(news)} 条 / 品牌 {len(brands)} 个")
    print(f"已写入 {js_path}\n        {json_path}")


if __name__ == "__main__":
    main()
