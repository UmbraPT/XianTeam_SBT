#!/usr/bin/env python3
"""
Real-time Xian monitor via WebSockets:
- Subscribes to CometBFT Tx events
- Decodes each transaction (base64 -> hex -> JSON)
- Filters for:
    * currency.transfer (points=1, amount from kwargs["amount"])
    * con_dex_v2.swapExactTokenForToken (points=5)
    * con_staking_v1.deposit (points=15)
    * con_xipoll_v0_clean.vote (points=5)
    * submission.submit_contract (points=50)
- Validates the sender holds your SBT before scoring
- Writes to MongoDB (traits collection keyed by address, plus processed tx hashes)
"""

import os
import json
import time
import base64
import binascii
import asyncio
from typing import Dict, Any, Optional, Tuple, List

import requests
import websockets         # pip install websockets
from pymongo import MongoClient, ASCENDING  # pip install pymongo

# ======== CONFIG ========
# Use env vars to flip between testnet/mainnet without touching code.
WS_URL        = os.getenv("XIAN_WS_URL",  "ws://94.16.113.241:26657/websocket")  # mainnet: wss://node.xian.org/websocket
GRAPHQL_URL   = os.getenv("GRAPHQL_URL",  "https://devnet.xian.org/graphql")  # mainnet: https://node.xian.org/graphql
SBT_CONTRACT  = os.getenv("SBT_CONTRACT", "con_sbtxian")  # your merged SBT+traits contract name
MONGO_URI     = os.getenv("MONGO_URI",    "mongodb://localhost:27017/")
DB_NAME       = os.getenv("DB_NAME",      "xian_monitor")
COLL_TRAITS   = os.getenv("COLL_TRAITS",  "traits")
COLL_PROCESSED= os.getenv("COLL_PROCESSED","processed")

# Refresh SBT holder list periodically to avoid per-tx graph lookups
SBT_REFRESH_INTERVAL = int(os.getenv("SBT_REFRESH_SECS", "20"))  # 5 min

# What to watch
WATCH_RULES = [
    ("currency",        "transfer",                1,  "amount"),
    ("con_dex_v2",      "swapExactTokenForToken",  5,  None),
    ("con_staking_v1",  "deposit",                15,  None),
    ("con_xipoll_v0_clean", "vote",                5,  None),
    ("submission",      "submit_contract",        50,  None),
]

# ======== DB SETUP ========
mongo = MongoClient(MONGO_URI)
db = mongo[DB_NAME]
traits_col = db[COLL_TRAITS]
processed_col = db[COLL_PROCESSED]
traits_col.create_index([("address", ASCENDING)], unique=True)
processed_col.create_index([("tx_hash", ASCENDING)], unique=True)


# ======== HELPERS ========
def has_processed(tx_hash: str) -> bool:
    return processed_col.find_one({"tx_hash": tx_hash}) is not None

def mark_processed(tx_hash: str):
    try:
        processed_col.insert_one({"tx_hash": tx_hash, "ts": int(time.time())})
    except Exception:
        pass  # ignore dup insert races

def ensure_user(address: str):
    traits_col.update_one(
        {"address": address},
        {"$setOnInsert": {"address": address, "score": 0, "amount": 0.0}},
        upsert=True
    )

def add_points(address: str, score: int, amount: float):
    traits_col.update_one(
        {"address": address},
        {"$inc": {"score": int(score), "amount": float(amount)}}
    )

def get_all_sbt_holders() -> set:
    """
    Uses GraphQL to fetch all keys like `<SBT_CONTRACT>.sbt_holders:<addr>`
    and returns a set of addresses.
    """
    query = {
        "query": f"""
        query {{
          allStates(
            filter: {{ key: {{ like: "{SBT_CONTRACT}.sbt_holders:%" }} }},
            first: 5000
          ) {{
            edges {{ node {{ key }} }}
          }}
        }}
        """
    }
    try:
        r = requests.post(GRAPHQL_URL, json=query, timeout=15)
        r.raise_for_status()
        edges = (r.json().get("data", {}) or {}).get("allStates", {}).get("edges", []) or []
        out = set()
        for e in edges:
            k = e["node"]["key"]
            # key format: con_sbtxian.sbt_holders:ADDRESS
            if ":" in k:
                addr = k.split(":", 1)[1]
                if addr:
                    out.add(addr)
        return out
    except Exception as e:
        print("❌ Error fetching SBT holders:", e)
        return set()

def decode_tx_b64_to_json(tx_b64: str) -> Optional[Dict[str, Any]]:
    """
    Xian txs arrive as base64-encoded bytes that are *hex* of the JSON payload.
    We need: base64 decode -> hex string -> unhexlify -> JSON.
    """
    try:
        hex_str = base64.b64decode(tx_b64).decode()          # bytes->hex-as-text
        raw = binascii.unhexlify(hex_str)                    # hex->bytes
        return json.loads(raw.decode())                      # bytes->json
    except Exception as e:
        print("❌ TX decode failed:", e)
        return None

def match_rule(contract: str, func: str) -> Optional[Tuple[int, Optional[str]]]:
    for c, f, pts, amt_field in WATCH_RULES:
        if c == contract and f == func:
            return pts, amt_field
        # Special: user wanted "%" to mean any contract for currency.transfer (already explicit above)
    return None

# ======== WEBSOCKET LOOP ========
async def ws_loop():
    """
    Subscribes to `Tx` events via JSON-RPC over WebSocket and processes each tx in real time.
    """
    last_refresh = 0
    sbt_holders: set = set()

    while True:
        # Refresh SBT holder set
        now = time.time()
        if now - last_refresh >= SBT_REFRESH_INTERVAL or not sbt_holders:
            sbt_holders = get_all_sbt_holders()
            last_refresh = now
            print(f"🔄 Refreshed SBT holders: {len(sbt_holders)} addresses")

        try:
            print(f"🔌 Connecting WS: {WS_URL}")
            async with websockets.connect(WS_URL, max_queue=1000) as ws:
                # Subscribe to Tx events
                sub = {
                    "jsonrpc": "2.0",
                    "method": "subscribe",
                    "id": 1,
                    "params": {"query": "tm.event='Tx'"}
                }
                await ws.send(json.dumps(sub))
                print("✅ Subscribed: tm.event='Tx'")

                async for message in ws:
                    # Parse the envelope (CometBFT JSON-RPC)
                    # Structure typically includes: result.data.value.TxResult.tx (base64) & result.events["tx.hash"]
                    # See CometBFT/Tendermint subscription docs. 
                    payload = json.loads(message)

                    if "result" not in payload:
                        continue
                    result = payload["result"]

                    # Pull tx hash if present
                    tx_hash = None
                    evmap = result.get("events") or {}
                    if "tx.hash" in evmap and evmap["tx.hash"]:
                        tx_hash = evmap["tx.hash"][0]

                    # Pull base64 tx bytes
                    tx_b64 = None
                    try:
                        tx_b64 = (
                            (((result.get("data") or {}).get("value") or {}).get("TxResult") or {}).get("tx")
                        )
                    except Exception:
                        tx_b64 = None

                    if not tx_b64:
                        # Some nodes may wrap differently; skip if no payload
                        continue

                    # Dedup by tx hash, if we have one
                    if tx_hash and has_processed(tx_hash):
                        continue

                    # Decode and inspect the tx JSON payload
                    tx_json = decode_tx_b64_to_json(tx_b64)
                    if not tx_json:
                        # Still mark as processed if we have a hash to avoid re-trying forever
                        if tx_hash:
                            mark_processed(tx_hash)
                        continue

                    payload_obj = tx_json.get("payload", {}) or {}
                    sender   = payload_obj.get("sender")
                    contract = payload_obj.get("contract")
                    func     = payload_obj.get("function")
                    kwargs   = payload_obj.get("kwargs", {}) or {}

                    mr = match_rule(contract, func)
                    if not mr:
                        # Not one we score
                        if tx_hash:
                            mark_processed(tx_hash)
                        continue

                    points, amt_field = mr
                    amount = 0.0
                    if amt_field and amt_field in kwargs:
                        try:
                            amount = float(kwargs[amt_field])
                        except Exception:
                            amount = 0.0

                    # Only score if sender holds SBT
                    if sender in sbt_holders:
                        ensure_user(sender)
                        add_points(sender, points, amount)
                        if tx_hash:
                            mark_processed(tx_hash)
                        tag = f" (tx {tx_hash})" if tx_hash else ""
                        extra = f" | amount={amount}" if amt_field else ""
                        print(f"🌟 {sender} +{points} pts{extra}{tag}")
                    else:
                        # Not an SBT holder; ignore noisily for visibility
                        if tx_hash:
                            mark_processed(tx_hash)
                        print(f"⛔ Ignored {sender}: no SBT")

        except Exception as e:
            # Connection dropped or failed -> short backoff, then retry
            print("⚠️  WS error:", e)
            await asyncio.sleep(3.0)


def main():
    print("🚀 Real-time monitor via WebSockets (Tx events)")
    print(f"   WS: {WS_URL}")
    print(f"   GraphQL: {GRAPHQL_URL}")
    print(f"   SBT contract: {SBT_CONTRACT}")
    asyncio.run(ws_loop())


if __name__ == "__main__":
    main()
