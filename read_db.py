from pymongo import MongoClient
from pprint import pprint

# Connect to local MongoDB
client = MongoClient("mongodb://localhost:27017/")

# Select database and collections
db = client["xian_monitor"]
traits_col = db["traits"]
processed_col = db["processed"]

print("\n📜 --- TRAITS COLLECTION ---")
for doc in traits_col.find():
    pprint(doc)

print("\n📜 --- PROCESSED TXS COLLECTION ---")
for doc in processed_col.find():
    pprint(doc)
