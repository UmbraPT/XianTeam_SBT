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
TRAIT_KEYS = ["Score", "Tier", "Stake Duration", "DEX Volume", "Game Wins", "Bots Created", "Pulse Influence"]

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

def get_offchain_traits(address: str):
    doc = traits_col.find_one({"address": address}, {"_id": 0, "score": 1})
    score = int(doc["score"]) if doc and "score" in doc and doc["score"] is not None else 0
    out = {}
    for k in TRAIT_KEYS:
        out[k] = score if k == "Score" else ""    # default others empty
    return out

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
