"""Logique sans interface de JS8 Carte : locators, distances, état des stations, client JS8Call.

JS8Call expose une API TCP (127.0.0.1:2242 par défaut) : un message JSON par ligne
{"type": ..., "value": ..., "params": {...}}. JS8Call envoie de lui-même RX.SPOT, RX.DIRECTED,
RIG.FREQ et LOG.QSO ; la station sélectionnée et la liste des stations entendues se demandent
(RX.GET_CALL_SELECTED, RX.GET_CALL_ACTIVITY).
"""

import json
import math
import os
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

APP_NAME = "JS8 Carte"
APP_VERSION = "0.8.3"

DEFAULTS = {
    "js8_host": "127.0.0.1",
    "js8_port": 2242,
    "my_grid": "",            # vide = demandé à JS8Call
    "window_minutes": 30,     # stations affichées : entendues depuis N minutes
    "band": "",               # vide = toutes les bandes
    "geometry": "",
    "qrz_user": "",
    "qrz_password": "",       # chiffré par Windows (DPAPI), jamais en clair
}

BANDS = [
    (1.8, 2.0, "160m"), (3.5, 4.0, "80m"), (5.25, 5.45, "60m"), (7.0, 7.3, "40m"),
    (10.1, 10.15, "30m"), (14.0, 14.35, "20m"), (18.068, 18.168, "17m"), (21.0, 21.45, "15m"),
    (24.89, 24.99, "12m"), (28.0, 29.7, "10m"), (50.0, 54.0, "6m"), (144.0, 148.0, "2m"),
]

_GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2}([0-9]{2})?)?$")


# --------------------------------------------------------------------------- fichiers

def data_dir():
    base = os.environ.get("JS8CARTE_DATA") or os.path.join(
        os.environ.get("APPDATA") or os.path.expanduser("~"), "JS8Carte")
    os.makedirs(base, exist_ok=True)
    return base


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(os.path.join(data_dir(), "config.json"), encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    path = os.path.join(data_dir(), "config.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- géographie

def valid_grid(grid):
    g = str(grid or "").strip().upper()
    return g != "RR73" and bool(_GRID_RE.match(g[:8]))   # RR73 = fin de QSO FT8, pas un locator


def grid_to_latlon(grid):
    """Centre du carré locator (4, 6 ou 8 caractères) -> (lat, lon), ou None."""
    g = str(grid or "").strip().upper()
    if not valid_grid(g):
        return None
    lon = (ord(g[0]) - 65) * 20 - 180
    lat = (ord(g[1]) - 65) * 10 - 90
    lon += int(g[2]) * 2
    lat += int(g[3])
    w, h = 2.0, 1.0
    if len(g) >= 6:
        lon += (ord(g[4]) - 65) * 5 / 60
        lat += (ord(g[5]) - 65) * 2.5 / 60
        w, h = 5 / 60, 2.5 / 60
    if len(g) >= 8:
        lon += int(g[6]) * 0.5 / 60
        lat += int(g[7]) * 0.25 / 60
        w, h = 0.5 / 60, 0.25 / 60
    return lat + h / 2, lon + w / 2


def distance_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def azimuth_deg(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def band_for_hz(hz):
    try:
        mhz = float(hz) / 1e6
    except (TypeError, ValueError):
        return ""
    for lo, hi, name in BANDS:
        if lo <= mhz <= hi:
            return name
    return ""


def base_call(call):
    """F/DL1ABC/P -> DL1ABC : la partie la plus longue qui contient un chiffre."""
    parts = [p for p in str(call or "").upper().split("/") if p]
    with_digit = [p for p in parts if any(c.isdigit() for c in p)]
    return max(with_digit or parts or [""], key=len)


# --------------------------------------------------------------------------- état des stations

class Station:
    __slots__ = ("call", "grid", "snr", "last", "band", "freq", "kind", "kind_ts", "text", "worked",
                 "heard_snr", "heard_ts", "book_grid")

    def __init__(self, call):
        self.call = call
        self.grid = ""
        self.snr = None
        self.last = 0.0
        self.band = ""
        self.freq = 0
        self.kind = "Spot"
        self.kind_ts = 0.0
        self.text = ""
        self.worked = False
        self.heard_snr = None     # report qu'elle me donne en réponse à mon HB (elle m'entend)
        self.heard_ts = 0.0
        self.book_grid = ""       # locator connu d'une séance précédente (carnet des locators)

    def best_grid(self):
        """Locator transmis aujourd'hui en priorité, sinon celui du carnet."""
        return self.grid or self.book_grid

    def latlon(self):
        return grid_to_latlon(self.best_grid())


class GridBook:
    """Carnet des locators : retient d'une séance à l'autre où se trouve chaque station déjà entendue."""

    def __init__(self, path=None):
        self.path = path
        self.data = {}
        self.dirty = False
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except (OSError, ValueError):
                self.data = {}

    def get(self, call):
        e = self.data.get(base_call(call))
        return e.get("grid", "") if isinstance(e, dict) else ""

    def put(self, call, grid):
        g, key = str(grid or "").strip().upper(), base_call(call)
        if not key or not valid_grid(g):
            return
        old = self.get(key)
        # garder le plus précis ; un nouveau carré (station qui a bougé) remplace l'ancien
        if old and old[:4] == g[:4] and len(old) > len(g):
            return
        if old != g:
            self.dirty = True
        self.data[key] = {"grid": g, "ts": int(time.time())}

    def save(self):
        if not (self.path and self.dirty):
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError:
            pass


_CALL_RE = re.compile(r"^(?=.*\d)(?=.*[A-Z])[A-Z0-9/]{3,}$")
_ACTIVITY_FROM_RE = re.compile(r"^\s*([A-Z0-9/]{3,}):\s")


def is_callsign(word):
    return bool(_CALL_RE.match(str(word or "").strip().upper()))


def tx_destination(text, my_call):
    """Texte que j'émets (« F4MAJ: 9A3SWO HEARTBEAT SNR -05 ») -> (destinataire, est_un_qso).
    Pas de destinataire pour un appel général (@ALLCALL, @HB, @APRSIS…). Une réponse automatique
    à un HB (« HEARTBEAT SNR », « ACK ») a un destinataire mais n'est pas un QSO."""
    t = str(text or "").replace("♢", " ").strip().upper()
    me = str(my_call or "").strip().upper()
    # Seule la 1re trame d'un message commence par « F4MAJ: » ; les suivantes sont la suite du texte
    # (« JS8CALL THAT SHOWS ON A… ») et ne disent pas à qui j'écris.
    m = re.match(r"([A-Z0-9/]+):\s*", t)
    if not m or (me and m.group(1) != me):
        return "", False
    t = t[m.end():]
    words = t.split()
    if not words:
        return "", False
    dest = words[0].rstrip(":")
    if dest.startswith("@") or dest == me or not is_callsign(dest):
        return "", False
    rest = " ".join(words[1:])
    if rest.startswith("HEARTBEAT SNR") or rest == "ACK" or rest.startswith("ACK "):
        return dest, False
    return dest, True


KIND_RANK = {"Spot": 0, "HB": 1, "Message": 2, "CQ": 2, "Ack": 2, "QSO": 3}
ACK_CMDS = ("HEARTBEAT SNR", "ACK")   # réponses automatiques à mon HB : « K1ABC: F4MAJ HEARTBEAT SNR -12 »
_SNR_RE = re.compile(r"\bSNR\s*([+-]?\d{1,2})\b")
KIND_HOLD = 300               # un CQ / message reste affiché 5 min même si de simples spots suivent
CONTACT_TIMEOUT = 600         # « en QSO » tant qu'on échange, oublié après 10 min de silence


def directed_kind(to, cmd, text, my_call=""):
    """Type d'un message JS8 reçu : Ack (réponse à MON HB), CQ, HB (heartbeat) ou Message."""
    to, words = str(to or "").upper(), " %s %s " % (str(cmd or "").upper(), str(text or "").upper())
    if str(cmd or "").strip().upper() in ACK_CMDS:
        # « M0IQF: 9A3SWO HEARTBEAT SNR -13 » répond au HB de 9A3SWO, pas au mien
        mine = bool(my_call) and base_call(to) == base_call(my_call)
        return "Ack" if mine else "HB"
    if to == "@HB" or "HEARTBEAT" in words:
        return "HB"
    if " CQ " in words or " CQ\n" in words:
        return "CQ"
    return "Message"


class Tracker:
    """Rassemble ce que JS8Call envoie. Les méthodes renvoient l'ensemble des indicatifs modifiés."""

    def __init__(self, book=None):
        self.book = book if book is not None else GridBook()
        self.tx_log_active = False    # True : le destinataire de mes émissions vient du journal ALL.TXT
        self.stations = {}
        self.qsos = []            # QSO loggés (dicts), plus récent en dernier
        self.selected = ""
        self.my_call = ""
        self.my_grid = ""
        self.dial = 0
        self.contact = ""         # station avec qui je suis en communication
        self.contact_ts = 0.0
        self.contact_dir = ""     # "tx" : c'est moi qui émets ; "rx" : c'est elle qui m'envoie
        self.lock = threading.Lock()

    def current_contact(self):
        if self.contact and time.time() - self.contact_ts < CONTACT_TIMEOUT:
            return self.contact
        return ""

    def _set_contact(self, call):
        call = str(call or "").strip().upper()
        if not call or call.startswith("@") or call == self.my_call:
            return False
        changed = call != self.current_contact()
        self.contact, self.contact_ts = call, time.time()
        self._station(call)
        return changed

    def _station(self, call):
        call = str(call or "").strip().upper()
        if not call or call.startswith("@"):
            return None
        st = self.stations.get(call)
        if st is None:
            st = self.stations[call] = Station(call)
            st.book_grid = self.book.get(call)
        return st

    def _update(self, call, grid="", snr=None, utc_ms=None, freq=0, kind="Spot", text=""):
        if str(call or "").strip().upper() == self.my_call:
            return set()
        st = self._station(call)
        if st is None:
            return set()
        g = str(grid or "").strip().upper()
        if valid_grid(g) and (len(g) >= len(st.grid) or not st.grid.startswith(g[:4])):
            st.grid = g
            self.book.put(st.call, g)
        if snr is not None and str(snr).lstrip("+-").isdigit():
            st.snr = int(snr)
        t = (utc_ms / 1000.0) if utc_ms else time.time()
        st.last = max(st.last, t)
        if freq:
            st.freq = int(freq)
            st.band = band_for_hz(freq) or st.band
        now = time.time()
        held = now - st.kind_ts < KIND_HOLD
        if KIND_RANK.get(kind, 0) >= KIND_RANK.get(st.kind, 0) or not held:
            if st.kind != "QSO" or kind == "QSO":
                st.kind, st.kind_ts = kind, now
        if text:
            st.text = text
        return {st.call}

    def handle(self, msg):
        """Traite un message JS8Call ; renvoie (indicatifs modifiés, autre_chose_modifiée)."""
        mtype = str(msg.get("type", ""))
        value = msg.get("value", "")
        p = msg.get("params") or {}
        with self.lock:
            if mtype == "RX.SPOT":
                return self._update(p.get("CALL"), p.get("GRID"), p.get("SNR"), None, p.get("FREQ"), "Spot"), False
            if mtype in ("RX.DIRECTED", "RX.DIRECTED.ME"):
                text = str(value or p.get("TEXT") or "").strip()
                kind = directed_kind(p.get("TO"), p.get("CMD"), text, self.my_call)
                changed = self._update(p.get("FROM"), p.get("GRID"), p.get("SNR"), p.get("UTC"), p.get("FREQ"),
                                       kind, text)
                to = base_call(p.get("TO"))
                other = False
                to_me = bool(self.my_call) and to == base_call(self.my_call)
                if to_me and kind == "Ack":
                    # réponse à mon HB : elle m'entend, avec ce report ; ce n'est pas un QSO
                    st = self.stations.get(str(p.get("FROM") or "").strip().upper())
                    m = _SNR_RE.search(text.upper())
                    if st is not None:
                        st.heard_ts = time.time()
                        if m:
                            st.heard_snr = int(m.group(1))
                elif to_me:                                            # elle m'écrit : on est en contact
                    other = self._set_contact(p.get("FROM"))
                    self.contact_dir = "rx"
                    changed |= {str(p.get("FROM") or "").upper()} - {""}
                return changed, other
            if mtype == "RX.ACTIVITY":
                # texte brut d'une trame : « OH3FZQ: @APRSIS MSG TO: … » -> station entendue
                m = _ACTIVITY_FROM_RE.match(str(value or "").upper())
                if m and is_callsign(m.group(1)):
                    return self._update(m.group(1), "", p.get("SNR"), p.get("UTC"), p.get("FREQ"), "Spot"), False
                return set(), False
            if mtype == "TX.FRAME":
                # sans journal ALL.TXT : je réponds sans doute à la station sélectionnée
                if self.selected and not self.tx_log_active:
                    other = self._set_contact(self.selected)
                    self.contact_dir = "tx"
                    return {self.selected}, other
                return set(), False
            if mtype == "RX.CALL_ACTIVITY":
                # historique de JS8Call : il ne crée aucune station (au démarrage, la carte part vide) ;
                # il complète seulement le locator des stations entendues depuis l'ouverture de JS8 Carte
                changed = set()
                for call, d in p.items():
                    if call.startswith("_") or not isinstance(d, dict):
                        continue
                    g = str(d.get("GRID") or "").strip().upper()
                    self.book.put(call, g)                         # le carnet apprend aussi de JS8Call
                    st = self.stations.get(str(call).strip().upper())
                    if st is not None and valid_grid(g) and len(g) > len(st.grid):
                        st.grid = g
                        changed.add(st.call)
                return changed, False
            if mtype == "RX.CALL_SELECTED":
                sel = str(value or "").strip().upper()
                if sel != self.selected:
                    self.selected = sel
                    if sel:
                        self._station(sel)
                    return {sel} if sel else set(), True
                return set(), False
            if mtype == "RIG.FREQ":
                self.dial = int(p.get("DIAL") or 0)
                return set(), True
            if mtype == "STATION.CALLSIGN":
                self.my_call = str(value or "").strip().upper()
                return set(), True
            if mtype == "STATION.GRID":
                if valid_grid(value):
                    self.my_grid = str(value).strip().upper()
                return set(), True
            if mtype == "LOG.QSO":
                call = str(p.get("CALL") or "").strip().upper()
                if not call:
                    return set(), False
                q = {"call": call, "grid": str(p.get("GRID") or "").upper(), "freq": p.get("FREQ") or 0,
                     "band": band_for_hz(p.get("FREQ")), "sent": p.get("RPT.SENT", ""),
                     "recv": p.get("RPT.RECV", ""), "name": p.get("NAME", ""),
                     "utc": (p.get("UTC.OFF") or p.get("UTC.ON") or time.time() * 1000) / 1000.0}
                self.qsos.append(q)
                changed = self._update(call, q["grid"], None, q["utc"] * 1000, q["freq"], "QSO")
                st = self.stations.get(call)
                if st:
                    st.worked = True
                if base_call(call) == base_call(self.contact):          # QSO loggé : fin du contact
                    self.contact = ""
                return changed, True
        return set(), False

    def handle_tx_text(self, text):
        """Ligne « Transmitting … » du journal ALL.TXT : à qui j'émets. Renvoie (modifiés, autre)."""
        with self.lock:
            dest, is_qso = tx_destination(text, self.my_call)
            if not dest or not is_qso:
                return set(), False                 # appel général ou réponse automatique à un HB
            other = self._set_contact(dest)
            self.contact_dir = "tx"
            return {dest}, other

    def visible(self, minutes, band=""):
        """Stations à afficher : entendues depuis `minutes` (QSO et sélection toujours gardés)."""
        limit = time.time() - minutes * 60
        with self.lock:
            out = []
            for st in self.stations.values():
                keep = st.call in (self.selected, self.current_contact())
                if band and st.band and st.band != band and not keep:
                    continue
                if st.last >= limit or st.worked or keep:
                    out.append(st)
            return sorted(out, key=lambda s: s.last, reverse=True)


def describe(station, my_grid):
    """Distance (km) et azimut (°) depuis ma station, ou (None, None)."""
    me = grid_to_latlon(my_grid)
    there = station.latlon() if station else None
    if not me or not there:
        return None, None
    return distance_km(me, there), azimuth_deg(me, there)


# --------------------------------------------------------------------------- conditions solaires

SOLAR_URL = "https://www.hamqsl.com/solarxml.php"


def parse_solar(xml_text):
    root = ET.fromstring(xml_text)
    sd = root.find("solardata")
    if sd is None:
        return {}

    def g(tag):
        e = sd.find(tag)
        return (e.text or "").strip() if e is not None and e.text else ""
    return {"sfi": g("solarflux"), "a": g("aindex"), "k": g("kindex"), "spots": g("sunspots"),
            "xray": g("xray"), "muf": g("muf"), "updated": g("updated")}


def fetch_solar(timeout=10):
    req = urllib.request.Request(SOLAR_URL, headers={"User-Agent": "%s/%s" % (APP_NAME, APP_VERSION)})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_solar(r.read().decode("utf-8", "replace"))


# --------------------------------------------------------------------------- client JS8Call

class JS8Client(threading.Thread):
    """Connexion à l'API TCP de JS8Call, reconnexion toutes les 5 s, questions périodiques."""

    POLL_SELECTED = 2.0
    POLL_ACTIVITY = 20.0

    def __init__(self, host, port, on_message, on_status):
        super().__init__(daemon=True)
        self.host, self.port = host, int(port)
        self.on_message, self.on_status = on_message, on_status
        self._stop_evt = threading.Event()
        self._sock = None
        self._send_lock = threading.Lock()
        # JS8Call ne renvoie une réponse qu'au client qui a posé la question portant ce _ID :
        # deux questions avec le même _ID = une seule réponse. Chaque question a donc son propre numéro.
        self._next_id = int(time.time() * 1000)

    def new_id(self):
        with self._send_lock:
            self._next_id += 1
            return self._next_id

    def stop(self):
        self._stop_evt.set()
        s = self._sock
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def send(self, mtype, value=""):
        s = self._sock
        if not s:
            return
        line = json.dumps({"type": mtype, "value": value, "params": {"_ID": self.new_id()}}) + "\n"
        with self._send_lock:
            try:
                s.sendall(line.encode())
            except OSError:
                pass

    def _poller(self, sock):
        next_act = 0.0
        while not self._stop_evt.is_set() and self._sock is sock:
            now = time.time()
            self.send("RX.GET_CALL_SELECTED")
            if now >= next_act:
                # liste des stations + rappel de mon indicatif, locator et fréquence (s'ils ont changé)
                for q in ("RX.GET_CALL_ACTIVITY", "STATION.GET_CALLSIGN", "STATION.GET_GRID", "RIG.GET_FREQ"):
                    self.send(q)
                next_act = now + self.POLL_ACTIVITY
            self._stop_evt.wait(self.POLL_SELECTED)

    def run(self):
        while not self._stop_evt.is_set():
            try:
                sock = socket.create_connection((self.host, self.port), timeout=3)
                sock.settimeout(None)
                self._sock = sock
                self.on_status(True, "")
                for q in ("STATION.GET_CALLSIGN", "STATION.GET_GRID", "RIG.GET_FREQ"):
                    self.send(q)
                threading.Thread(target=self._poller, args=(sock,), daemon=True).start()
                buf = b""
                while not self._stop_evt.is_set():
                    data = sock.recv(65536)
                    if not data:
                        break
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if line:
                            try:
                                self.on_message(json.loads(line.decode("utf-8", "replace")))
                            except ValueError:
                                pass
            except OSError as e:
                if not self._stop_evt.is_set():
                    self.on_status(False, str(e))
            finally:
                s, self._sock = self._sock, None
                if s:
                    try:
                        s.close()
                    except OSError:
                        pass
            if not self._stop_evt.is_set():
                self.on_status(False, "")
                self._stop_evt.wait(5)


# --------------------------------------------------------------------------- journal ALL.TXT de JS8Call

def default_all_txt():
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "JS8Call", "ALL.TXT")


_TX_LINE_RE = re.compile(r"\bTransmitting\b.*?\bJS8:\s*(.*\S)")


def tx_text_from_line(line):
    """« 2026-10-18 06:09:15  Transmitting 7.078 MHz  JS8:  F4MAJ: 9A3SWO HEARTBEAT SNR -05 » -> texte émis."""
    m = _TX_LINE_RE.search(str(line or ""))
    return m.group(1) if m else ""


class TxLogWatcher(threading.Thread):
    """Lit en continu les NOUVELLES lignes du journal ALL.TXT de JS8Call (lecture seule) pour savoir
    à qui j'émets : l'API de JS8Call signale l'émission (TX.FRAME) mais pas son destinataire."""

    def __init__(self, path, on_text, on_found=None):
        super().__init__(daemon=True)
        self.path, self.on_text, self.on_found = path, on_text, on_found
        self._stop_evt = threading.Event()

    def stop(self):
        self._stop_evt.set()

    def run(self):
        f, pos = None, 0
        while not self._stop_evt.is_set():
            try:
                if f is None:
                    f = open(self.path, "r", encoding="utf-8", errors="replace")
                    f.seek(0, os.SEEK_END)                 # seulement ce qui s'écrit après l'ouverture
                    pos = f.tell()
                    if self.on_found:
                        self.on_found(True)
                if os.path.getsize(self.path) < pos:       # fichier vidé ou recréé par JS8Call
                    f.close()
                    f = None
                    continue
                line = f.readline()
                if not line or not line.endswith("\n"):
                    f.seek(pos)
                    self._stop_evt.wait(1.0)
                    continue
                pos = f.tell()
                text = tx_text_from_line(line)
                if text:
                    self.on_text(text)
            except OSError:
                if f is not None:
                    try:
                        f.close()
                    except OSError:
                        pass
                    f = None
                    if self.on_found:
                        self.on_found(False)
                self._stop_evt.wait(5.0)


# --------------------------------------------------------------------------- QRZ.com (abonnement XML)

def _dpapi(data, encrypt):
    """Chiffre / déchiffre pour l'utilisateur Windows courant (DPAPI). None si impossible."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(text):
    """Mot de passe -> texte chiffré (base64) lisible seulement par cette session Windows."""
    import base64
    if not text:
        return ""
    try:
        data = _dpapi(text.encode("utf-8"), True)
    except (OSError, AttributeError):
        return ""
    return base64.b64encode(data).decode("ascii") if data else ""


def unprotect(b64):
    import base64
    if not b64:
        return ""
    try:
        data = _dpapi(base64.b64decode(b64), False)
    except (OSError, AttributeError, ValueError):
        return ""
    return data.decode("utf-8") if data else ""


QRZ_URL = "https://xmldata.qrz.com/xml/current/"
QRZ_TTL = 30 * 86400          # fiche trouvée gardée 30 jours
QRZ_TTL_MISSING = 7 * 86400   # indicatif absent de QRZ : redemandé au bout de 7 jours


class QRZError(Exception):
    pass


def parse_qrz(xml_text):
    """Réponse XML de QRZ.com -> (clé de session, fiche dict ou None, message d'erreur)."""
    root = ET.fromstring(xml_text)

    def find(parent, tag):
        if parent is None:
            return None
        for e in parent.iter():
            if e.tag.split("}")[-1] == tag:
                return e
        return None

    def text(parent, tag):
        e = find(parent, tag)
        return (e.text or "").strip() if e is not None and e.text else ""

    session = find(root, "Session")
    key, error = text(session, "Key"), text(session, "Error")
    cs = find(root, "Callsign")
    rec = None
    if cs is not None and text(cs, "call"):
        first = (text(cs, "nickname") or text(cs, "fname")).split(" ")[0].title()
        qsl = [lbl for tag, lbl in (("lotw", "LoTW"), ("eqsl", "eQSL"), ("mqsl", "direct")) if text(cs, tag) == "1"]
        if text(cs, "qslmgr"):
            qsl.append("via " + text(cs, "qslmgr"))
        rec = {"call": text(cs, "call").upper(), "first": first, "last": text(cs, "name").title(),
               "city": text(cs, "addr2").title(), "state": text(cs, "state"),
               "country": text(cs, "country") or text(cs, "land"), "grid": text(cs, "grid").upper(),
               "image": text(cs, "image"), "qsl": " · ".join(qsl)}
    return key, rec, error


class QRZClient:
    """Fiches QRZ.com avec cache disque (fiches et photos). Ne sert que pour la station choisie / en QSO."""

    def __init__(self, user, password, folder=None, opener=None, fetch=None):
        self.user, self.password = user, password
        self.key = ""
        self.folder = folder or os.path.join(data_dir(), "qrz")
        os.makedirs(os.path.join(self.folder, "photos"), exist_ok=True)
        self.cache_path = os.path.join(self.folder, "fiches.json")
        self.opener = opener or self._http
        self.fetch = fetch or self._download
        self.cache = {}
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                self.cache = json.load(f)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _http(params):
        url = QRZ_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "%s/%s" % (APP_NAME, APP_VERSION)})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode("utf-8", "replace")

    @staticmethod
    def _download(url):
        req = urllib.request.Request(url, headers={"User-Agent": "%s/%s" % (APP_NAME, APP_VERSION)})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read()

    def login(self):
        key, _, error = parse_qrz(self.opener({"username": self.user, "password": self.password,
                                               "agent": "JS8Carte-" + APP_VERSION}))
        if not key:
            raise QRZError(error or "connexion refusée")
        self.key = key
        return key

    def cached(self, call):
        c = self.cache.get(base_call(call))
        if not c:
            return None
        ttl = QRZ_TTL if c.get("found") else QRZ_TTL_MISSING
        return c if time.time() - c.get("ts", 0) < ttl else None

    def photo_path(self, call):
        return os.path.join(self.folder, "photos", base_call(call).replace("/", "_") + ".img")

    def lookup(self, call):
        """Fiche (dict, avec « photo » = chemin local ou "") ou None si inconnu de QRZ.
        Lève QRZError si QRZ refuse (identifiants, abonnement…)."""
        bc = base_call(call)
        c = self.cached(bc)
        if c:
            return c if c.get("found") else None
        if not self.key:
            self.login()
        _, rec, error = parse_qrz(self.opener({"s": self.key, "callsign": bc}))
        low = (error or "").lower()
        if rec is None and ("session" in low or "invalid" in low or "timeout" in low):
            self.login()                                       # clé de session expirée
            _, rec, error = parse_qrz(self.opener({"s": self.key, "callsign": bc}))
            low = (error or "").lower()
        if rec is None and error and "not found" not in low:
            raise QRZError(error)
        entry = dict(rec or {}, found=rec is not None, ts=time.time(), photo="")
        if rec and rec.get("image"):
            try:
                data = self.fetch(rec["image"])
                with open(self.photo_path(bc), "wb") as f:
                    f.write(data)
                entry["photo"] = self.photo_path(bc)
            except (OSError, ValueError):
                pass
        self.cache[bc] = entry
        self._save()
        return entry if rec else None

    def _save(self):
        try:
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False)
            os.replace(tmp, self.cache_path)
        except OSError:
            pass
