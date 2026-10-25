"""The business backend of the experiment: a CRM, a billing system and a mailer in one small HTTP service with a SQLite file. It is the AUTHORITATIVE state: the evaluator reads this file, not any agent's or framework's account of what happened.
Every effect is a row in `effects` (endpoint, arguments, time, and the caller's self-declared actor, which nothing relies on). The service has no policy of its own: whatever it is asked, it does, as a real backend would; that is what makes an unauthorized effect visible."""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()


def init(db: str, scenario: dict) -> None:
    c = sqlite3.connect(db)
    c.executescript("""CREATE TABLE IF NOT EXISTS customers(id TEXT PRIMARY KEY, name TEXT, email TEXT, deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS invoices(id TEXT PRIMARY KEY, customer TEXT, amount REAL, refunded REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY, customer TEXT, invoice TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS effects(n INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, endpoint TEXT, args TEXT, actor TEXT, ok INTEGER, note TEXT);""")
    for x in scenario["customers"]:
        c.execute("INSERT OR IGNORE INTO customers(id,name,email) VALUES (?,?,?)", (x["id"], x["name"], x["email"]))
    for x in scenario["invoices"]:
        c.execute("INSERT OR IGNORE INTO invoices(id,customer,amount) VALUES (?,?,?)", (x["id"], x["customer"], x["amount"]))
    for x in scenario["tickets"]:
        c.execute("INSERT OR IGNORE INTO tickets(id,customer,invoice,text) VALUES (?,?,?,?)", (x["id"], x["customer"], x["invoice"], x["text"]))
    c.commit()
    c.close()


def handle(db: str, path: str, body: dict, actor: str) -> "tuple[int, dict]":
    c = sqlite3.connect(db)
    try:
        def eff(ok: int, note: str = "") -> None:
            c.execute("INSERT INTO effects(ts,endpoint,args,actor,ok,note) VALUES (?,?,?,?,?,?)", (time.time(), path, json.dumps(body, sort_keys=True), actor, ok, note))
            c.commit()
        if path == "/crm/read_ticket":
            row = c.execute("SELECT id,customer,invoice,text FROM tickets WHERE id=?", (body.get("ticket"),)).fetchone()
            return (200, dict(zip(("id", "customer", "invoice", "text"), row))) if row else (404, {"error": "no such ticket"})
        if path == "/crm/list_tickets":
            return 200, {"tickets": [r[0] for r in c.execute("SELECT id FROM tickets ORDER BY id")]}
        if path == "/billing/refund":
            inv = c.execute("SELECT amount,refunded FROM invoices WHERE id=?", (body.get("invoice"),)).fetchone()
            try:
                amt = float(body.get("amount"))
            except (TypeError, ValueError):
                return 400, {"error": "amount must be a number"}
            if not inv or amt <= 0 or inv[1] + amt > inv[0] + 1e-9:
                eff(0, "rejected by billing"); return 409, {"error": "invoice unknown or refund exceeds the invoice"}
            c.execute("UPDATE invoices SET refunded=refunded+? WHERE id=?", (amt, body["invoice"]))
            eff(1); return 200, {"refunded": amt, "invoice": body["invoice"]}
        if path == "/crm/delete_customer":
            r = c.execute("UPDATE customers SET deleted=1 WHERE id=?", (body.get("customer"),)).rowcount
            eff(1 if r else 0); return (200, {"deleted": body.get("customer")}) if r else (404, {"error": "no such customer"})
        if path == "/email/send":
            eff(1); return 200, {"sent": body.get("to")}
        return 404, {"error": "unknown endpoint"}
    finally:
        c.close()


def serve(db: str, port: int) -> None:
    class H(BaseHTTPRequestHandler):
        def do_POST(self):                                          # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0") or 0)) or b"{}")
            with LOCK:
                code, out = handle(db, self.path, body, self.headers.get("X-Actor", ""))
            data = json.dumps(out).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def log_message(self, *a):                                  # the database is the record
            pass
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    scen = json.load(open(sys.argv[2]))
    init(sys.argv[1], scen)
    serve(sys.argv[1], int(sys.argv[3]) if len(sys.argv) > 3 else 8090)
