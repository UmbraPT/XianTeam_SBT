import requests
import sqlite3
import time

GRAPHQL_URL = "https://node.xian.org/graphql"

def setup_db():
    conn = sqlite3.connect("sbt3.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS traits (
            address TEXT PRIMARY KEY,
            score INTEGER DEFAULT 0,
            amount REAL DEFAULT 0.0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS processed (
            tx_hash TEXT PRIMARY KEY
        )
    """)
    conn.commit()
    conn.close()

def ensure_user_exists(address):
    conn = sqlite3.connect("sbt3.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO traits (address) VALUES (?)", (address,))
    conn.commit()
    conn.close()

def has_processed(tx_hash):
    conn = sqlite3.connect("sbt3.db")
    c = conn.cursor()
    c.execute("SELECT 1 FROM processed WHERE tx_hash = ?", (tx_hash,))
    result = c.fetchone()
    conn.close()
    return result is not None

def mark_processed(tx_hash):
    conn = sqlite3.connect("sbt3.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO processed (tx_hash) VALUES (?)", (tx_hash,))
    conn.commit()
    conn.close()

def increment_score_and_amount(address, score, amount):
    conn = sqlite3.connect("sbt3.db")
    c = conn.cursor()
    c.execute("UPDATE traits SET score = score + ?, amount = amount + ? WHERE address = ?", (score, amount, address))
    conn.commit()
    conn.close()

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
