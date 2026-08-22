import sqlite3
con = sqlite3.connect(r"data/reachout.db")
c = con.cursor()
# Remove the stale test email senders (and the vacant WA session) created from leftover/test data.
c.execute("delete from sender_accounts where id in ('EMAIL_SESSION_1','EMAIL_SESSION_TEST','WA_SESSION_1')")
con.commit()
print("remaining sender_accounts:")
for row in c.execute("select id,channel,status,identity from sender_accounts"):
    print("  ", row)
con.close()
print("cleanup done")
