"""MacroMind V6: single-user macro training, with traceable server-side data."""
import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, session, send_from_directory

UTC = timezone.utc
BASE = Path(__file__).parent


def now():
    return datetime.now(UTC)


def stamp():
    return now().isoformat()


MACRO = [
    ("cpi", "美国 CPI 同比", "CPIAUCSL", "pc1", "%", 75),
    ("core_cpi", "美国核心 CPI 同比", "CPILFESL", "pc1", "%", 75),
    ("pce", "美国 PCE 同比", "PCEPI", "pc1", "%", 80),
    ("core_pce", "美国核心 PCE 同比", "PCEPILFE", "pc1", "%", 80),
    ("payrolls", "美国非农月度变化", "PAYEMS", "chg", "千人", 65),
    ("unemployment", "美国失业率", "UNRATE", "lin", "%", 65),
    ("gdp", "美国实际 GDP 年化季环比", "A191RL1Q225SBEA", "lin", "%", 200),
    ("retail", "美国零售月环比", "RSAFS", "pch", "%", 75),
    ("fed", "有效联邦基金利率", "DFF", "lin", "%", 7),
]
MARKETS = [
    ("sp500", "S&P 500", "SP500", "点"),
    ("nasdaq100", "NASDAQ 100", "NASDAQ100", "点"),
    ("gold", "现货黄金", None, "美元/盎司"),
    ("wti", "WTI 现货参考价", "DCOILWTICO", "美元/桶"),
    ("vix", "VIX", "VIXCLS", "点"),
    ("dxy", "美元指数 DXY", None, "点"),
    ("us2y", "美国 2 年期国债收益率", "DGS2", "%"),
    ("us10y", "美国 10 年期国债收益率", "DGS10", "%"),
    ("usdcny", "美元/人民币", "DEXCHUS", "人民币/美元"),
    ("csi300", "沪深 300", None, "点"),
    ("hsi", "恒生指数", None, "点"),
]
QUESTIONS = [
    "增长与通胀分别在改善还是恶化？列出证据，并区分数据与推断。",
    "你的判断将如何经由政策、利率和美元传导到目标资产？",
    "什么数据会推翻你的判断？未来 7 天你准备观察什么？",
]


def fetch_json(url):
    # Never log URLs: provider keys are query parameters.
    try:
        with urlopen(url, timeout=15) as response:
            data = json.loads(response.read(2_000_000))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except HTTPError as exc:
        raise ValueError("供应商 HTTP %s；请检查密钥、额度或权限" % exc.code) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ValueError("供应商请求失败或响应无效") from None


def finite(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("非有效数值")
    return number


def create_app(config=None):
    app = Flask(__name__, static_folder="static")
    app.config.update(
        DB_PATH=os.environ.get("DB_PATH", str(BASE / "data/macromind.sqlite3")),
        APP_PASSWORD=os.environ.get("APP_PASSWORD", ""),
        SECRET_KEY=os.environ.get("SESSION_SECRET", "") or secrets.token_hex(32),
        FRED_API_KEY=os.environ.get("FRED_API_KEY", ""),
        TWELVE_DATA_API_KEY=os.environ.get("TWELVE_DATA_API_KEY", ""),
        MARKET_SYMBOLS_JSON=os.environ.get("MARKET_SYMBOLS_JSON", "{}"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=os.environ.get("APP_ENV") == "production",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=32_768,
        START_SCHEDULER=os.environ.get("ENABLE_SCHEDULER", "true") == "true",
    )
    if config:
        app.config.update(config)
    if os.environ.get("APP_ENV") == "production":
        if len(app.config["APP_PASSWORD"]) < 16 or len(os.environ.get("SESSION_SECRET", "")) < 32:
            raise RuntimeError("Production requires APP_PASSWORD >=16 and SESSION_SECRET >=32 characters")
    try:
        symbols = json.loads(app.config["MARKET_SYMBOLS_JSON"])
        if not isinstance(symbols, dict) or any(k not in {m[0] for m in MARKETS} or not isinstance(v, str) or len(v) > 100 for k, v in symbols.items()):
            raise ValueError()
    except (ValueError, TypeError):
        raise RuntimeError("MARKET_SYMBOLS_JSON must map supported asset IDs to provider symbols") from None

    path = Path(app.config["DB_PATH"])
    path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def db():
        con = sqlite3.connect(str(path), timeout=10)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    with db() as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS cache (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS refresh_state (kind TEXT PRIMARY KEY, attempted_at TEXT NOT NULL, completed_at TEXT);
        CREATE TABLE IF NOT EXISTS judgments (
            id TEXT PRIMARY KEY, day TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, payload TEXT NOT NULL, snapshot TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews (
            judgment_id TEXT NOT NULL, horizon INTEGER NOT NULL, created_at TEXT NOT NULL,
            notes TEXT NOT NULL, PRIMARY KEY(judgment_id, horizon),
            FOREIGN KEY(judgment_id) REFERENCES judgments(id));
        """)

    def put(item):
        with db() as con:
            con.execute("INSERT OR REPLACE INTO cache VALUES (?,?)", (item["id"], json.dumps(item, ensure_ascii=False)))

    def cached(key):
        with db() as con:
            row = con.execute("SELECT payload FROM cache WHERE id=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def metadata(kind):
        if kind == "macro":
            return [dict(id=m[0], label=m[1], series=m[2], units=m[3], unit=m[4], max_age_days=m[5], provider="FRED") for m in MACRO]
        result = []
        for key, label, series, unit in MARKETS:
            result.append(dict(id=key, label=label, series=series, units="lin", unit=unit,
                               max_age_days=7, provider="Twelve Data" if symbols.get(key) else "FRED" if series else None,
                               symbol=symbols.get(key)))
        return result

    def configured(meta):
        return bool(app.config["TWELVE_DATA_API_KEY"] if meta["provider"] == "Twelve Data" else app.config["FRED_API_KEY"] if meta["provider"] == "FRED" else False)

    def data_for(kind):
        items = []
        for meta in metadata(kind):
            item = cached(kind + ":" + meta["id"])
            if item is None:
                item = dict(meta, value=None, observation_date=None, fetched_at=None, source_url=None,
                            status="UNKNOWN" if configured(meta) else "UNCONFIGURED",
                            error=None)
            else:
                item = dict(item)
                item["id"] = meta["id"]
                if item.get("value") is not None:
                    age = (now().date() - datetime.fromisoformat(item["observation_date"]).date()).days
                    fetch_age = (now() - datetime.fromisoformat(item["fetched_at"])).total_seconds()
                    if age > meta["max_age_days"] or fetch_age > (172800 if kind == "macro" else 86400) or item.get("error"):
                        item["status"] = "STALE"
            items.append(item)
        if kind == "macro":
            for key, label in [("cn_gdp", "中国 GDP"), ("cn_cpi", "中国 CPI"), ("cn_ppi", "中国 PPI"), ("cn_pmi", "中国制造业 PMI"), ("cn_m2", "中国 M2"), ("cn_credit", "中国社融"), ("cn_loans", "中国新增贷款"), ("cn_lpr", "中国 LPR")]:
                items.append(dict(id=key, label=label, value=None, status="UNCONFIGURED", provider=None,
                                  unit="", observation_date=None, fetched_at=None, error="尚未接入可验证的中国官方数据源"))
        return items

    def fred(meta):
        params = dict(series_id=meta["series"], api_key=app.config["FRED_API_KEY"], file_type="json", sort_order="desc", limit=20, units=meta["units"])
        raw = fetch_json("https://api.stlouisfed.org/fred/series/observations?" + urlencode(params))
        rows = [r for r in raw.get("observations", []) if r.get("value") not in (None, ".")]
        if not rows:
            raise ValueError("供应商未返回有效观测值")
        row = rows[0]
        date = datetime.strptime(row["date"], "%Y-%m-%d").date()
        if date > now().date():
            raise ValueError("供应商返回未来观测日期")
        return dict(value=finite(row["value"]), previous=finite(rows[1]["value"]) if len(rows)>1 else None,
                    observation_date=date.isoformat(), previous_date=rows[1]["date"] if len(rows)>1 else None,
                    realtime_start=row.get("realtime_start"), realtime_end=row.get("realtime_end"),
                    source_url="https://fred.stlouisfed.org/series/" + meta["series"],
                    status="EOD" if meta["id"] in {m[0] for m in MARKETS} else "RELEASED",
                    timing_note="FRED 最新修订序列；观测日期不是发布日期，不作为历史首次发布值。")

    def twelve(meta):
        params = dict(symbol=meta["symbol"], interval="1day", outputsize=3, timezone="UTC", apikey=app.config["TWELVE_DATA_API_KEY"])
        raw = fetch_json("https://api.twelvedata.com/time_series?" + urlencode(params))
        # Current daily bar can still be forming. Only retain strictly earlier UTC dates.
        rows = [r for r in raw.get("values", []) if str(r.get("datetime", ""))[:10] < now().date().isoformat()]
        if not rows:
            raise ValueError("行情不可用；请检查 symbol、订阅权限和调用额度")
        date = datetime.strptime(rows[0]["datetime"][:10], "%Y-%m-%d").date()
        return dict(value=finite(rows[0]["close"]), previous=finite(rows[1]["close"]) if len(rows)>1 else None,
                    observation_date=date.isoformat(), previous_date=rows[1]["datetime"][:10] if len(rows)>1 else None,
                    source_url="https://twelvedata.com/", status="EOD",
                    timing_note="供应商日线收盘值；剔除当前 UTC 日期的未完成日线，不宣称实时。")

    locks = {"market": threading.Lock(), "macro": threading.Lock()}

    def refresh(kind):
        if not locks[kind].acquire(blocking=False):
            return False
        try:
            with db() as con:
                con.execute("INSERT OR REPLACE INTO refresh_state VALUES (?,?,NULL)", (kind, stamp()))
            for meta in metadata(kind):
                if not configured(meta):
                    continue
                key = kind + ":" + meta["id"]
                try:
                    fields = twelve(meta) if meta["provider"] == "Twelve Data" else fred(meta)
                    put(dict(meta, **fields, fetched_at=stamp(), last_attempt_at=stamp(), error=None) | {"id":key})
                except (ValueError, KeyError, TypeError, OverflowError):
                    old = cached(key) or dict(meta, id=key, value=None, observation_date=None, fetched_at=None, status="ERROR")
                    old.update(error="获取失败：请检查数据源密钥、权限、额度或网络", last_attempt_at=stamp())
                    put(old)
                if meta["provider"] == "Twelve Data":
                    time.sleep(8)  # conservative pacing; one worker only
            with db() as con:
                con.execute("UPDATE refresh_state SET completed_at=? WHERE kind=?", (stamp(), kind))
            return True
        finally:
            locks[kind].release()

    app.extensions["refresh"] = refresh
    app.extensions["data_for"] = data_for
    app.extensions["db"] = db

    def scheduler():
        due = {"macro": 0, "market": 0}
        while True:
            for kind, interval in (("macro", 21600), ("market", 3600)):
                if time.monotonic() >= due[kind]:
                    try:
                        refresh(kind)
                    except Exception:
                        app.logger.error("Refresh failed for %s (details redacted)", kind)
                    due[kind] = time.monotonic() + interval
            time.sleep(30)

    if app.config["START_SCHEDULER"]:
        threading.Thread(target=scheduler, daemon=True).start()

    login_attempts = []
    login_lock = threading.Lock()

    @app.before_request
    def auth():
        if request.path == "/api/health" or request.path == "/api/session" or not request.path.startswith("/api/"):
            return None
        if not app.config["APP_PASSWORD"]:
            return jsonify(error="请先在服务端设置 APP_PASSWORD"), 503
        if not session.get("authenticated"):
            return jsonify(error="请先登录"), 401
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session.get("csrf", "!")):
                return jsonify(error="会话验证失败，请重新登录"), 403

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.route("/api/session", methods=["GET", "POST", "DELETE"])
    def login():
        if request.method == "DELETE":
            if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session.get("csrf", "!")):
                return jsonify(error="会话验证失败"), 403
            session.clear()
            return jsonify(ok=True)
        if request.method == "POST":
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or not isinstance(body.get("password"), str):
                return jsonify(error="请输入密码"), 400
            password = app.config["APP_PASSWORD"]
            if not password:
                return jsonify(error="服务端尚未设置访问密码"), 503
            with login_lock:
                login_attempts[:] = [t for t in login_attempts if time.monotonic() - t < 60]
                if len(login_attempts) >= 10:
                    return jsonify(error="尝试过于频繁，请一分钟后再试"), 429
                login_attempts.append(time.monotonic())
            if not hmac.compare_digest(hashlib.sha256(body["password"].encode()).digest(), hashlib.sha256(password.encode()).digest()):
                return jsonify(error="密码不正确"), 401
            session.clear()
            session.update(authenticated=True, csrf=secrets.token_hex(32))
            session.permanent = True
        return jsonify(authenticated=bool(session.get("authenticated")), csrf=session.get("csrf") if session.get("authenticated") else None)

    @app.get("/api/health")
    def health():
        try:
            with db() as con:
                con.execute("SELECT COUNT(*) FROM judgments").fetchone()
            return jsonify(status="ok", version="6.0.0", database="ok")
        except sqlite3.Error:
            return jsonify(status="error", database="unavailable"), 503

    @app.get("/api/market")
    def market():
        return jsonify(items=data_for("market"), generated_at=stamp())

    @app.get("/api/macro")
    def macro():
        return jsonify(items=data_for("macro"), generated_at=stamp())

    @app.get("/api/macro/<series>")
    def macro_one(series):
        item = next((m for m in data_for("macro") if m["id"] == series), None)
        return (jsonify(item), 200) if item else (jsonify(error="未知指标"), 404)

    @app.get("/api/briefing")
    def briefing():
        markets, macros = data_for("market"), data_for("macro")
        available = [m for m in markets + macros if m.get("value") is not None and m["status"] not in ("STALE", "ERROR")]
        observations = []
        for m in available:
            change = None if m.get("previous") is None else round(m["value"] - m["previous"], 4)
            observations.append(dict(label=m["label"], value=m["value"], unit=m["unit"], change=change,
                                     observation_date=m["observation_date"], source_url=m.get("source_url")))
        return jsonify(date=now().astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(), generated_at=stamp(),
                       status="DATA_AVAILABLE" if available else "INSUFFICIENT_DATA", observations=observations,
                       coverage=dict(available=len(available), total=len(markets + macros)),
                       title="从证据出发，形成自己的判断", questions=QUESTIONS,
                       note="简报由已获取数据整理，不是新闻摘要或投资建议；缺少 Consensus 时不计算预期差。",
                       macro_score=None, score_note="尚未建立经验证的评分模型，不生成综合分数。")

    @app.get("/api/status")
    def status():
        with db() as con:
            runs = [dict(r) for r in con.execute("SELECT * FROM refresh_state")]
        return jsonify(fred_configured=bool(app.config["FRED_API_KEY"]), market_key_configured=bool(app.config["TWELVE_DATA_API_KEY"]),
                       market_symbols=sorted(symbols), refresh=runs, running={k:v.locked() for k,v in locks.items()})

    @app.post("/api/refresh/<kind>")
    def refresh_endpoint(kind):
        if kind not in locks:
            return jsonify(error="未知数据类别"), 404
        with db() as con:
            row = con.execute("SELECT attempted_at FROM refresh_state WHERE kind=?", (kind,)).fetchone()
        if locks[kind].locked() or (row and (now() - datetime.fromisoformat(row[0])).total_seconds() < 300):
            return jsonify(error="已在刷新或距上次刷新不足 5 分钟"), 429
        threading.Thread(target=refresh, args=(kind,), daemon=True).start()
        return jsonify(status="queued"), 202

    def judgment_rows():
        with db() as con:
            rows = con.execute("SELECT * FROM judgments ORDER BY day DESC").fetchall()
            reviews = [dict(r) for r in con.execute("SELECT * FROM reviews")]
        result = []
        for r in rows:
            item = dict(r)
            item["payload"] = json.loads(item["payload"])
            item["snapshot"] = json.loads(item["snapshot"])
            item["reviews"] = [v for v in reviews if v["judgment_id"] == item["id"]]
            item["review_due"] = {str(d):(datetime.fromisoformat(item["day"]) + timedelta(days=d)).date().isoformat() for d in (1,7,30)}
            result.append(item)
        return result

    @app.route("/api/judgments", methods=["GET", "POST"])
    def judgments():
        if request.method == "GET":
            return jsonify(items=judgment_rows())
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="需要 JSON 对象"), 400
        answers = body.get("answers")
        confidence = body.get("confidence")
        if (not isinstance(answers, list) or len(answers)!=3 or any(not isinstance(v,str) or not 5 <= len(v.strip()) <= 4000 for v in answers)
            or body.get("asset") not in {m[0] for m in MARKETS} or body.get("direction") not in ("up", "down", "neutral")
            or type(confidence) is not int or not 0 <= confidence <= 100):
            return jsonify(error="请填写三条 5–4000 字回答、目标资产、方向及 0–100 的信心值"), 400
        day = now().astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        payload = dict(answers=[s.strip() for s in answers], asset=body["asset"], direction=body["direction"], confidence=confidence)
        snapshot = dict(market=data_for("market"), macro=data_for("macro"), captured_at=stamp())
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("SELECT id FROM judgments WHERE day=?", (day,)).fetchone()
            if existing:
                return jsonify(error="今日判断已保存。为保留当时判断，原始记录不可覆盖。", id=existing[0]), 409
            key = secrets.token_hex(12)
            con.execute("INSERT INTO judgments VALUES (?,?,?,?,?,?)", (key, day, stamp(), stamp(), json.dumps(payload,ensure_ascii=False), json.dumps(snapshot,ensure_ascii=False)))
        return jsonify(id=key, day=day, saved=True), 201

    @app.post("/api/judgments/<key>/reviews")
    def review(key):
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or type(body.get("horizon")) is not int or body["horizon"] not in (1,7,30) or not isinstance(body.get("notes"), str) or not 5 <= len(body["notes"].strip()) <= 4000:
            return jsonify(error="请填写复盘周期和 5–4000 字复盘内容"), 400
        with db() as con:
            row = con.execute("SELECT day FROM judgments WHERE id=?", (key,)).fetchone()
            if not row:
                return jsonify(error="判断不存在"), 404
            due = datetime.fromisoformat(row[0]).date() + timedelta(days=body["horizon"])
            if now().astimezone(ZoneInfo("Asia/Shanghai")).date() < due:
                return jsonify(error="尚未到复盘日期"), 409
            con.execute("INSERT OR REPLACE INTO reviews VALUES (?,?,?,?)", (key, body["horizon"], stamp(), body["notes"].strip()))
        return jsonify(saved=True)

    @app.get("/api/export")
    def export():
        response = jsonify(version="6.0.0", exported_at=stamp(), judgments=judgment_rows())
        response.headers["Content-Disposition"] = 'attachment; filename="macromind-judgments.json"'
        return response

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error="请求内容过长"), 413

    return app
