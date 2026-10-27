import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from js8carte import core  # noqa: E402


class GeoTests(unittest.TestCase):
    def test_grid_to_latlon(self):
        lat, lon = core.grid_to_latlon("JN37QS")
        self.assertAlmostEqual(lat, 47.77, delta=0.05)
        self.assertAlmostEqual(lon, 7.38, delta=0.05)
        lat, lon = core.grid_to_latlon("jn37")
        self.assertAlmostEqual(lat, 47.5)
        self.assertAlmostEqual(lon, 7.0)

    def test_invalid_grid(self):
        for g in ("", None, "ZZ99", "JN3", "RR73", "73"):
            self.assertIsNone(core.grid_to_latlon(g), g)

    def test_distance_azimuth(self):
        me = core.grid_to_latlon("JN37QS")
        rome = core.grid_to_latlon("JN61FV")
        self.assertAlmostEqual(core.distance_km(me, rome), 765, delta=30)
        self.assertAlmostEqual(core.azimuth_deg(me, rome), 140, delta=10)
        usa = core.grid_to_latlon("EM44XT")
        self.assertAlmostEqual(core.azimuth_deg(me, usa), 300, delta=10)

    def test_band(self):
        self.assertEqual(core.band_for_hz(7078000), "40m")
        self.assertEqual(core.band_for_hz(14078500), "20m")
        self.assertEqual(core.band_for_hz("x"), "")

    def test_base_call(self):
        self.assertEqual(core.base_call("F/DL1ABC/P"), "DL1ABC")
        self.assertEqual(core.base_call("SV2BBO/A"), "SV2BBO")


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.t = core.Tracker()
        self.t.handle({"type": "STATION.CALLSIGN", "value": "F4MAJ"})

    def test_spot(self):
        changed, _ = self.t.handle({"type": "RX.SPOT", "value": "", "params": {
            "CALL": "ea4xy", "GRID": "IN80", "SNR": -12, "FREQ": 7079200}})
        self.assertEqual(changed, {"EA4XY"})
        st = self.t.stations["EA4XY"]
        self.assertEqual((st.grid, st.snr, st.band), ("IN80", -12, "40m"))

    def test_finer_grid_kept(self):
        self.t.handle({"type": "RX.SPOT", "params": {"CALL": "G4XYZ", "GRID": "IO91WM"}})
        self.t.handle({"type": "RX.SPOT", "params": {"CALL": "G4XYZ", "GRID": "IO91"}})
        self.assertEqual(self.t.stations["G4XYZ"].grid, "IO91WM")

    def test_own_call_and_groups_ignored(self):
        self.t.handle({"type": "RX.SPOT", "params": {"CALL": "F4MAJ", "GRID": "JN37"}})
        self.t.handle({"type": "RX.DIRECTED", "value": "x", "params": {"FROM": "@ALLCALL"}})
        self.assertEqual(self.t.stations, {})

    def test_directed(self):
        self.t.handle({"type": "RX.DIRECTED", "value": "DL1ABC: F4MAJ SNR -05", "params": {
            "FROM": "DL1ABC", "TO": "F4MAJ", "GRID": "", "SNR": -5, "FREQ": 7078900,
            "UTC": int(time.time() * 1000)}})
        st = self.t.stations["DL1ABC"]
        self.assertEqual(st.kind, "Message")
        self.assertIn("SNR -05", st.text)

    def test_call_activity_history_creates_nothing(self):
        # au démarrage, l'historique de JS8Call ne doit rien afficher
        now = int(time.time() * 1000)
        changed, _ = self.t.handle({"type": "RX.CALL_ACTIVITY", "params": {
            "_ID": 1, "ON4AA": {"SNR": -3, "GRID": "JO20", "UTC": now}, "K1ABC": {"SNR": -20, "GRID": "", "UTC": now}}})
        self.assertEqual(changed, set())
        self.assertEqual(self.t.stations, {})

    def test_call_activity_completes_grid(self):
        self.t.handle({"type": "RX.DIRECTED", "value": "M7KTG: F4MAJ HEARTBEAT SNR +08", "params": {
            "FROM": "M7KTG", "TO": "F4MAJ", "CMD": " HEARTBEAT SNR"}})
        changed, _ = self.t.handle({"type": "RX.CALL_ACTIVITY", "params": {"M7KTG": {"GRID": "IO82"}}})
        self.assertEqual(changed, {"M7KTG"})
        self.assertEqual(self.t.stations["M7KTG"].grid, "IO82")
        self.assertEqual(self.t.stations["M7KTG"].kind, "Ack")

    def test_selected(self):
        changed, other = self.t.handle({"type": "RX.CALL_SELECTED", "value": "IC8BNR"})
        self.assertTrue(other)
        self.assertEqual(self.t.selected, "IC8BNR")
        _, other = self.t.handle({"type": "RX.CALL_SELECTED", "value": "IC8BNR"})
        self.assertFalse(other)

    def test_log_qso(self):
        now = int(time.time() * 1000)
        self.t.handle({"type": "LOG.QSO", "value": "<adif>", "params": {
            "CALL": "WD5EED", "GRID": "EM44XT", "FREQ": 14078500, "RPT.SENT": "-10",
            "RPT.RECV": "-08", "UTC.ON": now - 60000, "UTC.OFF": now}})
        self.assertEqual(len(self.t.qsos), 1)
        st = self.t.stations["WD5EED"]
        self.assertTrue(st.worked)
        self.assertEqual(st.kind, "QSO")
        # un spot plus tard ne fait pas perdre le statut QSO
        self.t.handle({"type": "RX.SPOT", "params": {"CALL": "WD5EED", "GRID": "EM44"}})
        self.assertEqual(self.t.stations["WD5EED"].kind, "QSO")

    def test_visible_window_and_band(self):
        old = int((time.time() - 3600) * 1000)
        self.t.handle({"type": "RX.DIRECTED", "value": "x", "params": {"FROM": "OLD1", "TO": "@ALLCALL",
                                                                       "GRID": "JN18", "UTC": old}})
        self.t.handle({"type": "RX.SPOT", "params": {"CALL": "NEW1", "GRID": "JN18", "FREQ": 14078000}})
        self.t.handle({"type": "RX.SPOT", "params": {"CALL": "NEW2", "GRID": "JN18", "FREQ": 7078000}})
        calls = [s.call for s in self.t.visible(30)]
        self.assertIn("NEW1", calls)
        self.assertNotIn("OLD1", calls)
        self.assertEqual([s.call for s in self.t.visible(30, "40m")], ["NEW2"])

    def test_cq_and_heartbeat(self):
        self.t.handle({"type": "RX.DIRECTED", "value": "IU2ITE: @ALLCALL CQ CQ CQ JN45", "params": {
            "FROM": "IU2ITE", "TO": "@ALLCALL", "CMD": " CQ", "GRID": "JN45", "SNR": 3, "FREQ": 7079000}})
        self.t.handle({"type": "RX.DIRECTED", "value": "SM6TWY: @HB HEARTBEAT JO57", "params": {
            "FROM": "SM6TWY", "TO": "@HB", "CMD": " HEARTBEAT", "GRID": "JO57"}})
        self.assertEqual(self.t.stations["IU2ITE"].kind, "CQ")
        self.assertEqual(self.t.stations["IU2ITE"].grid, "JN45")
        self.assertEqual(self.t.stations["SM6TWY"].kind, "HB")
        # le spot qui suit le CQ ne le fait pas oublier
        self.t.handle({"type": "RX.SPOT", "params": {"CALL": "IU2ITE", "GRID": "JN45"}})
        self.assertEqual(self.t.stations["IU2ITE"].kind, "CQ")

    def test_contact_when_written_to(self):
        changed, other = self.t.handle({"type": "RX.DIRECTED", "value": "IC8BNR: F4MAJ SNR -08", "params": {
            "FROM": "IC8BNR", "TO": "F4MAJ", "CMD": " SNR", "GRID": ""}})
        self.assertTrue(other)
        self.assertEqual(self.t.current_contact(), "IC8BNR")
        self.assertIn("IC8BNR", [s.call for s in self.t.visible(30, "80m")])   # gardé malgré le filtre

    def test_contact_when_i_answer(self):
        self.t.handle({"type": "RX.CALL_SELECTED", "value": "WD5EED"})
        self.assertEqual(self.t.current_contact(), "")
        _, other = self.t.handle({"type": "TX.FRAME", "params": {"TONES": []}})
        self.assertTrue(other)
        self.assertEqual(self.t.current_contact(), "WD5EED")
        self.assertEqual(self.t.contact_dir, "tx")
        self.t.handle({"type": "RX.DIRECTED", "value": "WD5EED: F4MAJ RR", "params": {"FROM": "WD5EED", "TO": "F4MAJ"}})
        self.assertEqual(self.t.contact_dir, "rx")
        self.t.handle({"type": "LOG.QSO", "params": {"CALL": "WD5EED", "FREQ": 14078500}})
        self.assertEqual(self.t.current_contact(), "")

    def test_hb_reply_is_not_a_qso(self):
        # « ACK » (variante de JS8Call) ne porte pas de report : elle m'entend, sans niveau
        for frm, cmd, extra, heard in (("K1ABC", " HEARTBEAT SNR", " -12", -12), ("OZ9JEP", " ACK", "", None)):
            changed, other = self.t.handle({"type": "RX.DIRECTED", "value": "%s: F4MAJ%s%s ♢" % (frm, cmd, extra),
                                            "params": {"FROM": frm, "TO": "F4MAJ", "CMD": cmd, "SNR": -15}})
            self.assertFalse(other)
            st = self.t.stations[frm]
            self.assertEqual(st.kind, "Ack")
            self.assertTrue(st.heard_ts)
            self.assertEqual(st.heard_snr, heard)
            self.assertEqual(st.snr, -15)              # comment moi je la reçois, distinct de son report
        self.assertEqual(self.t.current_contact(), "")

    def test_reply_to_someone_elses_hb_is_not_ack(self):
        # cas réel du 17/10 17:57 : M0IQF répond au HB de 9A3SWO, pas au mien
        self.t.handle({"type": "RX.DIRECTED", "value": "M0IQF: 9A3SWO HEARTBEAT SNR -13 ♢", "params": {
            "FROM": "M0IQF", "TO": "9A3SWO", "CMD": " HEARTBEAT SNR", "SNR": 5}})
        st = self.t.stations["M0IQF"]
        self.assertEqual(st.kind, "HB")
        self.assertEqual(st.heard_ts, 0.0)
        self.assertIsNone(st.heard_snr)
        self.assertEqual(self.t.current_contact(), "")

    def test_request_ids_are_unique(self):
        c = core.JS8Client("127.0.0.1", 1, lambda m: None, lambda ok, e: None)
        ids = [c.new_id() for _ in range(50)]
        self.assertEqual(len(set(ids)), 50)

    def test_others_heartbeat_not_ack(self):
        self.t.handle({"type": "RX.DIRECTED", "value": "SM6TWY: @HB HEARTBEAT JO57", "params": {
            "FROM": "SM6TWY", "TO": "@HB", "CMD": " HEARTBEAT"}})
        self.assertEqual(self.t.stations["SM6TWY"].kind, "HB")

    def test_contact_expires(self):
        self.t.handle({"type": "RX.DIRECTED", "value": "x", "params": {"FROM": "DL1ABC", "TO": "F4MAJ"}})
        self.t.contact_ts -= core.CONTACT_TIMEOUT + 1
        self.assertEqual(self.t.current_contact(), "")

    def test_describe(self):
        self.t.handle({"type": "RX.SPOT", "params": {"CALL": "IC8BNR", "GRID": "JN70"}})
        d, az = core.describe(self.t.stations["IC8BNR"], "JN37QS")
        self.assertGreater(d, 800)
        self.assertTrue(120 < az < 170)
        self.assertEqual(core.describe(self.t.stations["IC8BNR"], ""), (None, None))


class GridBookTests(unittest.TestCase):
    def test_book_places_known_station(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "locators.json")
        book = core.GridBook(path)
        t = core.Tracker(book)
        t.handle({"type": "STATION.CALLSIGN", "value": "F4MAJ"})
        t.handle({"type": "RX.DIRECTED", "value": "M0IQF: @HB HEARTBEAT IO91", "params": {
            "FROM": "M0IQF", "TO": "@HB", "CMD": " HEARTBEAT", "GRID": "IO91"}})
        book.save()
        # le lendemain : nouvelle séance, M0IQF répond sans locator mais il est placé grâce au carnet
        t2 = core.Tracker(core.GridBook(path))
        t2.handle({"type": "STATION.CALLSIGN", "value": "F4MAJ"})
        t2.handle({"type": "RX.DIRECTED", "value": "M0IQF: F4MAJ HEARTBEAT SNR -01", "params": {
            "FROM": "M0IQF", "TO": "F4MAJ", "CMD": " HEARTBEAT SNR"}})
        st = t2.stations["M0IQF"]
        self.assertEqual((st.grid, st.book_grid, st.best_grid()), ("", "IO91", "IO91"))
        self.assertIsNotNone(st.latlon())

    def test_book_keeps_precise_and_follows_moves(self):
        b = core.GridBook()
        b.put("G4XYZ", "IO91WM")
        b.put("G4XYZ", "IO91")
        self.assertEqual(b.get("G4XYZ"), "IO91WM")
        b.put("G4XYZ/P", "IO83")                   # portable ailleurs
        self.assertEqual(b.get("G4XYZ"), "IO83")

    def test_call_activity_feeds_book_without_creating(self):
        t = core.Tracker()
        t.handle({"type": "RX.CALL_ACTIVITY", "params": {"KO4LOI": {"GRID": "FM18"}}})
        self.assertEqual(t.stations, {})
        self.assertEqual(t.book.get("KO4LOI"), "FM18")


class ActivityAndTxTests(unittest.TestCase):
    def setUp(self):
        self.t = core.Tracker()
        self.t.handle({"type": "STATION.CALLSIGN", "value": "F4MAJ"})

    def test_rx_activity_first_frame(self):
        changed, _ = self.t.handle({"type": "RX.ACTIVITY", "value": "OH3FZQ: @APRSIS MSG TO: ",
                                    "params": {"SNR": -22, "FREQ": 7079003}})
        self.assertEqual(changed, {"OH3FZQ"})
        self.assertEqual(self.t.stations["OH3FZQ"].band, "40m")
        for text in ("YO3FVR SUNT I", "-9", "F4MAJ: K1OF SNR -04"):     # suites de message / moi-même
            self.t.handle({"type": "RX.ACTIVITY", "value": text, "params": {}})
        self.assertEqual(set(self.t.stations), {"OH3FZQ"})

    def test_tx_text_from_all_txt_line(self):
        line = "2026-10-18 06:09:15  Transmitting 7.078 MHz  JS8:  F4MAJ: 9A3SWO HEARTBEAT SNR -05 \n"
        self.assertEqual(core.tx_text_from_line(line), "F4MAJ: 9A3SWO HEARTBEAT SNR -05")
        self.assertEqual(core.tx_text_from_line("2026-10-18 06:09:26  7.078 MHz  JS8"), "")

    def test_tx_destination(self):
        cases = {
            "F4MAJ: 9A3SWO HEARTBEAT SNR -05": ("9A3SWO", False),   # réponse automatique au HB : pas un QSO
            "F4MAJ: @HB HEARTBEAT JN37": ("", False),
            "F4MAJ: @ALLCALL CQ CQ CQ JN37": ("", False),
            "F4MAJ: K1OF SNR -04": ("K1OF", True),
            "F4MAJ: K1OF  OP : JEAN - QTH ALSACE": ("K1OF", True),
            "F4MAJ: EA8/DF5AM HW CPY?": ("EA8/DF5AM", True),
            "F4MAJ: K1OF ACK": ("K1OF", False),
            # trames suivantes d'un long message (QSO K2YF du 27/10) : pas de destinataire
            "JS8CALL THAT SHOWS ON A ": ("", False),
            "S DROPPING. ": ("", False),
            ": YAESU FTDX10": ("", False),
        }
        for text, expected in cases.items():
            self.assertEqual(core.tx_destination(text, "F4MAJ"), expected, text)

    def test_auto_hb_reply_with_selection_is_not_a_qso(self):
        # cas réel de ce matin : 9A3SWO sélectionné, mon JS8Call répond tout seul à son HB
        self.t.tx_log_active = True
        self.t.handle({"type": "RX.CALL_SELECTED", "value": "9A3SWO"})
        self.t.handle({"type": "TX.FRAME", "params": {}})
        self.t.handle_tx_text("F4MAJ: 9A3SWO HEARTBEAT SNR -05")
        self.assertEqual(self.t.current_contact(), "")

    def test_real_qso_from_tx_log(self):
        self.t.tx_log_active = True
        changed, other = self.t.handle_tx_text("F4MAJ: K1OF SNR -04")
        self.assertTrue(other)
        self.assertEqual((self.t.current_contact(), self.t.contact_dir), ("K1OF", "tx"))


QRZ_FICHE = """<?xml version="1.0" ?><QRZDatabase version="1.34" xmlns="http://xmldata.qrz.com">
<Callsign><call>IC8BNR</call><fname>GIOVANNI MARIA</fname><name>ROSSI</name><addr2>ISCHIA</addr2>
<state>NA</state><country>Italy</country><grid>JN70bq</grid><image>https://cdn.qrz.com/x/ic8bnr.jpg</image>
<lotw>1</lotw><eqsl>1</eqsl><mqsl>0</mqsl></Callsign>
<Session><Key>cle123</Key><Count>12</Count></Session></QRZDatabase>"""
QRZ_LOGIN = """<QRZDatabase xmlns="http://xmldata.qrz.com"><Session><Key>cle123</Key></Session></QRZDatabase>"""
QRZ_ABSENT = """<QRZDatabase xmlns="http://xmldata.qrz.com"><Session><Key>cle123</Key>
<Error>Not found: XX9XX</Error></Session></QRZDatabase>"""
QRZ_REFUS = """<QRZDatabase xmlns="http://xmldata.qrz.com"><Session>
<Error>Username/password incorrect</Error></Session></QRZDatabase>"""


class QRZTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.folder = tempfile.mkdtemp()
        self.calls = []

    def client(self, answers):
        def opener(params):
            self.calls.append(dict(params))
            return answers.pop(0)
        return core.QRZClient("F4MAJ", "secret", self.folder, opener=opener, fetch=lambda url: b"JPEG")

    def test_parse(self):
        key, rec, err = core.parse_qrz(QRZ_FICHE)
        self.assertEqual(key, "cle123")
        self.assertEqual((rec["first"], rec["last"], rec["city"], rec["country"], rec["grid"]),
                         ("Giovanni", "Rossi", "Ischia", "Italy", "JN70BQ"))
        self.assertEqual(rec["qsl"], "LoTW · eQSL")

    def test_lookup_login_cache_and_photo(self):
        q = self.client([QRZ_LOGIN, QRZ_FICHE])
        fiche = q.lookup("IC8BNR/P")
        self.assertEqual(fiche["first"], "Giovanni")
        self.assertTrue(os.path.exists(fiche["photo"]))
        self.assertEqual(self.calls[1]["callsign"], "IC8BNR")      # recherche sur l'indicatif de base
        # deuxième fois : cache disque, aucune nouvelle requête, même après redémarrage
        n = len(self.calls)
        q2 = self.client([])
        self.assertEqual(q2.lookup("IC8BNR")["city"], "Ischia")
        self.assertEqual(len(self.calls), n)

    def test_not_found_is_none(self):
        q = self.client([QRZ_LOGIN, QRZ_ABSENT])
        self.assertIsNone(q.lookup("XX9XX"))

    def test_bad_password_raises(self):
        q = self.client([QRZ_REFUS])
        with self.assertRaises(core.QRZError):
            q.lookup("IC8BNR")

    def test_password_is_encrypted(self):
        enc = core.protect("mon-mot-de-passe")
        self.assertTrue(enc)
        self.assertNotIn("mon-mot-de-passe", enc)
        self.assertEqual(core.unprotect(enc), "mon-mot-de-passe")


class SolarTests(unittest.TestCase):
    def test_parse(self):
        xml = ("<solar><solardata><solarflux>131</solarflux><aindex>12</aindex><kindex>2</kindex>"
               "<sunspots>81</sunspots><xray>B6.5</xray><muf>NoRpt</muf>"
               "<updated>17 Oct 2026 0335 GMT</updated></solardata></solar>")
        s = core.parse_solar(xml)
        self.assertEqual((s["sfi"], s["a"], s["k"], s["xray"]), ("131", "12", "2", "B6.5"))


if __name__ == "__main__":
    unittest.main()
