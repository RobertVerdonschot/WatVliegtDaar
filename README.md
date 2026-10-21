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

- Je telefoon stuurt je locatie naar de server (`/api/overhead`), elke 15
  seconden zolang de app open staat.
- De server haalt alle toestellen binnen ~18 km op bij
  [adsb.fi](https://adsb.fi) (reserve: [adsb.lol](https://adsb.lol)).
  Beide zijn gratis, door vrijwilligers gedragen ADS-B-netwerken.
- "Boven je" = minstens 35 graden boven de horizon. Een lijnvliegtuig op 10 km
  hoogte telt dan tot ~14 km afstand mee, een heli op 300 m tot ~430 m.
  Maximaal 3 toestellen, het hoogst-aan-de-hemel eerst. Is er niks, dan zie je
  welk toestel het dichtstbij is.
- Route, maatschappij en type komen van [adsbdb.com](https://www.adsbdb.com).

De drempel en zoekstraal staan bovenin `main.py` (`MIN_ELEVATION_DEG`,
`SEARCH_RADIUS_NM`) als je ze anders wil.

## Beperkingen, eerlijk gezegd

- **Route kan fout zijn.** adsbdb kent per vluchtnummer één vaste route en
  die wordt door vrijwilligers bijgehouden. Ligt het toestel duidelijk naast
  die route, dan toont de app "Route onbekend" in plaats van iets fouts. Een
  fout dat dicht bij de echte route ligt glipt er wel doorheen.
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
