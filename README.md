# TTC Elbe Dresden – 3 Siege

Zeigt, wer vom TTC Elbe Dresden in einem Punktspiel mindestens drei Spiele gewonnen hat:
wochenweise und als Saisonwertung.

- **Daten:** TischtennisLive (STTV, Bezirk Dresden, Stadt Dresden) und myTischtennis/click-TT
  (Regional-/Oberligen). Gezählt werden nur ausgespielte Spiele aus Punktspielen.
- **Aktualisierung:** GitHub Actions (`.github/workflows/update.yml`) baut die Seite jeden Montag
  früh neu und veröffentlicht sie auf GitHub Pages. Von Hand: Button „Jetzt aktualisieren“ auf der
  Seite → auf GitHub „Run workflow“ klicken → nach 1–2 Minuten die Seite neu laden.
- **Lokal:** `python update.py` erzeugt `index.html` (`--neu` sucht die Mannschaften neu).

`cache.json` speichert bestätigte Spielberichte, `teams.json` die gefundenen Mannschaften.
Beide werden vom Workflow gepflegt.
