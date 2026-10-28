# Small Airport Tycoon

A SimCity-style builder for a general aviation field. Place runway slabs, taxiways, hangars, and a tower from the same [Aircraft Fleet and Airfield](https://3dassets.dev/packs/aircraft-fleet-and-airfield) pack used by `atc-sim`. Traffic arrives when you have pavement and parking. Cash comes from landing fees, hangar rent, and fuel.

This is a toy tycoon, not a navigation sim.

Accounts are required. Signup is open; each account keeps one airport save on the server (SQLite). The save is autosaved as you build.

## Run

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
./venv/bin/python app.py
```

Local: http://localhost:8123/ (redirects to `/login`)

Public: https://airport-tycoon.mattbrewer.me/

LXC `web` (10.145.22.63) runs `airport-tycoon.service` on **8130**. Deploy with `./deploy.sh`. The deploy keeps remote `data/` (SQLite + session key) intact. The KAPA ATC sim stays on the same LXC at 8123 / https://atc.mattbrewer.me.

## Accounts and saves

- `/login` has **Sign in** and **Create account**. Usernames are 3–32 letters, numbers, `.`, `_`, or `-`; passwords are at least 8 characters.
- Sessions are Flask signed cookies (HttpOnly, SameSite=Lax, Secure when served over HTTPS through Cloudflare Tunnel).
- Saves live in `data/airport-tycoon.sqlite`, one JSON save per account. `data/secret_key` signs sessions. Back up `data/` to keep accounts.
- Old `localStorage` airports are imported once into the first account that signs in on that browser, then the local copy is removed.

## Play

- Click a catalog item, then click the grass to place. **R** rotates. **Esc** cancels.
- Runway slabs are 30 × 10 m (three 10 m cells). Taxi and apron tiles are one cell.
- Aircraft appear once you have a runway (or grass strip) and somewhere to park.
- Place a **helipad** for piston, turbine, and air-ambulance helicopters. They hover in and do not need a runway.
- **Demolish** refunds half the build cost.
- **M** toggles sound. Piston aircraft and the piston helicopter use a prop loop; jets (and turbine helicopters) use an idle loop, with a takeoff one-shot for jets.
- Progress saves automatically to your account. **Log out** flushes any pending save first.

## Assets

GLBs load from `cdn.3dassets.dev`, CC0. Same fleet keys as atc-sim (`trainer`, `lowwing`, `twin`, `jet`, `tug`, `bowser`) plus modular airfield tiles.

Sounds in `sounds/` are mono 32 kHz WAVs, with the loops crossfaded at the seam:

- Prop loop: [Airplane Prop Loop](https://opengameart.org/content/airplane-prop-loop) by AntumDeluge, based on "Airplane prop noise" by jakobthiesen — CC BY 3.0.
- Jet idle loop: [Idle F-22 Jet Plane](https://freesound.org/people/qubodup/sounds/187734/) by qubodup — CC0.
- Jet takeoff: [Jet Engine Takeoff](https://opengameart.org/content/jet-engine-takeoff) by dklon — CC BY 3.0.
