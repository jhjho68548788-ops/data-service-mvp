import csv
import json
import os
import re
import hashlib
import random
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(REPO_ROOT, "data")
STATIC_DIR = os.path.join(REPO_ROOT, "static")
FEATURE_SPECS_DIR = os.path.join(DATA_DIR, "feature-specs")
DEBUG = os.environ.get("MVP_DEBUG", "0") == "1"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
# 브라우저가 예전 프로세스에 붙었는지 구분용 (루트 JSON에도 포함)
MVP_BUILD_ID = "2026-03-27-code-hero"
# 기본 127.0.0.1: localhost(IPv6)와 IPv4 리스너가 갈라지는 경우를 줄임. LAN 공개는 MVP_HOST=0.0.0.0
MVP_HOST = os.environ.get("MVP_HOST", "127.0.0.1")


def _feature_spec_filename_ok(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._-]+\.json", name))

def _list_feature_spec_files() -> list[str]:
    try:
        names = [n for n in os.listdir(FEATURE_SPECS_DIR) if n.endswith(".json")]
    except FileNotFoundError:
        return []
    return sorted([n for n in names if _feature_spec_filename_ok(n)])

def _series_meta_list() -> list[dict]:
    items = []
    meta = STATE.get("series_meta_by_code") or {}
    for code, row in meta.items():
        if not isinstance(row, dict):
            continue
        items.append(
            {
                "code": code,
                "koName": (row.get("상세설명") or "").strip(),
                "enName": (row.get("영문명") or "").strip(),
                "pointFreq": (row.get("데이터포인트_주기") or "").strip(),
                "releaseFreq": (row.get("발표_주기") or "").strip(),
                "scale": (row.get("스케일") or "").strip(),
                "unit": (row.get("단위") or "").strip(),
            }
        )
    items.sort(key=lambda x: x["code"])
    return items


def _normalize_http_path(path: str) -> str:
    """Strip trailing slash so /app/ and /app both work (except root '/')."""
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path


def _safe_read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _stable_seed_int(seed_str: str) -> int:
    # Python hash is randomized per process; md5 gives stable results.
    h = hashlib.md5(seed_str.encode("utf-8")).hexdigest()[:16]
    return int(h, 16)


def _add_months(year: int, month: int, delta_months: int):
    # month is 1..12
    m = (month - 1) + delta_months
    y = year + (m // 12)
    mo = (m % 12) + 1
    return y, mo


def _mock_time_series(seed: str, periods: int = 12):
    """
    Deterministic mock series: linear-ish trend + noise.
    Returns list of {date: YYYY-MM-DD, value: float}.
    """
    rng = random.Random(_stable_seed_int(seed))

    # Start from 2024-01 to keep response compact and consistent.
    start_year, start_month = 2024, 1

    base = rng.uniform(0.5, 2.0)
    slope = rng.uniform(-0.1, 0.3)
    noise_scale = rng.uniform(0.05, 0.2)

    series = []
    for i in range(periods):
        y, m = _add_months(start_year, start_month, delta_months=i)
        date = f"{y:04d}-{m:02d}-01"
        value = base + slope * i + rng.gauss(0, noise_scale)
        series.append({"date": date, "value": round(float(value), 3)})
    return series


def _canonical_dataset_id(spec_id: str, x_source_id: str, y_source_id: str):
    raw = f"{spec_id}|{x_source_id}|{y_source_id}"
    h = hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]
    return f"ds-{h}"


def align_series_values_on_common_dates(x_vals: list, y_vals: list):
    """Return parallel value lists where both series have the same date (inner join, sorted)."""
    xm = {p["date"]: float(p["value"]) for p in x_vals}
    ym = {p["date"]: float(p["value"]) for p in y_vals}
    common = sorted(set(xm) & set(ym))
    if not common:
        return [], []
    return [xm[d] for d in common], [ym[d] for d in common]

def _is_nonempty_str_list(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v) and len(v) > 0


def _pearson_corr(xs, ys) -> float:
    n = len(xs)
    if n == 0 or n != len(ys):
        return 0.0
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    cov = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    x_var = sum((x - x_mean) ** 2 for x in xs)
    y_var = sum((y - y_mean) ** 2 for y in ys)
    denom = (x_var * y_var) ** 0.5
    if denom == 0:
        return 0.0
    return round(cov / denom, 4)


DEFAULT_SOURCES = [
    {
        "sourceId": "bank-ecos",
        "type": "public",
        "displayName": "Korea Bank ECOS (mock)",
        "connector": {"kind": "csv", "seriesCode": "ECOS.POLICY.RATE_BASE"},
        "ratePolicy": {"maxCallsPerHour": 120},
        "normalizationProfileId": "macro-index-v1",
    },
    {
        "sourceId": "vendor-steel",
        "type": "vendor",
        "displayName": "Worldsteel (mock)",
        "connector": {"kind": "csv", "seriesCode": "WS.MKT.STEEL_HRC_IDX"},
        "licensePolicy": {
            "quotaCallsPerMonth": 5000,
            "apiKeyValidityDays": 30,
            "estimatedMonthlyCost": 1200,
        },
        "ratePolicy": {"maxCallsPerHour": 30},
        "normalizationProfileId": "steel-index-v1",
    },
]

DEFAULT_THEMES = {
    "org-acme": {
        "orgId": "org-acme",
        "themeId": "acme-default",
        "tokens": {
            "fontFamily": "Noto Sans KR, Arial, sans-serif",
            "primaryColor": "#1E66F5",
            "secondaryColor": "#6B7280",
            "backgroundColor": "#FFFFFF",
            "surfaceColor": "#F9FAFB",
            "textColor": "#111827",
        },
        "componentRules": {"cardRadius": 12, "chartAccent": "primary"},
    }
}

DEFAULT_PERMISSIONS = {
    "users": [
        {
            "userId": "public-only",
            "allowedSourceIds": ["bank-ecos"],
            "allowedFeatureIds": ["*"],
        },
        {
            "userId": "full-access",
            "allowedSourceIds": ["bank-ecos", "vendor-steel"],
            "allowedFeatureIds": ["*"],
        },
    ]
}

DEFAULT_CODE_REGISTRY = {
    "domainToSource": {
        "ECOS": "bank-ecos",
        "BOK": "bank-ecos",
        "FRED": "bank-ecos",
        "WB": "bank-ecos",
        "KIS": "bank-ecos",
        "MOIS": "bank-ecos",
        "KEPCO": "bank-ecos",
        "WS": "vendor-steel",
        "LME": "vendor-steel",
        "SGX": "vendor-steel",
        "PLATTS": "vendor-steel",
    }
}


def load_series_values_csv():
    """Long-format CSV: code,date,value -> dict[code, list[{date,value}]]."""
    path = os.path.join(DATA_DIR, "series_values.csv")
    by_code: dict = {}
    if not os.path.isfile(path):
        return by_code
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("code") or "").strip()
            if not code:
                continue
            d = (row.get("date") or "").strip()
            if not d:
                continue
            try:
                val = float(row["value"])
            except (KeyError, ValueError):
                continue
            by_code.setdefault(code, []).append({"date": d, "value": val})
    for code in by_code:
        by_code[code].sort(key=lambda p: p["date"])
    return by_code


def load_series_meta_by_code():
    path = os.path.join(DATA_DIR, "series_meta.csv")
    by_code: dict = {}
    if not os.path.isfile(path):
        return by_code
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("code") or "").strip()
            if code:
                by_code[code] = row
    return by_code


def load_code_registry():
    path = os.path.join(DATA_DIR, "code_registry.json")
    raw = _safe_read_json(path, {})
    dom = raw.get("domainToSource")
    if isinstance(dom, dict) and dom:
        return {"domainToSource": dict(dom)}
    return {"domainToSource": dict(DEFAULT_CODE_REGISTRY["domainToSource"])}


def load_data():
    sources_path = os.path.join(DATA_DIR, "sources.json")
    permissions_path = os.path.join(DATA_DIR, "permissions.json")

    sources = _safe_read_json(sources_path, DEFAULT_SOURCES)
    permissions = _safe_read_json(permissions_path, DEFAULT_PERMISSIONS)

    themes = dict(DEFAULT_THEMES)
    themes_dir = os.path.join(DATA_DIR, "themes")
    if os.path.isdir(themes_dir):
        # Load any *.json as theme config for orgId.
        for name in os.listdir(themes_dir):
            if not name.endswith(".json"):
                continue
            theme = _safe_read_json(os.path.join(themes_dir, name), None)
            if theme and isinstance(theme, dict) and "orgId" in theme:
                themes[theme["orgId"]] = theme

    sources_by_id = {s["sourceId"]: s for s in sources}
    return sources, sources_by_id, themes, permissions


def make_state():
    sources, sources_by_id, themes_by_org, permissions = load_data()
    return {
        "sources": sources,
        "sources_by_id": sources_by_id,
        "themes_by_org": themes_by_org,
        "permissions": permissions,
        "series_by_code": load_series_values_csv(),
        "series_meta_by_code": load_series_meta_by_code(),
        "code_registry": load_code_registry(),
    }


STATE = make_state()


def reload_state():
    global STATE
    STATE = make_state()
    return STATE


def is_feature_allowed(user_permissions, spec_id: str) -> bool:
    allowed = user_permissions.get("allowedFeatureIds", ["*"])
    if not allowed:
        return False
    if "*" in allowed:
        return True
    return spec_id in allowed


def get_user_permissions(user_id: str):
    for u in STATE["permissions"].get("users", []):
        if u.get("userId") == user_id:
            return u
    return None


def fetch_raw_series(source_cfg: dict):
    connector = source_cfg.get("connector") or {}
    kind = connector.get("kind", "mock")
    if kind == "mock":
        seed = connector.get("seed", source_cfg.get("sourceId", "seed"))
        return _mock_time_series(seed=seed)
    if kind == "csv":
        code = connector.get("seriesCode")
        if not code:
            raise ValueError("csv connector requires seriesCode")
        rows = STATE["series_by_code"].get(code)
        if not rows:
            raise ValueError(f"No rows in series_values.csv for code: {code}")
        return [{"date": p["date"], "value": float(p["value"])} for p in rows]
    raise ValueError(f"Unsupported connector kind: {kind}")


def unit_label_for_source(source_cfg: dict) -> str:
    conn = source_cfg.get("connector") or {}
    code = conn.get("seriesCode")
    if not code:
        return ""
    row = STATE["series_meta_by_code"].get(code) or {}
    return (row.get("단위") or "").strip()


def domain_from_series_code(series_code: str) -> str:
    code = (series_code or "").strip()
    if "." not in code:
        raise ValueError(f"Invalid series code (expected DOMAIN.SUB...): {series_code!r}")
    return code.split(".", 1)[0].strip()


def resolve_source_id_for_series_code(series_code: str) -> str:
    dom = domain_from_series_code(series_code)
    reg = STATE.get("code_registry") or {}
    m = reg.get("domainToSource") or {}
    sid = m.get(dom)
    if not sid:
        raise ValueError(
            f"No domainToSource mapping for domain '{dom}' (code={series_code}). "
            "Edit data/code_registry.json"
        )
    return sid


def fetch_series_rows_by_code(series_code: str) -> list:
    rows = STATE["series_by_code"].get(series_code)
    if not rows:
        raise ValueError(
            f"No rows in series_values.csv for code: {series_code}. "
            "Check data/series_values.csv or run tools/generate_series_values.py"
        )
    return [{"date": p["date"], "value": float(p["value"])} for p in rows]


def unit_label_for_series_code(series_code: str) -> str:
    row = STATE["series_meta_by_code"].get(series_code) or {}
    return (row.get("단위") or "").strip()


def normalize_to_canonical(role: str, source_cfg: dict):
    """
    MVP normalization:
    - Produce canonical time_series dataset fields:
      - timeField is implicit: "date"
      - series name is role ("x" / "y")
    """
    raw_series = fetch_raw_series(source_cfg)
    return {"name": role, "values": raw_series}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, code: int, data: bytes, content_type: str):
        self.send_response(code)
        self._send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if DEBUG:
            # Debug-only logging: helps when clients send unexpected payload formats.
            # Keep it lightweight (truncate raw output).
            print(f"[mvp] POST body Content-Length={length}", file=sys.stderr, flush=True)
        if length <= 0:
            if DEBUG:
                print("[mvp] POST body length is 0/negative; cannot read JSON.", file=sys.stderr, flush=True)
            return None
        raw = self.rfile.read(length)
        if DEBUG:
            preview = raw[:200].decode("utf-8", errors="replace")
            print(f"[mvp] POST body raw preview={preview!r}", file=sys.stderr, flush=True)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            if DEBUG:
                print("[mvp] JSONDecodeError while parsing request body", file=sys.stderr, flush=True)
            return None

    def do_GET(self):
        parsed = urlparse(self.path)
        path = _normalize_http_path(parsed.path)

        if path == "/" or path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "data-service-mvp",
                    "mvpBuild": MVP_BUILD_ID,
                    "endpoints": {
                        "GET /app": "browser user demo (HTML)",
                        "GET /sources": "list data sources",
                        "GET /themes/{orgId}": "get theme by orgId",
                        "GET /series-meta": "list series codes from series_meta.csv",
                        "GET /feature-specs": "list feature specs (filenames)",
                        "GET /feature-specs/{file}.json": "read feature spec from data/feature-specs",
                        "POST /build-feature": "build feature runtime from spec",
                        "POST /reload-config": "reload data/* configs",
                    },
                },
            )
            return

        if path == "/app":
            app_path = os.path.join(STATIC_DIR, "app.html")
            try:
                with open(app_path, "rb") as f:
                    body = f.read()
                self._send_bytes(200, body, "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send_json(404, {"error": "app.html not found"})
            return

        if path == "/feature-specs":
            self._send_json(200, {"files": _list_feature_spec_files()})
            return

        if path == "/series-meta":
            self._send_json(200, {"series": _series_meta_list()})
            return

        m_spec = re.match(r"^/feature-specs/([^/]+)$", path)
        if m_spec:
            fn = m_spec.group(1)
            if not _feature_spec_filename_ok(fn):
                self._send_json(400, {"error": "Invalid feature spec filename"})
                return
            fp = os.path.join(FEATURE_SPECS_DIR, fn)
            try:
                with open(fp, "rb") as f:
                    body = f.read()
                self._send_bytes(200, body, "application/json; charset=utf-8")
            except FileNotFoundError:
                self._send_json(404, {"error": "Feature spec not found", "file": fn})
            return

        if path == "/sources":
            self._send_json(200, {"sources": STATE["sources"]})
            return

        m = re.match(r"^/themes/([^/]+)$", path)
        if m:
            org_id = m.group(1)
            theme = STATE["themes_by_org"].get(org_id)
            if not theme:
                self._send_json(404, {"error": "Theme not found", "orgId": org_id})
                return
            self._send_json(200, theme)
            return

        self._send_json(
            404,
            {
                "error": "Not found",
                "path": path,
                "hint": "브라우저 유저 데모: GET /app (끝 슬래시 없이도 됨). API 목록: GET /",
            },
        )

    def do_POST(self):
        parsed = urlparse(self.path)
        path = _normalize_http_path(parsed.path)

        if path == "/reload-config":
            if ADMIN_TOKEN:
                token = self.headers.get("X-Admin-Token", "")
                if token != ADMIN_TOKEN:
                    self._send_json(403, {"error": "Invalid admin token"})
                    return
            reload_state()
            self._send_json(200, {"ok": True})
            return

        if path != "/build-feature":
            self._send_json(404, {"error": "Not found"})
            return

        body = self._read_json_body()
        if not body or not isinstance(body, dict):
            self._send_json(400, {"error": "Invalid JSON body"})
            return

        user_id = body.get("userId")
        spec = body.get("spec")
        if not user_id or not spec:
            self._send_json(400, {"error": "Missing userId or spec"})
            return
        if not isinstance(spec, dict):
            self._send_json(400, {"error": "spec must be an object"})
            return

        spec_id = spec.get("specId", "unknown-spec")
        user_perm = get_user_permissions(user_id)
        if not user_perm:
            self._send_json(403, {"error": "User not found", "userId": user_id})
            return

        if not is_feature_allowed(user_perm, spec_id):
            self._send_json(403, {"error": "Feature not allowed", "specId": spec_id})
            return

        analysis_type = (spec.get("analysisType") or "").strip()
        if analysis_type not in ("correlation", "timeseries"):
            self._send_json(
                400,
                {"error": "Unsupported analysisType", "supported": ["correlation", "timeseries"]},
            )
            return

        allowed_source_ids = set(user_perm.get("allowedSourceIds", []))
        bindings = spec.get("seriesBindings")
        use_series_bindings = (
            isinstance(bindings, dict)
            and isinstance(bindings.get("x"), str)
            and isinstance(bindings.get("y"), str)
            and bindings.get("x").strip()
            and bindings.get("y").strip()
        )
        series_codes = spec.get("seriesCodes")
        use_series_codes = _is_nonempty_str_list(series_codes)

        resolution_meta = {}

        # Code-first timeseries: use spec.seriesCodes (preferred for single/multi series views)
        if analysis_type == "timeseries" and use_series_codes:
            codes = [c.strip() for c in series_codes]
            try:
                inferred = {c: resolve_source_id_for_series_code(c) for c in codes}
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
                return

            needed_sources = set(inferred.values())
            missing_perm = [s for s in needed_sources if s not in allowed_source_ids]
            if missing_perm:
                self._send_json(
                    403,
                    {
                        "error": "Source(s) not allowed (inferred from series code domain)",
                        "missingSourceIds": sorted(set(missing_perm)),
                        "userId": user_id,
                        "seriesCodes": codes,
                    },
                )
                return

            try:
                series = [{"name": c, "values": fetch_series_rows_by_code(c)} for c in codes]
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
                return

            dataset_id = _canonical_dataset_id(spec_id, "|".join(codes), "ts")
            units = {c: unit_label_for_series_code(c) for c in codes}
            resolution_meta = {
                "mode": "seriesCodes",
                "seriesCodes": codes,
                "sourceIds": sorted(needed_sources),
            }

        # Correlation: use spec.seriesBindings {x,y}
        elif analysis_type == "correlation" and use_series_bindings:
            x_code = bindings["x"].strip()
            y_code = bindings["y"].strip()
            try:
                x_source_id = resolve_source_id_for_series_code(x_code)
                y_source_id = resolve_source_id_for_series_code(y_code)
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
                return

            needed_sources = {x_source_id, y_source_id}
            missing_perm = [s for s in needed_sources if s not in allowed_source_ids]
            if missing_perm:
                self._send_json(
                    403,
                    {
                        "error": "Source(s) not allowed (inferred from series code domain)",
                        "missingSourceIds": sorted(set(missing_perm)),
                        "userId": user_id,
                        "xSeriesCode": x_code,
                        "ySeriesCode": y_code,
                    },
                )
                return

            try:
                x_series = {"name": "x", "values": fetch_series_rows_by_code(x_code)}
                y_series = {"name": "y", "values": fetch_series_rows_by_code(y_code)}
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
                return

            dataset_id = _canonical_dataset_id(spec_id, x_code, y_code)
            x_unit = unit_label_for_series_code(x_code)
            y_unit = unit_label_for_series_code(y_code)
            resolution_meta = {
                "mode": "seriesBindings",
                "xSeriesCode": x_code,
                "ySeriesCode": y_code,
                "xSourceId": x_source_id,
                "ySourceId": y_source_id,
            }
        else:
            required_sources = spec.get("requiredSources", [])
            if not isinstance(required_sources, list) or len(required_sources) == 0:
                self._send_json(
                    400,
                    {
                        "error": "Provide spec.seriesBindings {x,y} (correlation) or spec.seriesCodes (timeseries), "
                        "or legacy spec.requiredSources with role x/y",
                    },
                )
                return

            missing = []
            for rs in required_sources:
                sid = rs.get("sourceId")
                if sid not in allowed_source_ids:
                    missing.append(sid)

            if missing:
                self._send_json(
                    403,
                    {
                        "error": "Source(s) not allowed",
                        "missingSourceIds": sorted(set(missing)),
                        "userId": user_id,
                    },
                )
                return

            role_to_source_id = {}
            for rs in required_sources:
                sid = rs.get("sourceId")
                role = rs.get("role")
                if role in ("x", "y"):
                    role_to_source_id[role] = sid

            if "x" not in role_to_source_id or "y" not in role_to_source_id:
                self._send_json(
                    400,
                    {"error": "correlation requires role x and role y in requiredSources"},
                )
                return

            x_source_id = role_to_source_id["x"]
            y_source_id = role_to_source_id["y"]
            x_source_cfg = STATE["sources_by_id"].get(x_source_id)
            y_source_cfg = STATE["sources_by_id"].get(y_source_id)
            if not x_source_cfg or not y_source_cfg:
                self._send_json(400, {"error": "Unknown sourceId in requiredSources"})
                return

            try:
                x_series = normalize_to_canonical("x", x_source_cfg)
                y_series = normalize_to_canonical("y", y_source_cfg)
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
                return

            dataset_id = _canonical_dataset_id(spec_id, x_source_id, y_source_id)
            x_unit = unit_label_for_source(x_source_cfg)
            y_unit = unit_label_for_source(y_source_cfg)
            resolution_meta = {
                "mode": "requiredSources",
                "xSourceId": x_source_id,
                "ySourceId": y_source_id,
            }

        corr = None
        if analysis_type == "correlation":
            xs, ys = align_series_values_on_common_dates(x_series["values"], y_series["values"])
            if len(xs) < 2:
                self._send_json(
                    400,
                    {
                        "error": "Not enough overlapping dates between x and y series for correlation",
                        "xPoints": len(x_series["values"]),
                        "yPoints": len(y_series["values"]),
                        "overlapping": len(xs),
                    },
                )
                return
            corr = _pearson_corr(xs, ys)

        # Feature Builder (rule-based template for MVP)
        layout = spec.get("layout", {})
        cards = layout.get("cards", [])
        theme_id = spec.get("themeId", "unknown-theme")

        components = []
        if analysis_type == "correlation":
            if "kpi" in cards:
                components.append(
                    {
                        "type": "kpi-card",
                        "props": {
                            "metric": "latest",
                            "xLatest": x_series["values"][-1]["value"],
                            "yLatest": y_series["values"][-1]["value"],
                        },
                    }
                )

            if "timeseries" in cards:
                components.append(
                    {
                        "type": "timeseries-chart",
                        "props": {
                            "datasetRef": dataset_id,
                            "seriesNames": ["x", "y"],
                        },
                    }
                )

            if "correlation-matrix" in cards or "correlation" in cards:
                components.append(
                    {
                        "type": "correlation-card",
                        "props": {
                            "datasetRef": dataset_id,
                            "pearsonCorrelation": corr,
                        },
                    }
                )
        else:
            # timeseries
            components.append(
                {
                    "type": "timeseries-chart",
                    "props": {"datasetRef": dataset_id, "seriesNames": [s["name"] for s in series]},
                }
            )

        # Fallback: if layout cards are not specified, still return basic components.
        if not components:
            if analysis_type == "correlation":
                components = [
                    {
                        "type": "timeseries-chart",
                        "props": {"datasetRef": dataset_id, "seriesNames": ["x", "y"]},
                    },
                    {
                        "type": "correlation-card",
                        "props": {"datasetRef": dataset_id, "pearsonCorrelation": corr},
                    },
                ]
            else:
                components = [
                    {
                        "type": "timeseries-chart",
                        "props": {"datasetRef": dataset_id, "seriesNames": [s["name"] for s in series]},
                    }
                ]

        runtime = {
            "runtimeId": "rt-" + hashlib.md5(dataset_id.encode("utf-8")).hexdigest()[:8],
            "themeId": theme_id,
            "title": spec.get("title", "Untitled feature"),
            "components": components,
            "datasets": [
                {
                    "datasetId": dataset_id,
                    "canonicalType": "time_series",
                }
            ],
            "canonicalDataset": {
                "datasetId": dataset_id,
                "datasetType": "time_series",
                "timeField": "date",
                "series": [x_series, y_series] if analysis_type == "correlation" else series,
                "units": (
                    {"x": x_unit or "x", "y": y_unit or "y"}
                    if analysis_type == "correlation"
                    else units
                ),
            },
            "resolution": resolution_meta,
        }
        self._send_json(200, runtime)


def run(port: int = int(os.environ.get("PORT", 8000))):
    app_path = os.path.join(STATIC_DIR, "app.html")
    host = os.environ.get("MVP_HOST", "0.0.0.0")
    print(
        f"[mvp] pid={os.getpid()} build={MVP_BUILD_ID} listen={host}:{port}",
        file=sys.stderr,
        flush=True,
    )
    print(f"[mvp] open in browser: http://127.0.0.1:{port}/app", file=sys.stderr, flush=True)
    print(
        f"[mvp] app.html exists={os.path.isfile(app_path)} (path={app_path})",
        file=sys.stderr,
        flush=True,
    )
    server = ThreadingHTTPServer((host, port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    run()

