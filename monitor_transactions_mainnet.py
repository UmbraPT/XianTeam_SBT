import requests
import time
from pymongo import MongoClient

GRAPHQL_URL = "https://node.xian.org/graphql"

# MongoDB config (match server/testnet)
MONGO_URI = "mongodb://localhost:27017/"
MONGO_DB = "xian_monitor"
TRAITS_COLLECTION = "traits"
PROCESSED_COLLECTION = "processed"

# Setup Mongo client/collections
client = MongoClient(MONGO_URI)
db = client[MONGO_DB]
traits_col = db[TRAITS_COLLECTION]
processed_col = db[PROCESSED_COLLECTION]

def setup_db():
    """Ensure MongoDB indexes exist (idempotent)."""
    traits_col.create_index("address", unique=True)
    processed_col.create_index("tx_hash", unique=True)

def ensure_user_exists(address):
    traits_col.update_one(
        {"address": address},
        {"$setOnInsert": {"score": 0, "amount": 0.0}},
        upsert=True,
    )

def has_processed(tx_hash):
    return processed_col.count_documents({"tx_hash": tx_hash}, limit=1) > 0

def mark_processed(tx_hash):
    try:
        processed_col.insert_one({"tx_hash": tx_hash})
    except Exception:
        # Ignore duplicates
        pass

def increment_score_and_amount(address, score, amount):
    traits_col.update_one(
        {"address": address},
        {"$inc": {"score": score, "amount": amount}},
        upsert=True,
    )

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
    while True:
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
