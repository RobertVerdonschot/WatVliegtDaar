# Wat vliegt daar?

Simpele webapp: open 'm op je telefoon als er iets overkomt, en je ziet welk
vliegtuig of welke heli dat is. Maatschappij en vluchtnummer, van waar naar
waar, hoogte, snelheid, type toestel. Geen kaart, geen gedoe.

Draait op: https://wat-vliegt-daar-q57hkofe2q-ew.a.run.app

## Neerzetten

1. Ga naar [Google Cloud Shell](https://shell.cloud.google.com) en draai:

   ```bash
   git clone https://github.com/RobertVerdonschot/WatVliegtDaar.git
   cd WatVliegtDaar
   gcloud config set project meal-stress-analyzer
   ./deploy.sh
   ```

   Draait in hetzelfde Google Cloud-project als de Meal Stress Analyzer, als
   losse Cloud Run-service. Geen secrets nodig.
2. Het script print een URL. Open die op je telefoon in Chrome, geef
   toestemming voor je locatie, en kies menu > *Toevoegen aan startscherm*.

Kosten: binnen de gratis tier van Cloud Run, zolang alleen jij 'm af en toe
gebruikt.

## Hoe het werkt

- Je telefoon stuurt je locatie (GPS, nauwkeurig) naar de server
  (`/api/overhead`), elke 5 seconden zolang de app open staat. Is je locatie
  onnauwkeuriger dan 300 m, dan zegt de app dat.
- De server haalt alle toestellen binnen ~37 km op bij
  [adsb.fi](https://adsb.fi) (reserve: [adsb.lol](https://adsb.lol)).
  Beide zijn gratis, door vrijwilligers gedragen ADS-B-netwerken. Hun data is
  meestal minder dan 1-2 seconden oud (gemeten rond Schiphol).
- Elke positie wordt met koers en snelheid doorgerekend naar "nu" (max 20 s
  vooruit). Bij een test met 10 s oude data scheelde dat mediaan 870 m naar
  40 m afwijking.
- "Boven je" = minstens 35 graden boven de horizon. Een lijnvliegtuig op 10 km
  hoogte telt dan tot ~14 km afstand mee, een heli op 300 m tot ~430 m.
  Maximaal 3 toestellen, het hoogst-aan-de-hemel eerst. Is er niks, dan zie je
  welk toestel het dichtstbij is.
- Een laagvlieger is in een halve minuut over je heen. Daarom rekent de app
  met koers, snelheid en stijgsnelheid terug en vooruit: wat de afgelopen 2
  minuten over je heen kwam of in de komende 30 s komt, staat er ook bij
  ("Kwam 40 s geleden over je heen", of "Vloog 1 min geleden langs" als hij
  niet hoger dan 60 graden kwam). Rechte lijn, dus bij een bocht klopt het
  minder; daarom niet verder terug dan 2 minuten. Hoge toestellen die nu nog
  in beeld zijn krijgen geen tijd erbij.
- Route, maatschappij en type komen van [adsbdb.com](https://www.adsbdb.com).
- Is een toestel laag en stijgend vlak bij een luchthaven (< 3 km hoog, < 40 km
  van het veld), dan is het daar net vertrokken. Laag en dalend (< 1,5 km
  hoog, < 25 km) = gaat daar landen. Spreekt de routedatabase dat tegen, dan
  wint de positie en zie je bv. "Vanaf Eindhoven, bestemming onbekend". De
  luchthavenlijst (`airports.csv`, alleen velden met lijnvluchten) komt uit
  [OurAirports](https://ourairports.com/data/), public domain.

De drempel en zoekstraal staan bovenin `main.py` (`MIN_ELEVATION_DEG`,
`SEARCH_RADIUS_NM`) als je ze anders wil.

## Beperkingen, eerlijk gezegd

- **Route kan fout zijn.** adsbdb kent per vluchtnummer één vaste route en
  die wordt door vrijwilligers bijgehouden. Ryanair en andere maatschappijen
  hergebruiken vluchtnummers, dus dat gaat soms mis (getest: een Ryanair
  Eindhoven -> Milaan stond erin als Dortmund -> Malaga). Bij vertrek en
  landing vangt de luchthavencheck dat af. Op kruishoogte niet: ligt het
  toestel dan grofweg op de lijn van de foute route, dan zie je die foute
  route.
- **Niet alles is zichtbaar.** Alleen toestellen met een transponder die door
  een ontvanger van deze netwerken wordt opgepikt. Militair, sommige heli's
  en laagvliegende kleine toestellen kunnen ontbreken.
- **Geen privévluchten-route.** Kleine sportvliegtuigjes en heli's hebben
  meestal geen bekende route; je ziet dan wel hoogte, snelheid en type.
- **De URL is openbaar.** Geen login. Wie de link heeft kan 'm gebruiken; het
  ergste dat gebeurt is dat de gratis databronnen je even afknijpen.
- De gratis API's hebben geen garantie. Als ze stoppen of een key gaan
  vragen, moet de code aangepast.

## Icoontjes

De 3D-icoontjes in `static/icons/` en het startscherm-icoon komen van
[thiings.co](https://www.thiings.co), onder hun gratis licentie: alleen voor
persoonlijk, niet-commercieel gebruik, met zichtbare naamsvermelding (staat
in de footer van de app). Die licentie geldt voor de icoontjes, niet voor de
rest van deze repo. Wil je de app ooit commercieel inzetten, dan heb je een
betaalde licentie nodig of moeten de icoontjes eruit.

## Lokaal draaien

```bash
pip install -r requirements.txt
python main.py   # http://localhost:8080
```

Locatie werkt in de browser alleen via `localhost` of `https`.
