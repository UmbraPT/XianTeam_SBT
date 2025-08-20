from flask import Flask, request, jsonify, send_from_directory
from pymongo import MongoClient
import requests
import os

# ---- config ----
MONGO_URI = "mongodb://localhost:27017/"
MONGO_DB = "xian_monitor"
TRAITS_COLLECTION = "traits"

GRAPHQL_URL = "https://devnet.xian.org/graphql"
SBT_CONTRACT = "con_sbtxian"   # your merged contract name
TRAIT_KEYS = ["Score", "Stake Duration", "DEX Volume", "Total Sent XIAN"]

# ---- app ----
app = Flask(
    __name__,
    static_folder="public",     # serve /public as static root
    static_url_path=""          # so /index.html is at /
)

# ---- db ----
client = MongoClient(MONGO_URI)
db = client[MONGO_DB]
traits_col = db[TRAITS_COLLECTION]

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
    doc = traits_col.find_one(
        {"address": address},
        {"_id": 0, "score": 1, "dex_volume": 1, "stake_duration_sec": 1, "total_sent_xian": 1}
    ) or {}

    return {
        "Score":           as_int(doc.get("score", 0)),
        "Stake Duration":  as_int(doc.get("stake_duration_sec", 0)),
        "DEX Volume":      as_float(doc.get("dex_volume", 0.0)),
        "Total Sent XIAN": as_float(doc.get("total_sent_xian", 0.0)),
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
    edges = r.json().get("data", {}).get("allStates", {}).get("edges", []) or []
    chain = {k: "" for k in TRAIT_KEYS}
    for e in edges:
        key_str = e["node"]["key"]  # e.g. con_sbt_traits.traits:ADDR:Score
        try:
            after = key_str.split(".traits:", 1)[1]
            addr, trait_key = after.split(":", 1)
            if trait_key in chain:
                chain[trait_key] = e["node"]["value"]
        except Exception:
            continue
    return chain

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
    app.run(host="127.0.0.1", port=5000, debug=True)
