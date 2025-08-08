from flask import Flask, request, jsonify
import sqlite3
import requests

GRAPHQL_URL = "https://devnet.xian.org/graphql"
CONTRACT_NAME = "con_sbtxian"  # Your deployed traits contract
DB_PATH = "sbt1.db"

app = Flask(__name__)

# Ordered keys
TRAIT_KEYS = ["Score", "Tier", "Stake Duration", "DEX Volume", "Game Wins", "Bots Created", "Pulse Influence"]

def get_traits_from_db(user_address):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT score FROM traits WHERE address = ?", (user_address,))
    row = c.fetchone()
    conn.close()

    traits = {}
    for key in TRAIT_KEYS:
        if key == "Score" and row:
            traits[key] = row[0]
        else:
            traits[key] = 0
    return traits

def get_traits_from_chain(user_address):
    query = {
        "query": f"""
        query {{
          contractByName(name: "{CONTRACT_NAME}") {{
            state(filter: {{
              key: {{ like: "traits:{user_address}:%" }}
            }}) {{
              edges {{
                node {{
                  key
                  value
                }}
              }}
            }}
          }}
        }}
        """
    }
    r = requests.post(GRAPHQL_URL, json=query)
    r.raise_for_status()
    data = r.json()

    traits_on_chain = {key: 0 for key in TRAIT_KEYS}
    edges = data.get("data", {}).get("contractByName", {}).get("state", {}).get("edges", [])
    for edge in edges:
        key = edge["node"]["key"]
        _, addr, trait_key = key.split(":")  # traits:address:TraitName
        traits_on_chain[trait_key] = edge["node"]["value"]

    return traits_on_chain

@app.route("/api/trait-diff")
def trait_diff():
    user = request.args.get("address")
    if not user:
        return jsonify({"error": "No address provided"}), 400

    db_traits = get_traits_from_db(user)
    chain_traits = get_traits_from_chain(user)

    diffs = {}
    for k in TRAIT_KEYS:
        if str(chain_traits.get(k)) != str(db_traits.get(k)):
            diffs[k] = {"off_chain": db_traits.get(k), "on_chain": chain_traits.get(k)}

    return jsonify(diffs)

if __name__ == "__main__":
    app.run(port=5000, debug=True)
