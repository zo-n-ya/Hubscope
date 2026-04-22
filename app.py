from __future__ import annotations

import io
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from flask import Flask, make_response, render_template, request

app = Flask(__name__)

# Public-safe configuration via environment variables
API_BASE_URL = os.environ.get("API_BASE_URL", "https://api.example.com").rstrip("/")
LOGIN_PATH = os.environ.get("API_LOGIN_PATH", "/auth/login")
ORDERS_PATH = os.environ.get("API_ORDERS_PATH", "/orders/query")
LOGIN_URL = f"{API_BASE_URL}{LOGIN_PATH}"
ORDERS_URL = f"{API_BASE_URL}{ORDERS_PATH}"

# Tool A display order
DISPLAY_ORDER = ["HUB_A", "HUB_B", "HUB_C", "HUB_D", "HUB_E", "HUB_F", "HUB_G", "HUB_H", "HUB_I", "HUB_J"]

# Main hub -> sub hub mapping (example data)
WAREHOUSE_GROUPS = {
    "HUB_A": ["HUB_A", "A1", "A2", "A3", "A4"],
    "HUB_B": ["HUB_B", "B1", "B2", "B3"],
    "HUB_C": ["HUB_C", "C1"],
    "HUB_D": ["HUB_D", "D1"],
    "HUB_E": ["HUB_E", "E1"],
    "HUB_F": ["HUB_F", "F1"],
    "HUB_G": ["HUB_G", "G1"],
    "HUB_H": ["HUB_H"],
    "HUB_I": ["HUB_I", "I1"],
    "HUB_J": ["HUB_J"],
}

# Sub hub -> main hub
SUB_TO_MAIN: Dict[str, str] = {}
for main_wh, subs in WAREHOUSE_GROUPS.items():
    for sub in subs:
        SUB_TO_MAIN[sub] = main_wh

# Tool B main hub route ranges (example data)
MAIN_WAREHOUSE_RANGES: Dict[str, Tuple[int, int]] = {
    "HUB_A": (100001, 100999),
    "HUB_B": (200001, 200999),
    "HUB_C": (300001, 300999),
    "HUB_D": (400001, 400999),
    "HUB_E": (500001, 500999),
    "HUB_F": (600001, 600999),
    "HUB_G": (700001, 700999),
    "HUB_H": (800001, 800999),
    "HUB_I": (900001, 900999),
    "HUB_J": (910001, 910999),
}

# Tool B example route mapping table
DEFAULT_ROUTE_TABLE = """HUB_A (100001-100099)\tA1 (100100-109)\tA2 (100110-119)\tA3 (100120-129)\tA4 (100130 132 140)
HUB_B (200001-200079)\tB1 (200080-089)\tB2 (200090-099)\tB3 (200120-124)
HUB_C (300001-300999)\tC1 (300050-059)
HUB_D (400001-400999)\tD1 (400020-024)
HUB_E (500001-500999)\tE1 (500010-014)
HUB_F (600001-600999)\tF1 (600090-094)
HUB_G (700001-700999)\tG1 (700070-075)
HUB_H (800001-800999)
HUB_I (900001-900999)\tI1 (900050-055)
HUB_J (910001-910999)
"""

CACHE: Dict[str, Dict[str, object]] = {}
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "7200"))

LAST_TOOL_A_RESULT: List[Tuple[str, Dict[str, int]]] = []
LAST_TOOL_A_NUMS = ""
LAST_TOOL_A_USERNAME = ""
LAST_TOOL_A_TEXT = ""
LAST_TOOL_A_MESSAGE = ""
LAST_TOOL_A_ERROR = ""

LAST_TOOL_B_DETAIL: Optional[pd.DataFrame] = None
LAST_TOOL_B_MAIN: Optional[pd.DataFrame] = None
LAST_TOOL_B_GROUPED: Optional[pd.DataFrame] = None
LAST_TOOL_B_DETAIL_HTML = ""
LAST_TOOL_B_USERNAME = ""
LAST_TOOL_B_BATCHES = ""
LAST_TOOL_B_MESSAGE = ""
LAST_TOOL_B_ERROR = ""
LAST_TOOL_B_DOWNLOAD_NOTE = ""


def cache_get(key: str):
    value = CACHE.get(key)
    if not value:
        return None
    if time.time() - float(value["time"]) > CACHE_TTL_SECONDS:
        return None
    return value["data"]


def cache_set(key: str, data):
    CACHE[key] = {"time": time.time(), "data": data}


def login(username: str, password: str) -> str:
    if not username or not password:
        raise RuntimeError("Username or password is empty.")

    r = requests.post(
        LOGIN_URL,
        json={"username": username, "password": password},
        timeout=30,
    )
    r.raise_for_status()
    payload = r.json()

    if payload.get("status") != "SUCCESS":
        raise RuntimeError(f"Login failed: {payload.get('ret_msg') or payload}")

    token = (payload.get("data") or {}).get("token")
    if not token:
        raise RuntimeError("Login succeeded but token missing.")

    return token


def fetch_orders(batch_ref: str, token: str) -> List[dict]:
    batch_ref = (batch_ref or "").strip()
    if not batch_ref:
        return []

    cached = cache_get(batch_ref)
    if cached is not None:
        return cached

    r = requests.get(
        ORDERS_URL,
        params={"batch_ref": batch_ref},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0",
        },
        timeout=60,
    )
    r.raise_for_status()
    payload = r.json()

    if payload.get("status") != "SUCCESS":
        raise RuntimeError(f"Orders API failed for {batch_ref}: {payload.get('ret_msg') or payload}")

    data = payload.get("data", [])
    if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
        orders = data[1]
    elif isinstance(data, list):
        orders = data
    else:
        orders = []

    cache_set(batch_ref, orders)
    return orders


def normalize_to_main(name: object) -> str:
    text = str(name).upper().strip()

    for main_wh, subs in WAREHOUSE_GROUPS.items():
        for sub in subs:
            if sub in text:
                return main_wh

    return "UNKNOWN"


def get_route_value(row: dict) -> Optional[int]:
    for key in ("shipping_staff_id", "route_no", "service_number"):
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return int(str(value).strip())
        except Exception:
            continue
    return None


def _expand_compact_range(start_str: str, end_str: str) -> Tuple[int, int]:
    start_str = start_str.strip()
    end_str = end_str.strip()

    start = int(start_str)

    if end_str.isdigit() and len(end_str) < len(start_str):
        prefix = start_str[: len(start_str) - len(end_str)]
        end = int(prefix + end_str)
    else:
        end = int(end_str)

    if start > end:
        start, end = end, start

    return start, end


def _parse_payload_to_ranges(payload: str) -> List[Tuple[int, int]]:
    s = payload.strip()
    s = s.replace("，", " ").replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip()

    if not s:
        return []

    if "-" in s:
        left, right = s.split("-", 1)
        start, end = _expand_compact_range(left, right)
        return [(start, end)]

    nums = [x for x in s.split(" ") if x]
    if not nums:
        return []

    base_int = int(nums[0])
    base_str = str(base_int)

    out: List[Tuple[int, int]] = [(base_int, base_int)]

    for tok in nums[1:]:
        tok = tok.strip()
        if not tok:
            continue

        if tok.isdigit() and len(tok) < len(base_str):
            full = int(base_str[: len(base_str) - len(tok)] + tok)
        else:
            full = int(tok)

        out.append((full, full))

    return out


def parse_route_mapping(text: str) -> List[Tuple[str, int, int]]:
    rules: List[Tuple[str, int, int]] = []
    pattern = re.compile(r"([A-Za-z0-9_]+)\s*[（(]\s*([^）)]+)\s*[）)]")

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        cells = [c.strip() for c in line.split("\t") if c.strip()] if "\t" in line else [line]

        for cell in cells:
            for match in pattern.finditer(cell):
                wh = match.group(1).upper().strip()
                payload = match.group(2).strip()

                for start, end in _parse_payload_to_ranges(payload):
                    rules.append((wh, start, end))

    if not rules:
        raise ValueError("Route mapping table parsed to empty.")

    rules.sort(key=lambda x: (x[2] - x[1], x[1], x[0]))
    return rules


ROUTE_RULES = parse_route_mapping(DEFAULT_ROUTE_TABLE)


def route_to_sub_wh(route_val: int) -> str:
    for wh, start, end in ROUTE_RULES:
        if start <= route_val <= end:
            return wh
    return "UNMAPPED"


def route_to_main_wh(route_val: int) -> str:
    for wh, (start, end) in MAIN_WAREHOUSE_RANGES.items():
        if start <= route_val <= end:
            return wh
    return "UNKNOWN"


def resolve_warehouse_by_route(route_val: int) -> str:
    sub_wh = route_to_sub_wh(route_val)
    if sub_wh != "UNMAPPED":
        return sub_wh
    return route_to_main_wh(route_val)


def tool_a(text: str, token: str):
    results: List[Tuple[str, Dict[str, int]]] = []
    lines_for_copy: List[str] = []

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        parts = line.split()
        if len(parts) < 2:
            raise ValueError(f"Bad line: {line}. Expected REGION BATCH")

        region = parts[0].strip()
        batch_ref = parts[1].strip()

        orders = fetch_orders(batch_ref, token)
        df = pd.DataFrame(orders)

        if df.empty:
            counts = {k: 0 for k in DISPLAY_ORDER}
        else:
            if "order_id" not in df.columns or "name" not in df.columns:
                raise RuntimeError(f"{batch_ref}: missing order_id or name. Columns: {list(df.columns)}")

            df = df.drop_duplicates(subset=["order_id"]).copy()
            df["main_wh"] = df["name"].apply(normalize_to_main)
            grouped = df.groupby("main_wh").size().to_dict()
            counts = {k: int(grouped.get(k, 0)) for k in DISPLAY_ORDER}

        results.append((region, counts))

        total = sum(counts.values())
        count_line = "\t".join(str(counts[k]) for k in DISPLAY_ORDER)
        pct_line = "\t".join(
            f"{(counts[k] / total * 100):.2f}%" if total > 0 else "0.00%"
            for k in DISPLAY_ORDER
        )

        lines_for_copy.append(region)
        lines_for_copy.append(count_line)
        lines_for_copy.append(pct_line)
        lines_for_copy.append("")

    nums_text = "\n".join(lines_for_copy).rstrip()
    return results, nums_text


def tool_b(batch_refs: str, token: str):
    batch_list = [x.strip() for x in (batch_refs or "").split(",") if x.strip()]
    if not batch_list:
        raise ValueError("Tool B: please input batch reference(s).")

    all_orders: List[dict] = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = executor.map(lambda item: fetch_orders(item, token), batch_list)
        for orders in results:
            all_orders.extend(orders)

    df = pd.DataFrame(all_orders)

    if df.empty:
        return (
            pd.DataFrame(columns=["warehouse", "count"]),
            pd.DataFrame(columns=["main", "count"]),
            pd.DataFrame(columns=["main", "warehouse", "count"]),
        )

    if "order_id" not in df.columns:
        raise RuntimeError(f"Tool B: missing order_id. Columns: {list(df.columns)}")

    df = df.drop_duplicates(subset=["order_id"]).copy()

    df["route_val"] = df.apply(lambda row: get_route_value(row.to_dict()), axis=1)
    df = df[df["route_val"].notna()].copy()

    if df.empty:
        raise ValueError(
            "Tool B: all orders have empty route number. Tried shipping_staff_id, route_no, service_number."
        )

    df["route_val"] = df["route_val"].astype(int)
    df["warehouse"] = df["route_val"].apply(resolve_warehouse_by_route)
    df["main"] = df["route_val"].apply(route_to_main_wh)
    df = df[df["main"] != "UNKNOWN"].copy()

    if df.empty:
        raise ValueError("Tool B: no valid rows matched main warehouse ranges.")

    detail = (
        df.groupby("warehouse")
        .size()
        .reset_index(name="count")
        .sort_values(by="count", ascending=False)
        .reset_index(drop=True)
    )

    main_rows = []
    for main_wh in DISPLAY_ORDER:
        total = int(df[df["main"] == main_wh].shape[0])
        main_rows.append([main_wh, total])

    main_df = pd.DataFrame(main_rows, columns=["main", "count"])

    grouped_rows: List[Dict[str, object]] = []

    for main_wh in DISPLAY_ORDER:
        sub_df = (
            df[df["main"] == main_wh]
            .groupby("warehouse")
            .size()
            .reset_index(name="count")
            .sort_values(by="count", ascending=False)
            .reset_index(drop=True)
        )

        if sub_df.empty:
            continue

        grouped_rows.append({
            "main": main_wh,
            "warehouse": f"{main_wh} TOTAL",
            "count": int(sub_df["count"].sum()),
        })

        for _, row in sub_df.iterrows():
            grouped_rows.append({
                "main": main_wh,
                "warehouse": str(row["warehouse"]),
                "count": int(row["count"]),
            })

    grouped_df = pd.DataFrame(grouped_rows, columns=["main", "warehouse", "count"])

    return detail, main_df, grouped_df


def build_tool_b_detail_html(detail_df: pd.DataFrame, main_df: pd.DataFrame, grouped_df: pd.DataFrame) -> str:
    html = ""
    html += '<div style="font-weight:900; margin-top:6px;">Warehouse Detail</div>'
    html += detail_df.to_html(index=False, escape=False)

    html += '<div style="font-weight:900; margin-top:14px;">Main Hub Totals</div>'
    html += main_df.to_html(index=False, escape=False)

    html += '<div style="font-weight:900; margin-top:14px;">Main + Sub Detail</div>'
    html += grouped_df.to_html(index=False, escape=False)

    return html


@app.route("/", methods=["GET", "POST"])
def home():
    global LAST_TOOL_A_RESULT, LAST_TOOL_A_NUMS, LAST_TOOL_A_USERNAME, LAST_TOOL_A_TEXT
    global LAST_TOOL_A_MESSAGE, LAST_TOOL_A_ERROR
    global LAST_TOOL_B_DETAIL, LAST_TOOL_B_MAIN, LAST_TOOL_B_GROUPED, LAST_TOOL_B_DETAIL_HTML
    global LAST_TOOL_B_USERNAME, LAST_TOOL_B_BATCHES, LAST_TOOL_B_MESSAGE, LAST_TOOL_B_ERROR
    global LAST_TOOL_B_DOWNLOAD_NOTE

    ctx = {
        "username": LAST_TOOL_A_USERNAME or "",
        "text": LAST_TOOL_A_TEXT or "",
        "a_result": LAST_TOOL_A_RESULT or [],
        "a_nums": LAST_TOOL_A_NUMS or "",
        "a_error": LAST_TOOL_A_ERROR or "",
        "a_message": LAST_TOOL_A_MESSAGE or "",
        "username_b": LAST_TOOL_B_USERNAME or "",
        "atsubs": LAST_TOOL_B_BATCHES or "",
        "b_detail_html": LAST_TOOL_B_DETAIL_HTML or "",
        "b_error": LAST_TOOL_B_ERROR or "",
        "b_message": LAST_TOOL_B_MESSAGE or "",
        "b_download_note": LAST_TOOL_B_DOWNLOAD_NOTE or "",
    }

    if request.method == "POST":
        tool = (request.form.get("tool") or "").strip().upper()

        try:
            if tool == "A":
                username = (request.form.get("username") or "").strip()
                password = request.form.get("password") or ""
                text = request.form.get("text") or ""

                token = login(username, password)
                a_result, a_nums = tool_a(text, token)

                LAST_TOOL_A_USERNAME = username
                LAST_TOOL_A_TEXT = text
                LAST_TOOL_A_RESULT = a_result or []
                LAST_TOOL_A_NUMS = a_nums or ""
                LAST_TOOL_A_MESSAGE = "Tool A generated successfully."
                LAST_TOOL_A_ERROR = ""

                ctx["username"] = LAST_TOOL_A_USERNAME
                ctx["text"] = LAST_TOOL_A_TEXT
                ctx["a_result"] = LAST_TOOL_A_RESULT
                ctx["a_nums"] = LAST_TOOL_A_NUMS
                ctx["a_message"] = LAST_TOOL_A_MESSAGE
                ctx["a_error"] = LAST_TOOL_A_ERROR

            elif tool == "B":
                username_b = (request.form.get("username_b") or "").strip()
                password_b = request.form.get("password_b") or ""
                batch_refs = request.form.get("atsubs") or ""

                token = login(username_b, password_b)
                detail_df, main_df, grouped_df = tool_b(batch_refs, token)
                detail_html = build_tool_b_detail_html(detail_df, main_df, grouped_df)

                LAST_TOOL_B_USERNAME = username_b
                LAST_TOOL_B_BATCHES = batch_refs
                LAST_TOOL_B_DETAIL = detail_df
                LAST_TOOL_B_MAIN = main_df
                LAST_TOOL_B_GROUPED = grouped_df
                LAST_TOOL_B_DETAIL_HTML = detail_html
                LAST_TOOL_B_MESSAGE = "Tool B generated successfully."
                LAST_TOOL_B_ERROR = ""
                LAST_TOOL_B_DOWNLOAD_NOTE = "Download will export detail, main totals, and main + sub detail in one sheet."

                ctx["username_b"] = LAST_TOOL_B_USERNAME
                ctx["atsubs"] = LAST_TOOL_B_BATCHES
                ctx["b_detail_html"] = LAST_TOOL_B_DETAIL_HTML
                ctx["b_message"] = LAST_TOOL_B_MESSAGE
                ctx["b_error"] = LAST_TOOL_B_ERROR
                ctx["b_download_note"] = LAST_TOOL_B_DOWNLOAD_NOTE

            else:
                raise ValueError("Unknown tool selection.")

        except Exception as e:
            if tool == "B":
                LAST_TOOL_B_ERROR = str(e)
                LAST_TOOL_B_MESSAGE = ""
                ctx["b_error"] = LAST_TOOL_B_ERROR
                ctx["b_message"] = LAST_TOOL_B_MESSAGE
            else:
                LAST_TOOL_A_RESULT = []
                LAST_TOOL_A_NUMS = ""
                LAST_TOOL_A_ERROR = str(e)
                LAST_TOOL_A_MESSAGE = ""
                ctx["a_result"] = []
                ctx["a_nums"] = ""
                ctx["a_error"] = LAST_TOOL_A_ERROR
                ctx["a_message"] = LAST_TOOL_A_MESSAGE

    return render_template("index.html", **ctx)


@app.route("/download_cached", methods=["POST"])
def download_cached():
    global LAST_TOOL_B_DETAIL, LAST_TOOL_B_MAIN, LAST_TOOL_B_GROUPED

    if LAST_TOOL_B_DETAIL is None or LAST_TOOL_B_MAIN is None or LAST_TOOL_B_GROUPED is None:
        return "No cached Tool B result yet. Please generate Tool B first.", 400

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        LAST_TOOL_B_DETAIL.to_excel(writer, index=False, sheet_name="ToolB", startrow=0)
        LAST_TOOL_B_MAIN.to_excel(
            writer,
            index=False,
            sheet_name="ToolB",
            startrow=len(LAST_TOOL_B_DETAIL) + 3,
        )
        LAST_TOOL_B_GROUPED.to_excel(
            writer,
            index=False,
            sheet_name="ToolB",
            startrow=len(LAST_TOOL_B_DETAIL) + len(LAST_TOOL_B_MAIN) + 6,
        )

    output.seek(0)
    response = make_response(output.read())
    response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    response.headers["Content-Disposition"] = "attachment; filename=hubscope_tool_b.xlsx"
    return response


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5051"))
    app.run(host="0.0.0.0", port=port, debug=False)
