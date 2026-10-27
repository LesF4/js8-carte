"""JS8 Carte : interface HTML (Leaflet) dans une fenêtre Windows (WebView2, via pywebview).

Toute la logique vient de core.py. Python calcule l'état complet de l'écran et l'envoie à la page chaque
seconde (window.update) ; la page appelle l'objet Api pour les clics, les filtres et les réglages.
"""

import base64
import io
import json
import os
import queue
import shutil
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone

import webview

from . import core

KIND_LABEL = {"Spot": "Entendue", "Message": "Entendue", "HB": "Balise HB", "CQ": "Appel CQ",
              "QSO": "Déjà contacté", "Ack": "T'entend"}
DURATIONS = [15, 30, 60, 120, 240]
JOURNAL_FILE = "journal.json"


def web_dir():
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "js8carte", "web")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def clear_cache_on_update(storage):
    """WebView2 garde l'ancienne page en cache : on le vide quand la version change (réglages conservés)."""
    mark = os.path.join(storage, "version.txt")
    try:
        with open(mark, encoding="utf-8") as f:
            if f.read().strip() == core.APP_VERSION:
                return
    except OSError:
        pass
    for sub in ("Cache", "Code Cache"):
        shutil.rmtree(os.path.join(storage, "EBWebView", "Default", sub), ignore_errors=True)
    try:
        os.makedirs(storage, exist_ok=True)
        with open(mark, "w", encoding="utf-8") as f:
            f.write(core.APP_VERSION)
    except OSError:
        pass


def hhmm(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M") if ts else ""


def ago(ts):
    if not ts:
        return ""
    s = max(0, int(time.time() - ts))
    if s < 60:
        return "à l'instant"
    if s < 3600:
        return "il y a %d min" % (s // 60)
    return "il y a %d h %02d" % (s // 3600, (s % 3600) // 60)


def heard_text(st, long=False):
    """Report qu'une station me donne en réponse à mon HB."""
    if not st or not st.heard_ts:
        return ""
    if st.heard_snr is None:
        return ("oui à " + hhmm(st.heard_ts)) if long else ""
    return ("%+d dB à %s" % (st.heard_snr, hhmm(st.heard_ts))) if long else "%+d" % st.heard_snr


class Api:
    """Méthodes appelées par la page : window.pywebview.api.<nom>(...)."""

    def __init__(self, app):
        self._app = app

    def select(self, call):
        self._app.focus_call = str(call or "").upper()
        return True

    def set_filters(self, minutes, band):
        self._app.cfg["window_minutes"] = int(minutes)
        self._app.cfg["band"] = "" if band in ("", "all") else str(band)
        self._app.save_config()
        return True

    def photo(self, call):
        return self._app.photo_data(call)

    def get_settings(self):
        return {"qrz_user": self._app.cfg.get("qrz_user", ""),
                "has_password": bool(self._app.cfg.get("qrz_password"))}

    def test_qrz(self, user, password):
        pw = password or core.unprotect(self._app.cfg.get("qrz_password", ""))
        try:
            core.QRZClient(str(user).strip().upper(), pw).login()
            return {"ok": True, "message": "Connexion réussie."}
        except (core.QRZError, OSError) as e:
            return {"ok": False, "message": "Refusé : %s" % e}

    def save_settings(self, user, password):
        a = self._app
        a.cfg["qrz_user"] = str(user).strip().upper()
        if password:
            a.cfg["qrz_password"] = core.protect(password)
        elif not a.cfg["qrz_user"]:
            a.cfg["qrz_password"] = ""
        a.save_config()
        a.qrz_client, a.qrz_error = None, ""
        a.qrz_asked.clear()
        return True

    def open_download(self):
        webbrowser.open(core.RELEASES_PAGE)              # adresse fixe : la page ne choisit pas où aller
        return True

    def dismiss_update(self):
        self._app.update_version = ""
        return True


class App:
    def __init__(self):
        self.cfg = core.load_config()
        self.book = core.GridBook(os.path.join(core.data_dir(), "locators.json"))
        self.tracker = core.Tracker(self.book)
        self.events = queue.Queue()
        self.connected, self.conn_error = False, ""
        self.focus_call = ""
        self._last_contact = ""       # le contact ne passe au premier plan que lorsqu'il commence
        self.solar = {}
        self.update_version = ""      # nouvelle version publiée sur GitHub, sinon ""
        self.journal = self._load_journal()
        self.qrz_info, self.qrz_photos, self.qrz_asked, self.qrz_error = {}, {}, set(), ""
        self.qrz_queue = queue.Queue()
        self.qrz_client = None
        self.window = None
        self.client = core.JS8Client(self.cfg["js8_host"], self.cfg["js8_port"],
                                     lambda m: self.events.put(("msg", m)),
                                     lambda ok, err: self.events.put(("status", (ok, err))))
        self.txlog = core.TxLogWatcher(self.cfg.get("js8_all_txt") or core.default_all_txt(),
                                       lambda t: self.events.put(("tx", t)),
                                       lambda ok: self.events.put(("txlog", ok)))

    # ------------------------------------------------------------------ fichiers
    def save_config(self):
        try:
            core.save_config({k: v for k, v in self.cfg.items() if not k.startswith("_")})
        except OSError:
            pass

    def _journal_path(self):
        return os.path.join(core.data_dir(), JOURNAL_FILE)

    def _load_journal(self):
        try:
            with open(self._journal_path(), encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _journal_add(self, q):
        self.journal = (self.journal + [q])[-1000:]
        try:
            tmp = self._journal_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.journal, f, ensure_ascii=False)
            os.replace(tmp, self._journal_path())
        except OSError:
            pass

    # ------------------------------------------------------------------ événements
    def _drain(self):
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "msg":
                _, other = self.tracker.handle(data)
                mtype = data.get("type")
                if mtype == "LOG.QSO" and self.tracker.qsos:
                    self._journal_add(self.tracker.qsos[-1])
                if mtype == "RX.CALL_SELECTED" and other and self.tracker.selected:
                    self.focus_call = self.tracker.selected
            elif kind == "tx":
                self.tracker.handle_tx_text(data)
            elif kind == "txlog":
                self.tracker.tx_log_active = bool(data)
            elif kind == "status":
                self.connected, self.conn_error = data
            elif kind == "solar":
                self.solar = data
            elif kind == "update":
                self.update_version = data
            elif kind == "qrz":
                call, fiche = data
                self.qrz_info[call] = fiche
                self.qrz_error = ""
                if fiche and core.valid_grid(fiche.get("grid")):
                    self.book.put(call, fiche["grid"])
                    for st in self.tracker.stations.values():
                        if core.base_call(st.call) == call and not st.book_grid:
                            st.book_grid = fiche["grid"].upper()
            elif kind == "qrzerr":
                self.qrz_error = data
        contact = self.tracker.current_contact()
        if contact and contact != self._last_contact:
            self.focus_call = contact                          # nouveau contact : il passe au premier plan
        self._last_contact = contact

    # ------------------------------------------------------------------ état envoyé à la page
    def my_grid(self):
        return (self.cfg.get("my_grid") or self.tracker.my_grid or "").upper()

    def state(self):
        t = self.tracker
        grid = self.my_grid()
        me = core.grid_to_latlon(grid)
        contact = t.current_contact()
        rows, target = [], None
        for st in t.visible(int(self.cfg.get("window_minutes", 30)), self.cfg.get("band", ""))[:300]:
            pos = st.latlon()
            if pos is None:
                self._ask_qrz(st.call)                         # sans locator : QRZ.com donne sa position
            d, _ = core.describe(st, grid)
            base_kind = "QSO" if st.worked else st.kind
            if st.call == contact:
                kind, label = "contact", "En QSO"
            elif st.call == self.focus_call:
                kind, label = "sel", KIND_LABEL.get(base_kind, base_kind)
            else:
                kind, label = base_kind, KIND_LABEL.get(base_kind, base_kind)
            if base_kind == "Ack" and st.heard_ts:
                label += ((" (%+d)" % st.heard_snr) if st.heard_snr is not None else "") + " à " + hhmm(st.heard_ts)
            rows.append({"call": st.call, "lat": pos[0] if pos else None, "lon": pos[1] if pos else None,
                         "kind": kind, "base": base_kind, "label": label,
                         "extra": heard_text(st) if base_kind == "Ack" else "",
                         "time": hhmm(st.last), "snr": "" if st.snr is None else "%+d" % st.snr,
                         "band": st.band, "grid": st.best_grid(), "dist": "" if d is None else "%d km" % d})
            if pos and (st.call == contact or (not contact and st.call == self.focus_call)):
                target = {"call": st.call, "pos": list(pos), "contact": st.call == contact}
        return {
            "header": {"my_call": t.my_call, "my_grid": grid,
                       "freq": ("%.3f MHz · %s" % (t.dial / 1e6, core.band_for_hz(t.dial))) if t.dial else ""},
            "status": {"connected": self.connected,
                       "text": "Connecté à JS8Call" if self.connected else
                               "JS8Call non joignable (%s:%s) — nouvel essai toutes les 5 s"
                               % (self.cfg["js8_host"], self.cfg["js8_port"])},
            "filters": {"minutes": int(self.cfg.get("window_minutes", 30)), "band": self.cfg.get("band", ""),
                        "durations": DURATIONS, "bands": [b[2] for b in core.BANDS]},
            "me": list(me) if me else None, "stations": rows, "target": target, "dir": t.contact_dir,
            "focus": self._focus_state(contact, grid), "journal": self._journal_rows(grid),
            "solar": self.solar, "update": self.update_version,
        }

    def _focus_state(self, contact, grid):
        call = self.focus_call
        if not call:
            return None
        st = self.tracker.stations.get(call)
        d, az = core.describe(st, grid) if st else (None, None)
        if call == contact:
            state = "en communication avec toi"
        elif call == self.tracker.selected:
            state = "sélection JS8Call"
        else:
            state = "choisi sur la carte ou dans la liste"
        if st and st.kind in ("CQ", "HB") and not st.worked and call != contact:
            state += " · " + ("lance appel (CQ)" if st.kind == "CQ" else "balise HB")
        if st and st.grid:
            g = st.grid
        elif st and st.book_grid:
            g = st.book_grid + " (carnet)"
        else:
            g = "inconnu"
        n = sum(1 for q in self.journal if core.base_call(q.get("call")) == core.base_call(call))
        bc = self._ask_qrz(call)
        fiche = self.qrz_info.get(bc)
        if fiche:
            qrz = {"name": " ".join(x for x in (fiche.get("first"), fiche.get("last")) if x),
                   "place": ", ".join(x for x in (fiche.get("city"), fiche.get("country")) if x),
                   "qsl": fiche.get("qsl") or "—", "photo": bc if fiche.get("photo") else ""}
            note = ""
        else:
            qrz = None
            if self.qrz_error:
                note = "QRZ.com : " + self.qrz_error
            elif not self.cfg.get("qrz_user"):
                note = "Fiche QRZ.com : ajoute tes identifiants dans Réglages."
            elif bc in self.qrz_info:
                note = "Pas de fiche sur QRZ.com."
            else:
                note = "Recherche sur QRZ.com…"
        return {"call": call, "contact": call == contact, "state": state, "qrz": qrz, "note": note,
                "msg": (st.text[:200] if st and st.text else ""),
                "rows": {"grid": g, "dist": "" if d is None else "%d km" % d, "az": "" if az is None else "%d°" % az,
                         "snr": "" if not st or st.snr is None else "%+d dB" % st.snr, "band": st.band if st else "",
                         "heard": heard_text(st, long=True) if st else "", "last": ago(st.last) if st else "",
                         "worked": ("oui (%d)" % n) if n else "non", "qsl": qrz["qsl"] if qrz else ""}}

    def _journal_rows(self, grid):
        me = core.grid_to_latlon(grid)
        out = []
        for q in reversed(self.journal[-200:]):
            pos = core.grid_to_latlon(q.get("grid"))
            out.append({"when": datetime.fromtimestamp(q.get("utc") or 0, timezone.utc).strftime("%d/%m %H:%M"),
                        "call": q.get("call", ""), "band": q.get("band", ""), "sent": q.get("sent", ""),
                        "recv": q.get("recv", ""), "grid": q.get("grid", ""),
                        "dist": "%d km" % core.distance_km(me, pos) if me and pos else ""})
        return out

    # ------------------------------------------------------------------ QRZ.com
    def photo_data(self, call):
        """Photo QRZ en data-URI (réduite), mise en cache."""
        bc = core.base_call(call)
        if bc in self.qrz_photos:
            return self.qrz_photos[bc]
        fiche = self.qrz_info.get(bc) or {}
        uri = ""
        path = fiche.get("photo")
        if path and os.path.exists(path):
            try:
                from PIL import Image
                im = Image.open(path).convert("RGB")
                im.thumbnail((280, 210))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=85)
                uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
            except (OSError, ValueError, ImportError):
                uri = ""
        self.qrz_photos[bc] = uri
        return uri

    def _ask_qrz(self, call):
        """Met l'indicatif dans la file QRZ (une fois par session) ; renvoie l'indicatif de base."""
        bc = core.base_call(call)
        if bc not in self.qrz_asked and self.cfg.get("qrz_user") and self.cfg.get("qrz_password"):
            self.qrz_asked.add(bc)
            self.qrz_queue.put(bc)
        return bc

    def _qrz_loop(self):
        while True:
            bc = self.qrz_queue.get()
            user = self.cfg.get("qrz_user", "")
            password = core.unprotect(self.cfg.get("qrz_password", ""))
            if not user or not password:
                continue
            try:
                if self.qrz_client is None or self.qrz_client.user != user:
                    self.qrz_client = core.QRZClient(user, password)
                self.events.put(("qrz", (bc, self.qrz_client.lookup(bc))))
            except core.QRZError as e:
                self.qrz_asked.discard(bc)
                self.events.put(("qrzerr", str(e)))
            except OSError:
                self.qrz_asked.discard(bc)
                self.events.put(("qrzerr", "pas de réponse (réseau ?)"))

    def _solar_loop(self):
        while True:
            try:
                self.events.put(("solar", core.fetch_solar()))
                time.sleep(900)
            except Exception:
                time.sleep(120)

    def _update_loop(self):
        """Au démarrage puis une fois par jour : une version plus récente est-elle publiée ?"""
        time.sleep(5)
        while True:
            try:
                latest = core.fetch_latest_version()
                if core.is_newer(latest):
                    self.events.put(("update", latest))
                time.sleep(86400)
            except Exception:
                time.sleep(3600)                           # pas d'Internet ou GitHub injoignable : plus tard

    # ------------------------------------------------------------------ boucle et fenêtre
    def _loop(self):
        time.sleep(0.8)
        n = 0
        while True:
            self._drain()
            try:
                self.window.evaluate_js("window.update && window.update(%s)" % json.dumps(self.state()))
            except Exception:
                pass
            n += 1
            if n % 15 == 0:
                self.book.save()
            time.sleep(1.0)

    def _on_closing(self):
        try:
            w = self.window
            self.cfg["geometry"] = "%dx%d+%d+%d" % (w.width, w.height, w.x, w.y)
        except Exception:
            pass
        self.save_config()
        self.book.save()

    def _geometry(self):
        try:
            size, x, y = self.cfg.get("geometry", "").split("+")
            w, h = size.split("x")
            return int(w), int(h), int(x), int(y)
        except ValueError:
            return 1500, 900, None, None

    def run(self, hidden=False):
        self.client.start()
        self.txlog.start()
        threading.Thread(target=self._qrz_loop, daemon=True).start()
        threading.Thread(target=self._solar_loop, daemon=True).start()
        threading.Thread(target=self._update_loop, daemon=True).start()
        w, h, x, y = self._geometry()
        self.window = webview.create_window("%s %s" % (core.APP_NAME, core.APP_VERSION),
                                            url=os.path.join(web_dir(), "index.html"), js_api=Api(self),
                                            width=max(w, 1000), height=max(h, 640), x=x, y=y, min_size=(1000, 640),
                                            background_color="#0a0f1a", hidden=hidden)
        self.window.events.closing += self._on_closing
        threading.Thread(target=self._loop, daemon=True).start()
        storage = os.path.join(core.data_dir(), "webview")
        clear_cache_on_update(storage)
        webview.start(http_server=True, private_mode=False, storage_path=storage)
        self.book.save()
        self.client.stop()
        self.txlog.stop()


def main():
    App().run()
