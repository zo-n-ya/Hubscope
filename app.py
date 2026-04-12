from flask import Flask, request, render_template, make_response
import requests
import pandas as pd
import time
import io
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)

LOGIN_URL = "https://dispatch-api.uniuni.com/map/login"
ORDERS_URL = "https://dispatch-api.uniuni.com/business/getordersandcheckinput"

DISPLAY_ORDER = ["ATL", "BHM", "BFM", "CAE", "CHS", "SAV", "BNA", "JAN", "GSP", "TYS"]

WAREHOUSE_GROUPS = {
    "ATL": ["ATL", "MAC", "CTG", "GLB"],
    "BHM": ["BHM", "ARB", "HUS", "MML"],
    "BFM": ["BFM", "PEN"],
    "CAE": ["CAE"],
    "CHS": ["CHS", "ILM"],
    "SAV": ["SAV", "AUG"],
    "BNA": ["BNA", "EVA"],
    "JAN": ["JAN"],
    "GSP": ["GSP", "ASH"],
    "TYS": ["TYS"],
}

CACHE = {}
CACHE_TTL_SECONDS = 2 * 60 * 60

LAST_TOOL_A_RESULT = []
LAST_TOOL_A_NUMS = ""
LAST_TOOL_A_USERNAME = ""
LAST_TOOL_A_TEXT = ""
LAST_TOOL_A_MESSAGE = ""
LAST_TOOL_A_ERROR = ""

LAST_TOOL_B_DETAIL = None
LAST_TOOL_B_MAIN = None
LAST_TOOL_B_DETAIL_HTML = ""
LAST_TOOL_B_USERNAME = ""
LAST_TOOL_B_ATSUBS = ""
LAST_TOOL_B_MESSAGE = ""
LAST_TOOL_B_ERROR = ""
LAST_TOOL_B_DOWNLOAD_NOTE = ""


def cache_get(key):
    value = CACHE.get(key)
    if not value:
        return None
    if time.time() - value["time"] > CACHE_TTL_SECONDS:
        return None
    return value["data"]


def cache_set(key, data):
    CACHE[key] = {"time": time.time(), "data": data}


def login(username, password):
    if not username or not password:
        raise RuntimeError("Username or password is empty.")

    r = requests.post(
        LOGIN_URL,
        json={"username": username, "password": password},
        timeout=30
    )
    r.raise_for_status()
    payload = r.json()

    if payload.get("status") != "SUCCESS":
        raise RuntimeError(f"Login failed: {payload.get('ret_msg') or payload}")

    token = (payload.get("data") or {}).get("token")
    if not token:
        raise RuntimeError("Login succeeded but token missing.")

    return token


def fetch_orders(atsub, token):
    atsub = (atsub or "").strip()
    if not atsub:
        return []

    cached = cache_get(atsub)
    if cached is not None:
        return cached

    r = requests.get(
        ORDERS_URL,
        params={"sub_references": atsub, "is_LG": "0"},
        headers={
            "Authorization": f"Bearer {token}",
            "token": token,
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0",
        },
        timeout=60
    )
    r.raise_for_status()
    payload = r.json()

    if payload.get("status") != "SUCCESS":
        raise RuntimeError(f"Orders API failed for {atsub}: {payload.get('ret_msg') or payload}")

    data = payload.get("data", [])
    if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
        orders = data[1]
    elif isinstance(data, list):
        orders = data
    else:
        orders = []

    cache_set(atsub, orders)
    return orders


def normalize(name):
    text = str(name).upper()
    for group, subs in WAREHOUSE_GROUPS.items():
        for sub in subs:
            if sub in text:
                return group
    return "UNKNOWN"


def tool_a(text, token):
    results = []
    lines_for_copy = []

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        parts = line.split()
        if len(parts) < 2:
            raise ValueError(f"Bad line: {line}. Expected REGION ATSUB")

        region = parts[0].strip()
        atsub = parts[1].strip()

        orders = fetch_orders(atsub, token)
        df = pd.DataFrame(orders)

        if df.empty:
            counts = {k: 0 for k in DISPLAY_ORDER}
        else:
            if "order_id" not in df.columns or "name" not in df.columns:
                raise RuntimeError(f"{atsub}: missing order_id or name. Columns: {list(df.columns)}")

            df = df.drop_duplicates(subset=["order_id"]).copy()
            df["wh"] = df["name"].apply(normalize)
            grouped = df.groupby("wh").size().to_dict()
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


def tool_b(atsubs, token):
    atsub_list = [x.strip() for x in (atsubs or "").split(",") if x.strip()]
    if not atsub_list:
        raise ValueError("Tool B: please input ATSUB(s).")

    all_orders = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = executor.map(lambda item: fetch_orders(item, token), atsub_list)
        for orders in results:
            all_orders.extend(orders)

    df = pd.DataFrame(all_orders)

    if df.empty:
        return (
            pd.DataFrame(columns=["warehouse", "count"]),
            pd.DataFrame(columns=["main", "count"])
        )

    if "order_id" not in df.columns or "name" not in df.columns:
        raise RuntimeError(f"Tool B: missing order_id or name. Columns: {list(df.columns)}")

    df = df.drop_duplicates(subset=["order_id"]).copy()
    df["wh"] = df["name"].apply(normalize)

    detail = df.groupby("wh").size().reset_index(name="count")
    detail = detail.rename(columns={"wh": "warehouse"})
    detail = detail.sort_values(by="count", ascending=False).reset_index(drop=True)

    main_rows = []
    for warehouse in DISPLAY_ORDER:
        total = int(detail.loc[detail["warehouse"] == warehouse, "count"].sum())
        main_rows.append([warehouse, total])

    main_df = pd.DataFrame(main_rows, columns=["main", "count"])
    return detail, main_df


def build_tool_b_detail_html(detail_df, main_df):
    return (
        '<div style="font-weight:900; margin-top:6px;">Warehouse Detail</div>'
        + detail_df.to_html(index=False, escape=False)
        + '<div style="font-weight:900; margin-top:14px;">Main Warehouse Totals</div>'
        + main_df.to_html(index=False, escape=False)
    )


@app.route("/", methods=["GET", "POST"])
def home():
    global LAST_TOOL_A_RESULT, LAST_TOOL_A_NUMS, LAST_TOOL_A_USERNAME, LAST_TOOL_A_TEXT
    global LAST_TOOL_A_MESSAGE, LAST_TOOL_A_ERROR
    global LAST_TOOL_B_DETAIL, LAST_TOOL_B_MAIN, LAST_TOOL_B_DETAIL_HTML
    global LAST_TOOL_B_USERNAME, LAST_TOOL_B_ATSUBS, LAST_TOOL_B_MESSAGE, LAST_TOOL_B_ERROR
    global LAST_TOOL_B_DOWNLOAD_NOTE

    ctx = {
        "username": LAST_TOOL_A_USERNAME or "",
        "text": LAST_TOOL_A_TEXT or "",
        "a_result": LAST_TOOL_A_RESULT or [],
        "a_nums": LAST_TOOL_A_NUMS or "",
        "a_error": LAST_TOOL_A_ERROR or "",
        "a_message": LAST_TOOL_A_MESSAGE or "",

        "username_b": LAST_TOOL_B_USERNAME or "",
        "atsubs": LAST_TOOL_B_ATSUBS or "",
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
                atsubs = request.form.get("atsubs") or ""

                token = login(username_b, password_b)
                detail_df, main_df = tool_b(atsubs, token)
                detail_html = build_tool_b_detail_html(detail_df, main_df)

                LAST_TOOL_B_USERNAME = username_b
                LAST_TOOL_B_ATSUBS = atsubs
                LAST_TOOL_B_DETAIL = detail_df
                LAST_TOOL_B_MAIN = main_df
                LAST_TOOL_B_DETAIL_HTML = detail_html
                LAST_TOOL_B_MESSAGE = "Tool B generated successfully."
                LAST_TOOL_B_ERROR = ""
                LAST_TOOL_B_DOWNLOAD_NOTE = "Download will export one sheet with detail and main totals."

                ctx["username_b"] = LAST_TOOL_B_USERNAME
                ctx["atsubs"] = LAST_TOOL_B_ATSUBS
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
    global LAST_TOOL_B_DETAIL, LAST_TOOL_B_MAIN

    if LAST_TOOL_B_DETAIL is None or LAST_TOOL_B_MAIN is None:
        return "No cached Tool B result yet. Please generate Tool B first.", 400

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        LAST_TOOL_B_DETAIL.to_excel(writer, index=False, sheet_name="ToolB", startrow=0)
        LAST_TOOL_B_MAIN.to_excel(
            writer,
            index=False,
            sheet_name="ToolB",
            startrow=len(LAST_TOOL_B_DETAIL) + 3
        )

    output.seek(0)
    response = make_response(output.read())
    response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    response.headers["Content-Disposition"] = "attachment; filename=hubscope_tool_b.xlsx"
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)