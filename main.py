# -*- coding: utf-8 -*-
"""
UFOServent - FGO 抽卡插件
由 mirai 版 Fuctions/ChouKa.java 移植。

集成 ufo_points: 群消息积分/签到/看看存石 + 抽卡(消耗圣晶石)。

抽卡:
- 来发单抽: 默认 3 石, 随机 1 张
- 来发十连: 默认 30 石, 11 张并拼成一张大图(含保底规则)
资源固定位于 plugin_data/astrbot_plugin_fgo_gacha/resources/image/chouka
(servent3/4/5, lizhuang3/4/5 为常驻池; 剧情限定/期间限定/礼装-*为留档; UP 按配置取图)
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
import astrbot.api.message_components as Comp
from astrbot.api.web import error_response, json_response, request


def astrbot_data_dir() -> Path:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
    return Path(get_astrbot_data_path())


def store_dir() -> Path:
    """运行数据(积分/抽卡计数)固定放本插件专属数据目录"""
    return astrbot_data_dir() / "plugin_data" / "astrbot_plugin_fgo_gacha"


# ================= 自管理配置(不依赖 _conf_schema.json) =================
# WebUI 设置页按此列表渲染; 新增参数只需在这里加一项
FIELDS = [
    {"key": "group_scope", "type": "tags", "label": "生效群ID", "section": "基础",
     "desc": "启用本插件的群; 留空 = 所有群", "placeholder": "输入群号后回车, 可连续添加", "digits": True},
    {"key": "enable_points", "type": "bool", "label": "积分功能", "section": "积分/签到",
     "desc": "关: 水群/签到不再得分，签到与看存石停用；抽卡照常但免费(不扣石、不校验余额)"},
    {"key": "points_off_reply", "type": "bool", "label": "积分关闭提示", "section": "积分/签到",
     "desc": "积分功能关闭时，收到签到/看看存石是否回复“已关闭”提示；关 = 静默不回复"},
    {"key": "points_msg_text", "type": "int", "label": "文字消息积分", "section": "积分/签到",
     "desc": "每条普通文字消息获得的圣晶石, 默认 5"},
    {"key": "points_msg_image", "type": "int", "label": "图片消息积分", "section": "积分/签到",
     "desc": "每条含图片的消息获得的圣晶石, 默认 10"},
    {"key": "points_checkin", "type": "int", "label": "每日签到积分", "section": "积分/签到",
     "desc": "每天首次签到获得的圣晶石, 默认 30"},
    {"key": "command_keys_no_points", "type": "tags", "label": "不加分的命令", "section": "积分/签到",
     "desc": "这些命令消息不获得水群积分", "placeholder": "输入命令后回车, 可连续添加"},
    {"key": "cost_danchou", "type": "int", "label": "单抽消耗(圣晶石)", "section": "抽卡价格",
     "desc": "默认 3; 填 0 = 免费单抽"},
    {"key": "cost_shilian", "type": "int", "label": "十连消耗(圣晶石)", "section": "抽卡价格",
     "desc": "默认 30; 填 0 = 免费十连"},
    {"key": "enable_daily_scrape", "type": "bool", "label": "每日自动抓取", "section": "定时抓取",
     "desc": "每天按时自动增量抓取 fgo.wiki 从者+礼装素材"},
    {"key": "scrape_time", "type": "string", "label": "每日抓取时间", "section": "定时抓取",
     "desc": "24小时制, 如 04:00"},
    {"key": "rate_up_s3", "type": "float", "label": "3★从者UP概率", "section": "UP 概率(%)", "desc": "默认 4%"},
    {"key": "rate_up_s4", "type": "float", "label": "4★从者UP概率", "section": "UP 概率(%)", "desc": "默认 2.1%"},
    {"key": "rate_up_s5", "type": "float", "label": "5★从者UP概率", "section": "UP 概率(%)", "desc": "默认 0.8%"},
    {"key": "rate_up_ce3", "type": "float", "label": "3★礼装UP概率", "section": "UP 概率(%)", "desc": "默认 8%"},
    {"key": "rate_up_ce4", "type": "float", "label": "4★礼装UP概率", "section": "UP 概率(%)", "desc": "默认 4%"},
    {"key": "rate_up_ce5", "type": "float", "label": "5★礼装UP概率", "section": "UP 概率(%)", "desc": "默认 2.8%"},
    {"key": "cn_pool", "type": "bool", "label": "国服卡池", "section": "卡池规则",
     "desc": "开: 只抽取 id ≤ 国服已实装上限 的从者/礼装(上限自动跟随抓取的 meta)"},
    {"key": "story_pool", "type": "bool", "label": "剧情卡池", "section": "卡池规则",
     "desc": "开: 基础随机池在圣晶石常驻之外额外并入 剧情限定(同星级)"},
]
DEFAULT_CONFIG = {
    "group_scope": [],
    "enable_points": True,
    "points_off_reply": True,
    "points_msg_text": 5,
    "points_msg_image": 10,
    "points_checkin": 30,
    "command_keys_no_points": ["来发十连", "来发单抽", "看看存石", "签到"],
    "cost_danchou": 3,
    "cost_shilian": 30,
    "enable_daily_scrape": True,
    "scrape_time": "04:00",
    "rate_up_s3": 4.0, "rate_up_s4": 2.1, "rate_up_s5": 0.8,
    "rate_up_ce3": 8.0, "rate_up_ce4": 4.0, "rate_up_ce5": 2.8,
    "up_s3": None, "up_s4": None, "up_s5": None,
    "up_ce3": None, "up_ce4": None, "up_ce5": None,
    "up_servant": None,  # 兼容旧键, 加载时迁移到 up_s5
    "cn_pool": False,   # 国服卡池: 只抽 id <= 已实装上限
    "story_pool": False,  # 剧情卡池: 基础池并入 剧情限定
}
CONFIG_PATH = astrbot_data_dir() / "plugin_data" / "astrbot_plugin_fgo_gacha" / "config.json"


# ---------- 共用积分存档 ----------


def load_json(path: Path, default: dict | list) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def nonneg_int(value, default: int) -> int:
    """配置里的非负整数(容忍手工改坏的值)"""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ========== 卡池(含 UP 切片) ==========
# 稀有度总概率(%), UP 从其中切一块, 未设 UP 则整块走普通随机
BASE_RATES = {"s3": 40.0, "s4": 3.0, "s5": 1.0, "ce3": 40.0, "ce4": 12.0, "ce5": 4.0}
CAT_FOLDER = {"s3": "servent3", "s4": "servent4", "s5": "servent5",
              "ce3": "lizhuang3", "ce4": "lizhuang4", "ce5": "lizhuang5"}
ALL_CATS = list(BASE_RATES)

_BASE_STAR = {"s3": 3, "s4": 4, "s5": 5, "ce3": 3, "ce4": 4, "ce5": 5}


_ID_RE = re.compile(r"(\d+)\.jpg$")


def _file_id(p: Path) -> int:
    """从文件名解析 id(支持 Servant001.jpg / 礼装001.jpg / 1.jpg)"""
    m = _ID_RE.search(p.name)
    return int(m.group(1)) if m else 0


def _pool_files(chouka_dir: Path, cat: str, rules=None):
    """该分类的随机池文件列表, 应用 剧情卡池/国服卡池 规则"""
    rules = rules or {}
    pool = list_card_files(chouka_dir / CAT_FOLDER[cat])  # 圣晶石常驻
    if rules.get("story"):
        extra = "礼装-剧情限定" if cat.startswith("ce") else "剧情限定"
        stars = (rules.get("ce_stars") if cat.startswith("ce") else rules.get("s_stars")) or {}
        need = _BASE_STAR[cat]
        for pp in list_card_files(chouka_dir / extra):
            cid = _file_id(pp)
            if cid and int(stars.get(cid) or 0) == need:
                pool.append(pp)
    if rules.get("cn"):
        cutoff = rules.get("ce_cutoff") if cat.startswith("ce") else rules.get("s_cutoff")
        if cutoff:
            cint = int(cutoff)
            pool = [pp for pp in pool if not _file_id(pp) or _file_id(pp) <= cint]
    return pool


def list_card_files(dir_path: Path):
    """列出文件夹里的 jpg 图片(兼容任意命名)"""
    try:
        return [pth for pth in dir_path.glob("*.jpg") if pth.is_file()]
    except Exception:
        return []


def up_file_for(chouka_dir: Path, cat: str, uid):
    """按 id 在各池/留档目录里找 UP 的本地图"""
    if uid is None:
        return None
    uid = int(uid)
    if cat.startswith("s"):
        folders = ("servent5", "servent4", "servent3", "剧情限定", "期间限定")
        name = f"Servant{uid:03d}.jpg"
    else:
        folders = ("lizhuang5", "lizhuang4", "lizhuang3", "礼装-剧情限定", "礼装-期间限定")
        name = f"礼装{uid:03d}.jpg"
    for fd in folders:
        fp = chouka_dir / fd / name
        if fp.is_file():
            return str(fp)
    return None


def _pick_bucket(cats, ups, rates):
    """按带权表抽一个分类; 返回 (cat, kind): kind in ('up','other',None)"""
    buckets = []
    for cat in cats:
        base = BASE_RATES[cat]
        up_rate = float(rates.get(cat) or 0.0)
        if up_rate <= 0 or ups.get(cat) is None:
            buckets.append((base, cat, None))
        else:
            up_rate = min(up_rate, base)
            buckets.append((up_rate, cat, "up"))
            rest = base - up_rate
            if rest > 0:
                buckets.append((rest, cat, "other"))
    total = sum(b[0] for b in buckets)
    r = random.random() * total
    for w, cat, kind in buckets:
        r -= w
        if r < 0:
            return cat, kind
    return buckets[-1][1], buckets[-1][2]


def _resolve_card(chouka_dir: Path, cat: str, kind, ups, rules=None):
    """分类已定: up -> 指定图; 其他/未设up -> 随机(排除up图避免双算; 按规则过滤池)"""
    up_path = None
    if ups.get(cat) is not None:
        up_path = up_file_for(chouka_dir, cat, ups[cat].get("id"))
    pool = _pool_files(chouka_dir, cat, rules)
    if kind == "up":
        if up_path:
            return up_path
        return str(random.choice(pool)) if pool else None
    choices = [pp for pp in pool if str(pp) != up_path] if up_path else pool
    return str(random.choice(choices)) if choices else None


def _pick_card_with_up(chouka_dir: Path, cats, ups, rates, rules=None):
    """完整抽一张: (cat, path)"""
    cat, kind = _pick_bucket(cats, ups, rates)
    return cat, _resolve_card(chouka_dir, cat, kind, ups, rules)


def dan_chou(chouka_dir: Path, ups=None, rates=None, rules=None) -> str:
    """单抽(带 UP 切片 + 卡池规则); 未设 UP 的分类回退为普通随机"""
    ups = ups or {}
    rates = rates or {}
    _cat, path = _pick_card_with_up(chouka_dir, ALL_CATS, ups, rates, rules)
    return path or ""


def shi_lian(chouka_dir: Path, cache_file: Path, ups=None, rates=None, rules=None) -> str:
    """十连: 11 张, 带保底规则, 拼成一张大图"""
    try:
        from PIL import Image as PILImage
    except ImportError:
        raise RuntimeError("缺少 Pillow 依赖, 请 pip install pillow")

    ups = ups or {}
    rates = rates or {}
    GOLD_CATS = ["s4", "s5", "ce4", "ce5"]      # 第10抽保底金卡(4★/5★)
    SERVANT_CATS = ["s3", "s4", "s5"]           # 第11抽保底从者

    cards = []
    jin_ka = False
    cong_zhe = False
    for i in range(11):
        if i == 9 and not jin_ka:
            cat, path = _pick_card_with_up(chouka_dir, GOLD_CATS, ups, rates, rules)
            jin_ka = True
            if cat in ("s4", "s5"):
                cong_zhe = True
        elif i == 10 and not cong_zhe:
            cat, path = _pick_card_with_up(chouka_dir, SERVANT_CATS, ups, rates, rules)
            cong_zhe = True
            if cat in ("s4", "s5"):
                jin_ka = True
        else:
            cat, path = _pick_card_with_up(chouka_dir, ALL_CATS, ups, rates, rules)
            if cat in ("s4", "s5"):
                jin_ka = True
            if cat.startswith("s"):
                cong_zhe = True
        if path:
            cards.append(path)

    # 拼 6x2 大图
    imgs = []
    for cp in cards:
        try:
            imgs.append(PILImage.open(cp).convert("RGB"))
        except Exception:
            continue
    if not imgs:
        raise RuntimeError("卡池图片不存在: " + str(chouka_dir))

    w, h = imgs[0].size
    canvas = PILImage.new("RGB", (w * 6, h * 2), "white")
    for idx, img in enumerate(imgs):
        if idx < 6:
            x, y = idx * w, 0
        else:
            x, y = int((idx - 6 + 0.5) * w), h
        canvas.paste(img, (x, y))
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(cache_file)
    return str(cache_file)


class Main(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self._lock = asyncio.Lock()
        self._rules_cache = None     # 抽卡规则缓存(120s)
        self._cfg = self._load_cfg()  # 自管理配置: 默认值 + config.json 覆盖
        self.context.register_web_api("/astrbot_plugin_fgo_gacha/config", self.get_config, ["GET"], "读取抽卡配置")
        self.context.register_web_api("/astrbot_plugin_fgo_gacha/config", self.post_config, ["POST"], "保存抽卡配置")
        self.context.register_web_api("/astrbot_plugin_fgo_gacha/scrape", self.scrape_now, ["POST"], "立即增量抓取")
        self.context.register_web_api("/astrbot_plugin_fgo_gacha/up_candidates", self.get_up_candidates, ["GET"], "UP候选列表")
        self.context.register_web_api("/astrbot_plugin_fgo_gacha/up", self.post_up, ["POST"], "设置UP从者")

    def _chouka_dir(self) -> Path:
        return self._assets_dir() / "image" / "chouka"

    def _draw_rules(self) -> dict:
        """国服/剧情 卡池规则; id 上限读取 meta 里预存的 cn_installed_cutoff (120s 缓存)"""
        now = time.time()
        if self._rules_cache and now - self._rules_cache[0] < 120:
            return self._rules_cache[1]
        assets = self._assets_dir()
        smeta = load_json(assets / "servant_meta.json", {})
        cmeta = load_json(assets / "ce_meta.json", {})
        rules = {
            "cn": bool(self._get("cn_pool")),
            "story": bool(self._get("story_pool")),
            "s_cutoff": int(smeta.get("cn_installed_cutoff") or 0),
            "ce_cutoff": int(cmeta.get("cn_installed_cutoff") or 0),
            "s_stars": {int(x.get("id") or 0): int(x.get("star") or 0) for x in smeta.get("servants") or []},
            "ce_stars": {int(x.get("id") or 0): int(x.get("star") or 0) for x in cmeta.get("ces") or []},
        }
        self._rules_cache = (now, rules)
        return rules

    def _pool_cfg(self):
        """返回 (ups, rates, rules): 各分类 UP / UP概率 / 卡池规则"""
        ups = {}
        for cat in ("s3", "s4", "s5", "ce3", "ce4", "ce5"):
            u = self._get(f"up_{cat}")
            ups[cat] = u if (u and u.get("id")) else None
        rules = self._draw_rules()
        # 国服模式: UP id 超过已实装上限 => 该分类视为未设 UP
        if rules["cn"]:
            for cat in ("s3", "s4", "s5", "ce3", "ce4", "ce5"):
                u = ups[cat]
                if u is None:
                    continue
                cutoff = rules["ce_cutoff"] if cat.startswith("ce") else rules["s_cutoff"]
                if cutoff and int(u.get("id") or 0) > int(cutoff):
                    ups[cat] = None
        rates = {cat: float(self._get(f"rate_up_{cat}") or 0.0)
                 for cat in ("s3", "s4", "s5", "ce3", "ce4", "ce5")}
        return ups, rates, rules

    # ================= 自管理配置存取 =================
    def _cfg_path(self) -> Path:
        return Path(CONFIG_PATH)

    def _load_cfg(self) -> dict:
        cfg = dict(DEFAULT_CONFIG)
        try:
            saved = json.loads(self._cfg_path().read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update({k: v for k, v in saved.items() if k in cfg})
        except Exception:
            pass
        # 兼容: 旧 up_servant -> up_s5
        if cfg.get("up_s5") is None and cfg.get("up_servant"):
            cfg["up_s5"] = cfg["up_servant"]
        return cfg

    def _save_cfg(self, updates: dict) -> bool:
        if not hasattr(self, "_cfg"):
            self._cfg = self._load_cfg()
        self._cfg.update({k: v for k, v in updates.items() if k in DEFAULT_CONFIG})
        path = self._cfg_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True

    def _get(self, key: str, default=None):
        return self._cfg.get(key, default) if hasattr(self, "_cfg") else default

    # ================= 每日定时增量抓取 =================
    async def initialize(self):
        """插件激活时启动后台抓取循环"""
        self._scrape_task: asyncio.Task | None = None
        self._scrape_task = asyncio.create_task(self._scrape_loop())

    async def terminate(self):
        if getattr(self, "_scrape_task", None):
            self._scrape_task.cancel()

    def _scrape_script_path(self) -> Path:
        return Path(__file__).resolve().parent / "scrape_fgo.py"

    def _seconds_until_time(self, hhmm: str) -> float:
        parts = (hhmm or "04:00").split(":")
        try:
            h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            h, m = 4, 0
        now = datetime.now()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    async def _scrape_loop(self):
        """每分钟检查一次: 开启且到点了就跑增量抓取"""
        while True:
            try:
                if self._get("enable_daily_scrape"):
                    await asyncio.sleep(max(1.0, self._seconds_until_time(str(self._get("scrape_time") or "04:00"))))
                    if self._get("enable_daily_scrape"):
                        await self._run_scrape()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"fgo_gacha 定时抓取循环异常: {e}")
            await asyncio.sleep(60)

    async def _run_scrape(self):
        script = self._scrape_script_path()
        if not script.exists():
            logger.warning(f"fgo_gacha 抓取脚本不存在: {script}")
            return
        logger.info("fgo_gacha 开始每日增量抓取抽卡素材...")
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(script), str(self._assets_dir()),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                logger.info("fgo_gacha 每日抓取完成(增量)")
            else:
                tail = (stdout or b"").decode("utf-8", "ignore")[-500:]
                logger.error(f"fgo_gacha 抓取失败 rc={proc.returncode}: {tail}")
        except Exception as e:
            logger.error(f"fgo_gacha 抓取执行异常: {e}")


    # ---------- WebUI 配置界面 API(自管理配置, 不依赖 _conf_schema.json) ----------
    @staticmethod
    def _coerce(field_type: str, value):
        try:
            if field_type in ("int", "integer"):
                return int(value)
            if field_type == "float":
                return float(value)
            if field_type == "bool":
                if isinstance(value, str):
                    return value.strip().lower() in ("1", "true", "yes", "on")
                return bool(value)
            if field_type == "list":
                if isinstance(value, str):
                    sep = chr(13) + chr(10)
                    return [x.strip() for x in value.replace(sep, chr(10)).split(chr(10)) if x.strip()]
                return [x for x in value] if isinstance(value, (list, tuple)) else []
            if field_type == "tags":
                if isinstance(value, str):
                    text = value.replace(chr(65292), " ").replace(chr(65307), " ")
                    return [x for x in text.replace(",", " ").replace(";", " ").split() if x]
                return [str(x).strip() for x in value if str(x).strip()] if isinstance(value, (list, tuple)) else []
            if field_type in ("dict", "object"):
                if isinstance(value, str):
                    return json.loads(value)
                return dict(value) if isinstance(value, dict) else {}
            return str(value)
        except Exception:
            return value

    async def get_config(self):
        """WebUI 读取: 自管理配置 + 字段定义"""
        return json_response({"ok": True, "config": dict(self._cfg), "fields": FIELDS})

    async def post_config(self):
        """WebUI 保存: 写入自管理 config.json"""
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("配置必须是 JSON 对象")
        type_map = {f["key"]: f.get("type", "string") for f in FIELDS}
        new = {}
        for key, typ in type_map.items():
            if key in payload:
                new[key] = self._coerce(typ, payload[key])
        if not new:
            return error_response("没有可保存的配置项")
        try:
            self._save_cfg(new)
        except Exception as e:
            logger.error(f"fgo_gacha 保存配置失败: {e}")
            return error_response(f"保存失败: {e}")
        logger.info(f"fgo_gacha 配置已保存: {list(new.keys())}")
        return json_response({"ok": True, "saved": True, "config": dict(self._cfg)})

    async def scrape_now(self):
        """WebUI 按钮: 立即跑一次增量抓取(后台执行)"""
        asyncio.create_task(self._run_scrape())
        return json_response({"ok": True, "started": True})

    def _assets_dir(self) -> Path:
        # 抽卡素材按 AstrBot 规范放插件专属数据目录, 随插件配置一起持久化
        return astrbot_data_dir() / "plugin_data" / "astrbot_plugin_fgo_gacha" / "resources"

    def _cat_star(self, cat: str) -> int:
        return {"s3": 3, "s4": 4, "s5": 5, "ce3": 3, "ce4": 4, "ce5": 5}.get(cat, 0)

    async def get_up_candidates(self):
        """按分类返回 UP 候选(5★/4★/3★ 从者 或 礼装), 供页面弹层选择"""
        cat = request.query.get("cat", "s5")
        if cat not in {"s3", "s4", "s5", "ce3", "ce4", "ce5"}:
            return error_response("cat 不合法")
        star = self._cat_star(cat)
        want_servant = cat.startswith("s")
        meta_file = "servant_meta.json" if want_servant else "ce_meta.json"
        key = "method" if want_servant else "type"
        data = load_json(self._assets_dir() / meta_file, {})
        items = data.get("servants") if want_servant else data.get("ces")
        cn = bool(self._get("cn_pool"))
        cutoff = int(data.get("cn_installed_cutoff") or 0) if cn else 0
        out = []
        for x in items or []:
            if int(x.get("star") or 0) != star:
                continue
            if cutoff and int(x.get("id") or 0) > cutoff:
                continue
            m = str(x.get(key) or "")
            # 从者叫"圣晶石常驻", 礼装叫"卡池常驻", 分开匹配
            allow = ("圣晶石常驻", "剧情限定", "期间限定") if want_servant else ("卡池常驻", "剧情限定", "期间限定")
            if not any(k in m for k in allow):
                continue
            out.append({
                "id": int(x.get("id") or 0),
                "name": x.get("name") or "",
                "method": m,
                "type": "servant" if want_servant else "ce",
                "avatar": x.get("avatar") or x.get("icon") or "",
            })
        out.sort(key=lambda v: v["id"])
        return json_response({"ok": True, "cat": cat, "count": len(out),
                              "up": self._get(f"up_{cat}"), "candidates": out})

    async def post_up(self):
        """把某分类的 UP 设为指定从者/礼装(按 id 记录; 抽卡时按 id 取本地图)"""
        payload = await request.json(default={})
        cat = payload.get("cat", "s5")
        if cat not in {"s3", "s4", "s5", "ce3", "ce4", "ce5"}:
            return error_response("cat 不合法")
        try:
            uid = int(payload.get("id") or 0)
        except (TypeError, ValueError):
            return error_response("id 不合法")
        if uid <= 0:
            # id <= 0: 清除该分类 UP, 恢复普通随机
            self._save_cfg({f"up_{cat}": None})
            logger.info(f"fgo_gacha UP[{cat}] 已清除")
            return json_response({"ok": True, "cat": cat, "up": None})
        src = up_file_for(self._chouka_dir(), cat, uid)
        if not src:
            kind = "从者" if cat.startswith("s") else "礼装"
            return error_response(f"未找到该 {kind} 的本地图(id={uid}), 请先抓取素材")
        want_servant = cat.startswith("s")
        meta_file = "servant_meta.json" if want_servant else "ce_meta.json"
        key = "method" if want_servant else "type"
        info = {"id": uid, "name": "", "method": "", "avatar": ""}
        data = load_json(self._assets_dir() / meta_file, {})
        items = data.get("servants") if want_servant else data.get("ces")
        for x in items or []:
            if int(x.get("id") or 0) == uid:
                info = {
                    "id": uid,
                    "name": x.get("name") or "",
                    "method": str(x.get(key) or ""),
                    "avatar": x.get("avatar") or x.get("icon") or "",
                }
                break
        self._save_cfg({f"up_{cat}": info})
        logger.info(f"fgo_gacha UP[{cat}] = id {uid} {info.get('name')}")
        return json_response({"ok": True, "cat": cat, "up": info, "file": src})

    @filter.event_message_type(
        filter.EventMessageType.GROUP_MESSAGE | filter.EventMessageType.PRIVATE_MESSAGE
    )
    async def on_message(self, event: AstrMessageEvent):
        """群/私聊消息: 群聊积分记账+签到/看石+抽卡(扣石); 私聊抽卡免费且不涉及积分"""
        gid = event.get_group_id()
        uid = event.get_sender_id()
        if not uid or uid == event.get_self_id():
            return
        is_private = not gid
        if not is_private:
            scope = self._get("group_scope") or []
            if scope and gid not in scope:
                return

        text = event.message_str.strip()
        ep = self._get("enable_points", True)
        enable_points = (ep.strip().lower() in ("1", "true", "yes", "on")) if isinstance(ep, str) else bool(ep)
        pr = self._get("points_off_reply", True)
        points_off_reply = (pr.strip().lower() in ("1", "true", "yes", "on")) if isinstance(pr, str) else bool(pr)

        # 私聊不做任何积分限制; 群聊积分关闭时同样免费
        gacha_free = is_private or not enable_points
        if is_private:
            if text in ("签到", "看看存石"):
                yield event.plain_result("私聊不统计积分，签到和看看存石请在群聊中使用。")
                return
            if text not in ("来发单抽", "来发十连"):
                return
        elif not enable_points:
            # 积分关闭: 水群/签到不再得分, 签到/看存石停用; 抽卡照常但免费(不扣石、不校验余额)
            if text in ("签到", "看看存石"):
                if points_off_reply:
                    yield event.plain_result("积分功能已关闭，签到和看看存石暂时都不可用。")
                return
            if text not in ("来发单抽", "来发十连"):
                return

        comps = event.get_messages()
        has_pic = any(getattr(c, "type", None) == "Image" for c in comps)
        no_points = [str(k) for k in (self._get("command_keys_no_points") or [])]
        pts_text = nonneg_int(self._get("points_msg_text"), 5)
        pts_image = nonneg_int(self._get("points_msg_image"), 10)
        pts_checkin = nonneg_int(self._get("points_checkin"), 30)

        store = store_dir()
        points_path = store / "points.json"
        gacha_count_path = store / "gacha_count.json"
        count_key = f"private_{uid}" if is_private else gid

        gacha_cmd = None
        reply = None       # 签到/看看存石 的文本回答
        at_reply = None    # 需要 @ 的文本回答 (uid, text)

        if gacha_free:
            # 免费抽卡: 只记录抽卡次数, 完全不读写 points.json
            gacha_cmd = text
            async with self._lock:
                counts = load_json(gacha_count_path, {})
                g = counts.setdefault(count_key, {})
                today = date.today().isoformat()
                g[today] = int(g.get(today) or 0) + (10 if text == "来发十连" else 1)
                save_json(gacha_count_path, counts)
        else:
            async with self._lock:
                data = load_json(points_path, {})
                me = data.setdefault(gid, {}).setdefault(uid, {"points": 0, "checkin": ""})

                # ---- 积分记账(与 ufo_points 原逻辑一致) ----
                if has_pic:
                    me["points"] += pts_image
                elif text and text not in no_points:
                    me["points"] += pts_text

                # ---- 签到 / 看看存石 ----
                if text == "签到":
                    today = date.today().isoformat()
                    if me["checkin"] == today:
                        reply = "今天已经签到过了，请明日再来吧！"
                    else:
                        me["checkin"] = today
                        me["points"] += pts_checkin
                        reply = f"签到成功，当前有{me['points']}颗石头，请多多水群赚取石头吧！"
                elif text == "看看存石":
                    reply = f"当前圣晶石只有{me['points']}颗，请多多水群或是每日签到赚取石头哦！"

                # ---- 抽卡命令: 校验并扣石 ----
                if text in ("来发单抽", "来发十连"):
                    default_cost = 3 if text == "来发单抽" else 30
                    cost = nonneg_int(self._get("cost_danchou" if text == "来发单抽" else "cost_shilian"), default_cost)
                    points = int(me.get("points") or 0)
                    if points < cost:
                        at_reply = (uid, f"当前圣晶石只有{points}颗，本次需要{cost}颗，请多多水群或是每日签到赚取石头哦！")
                    else:
                        me["points"] = points - cost
                        gacha_cmd = text
                        counts = load_json(gacha_count_path, {})
                        g = counts.setdefault(gid, {})
                        g[date.today().isoformat()] = int(g.get(date.today().isoformat()) or 0) + (10 if text == "来发十连" else 1)
                        save_json(gacha_count_path, counts)

                save_json(points_path, data)

        # ---- 锁外回复(不阻塞其它消息的积分读写) ----
        at_user = [] if is_private else [Comp.At(qq=uid)]
        if reply:
            yield event.plain_result(reply)
        if at_reply:
            yield event.chain_result([Comp.At(qq=at_reply[0]), Comp.Plain(at_reply[1])])
        if not gacha_cmd:
            return

        try:
            ups, rates, rules = self._pool_cfg()
            if gacha_cmd == "来发单抽":
                img = dan_chou(self._chouka_dir(), ups, rates, rules)
            else:
                cache = self._chouka_dir() / "cache" / "1.png"
                img = shi_lian(self._chouka_dir(), cache, ups, rates, rules)
            if not img:
                yield event.chain_result(at_user + [Comp.Plain("卡池图片缺失，请先在设置页执行「立即增量抓取」")])
            else:
                yield event.chain_result(at_user + [Comp.Image.fromFileSystem(img)])
        except Exception as e:
            logger.error(f"fgo_gacha 抽卡失败: {e}")
            yield event.chain_result(at_user + [Comp.Plain(f"抽卡失败: {e}")])
