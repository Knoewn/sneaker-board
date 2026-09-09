#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 index.html + data.json 打包成一个可单独分发的 HTML 文件。

用法：
    python build_standalone.py
产物：
    EazyReview-看板-单文件版.html   （双击即可打开，离线可看；联网会自动尝试更新）

原理：
    把 data.json 内嵌为 window.EMBEDDED_DATA，index.html 里的 pickInitial()
    会自动识别它作为「内嵌快照」。之后每次打开，自动更新引擎会依次尝试
    远程数据包 URL → 同源 data.json → 浏览器内直抓，成功则覆盖内嵌快照。
"""

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_HTML = os.path.join(HERE, "index.html")
SRC_DATA = os.path.join(HERE, "data.json")
OUT = os.path.join(HERE, "EazyReview-看板-单文件版.html")


def main():
    if not os.path.exists(SRC_DATA):
        sys.exit("找不到 data.json，请先运行 fetch_sneaker_data.py 生成数据")

    with io.open(SRC_HTML, encoding="utf-8") as f:
        html = f.read()
    with io.open(SRC_DATA, encoding="utf-8") as f:
        data = json.load(f)

    marker = '<script src="data.js"></script>'
    if marker not in html:
        sys.exit("index.html 里找不到 data.js 引用标记，打包中止")

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")  # 防止提前闭合 script 标签

    injected = ("<script>/* ===== 内嵌数据快照（由 build_standalone.py 打包）===== */\n"
                "window.EMBEDDED_DATA = " + payload + ";\n</script>")

    out = html.replace(marker, injected)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write(out)

    kb = os.path.getsize(OUT) / 1024
    print("已生成：%s" % OUT)
    print("  内嵌发售 %d 款 / 新闻 %d 条 / 品牌 %d 个，更新时间 %s"
          % (len(data.get("releases", [])), len(data.get("news", [])),
             len(data.get("brands", [])), data.get("updatedAt", "—")))
    print("  文件体积 %.0f KB" % kb)


if __name__ == "__main__":
    main()
