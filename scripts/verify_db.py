import sqlite3

con = sqlite3.connect(r"data/reachout.db")
c = con.cursor()


def q(s, *a):
    return c.execute(s, a).fetchall()


print("contacts:", q("select count(*) from contacts")[0][0])
print("companies:", q("select count(*) from companies")[0][0])
print("attempts:", q("select count(*) from outreach_attempts")[0][0])
print("attempts by status:", q("select status,count(*) from outreach_attempts group by status"))
print(
    "attempts with NULL sender_account_id:",
    q("select count(*) from outreach_attempts where sender_account_id is null")[0][0],
)
print(
    "duplicate phones in contacts:",
    q("select phone,count(*) from contacts where phone is not null group by phone having count(*)>1"),
)
print(
    "unreachable contacts:",
    q("select count(*) from contacts where (phone is null or phone='') and (email is null or email='')")[0][0],
)
print("FK outreach_attempts:", q("pragma foreign_key_list(outreach_attempts)"))
print("indexes/unique on sender_accounts:", q("pragma index_list(sender_accounts)"))
print("suppression unique idx:", q("pragma index_list(suppression_records)"))
print("contacts check:", q("select sql from sqlite_master where type='table' and name='contacts'")[0][0][:400])
