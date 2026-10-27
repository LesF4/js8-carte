"""Faux JS8Call (API TCP) pour tester JS8 Carte sans radio : python tests/simulate.py [port]

Répond aux questions de l'app (indicatif, locator, fréquence, station sélectionnée, stations entendues)
et envoie des RX.SPOT, RX.DIRECTED et un LOG.QSO. Port par défaut 2252 (le vrai JS8Call est sur 2242).
"""
import json
import random
import socket
import sys
import threading
import time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 2252
ALL_TXT = sys.argv[2] if len(sys.argv) > 2 else ""   # faux journal ALL.TXT (lignes « Transmitting »)
DIAL = 14078000
STATIONS = [("WD5EED", "EM44XT"), ("IC8BNR", "JN70"), ("F4GYY", "JN18EU"), ("EA4XY", "IN80"),
            ("DL1ABC", "JO62"), ("G4XYZ", "IO91WM"), ("ON4AA", "JO20"), ("SV2BBO/A", "KN10"),
            ("OH2XX", "KP20"), ("K1ABC", "FN42"), ("PY2ZZ", "GG66"), ("VK2AAA", "QF56"),
            ("LA9QQ", "JP99"), ("F4LNC", "JN37RT"), ("HB9ZZ", "JN47"), ("SP5ABC", "KO02"), ("CT1XYZ", "IM58")]


def tx_log(text):
    if ALL_TXT:
        with open(ALL_TXT, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "  Transmitting 14.078 MHz  JS8:  "
                    + text + " " + chr(10))


def now_ms():
    return int(time.time() * 1000)


def send(conn, mtype, value="", **params):
    conn.sendall((json.dumps({"type": mtype, "value": value, "params": params}) + "\n").encode())


def answer(conn, state):
    """Comme le vrai JS8Call : une réponse ne part que si sa question (_ID) est encore en attente,
    et elle retire ce _ID ; deux questions avec le même _ID n'obtiennent donc qu'une seule réponse."""
    buf = b""
    pending = set()
    while True:
        data = conn.recv(65536)
        if not data:
            return
        buf += data
        lines = []
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            pending.add((msg.get("params") or {}).get("_ID"))
            lines.append(msg)
        for msg in lines:
            rid = (msg.get("params") or {}).get("_ID")
            if rid not in pending:
                print("[faux JS8Call] réponse perdue (même _ID) :", msg.get("type"), flush=True)
                continue
            pending.discard(rid)
            t = msg.get("type")
            if t == "STATION.GET_CALLSIGN":
                send(conn, "STATION.CALLSIGN", "F4MAJ")
            elif t == "STATION.GET_GRID":
                send(conn, "STATION.GRID", "JN37QS")
            elif t == "RIG.GET_FREQ":
                send(conn, "RIG.FREQ", "", DIAL=DIAL, FREQ=DIAL + 1500, OFFSET=1500)
            elif t == "RX.GET_CALL_SELECTED":
                send(conn, "RX.CALL_SELECTED", state["selected"])
            elif t == "RX.GET_CALL_ACTIVITY":
                calls = {c: {"SNR": random.randint(-22, 5), "GRID": g, "UTC": now_ms() - random.randint(0, 900) * 1000}
                         for c, g in STATIONS[:10]}
                send(conn, "RX.CALL_ACTIVITY", "", **calls)


def main():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(1)
    print("[faux JS8Call] en attente sur", PORT, flush=True)
    conn, _ = srv.accept()
    print("[faux JS8Call] app connectée", flush=True)
    state = {"selected": ""}
    threading.Thread(target=answer, args=(conn, state), daemon=True).start()
    time.sleep(2)
    for c, g in STATIONS[10:]:
        send(conn, "RX.SPOT", "", CALL=c, GRID=g, SNR=random.randint(-20, 0), FREQ=DIAL + random.randint(500, 2500))
        time.sleep(0.3)
    for c, g in (("IU2ITE", "JN45"), ("CT1XYZ", "IM58"), ("LA9QQ", "JP99")):
        send(conn, "RX.DIRECTED", "%s: @ALLCALL CQ CQ CQ %s ♢" % (c, g), FROM=c, TO="@ALLCALL", CMD=" CQ",
             GRID=g, SNR=random.randint(-15, 5), FREQ=DIAL + random.randint(500, 2500), UTC=now_ms())
        time.sleep(0.3)
    send(conn, "RX.DIRECTED", "SM6TWY: @HB HEARTBEAT JO57 ♢", FROM="SM6TWY", TO="@HB", CMD=" HEARTBEAT",
         GRID="JO57", SNR=-6, FREQ=DIAL + 900, UTC=now_ms())
    for c, rpt in (("SP5ABC", "-12"), ("G4XYZ", "+02")):          # réponses à mon HB
        send(conn, "RX.DIRECTED", "%s: F4MAJ HEARTBEAT SNR %s ♢" % (c, rpt), FROM=c, TO="F4MAJ",
             CMD=" HEARTBEAT SNR", GRID="", SNR=random.randint(-18, 0), FREQ=DIAL + 700, UTC=now_ms())
        time.sleep(0.3)
    state["selected"] = "IC8BNR"
    time.sleep(2.5)
    tx_log("F4MAJ: SM6TWY HEARTBEAT SNR -06")                  # réponse automatique à un HB : pas un QSO
    send(conn, "TX.FRAME", "", TONES=[])
    time.sleep(0.5)
    send(conn, "RX.ACTIVITY", "OH3FZQ: @APRSIS MSG TO: ", SNR=-22, FREQ=DIAL + 1003, UTC=now_ms())
    send(conn, "RX.DIRECTED", "M0IQF: F4MAJ HEARTBEAT SNR -01 ♢", FROM="M0IQF", TO="F4MAJ",
         CMD=" HEARTBEAT SNR", GRID="", SNR=-3, FREQ=DIAL + 1178, UTC=now_ms())   # sans locator
    time.sleep(0.5)
    tx_log("F4MAJ: IC8BNR SNR -08")                            # je réponds vraiment à IC8BNR
    send(conn, "TX.FRAME", "", TONES=[])
    time.sleep(1)
    send(conn, "RX.DIRECTED", "IC8BNR: F4MAJ SNR -08 ♢", FROM="IC8BNR", TO="F4MAJ", CMD=" SNR", GRID="",
         SNR=-8, FREQ=DIAL + 1520, UTC=now_ms())
    time.sleep(3)
    send(conn, "LOG.QSO", "<call:6>WD5EED <eor>", CALL="WD5EED", GRID="EM44XT", FREQ=DIAL + 1500,
         MODE="MFSK", SUBMODE="JS8", **{"RPT.SENT": "-10", "RPT.RECV": "-08", "UTC.ON": now_ms() - 600000,
                                        "UTC.OFF": now_ms()})
    print("[faux JS8Call] scénario envoyé, je reste connecté", flush=True)
    while True:
        time.sleep(5)
        c, g = random.choice(STATIONS)
        send(conn, "RX.SPOT", "", CALL=c, GRID=g, SNR=random.randint(-20, 0), FREQ=DIAL + random.randint(500, 2500))


if __name__ == "__main__":
    main()
