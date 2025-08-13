import requests
import sqlite3
import time
from pymongo import MongoClient

GRAPHQL_URL = "https://devnet.xian.org/graphql"
CONTRACT_NAME = "con_sbtxian"
SBT_REFRESH_INTERVAL = 60 

client = MongoClient("mongodb://localhost:27017/")
db = client["xian_monitor"]
traits_col = db["traits"]
processed_col = db["processed"]

traits_col.create_index("address", unique=True)
processed_col.create_index("tx_hash", unique=True)

def setup_db():
   """No schema setup needed for MongoDB, but we'll ensure indexes exist."""
   traits_col.create_index("address", unique=True)
   processed_col.create_index("tx_hash", unique=True)

def ensure_user_exists(address):
    """Ensure a user exists in the traits collection."""
    traits_col.update_one(
        {"address": address},
        {"$setOnInsert": {"score": 0, "amount": 0.0}},
        upsert=True
    )

def has_processed(tx_hash):
    """Check if a transaction hash is already processed."""
    return processed_col.count_documents({"tx_hash": tx_hash}, limit=1) > 0

def mark_processed(tx_hash):
    """Mark a transaction as processed."""
    try:
        processed_col.insert_one({"tx_hash": tx_hash})
    except Exception:
        # Ignore duplicate key errors
        pass

def increment_score_and_amount(address, score, amount):
    """Increment a user's score and amount."""
    traits_col.update_one(
        {"address": address},
        {"$inc": {"score": score, "amount": amount}}
    )

def get_all_sbt_holders():
    query = {
        "query": f"""
        query {{
          allStates(
            filter: {{ key: {{ like: "{CONTRACT_NAME}.sbt_holders:%" }} }},
            first: 500
          ) {{
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
        r.raise_for_status()
        data = r.json()
        edges = data.get("data", {}).get("allStates", {}).get("edges", [])
        holders = []
        for edge in edges:
            key = edge["node"]["key"]
            if ":" in key:
                addr = key.split(":", 1)[1]
                holders.append(addr)
        return set(holders)  # use set for fast lookup
    except Exception as e:
        print("❌ Error fetching holders:", e)
        return set()

def run_query(contract, function, points, amount_field=None):
    query = {
        "query": f"""
        query {{
          allTransactions(
            condition: {{ success: true }}
            filter: {{
              and: [
                {{ contract: {{ like: "{contract}" }} }},
                {{ function: {{ like: "{function}" }} }}
              ]
            }}
            orderBy: BLOCK_TIME_DESC
            first: 1
          ) {{
            edges {{
              node {{
                sender
                jsonContent
              }}
            }}
          }}
        }}
        """
    }

    try:
        r = requests.post(GRAPHQL_URL, json=query)
        r.raise_for_status()
        data = r.json()
        edges = data.get("data", {}).get("allTransactions", {}).get("edges", [])
        if not edges:
            return []

        results = []
        for edge in edges:
            node = edge["node"]
            sender = node.get("sender")
            json_data = node.get("jsonContent", {})
            tx_hash = json_data.get("b_meta", {}).get("hash")
            kwargs = json_data.get("payload", {}).get("kwargs", {})

            if sender and tx_hash:
                amount = 0.0
                if amount_field and amount_field in kwargs:
                    try:
                        amount = float(kwargs[amount_field])
                    except (TypeError, ValueError):
                        amount = 0.0

                results.append((tx_hash, sender, points, amount))
        return results

    except Exception as e:
        print(f"❌ Error fetching {contract}.{function}:", e)
        return []
    
def main_loop():
    print("🚀 Watching for currency.transfer, dex.swap, staking.deposit...")
    last_refresh = 0    
    sbt_holders = set()
    
    while True:
        # Refresh SBT holders if interval passed
        now = time.time()
        if now - last_refresh >= SBT_REFRESH_INTERVAL:
            sbt_holders = get_all_sbt_holders()
            last_refresh = now
            print(f"🔄 Refreshed SBT holders list ({len(sbt_holders)} addresses)")        
        
        all_events = []

        # 🔁 Transfer = 1 point
        all_events += run_query("%", "transfer", points=1, amount_field="amount")

        # 💱 Swap = 5 points
        all_events += run_query("con_dex_v2", "swapExactTokenForToken", points=5)

        # 📥 Stake = 15 points
        all_events += run_query("con_staking_v1", "deposit", points=15)

         # 📥 Voting = 15 points
        all_events += run_query("con_xipoll_v0_clean", "vote", points=5)

        # Contract submiting = 50 points
        all_events += run_query("submission", "submit_contract", points=50)

        for tx_hash, sender, score, amount in all_events:
            if sender not in sbt_holders:
            #    print(f"⛔ Ignored {sender} (no SBT)")
                continue

            if has_processed(tx_hash):
                continue

            print(f"🌟 {sender} earned +{score} pts (tx {tx_hash})")
            ensure_user_exists(sender)
            increment_score_and_amount(sender, score, amount)
            mark_processed(tx_hash)

        time.sleep(3)


if __name__ == "__main__":
    setup_db()
    main_loop()
