"""
Consent-URL construction and the 403 hint text.

Guards the shape of the authorization request - no scope by default,
matching the implementation known to work - and that a 403 explains itself
rather than repeating Yahoo's misleading "application is not authorized".

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ["YAHOO_CONSUMER_KEY"]="k"; os.environ["YAHOO_CONSUMER_SECRET"]="s"
os.environ["FLASK_SECRET_KEY"]="scope-test"; os.environ["SKIP_SCHEMA_INIT"]="1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app                      # noqa: E402
app.config["TESTING"]=True
FAILS=[]
def check(l,c,d=""):
    print(f"  [{'PASS' if c else 'FAIL'}] {l}"+(f"  -- {d}" if d and not c else ""))
    if not c: FAILS.append(l)

def consent_url():
    c=app.test_client()
    r=c.post("/login", json={"league_id":"1","terms_accepted":True})
    return parse_qs(urlparse(r.get_json()["auth_url"]).query)

print("\n=== default scope ===")
q=consent_url()
check("no scope param by default", "scope" not in q, q)
check("state still there", len(q.get("state",[""])[0])>20)
check("redirect_uri still there", q.get("redirect_uri")==["http://localhost/callback"])

print("\n=== configurable ===")
app.config["YAHOO_SCOPE"]="openid fspt-w"
q=consent_url()
check("multi-scope passes through", q.get("scope")==["openid fspt-w"], q.get("scope"))
enc=urlparse(app.test_client().post("/login",json={"league_id":"1","terms_accepted":True}).get_json()["auth_url"]).query
check("space is url-encoded, not raw", " " not in enc and ("fspt-w" in enc), enc[:120])

app.config["YAHOO_SCOPE"]="fspt-r"
check("read-only honoured", consent_url().get("scope")==["fspt-r"])

print("\n=== empty scope omits the param (old broken behaviour) ===")
app.config["YAHOO_SCOPE"]=""
check("no scope key when blank", "scope" not in consent_url())

print("\n=== 403 hint names scope first ===")
app.config["YAHOO_SCOPE"]="fspt-w"
import yahoo_auth                        # noqa: E402
with app.test_request_context():
    h=yahoo_auth._forbidden_hint('{"error":{"description":"This application is not authorized"}}')
check("guid-present 403 points at app permission", "developer.yahoo.com" in h, h)
h2=yahoo_auth._forbidden_hint("{}", token_had_guid=False)
check("guid-less 403 says re-consent will not help", "will NOT help" in h2, h2)
check("guid-less 403 names xoauth_yahoo_guid", "xoauth_yahoo_guid" in h2, h2)


print("\n"+"="*46)
if FAILS: print(f"{len(FAILS)} FAILED: "+"; ".join(FAILS)); sys.exit(1)
print("All checks passed.")
