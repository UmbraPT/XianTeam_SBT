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
        {"$setOnInsert": {
            "address": address,
            "score": 0,
            "amount": 0.0,
            "dex_volume": 0.0,
            "dex_swaps": 0,
            "stake_duration_sec": 0,
            "stake_active": False,
            "stake_last_update": None,
            "total_sent_xian": 0.0,
            "updated_at": time.time(),
        }},
        upsert=True
    )

def add_points(address: str, score: int, amount_to_add: float = 0.0):
    traits_col.update_one(
        {"address": address},
        {"$inc": {"score": int(score), "amount": float(amount_to_add)}, "$set": {"updated_at": time.time()}}
    )

def inc_total_sent_xian(address: str, amount: float):
    traits_col.update_one(
        {"address": address},
        {"$inc": {"total_sent_xian": float(amount)}, "$set": {"updated_at": time.time()}}
    )

def inc_dex_volume(address: str, vol: float):
    traits_col.update_one(
        {"address": address},
        {"$inc": {"dex_volume": float(vol), "dex_swaps": 1}, "$set": {"updated_at": time.time()}}
    )

def stake_start_or_refresh(address: str, now_ts: float):
    doc = traits_col.find_one({"address": address}, {"stake_active": 1, "stake_last_update": 1, "stake_duration_sec": 1})
    if not doc or not doc.get("stake_active"):
        traits_col.update_one(
            {"address": address},
            {"$set": {"stake_active": True, "stake_last_update": now_ts, "updated_at": now_ts}}
        )
    else:
        # already active: accrue elapsed then refresh
        last = doc.get("stake_last_update") or now_ts
        elapsed = max(0, int(now_ts - last))
        traits_col.update_one(
            {"address": address},
            {"$inc": {"stake_duration_sec": elapsed},
             "$set": {"stake_last_update": now_ts, "updated_at": now_ts}}
        )

def stake_stop(address: str, now_ts: float):
    doc = traits_col.find_one({"address": address}, {"stake_active": 1, "stake_last_update": 1})
    last = (doc or {}).get("stake_last_update")
    elapsed = max(0, int(now_ts - last)) if last else 0
    traits_col.update_one(
        {"address": address},
        {"$inc": {"stake_duration_sec": elapsed},
         "$set": {"stake_active": False, "stake_last_update": now_ts, "updated_at": now_ts}}
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

                    now_ts = time.time()

                    # currency.transfer — points +1, and track total_sent_xian
                    if contract == "currency" and func == "transfer":
                        amount = 0.0
                        if "amount" in kwargs:
                            try: amount = float(kwargs["amount"])
                            except: amount = 0.0

                        if sender in sbt_holders:
                            ensure_user(sender)
                            add_points(sender, score=1, amount_to_add=amount)    # your original behavior
                            inc_total_sent_xian(sender, amount)           # NEW aggregate
                            print(f"⚡ transfer {sender} +1pt | total_sent_xian+={amount}")
                        if tx_hash:
                            mark_processed(tx_hash)
                        continue

                    # con_dex_v2.swapExactTokenForToken — points +5, track dex_volume (+ count)
                    if contract == "con_dex_v2" and func == "swapExactTokenForToken":
                        vol = 1.0
                        for k in ("amountIn", "amount_in", "amount"):
                            if k in kwargs:
                                try:
                                    vol = float(kwargs[k]); break
                                except:
                                    pass

                        if sender in sbt_holders:
                            ensure_user(sender)
                            add_points(sender, score=5, amount_to_add=0.0)
                            inc_dex_volume(sender, vol)
                            print(f"💱 swap {sender} +5pts | dex_volume+={vol}")
                        if tx_hash:
                            mark_processed(tx_hash)
                        continue

                    # con_staking_v1.deposit — points +15, mark stake active/refresh
                    if contract == "con_staking_v1" and func == "deposit":
                        if sender in sbt_holders:
                            ensure_user(sender)
                            add_points(sender, score=15, amount=0.0)
                            stake_start_or_refresh(sender, now_ts)
                            print(f"📥 stake start/refresh {sender}")
                        if tx_hash:
                            mark_processed(tx_hash)
                        continue

                    # con_staking_v1 withdrawals — accrue duration and stop
                    if contract == "con_staking_v1" and func in ("withdraw", "unstake", "emergency_withdraw"):
                        if sender in sbt_holders:
                            ensure_user(sender)
                            stake_stop(sender, now_ts)
                            print(f"🏁 stake stop {sender}")
                        if tx_hash:
                            mark_processed(tx_hash)
                        continue

                    # other functions you might still want to score (e.g., votes, submissions)
                    if contract == "con_xipoll_v0_clean" and func == "vote":
                        if sender in sbt_holders:
                            ensure_user(sender)
                            add_points(sender, score=5, amount=0.0)
                            print(f"🗳️ vote {sender} +5pts")
                        if tx_hash:
                            mark_processed(tx_hash)
                        continue

                    if contract == "submission" and func == "submit_contract":
                        if sender in sbt_holders:
                            ensure_user(sender)
                            add_points(sender, score=50, amount=0.0)
                            print(f"📜 submit_contract {sender} +50pts")
                        if tx_hash:
                            mark_processed(tx_hash)
                        continue

                    # Not tracked — just mark processed so we don't revisit
                    if tx_hash:
                        mark_processed(tx_hash)


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
