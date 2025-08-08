import sqlite3

conn = sqlite3.connect("sbt1.db")
c = conn.cursor()

for row in c.execute("SELECT address, score, amount FROM traits"):
    print(row)

conn.close()