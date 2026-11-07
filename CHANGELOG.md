# Journal des versions

## 0.8.5 — 30 octobre 2026

- Une seule copie de JS8 Carte à la fois : un nouveau clic sur le raccourci remet la fenêtre déjà
  ouverte au premier plan (et sur l'écran principal si elle était hors écran), au lieu de lancer
  une copie de plus. Les copies en trop prenaient les connexions de JS8Call et affichaient
  « JS8Call non joignable ».
- La fenêtre ne s'ouvre plus hors de l'écran : une position mémorisée invalide (fenêtre fermée
  alors qu'elle était réduite, écran débranché) est ignorée et la fenêtre s'ouvre au centre.

## 0.8.4 — 27 octobre 2026 (première version publique)

- Carte fluide Leaflet (fonds Sombre, Sombre FR, Clair), zoom progressif.
- Stations entendues depuis l'ouverture : CQ, balises HB, réponses à ton HB (« T'entend » avec report).
- « En QSO » avec trait animé vers ton correspondant (détecté dans le journal ALL.TXT de JS8Call).
- Ta station représentée par une maison.
- Fiche QRZ.com avec photo (abonnement XML, mot de passe chiffré par Windows) ; les stations sans
  locator sont placées grâce à leur fiche.
- Carnet des locators qui se complète tout seul, journal des QSO, conditions solaires (hamqsl.com).
- Filtres durée et bande.
- Installeur Windows (Inno Setup), sans droits administrateur.
- Alerte au démarrage quand une nouvelle version est publiée (bouton « Télécharger », rien ne s’installe tout seul).

Inspiré du MultiDigi Tracker de Nicolas F4LPS.
