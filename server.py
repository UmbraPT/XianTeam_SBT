from flask import Flask, request, jsonify, send_from_directory
from pymongo import MongoClient
import requests
import os
from flask_cors import CORS

# ---- config ----
MONGO_URI = "mongodb://localhost:27017/"
MONGO_DB = "xian_monitor"
TRAITS_COLLECTION = "traits"

GRAPHQL_URL = "https://devnet.xian.org/graphql"
SBT_CONTRACT = "con_sbtxian_v"   # your merged contract name
TRAIT_KEYS = [
    "Score",
    "Tier",
    "Stake Duration",
    "DEX Volume",
    "Pulse Influence",
    "Transaction Volume",
    "Bridge Volume",
    "Volume Played",
]

# UI → on-chain key mapping
CHAIN_TO_UI = {
    "Score": "Score",
    "Tier": "Tier",
    "Stake Duration": "Stake Duration",
    "DEX Volume": "DEX Volume",
    "Pulse Influence": "Pulse Influence",
    "Trx Volume": "Transaction Volume",
    "Xian Bridged": "Bridge Volume",
    "Volume Played": "Volume Played",
}
UI_TO_CHAIN = {v: k for k, v in CHAIN_TO_UI.items()}

def derive_tier_label(score: int):
    s = int(score or 0)
    if s < 500:     return "Leafling"
    if s < 1500:    return "Vine Crawler"
    if s < 3000:    return "Canopy Dweller"
    if s < 5000:    return "Rainkeeper"
    if s < 10000:   return "Jaguar Fang"
    return "Spirit of the Jungle"


# ---- app ----
app = Flask(__name__, static_folder="public", static_url_path="")
# Allow common dev origins; use "*" if you prefer (no cookies are used)
CORS(
    app,
    resources={r"/api/*": {"origins": [
        "http://127.0.0.1:5000",
        "http://localhost:5000",
        # Allow any ngrok preview:
        "https://*.ngrok-free.app",
    ]}},
)

# ---- db ----
client = MongoClient(MONGO_URI)
db = client[MONGO_DB]
traits_col = db[TRAITS_COLLECTION]

def _to_num(x):
    try:
        f = float(x)
        return int(f) if f.is_integer() else f
    except Exception:
        return 0

def _first_num(doc, keys, default=0):
    for k in keys:
        if k in doc and doc.get(k) is not None:
            return _to_num(doc.get(k))
    return default

def as_float(x, default=0.0):
    try:
        if isinstance(x, (int, float)): return float(x)
        if isinstance(x, str) and x.strip() != "": return float(x)
    except Exception:
        pass
    return float(default)

def as_int(x, default=0):
    try:
        if isinstance(x, (int, float)): return int(x)
        if isinstance(x, str) and x.strip() != "": return int(float(x))
    except Exception:
        pass
    return int(default)

def get_offchain_traits(address: str):
    doc = traits_col.find_one({"address": address}) or {}

    score           = _first_num(doc, ["score"], 0)
    stake_seconds   = _first_num(doc, ["stake_seconds", "stake_duration_sec"], 0)
    dex_volume      = _first_num(doc, ["dex_volume"], 0)
    pulse_influence = _first_num(doc, ["pulse_influence"], 0)
    # 👇 tolerate all historical names you’ve used
    tx_volume       = _first_num(doc, ["transaction_volume", "trx_volume", "total_sent_xian", "amount"], 0)
    bridge_volume   = _first_num(doc, ["bridge_volume", "xian_bridged"], 0)
    volume_played   = _first_num(doc, ["volume_played"], 0)

    return {
        "Score":              score,
        "Tier":               derive_tier_label(score),
        "Stake Duration":     stake_seconds,
        "DEX Volume":         dex_volume,
        "Pulse Influence":    pulse_influence,
        "Transaction Volume": tx_volume,
        "Bridge Volume":      bridge_volume,
        "Volume Played":      volume_played,
    }

def get_onchain_traits(address: str):
    q = {
        "query": f"""
        query {{
          allStates(
            filter: {{ key: {{ like: "{SBT_CONTRACT}.traits:{address}:%" }} }},
            first: 500
          ) {{
            edges {{ node {{ key value }} }}
          }}
        }}
        """
    }
    r = requests.post(GRAPHQL_URL, json=q, timeout=20)
    r.raise_for_status()
    edges = (r.json().get("data", {}) or {}).get("allStates", {}).get("edges", []) or []

    out = {k: ("" if k == "Tier" else 0) for k in TRAIT_KEYS}
    for e in edges:
        key_str = e["node"]["key"]  # con_sbtxian.traits:ADDR:Trx Volume
        try:
            after = key_str.split(".traits:", 1)[1]
            addr, chain_key = after.split(":", 1)
            if addr != address:
                continue
            ui_key = CHAIN_TO_UI.get(chain_key)
            if not ui_key:
                continue
            val = e["node"]["value"]
            out[ui_key] = (str(val) if ui_key == "Tier" else _to_num(val))
        except Exception:
            continue
    return out

# ---------- API ----------
@app.get("/api/compare_traits")
def compare_traits():
    address = request.args.get("address", "").strip()
    if not address:
        return jsonify({"error": "Address is required"}), 400

    offchain = get_offchain_traits(address)
    onchain = get_onchain_traits(address)

    diffs = {}
    if str(offchain["Score"]) != str(onchain["Score"]):
        diffs["Score"] = {"off_chain": offchain["Score"], "on_chain": onchain["Score"]}

    return jsonify({"address": address, "offchain": offchain, "onchain": onchain, "diffs": diffs})

# ---------- frontend ----------
# Flask will serve /public automatically as static root.
# Ensure /public/index.html exists. Root route returns it:
@app.get("/")
def root():
    return send_from_directory("public", "index.html")

if __name__ == "__main__":
    # optional: show absolute path for sanity
    print("Serving static from:", os.path.abspath("public"))
    app.run(host="0.0.0.0", port=5000, debug=True)
