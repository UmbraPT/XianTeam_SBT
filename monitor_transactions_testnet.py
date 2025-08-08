# ✅ Full SBT Monitor with DB Integration

import requests
import sqlite3
import time
import base64
import json
import binascii

GRAPHQL_URL = "https://devnet.xian.org/graphql"
CONTRACT_NAME = "con_sbtxian"
LATEST_BLOCK = 0

def ensure_user_exists(address):
    conn = sqlite3.connect("sbt1.db")
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS traits (address TEXT PRIMARY KEY, score INTEGER DEFAULT 0)")
    c.execute("INSERT OR IGNORE INTO traits (address) VALUES (?)", (address,))
    conn.commit()
    conn.close()

def increment_score(address, amount=1):
    conn = sqlite3.connect("sbt1.db")
    c = conn.cursor()
    c.execute("UPDATE traits SET score = score + ? WHERE address = ?", (amount, address))
    conn.commit()
    conn.close()

def get_all_sbt_holders():
    query = {
        "query": f"""
        query {{
          allStates(filter: {{ key: {{ like: \"{CONTRACT_NAME}.sbt_holders:%\" }} }}, first: 500) {{
            edges {{
              node {{
                key
              }}
            }}
          }}
        }}
        """
    }
    try:
        r = requests.post(GRAPHQL_URL, json=query)
        data = r.json()
        edges = data.get("data", {}).get("allStates", {}).get("edges", [])
        holders = []
        for edge in edges:
            key = edge["node"]["key"]
            if ":" in key:
                addr = key.split(":", 1)[1]
                holders.append(addr)
        return holders
    except Exception as e:
        print("❌ Error fetching holders:", e)
        return []

def get_latest_block():
    query = {
        "query": """
        query {
          allTransactions(orderBy: BLOCK_HEIGHT_DESC, first: 1) {
            nodes {
              blockHeight
            }
          }
        }
        """
    }
    try:
        r = requests.post(GRAPHQL_URL, json=query)
        data = r.json()
        return int(data["data"]["allTransactions"]["nodes"][0]["blockHeight"])
    except Exception as e:
        print("❌ Error getting latest block:", e)
        return None

def get_block(height):
    try:
        r = requests.get(f"https://testnet.xian.org/block?height={height}")
        return r.json()
    except Exception as e:
        print(f"❌ Error fetching block {height}:", e)
        return None

def analyze_block(height, watch_addresses):
    block = get_block(height)
    if not block:
        return

    txs = block.get("result", {}).get("block", {}).get("data", {}).get("txs", [])
    for tx_base64 in txs:
        try:
            hex_data = base64.b64decode(tx_base64).decode()
            json_bytes = binascii.unhexlify(hex_data)
            tx_json = json.loads(json_bytes.decode())

            sender = tx_json.get("payload", {}).get("sender")
            contract = tx_json.get("payload", {}).get("contract")
            function = tx_json.get("payload", {}).get("function")

            if sender in watch_addresses:
                print(f"⚡ Tx from {sender} → contract={contract}, function={function}")
                ensure_user_exists(sender)
                increment_score(sender)

        except Exception as inner:
            print("❌ Error decoding tx:", inner)

def poll_blocks():
    global LATEST_BLOCK
    watch_addresses = get_all_sbt_holders()
    print("📍 SBT Holders:", watch_addresses)

    while True:
        latest = get_latest_block()
        if latest is None:
            time.sleep(3)
            continue

        if LATEST_BLOCK == 0:
            LATEST_BLOCK = latest - 10

        while LATEST_BLOCK <= latest:
            print(f"🔍 Scanning block {LATEST_BLOCK}...")
            analyze_block(LATEST_BLOCK, watch_addresses)
            LATEST_BLOCK += 1
            time.sleep(0.5)

        time.sleep(5)

if __name__ == "__main__":
    print("🚀 Watching for transactions involving SBT holders...")
    poll_blocks()
