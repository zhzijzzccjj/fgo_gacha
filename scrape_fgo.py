# -*- coding: utf-8 -*-
"""
UFOServent 抽卡素材自动抓取脚本 (fgo.wiki 英灵图鉴)

数据来源(单一页面):
  1) 内嵌 29 列 CSV      -> id, star(星级), avatar(头像URL), class_link, faction ...
  2) 内嵌 key=value 块   -> id -> method(获取途径文本, 多途径用 <br> 分隔)
  3) 页面 JS 过滤逻辑     -> 进度: 国服已实装 = id <= 456 (存入 meta 备用)

归档规则:
  servent3/4/5  <- method 含"圣晶石常驻" 且 星级 3/4/5
  剧情限定      <- method 含"剧情限定" (全部星级, 留档)
  期间限定      <- method 含"期间限定" (全部星级, 留档)
  其余途径      -> 只记录到 meta, 不入卡池

用法: python scrape_fgo.py [资源目录]   (默认 plugin_data/astrbot_plugin_fgo_gacha/resources)
可重复运行: 已存在的文件会跳过(断点续传)
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import re
import ssl
import sys
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit, urlunsplit
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"}
PAGE = "https://fgo.wiki/w/%E8%8B%B1%E7%81%B5%E5%9B%BE%E9%89%B4"
PAGE_CE = "https://fgo.wiki/w/%E7%A4%BC%E8%A3%85%E5%9B%BE%E9%89%B4"
CN_INSTALLED_CUTOFF = 456  # 国服已实装上限 id(取自页面 JS get_process_status)


def _encode_url(url: str) -> str:
    """把 URL 里的非 ASCII 字符(如 礼装001.jpg)百分号编码, 避免 UnicodeEncodeError"""
    try:
        parts = urlsplit(url)
        # safe 含 %: 保留已编码的 %xx, 只编码未编码的中文/特殊字符
        return urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe="/%"), parts.query, parts.fragment))
    except Exception:
        return url


def _ssl_context():
    """优先用系统证书库(truststore), 其次 certifi, 最后 Python 默认上下文"""
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if not cafile:
        try:
            import certifi
            cafile = certifi.where()
        except Exception:
            cafile = None
    try:
        if cafile and pathlib.Path(cafile).is_file():
            return ssl.create_default_context(cafile=cafile)
    except Exception:
        pass
    return ssl.create_default_context()


_SSL_CONTEXT = _ssl_context()


def fetch(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(_encode_url(url), headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as r:
            return r.read()
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, ssl.SSLError):
            raise RuntimeError(
                "SSL 证书校验失败: 请安装/更新 certifi (pip install -U certifi), "
                "或设置环境变量 SSL_CERT_FILE 指向可用的 CA 证书"
            ) from e
        raise


def parse_csv(html: str):
    end = html.find("csv_data = table_parser")
    if end < 0:
        end = len(html)
    i = html.find("id,star,cost,faction")
    seg = html[i:end].replace("\\n", "\n")
    header = None
    rows = []
    for ln in seg.split("\n"):
        ln = ln.strip()
        if ln.startswith("id,star,cost,faction"):
            header = ln
            continue
        if header and ln:
            rows.append(ln)
    names = header.split(",")
    idx = {n: i for i, n in enumerate(names)}
    data = []
    for r in rows:
        vals = r.split(",")
        if len(vals) != len(names):
            continue
        data.append({
            "id": int(vals[idx["id"]]),
            "star": int(vals[idx["star"]]),
            "class": vals[idx["class_link"]],
            "faction": vals[idx["faction"]],
            "avatar": "https:" + vals[idx["avatar"]] if vals[idx["avatar"]].startswith("//") else vals[idx["avatar"]],
        })
    return data


def parse_method(html: str) -> dict:
    """key=value 块: id -> method 文本"""
    methods = {}
    cur = {}
    for t in html.split("\\n"):
        t = t.strip()
        if not t:
            continue
        if "=" in t:
            k, v = t.split("=", 1)
            cur[k] = v
        if k == "tag" and "id" in cur and cur["id"].strip().isdigit():
            methods[int(cur["id"])] = {
                "method": cur.get("method", ""),
                "name": cur.get("name_link") or cur.get("name_cn", ""),
            }
            cur = {}
    return methods


def parse_ce_csv(html: str):
    end = html.find("csv_data = table_parser")
    if end < 0:
        end = len(html)
    i = html.find("id,star,star_str,cost")
    seg = html[i:end].replace("\\n", "\n")
    header = None
    rows = []
    for ln in seg.split("\n"):
        ln = ln.strip()
        if ln.startswith("id,star,star_str,cost"):
            header = ln
            continue
        if header and ln:
            rows.append(ln)
    names = header.split(",")
    idx = {n: k for k, n in enumerate(names)}
    data = []
    for r in rows:
        vals = r.split(",")
        if len(vals) != len(names):
            continue
        icon = vals[idx["icon"]]
        data.append({
            "id": int(vals[idx["id"]]),
            "star": int(vals[idx["star"]]),
            "icon": "https:" + icon if icon.startswith("//") else icon,
        })
    return data


def parse_ce_type(html: str) -> dict:
    """礼装 key=value 块 -> id -> (type, name)"""
    info = {}
    cur = {}
    for t in html.split("\\n"):
        t = t.strip()
        if not t:
            continue
        if "=" in t:
            k, v = t.split("=", 1)
            cur[k] = v
        if k == "event" and "id" in cur and cur["id"].strip().isdigit():
            info[int(cur["id"])] = {"type": cur.get("type", ""), "name": cur.get("name", "")}
            cur = {}
    return info


def parse_ce_cutoff(html: str, default: int) -> int:
    """礼装国服已实装上限 id: 页面 JS 里 (id <= N)"""
    m = re.search(r"\(id\s*<=\s*(\d+)\)", html)
    return int(m.group(1)) if m else default


def ce_target_dirs(star: int, typ: str) -> list:
    dirs = []
    if "卡池常驻" in typ and star in (3, 4, 5):
        dirs.append(f"lizhuang{star}")
    if "剧情限定" in typ:
        dirs.append("礼装-剧情限定")
    if "期间限定" in typ:
        dirs.append("礼装-期间限定")
    return dirs


def scrape_ces(assets: pathlib.Path, chouka: pathlib.Path) -> None:
    CE_CUTOFF_DEFAULT = 2472
    print("\n抓取礼装页面...")
    html = fetch(PAGE_CE).decode("utf-8", "ignore")
    ces = parse_ce_csv(html)
    info = parse_ce_type(html)
    cutoff = parse_ce_cutoff(html, CE_CUTOFF_DEFAULT)
    print(f"礼装 CSV: {len(ces)} 个, type 条目: {len(info)} 个, 国服已实装上限 id = {cutoff}")

    meta = {"cn_installed_cutoff": cutoff, "saved_at": "", "ces": []}
    jobs = []
    for ce in ces:
        ce["type"] = info.get(ce["id"], {}).get("type", "")
        ce["name"] = info.get(ce["id"], {}).get("name", "")
        ce["cn_installed"] = ce["id"] <= cutoff
        meta["ces"].append(ce)
        dirs = ce_target_dirs(ce["star"], ce["type"])
        if not dirs:
            continue
        for d in dirs:
            folder = chouka / d
            folder.mkdir(parents=True, exist_ok=True)
            out = folder / f"礼装{ce['id']:03d}.jpg"
            jobs.append((d, ce["icon"], out))

    print(f"礼装待归档: {len(jobs)} 个")
    import datetime

    def dl(job):
        d, url, out = job
        if out.exists() and out.stat().st_size > 0:
            return "skip"
        try:
            data = fetch(url, timeout=30)
            if not data:
                return "empty"
            out.write_bytes(data)
            return "ok"
        except Exception as e:
            return "err"

    skipped = new = errs = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(dl, jobs):
            if res == "skip":
                skipped += 1
            elif res == "ok":
                new += 1
            else:
                errs += 1
    meta["saved_at"] = datetime.datetime.now().isoformat()
    (assets / "ce_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"礼装下载: 新增 {new}, 跳过 {skipped}, 失败 {errs}")
    for d in ("lizhuang5", "lizhuang4", "lizhuang3", "礼装-剧情限定", "礼装-期间限定"):
        n = len(list((chouka / d).glob("*.jpg"))) if (chouka / d).exists() else 0
        print(f"  {d}: 现有 {n} 张")


def target_dirs(star: int, method: str) -> list:
    dirs = []
    if "圣晶石常驻" in method and star in (3, 4, 5):
        dirs.append(f"servent{star}")
    if "剧情限定" in method:
        dirs.append("剧情限定")
    if "期间限定" in method:
        dirs.append("期间限定")
    return dirs


def main():
    root_arg = sys.argv[1] if len(sys.argv) > 1 else None
    if root_arg:
        assets = pathlib.Path(root_arg)
    else:
        data_root = pathlib.Path(__file__).resolve().parents[2]
        assets = data_root / "plugin_data" / "astrbot_plugin_fgo_gacha" / "resources"
    chouka = assets / "image" / "chouka"
    chouka.mkdir(parents=True, exist_ok=True)

    print("抓取页面...")
    html = fetch(PAGE).decode("utf-8", "ignore")
    servants = parse_csv(html)
    methods = parse_method(html)
    print(f"CSV 从者: {len(servants)} 个, method 条目: {len(methods)} 个")

    # 动态读取"国服已实装上限 id"(页面 JS: is_cn_process = (id <= N)), 读不到则用默认
    cutoff = CN_INSTALLED_CUTOFF
    mt = re.search(r"is_cn_process\s*=\s*\(id\s*<=\s*(\d+)\)", html)
    if not mt:
        mt = re.search(r"id\s*<=\s*(\d+)", html)
    if mt:
        cutoff = int(mt.group(1))
        print(f"国服已实装上限 id = {cutoff} (动态解析)")
    else:
        print(f"国服已实装上限 id = {cutoff} (默认)")

    # meta
    meta = {"cn_installed_cutoff": cutoff, "saved_at": "", "servants": []}
    jobs = []      # (folder_name, url, out_path)
    pool_count = {"servent5": 0, "servent4": 0, "servent3": 0, "剧情限定": 0, "期间限定": 0}
    n_noimg = 0

    for svt in servants:
        _info = methods.get(svt["id"], {})
        svt["method"] = _info.get("method", "") if isinstance(_info, dict) else str(_info)
        svt["name"] = _info.get("name", "") if isinstance(_info, dict) else ""
        svt["cn_installed"] = svt["id"] <= cutoff
        meta["servants"].append(svt)
        dirs = target_dirs(svt["star"], svt["method"])
        if not dirs:
            continue
        for d in dirs:
            folder = chouka / d
            folder.mkdir(parents=True, exist_ok=True)
            out = folder / f"Servant{svt['id']:03d}.jpg"
            jobs.append((d, svt["avatar"], out))

    print(f"待归档图片任务: {len(jobs)} 个")
    # 下载(并发), 跳过已存在
    skipped = 0
    new_download = 0
    fail = []

    def dl(job):
        d, url, out = job
        if out.exists() and out.stat().st_size > 0:
            return "skip"
        try:
            data = fetch(url, timeout=30)
            if not data:
                return "empty"
            out.write_bytes(data)
            return "ok"
        except Exception as e:
            fail.append((str(out), str(e)))
            return "err"

    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(dl, jobs):
            if res == "skip":
                skipped += 1
            elif res == "ok":
                new_download += 1

    import datetime
    meta["saved_at"] = datetime.datetime.now().isoformat()
    meta_path = assets / "servant_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"下载完成: 新增 {new_download}, 跳过(已存在) {skipped}, 失败 {len(fail)}")
    if fail:
        print("失败样例:", fail[:5])
    print("meta 已保存:", meta_path)
    for d in pool_count:
        n = len(list((chouka / d).glob("*.jpg"))) if (chouka / d).exists() else 0
        print(f"  {d}: 现有 {n} 张")

    # 礼装
    scrape_ces(assets, chouka)


if __name__ == "__main__":
    main()
