# Operations: the whole food bank as data in the twin

The captured zones show what the building looks like. This layer models what the building does: food arrives, gets sorted, waits in the right temperature zone, becomes meals in the community kitchen, and leaves for partner agencies. It follows the project's three steps.

| Step | What the operations layer adds | Where |
| --- | --- | --- |
| 1. Video to digital twin | Every operational area is a footprint in the twin's metric frame, tagged with the capture that covers it (`Racking`, `Sorting`, `Market`, and `Kitchen` once the kitchen capture lands). | `src/telemetry/operations.json` `zones` |
| 2. Existing data on AWS | The telemetry Lambda also answers `GET /operations` with inventory, flows, dock loads and kitchen batches. | `src/telemetry/operations.py`, `template.yaml` |
| 3. Omniverse as the visualisation layer | `public/twin.usda` puts the captures and the operations layer in one frame; `public/operations.usda` carries areas, kitchen stations and flows as OpenUSD prims keyed by the same ids. The browser shows the same data on `operations.html` and as floor outlines in the 3D viewer. | `scripts/operations_to_usd.py`, `public/operations.html`, `public/js/ops-overlay.js` |

## How a food bank runs, as the model sees it

```
 sources                 receiving        sorting           storage              outbound               channels
 USDA commodities  ┐                                    ┌─ freezer (frozen) ─┐  ┌─ order staging ──┬─ partner agencies
 grocery rescue    ┼──>  receiving dock ─> product  ──> ├─ cooler (chilled) ─┼──┤                  └─ mobile pantries
 produce growers   ┤          │            recovery     └─ dry racking ──────┤  ├─ client market ──── market shoppers
 purchased food    ┤          └──── direct put-away ──────────────────────────┘  └─ community kitchen ─ kids and senior meals
 food drives       ┘
```

- **Inbound.** Five source types with their own product mix. Grocery rescue and food drives go through the product recovery (sorting) room; USDA, purchased and grower loads go straight to put-away. Trucks are scheduled across 06:00 to 15:00.
- **Storage and the cold chain.** Six inventory families each have a home temperature zone, a pallet weight, a shelf life and a days-of-supply target. Stock carries over from day to day: each day's outbound is set so stock follows a slow target curve, so the closing stock of one day is the opening stock of the next. The freezer, cooler, dry store and the kitchen walk-in are the four cold-chain sensors in `GET /telemetry`; an area whose sensor is in alarm reports its stock as `at_risk_lbs`.
- **Community kitchen.** About 950 meals a day, Monday to Friday, for the Summer Food Service Program, the Child and Adult Care Food Program and Kids Cafe. Ingredients are picked from storage at 05:30, then each batch of 250 meals is routed prep, cook (kettle, tilt skillet or combi oven, by recipe), blast chill, portion and pack, and cold hold until dispatch between 13:00 and 15:30. The kitchen is modelled the way the Michelin kitchen twin models a restaurant, as a small factory with a bill of materials and a routing per recipe.
- **Outbound.** Partner agencies (pantries, shelters, school pantries, senior centers) collect from order staging, mobile pantries leave from the same docks, the client market draws its own stock, and the kitchen's meals go to meal sites. Distributed pounds convert to meals at Feeding America's 1.2 lb per meal.

Sunday is closed: nothing moves and stock waits for Monday. Everything is a pure function of the clock, so the Lambda needs no database and two callers in the same second see the same numbers.

## Published versus estimated

Published (cited in `operations.json` `references`): 74 million lbs a year, 26.6 million of them produce; 251 partner agencies in 21 counties; a 135,000 sq ft facility with about 7,000 pallet positions, over 35,000 sq ft of cooler and freezer and 15 docks; a 4,200 sq ft production kitchen with steam-jacketed kettles, combi ovens, blast chillers and tilt skillets; about 950 kitchen meals a day; the culinary training program; the tour route (warehouse, cooler and freezer, product recovery room, production kitchen).

Estimated (marked `"est": true`): the source and channel shares and product mixes, pallet weights, days-of-supply targets, area capacities, time windows and lags, equipment counts, recipes, batch sizes and step times. Central Texas Food Bank is the inspiration, not a replica; the twin's building is a scaled-down stand-in, while volumes are for the whole facility.

## Contract

`GET /operations` (additive to `GET /telemetry`; both from the same Lambda):

| Field | Meaning |
| --- | --- |
| `kpis` | Received, distributed (and meal equivalent), on hand and days of supply, kitchen meals, trucks and pickups, stock at risk |
| `areas[]` | `id`, `kind`, `footprint` and `position` (metres, telemetry frame), `on_hand_lbs`, `pallets`, `capacity_pallets`, `utilisation`, `telemetry_zone`, `cold_status`, `at_risk_lbs`, `capture` |
| `inventory[]` | Per family: `on_hand_lbs`, `pallets`, `days_of_supply` against `target_days`, in and out today, `status` |
| `flows[]` | `from`, `to`, `lbs_today`, `lbs_plan` between sources, areas and channels |
| `kitchen` | Recipe, meals planned, cooked, chilled, packed, dispatched, `programs[]`, `batches[]` with stage, `equipment[]` with `in_use` and `position` |
| `trucks` | Today's inbound loads with door, due time and status; the next outbound partner pickups |

`public/js/operations.js` is a line-for-line port that runs when the API is unset or unreachable; `tests/operations.test.mjs` checks it gives the same payload as the Python. A real warehouse management system, a donor portal or the kitchen's production sheets can replace the simulator behind the same JSON.

## Omniverse

```sh
pip install -r requirements-render.txt      # usd-core
python scripts/operations_to_usd.py          # writes public/operations.usda
python scripts/align_twin_usd.py             # writes public/twin.usda + twin_crop.usdc
```

Open `public/twin.usda` in Omniverse: it is the whole twin in one frame. It references the captures from `site.usdz`, re-placed with the same `position` and `yaw_deg` the browser uses (`twin.config.json` is the single source of placement), crops them like the browser through `twin_crop.usdc` (opacity overrides, about 0.5 MB), and references `operations.usda`. Regenerate it with `python scripts/align_twin_usd.py` whenever a placement changes; `tests/twin_usd.test.mjs` fails until you do.

The operations layer has `/World/Operations/Areas/<id>` (footprint mesh plus `twin:*` attributes), `/World/Operations/Kitchen/<station>` and `/World/Operations/Flows/<from>_to_<to>`, with values from a reference moment as defaults. A Kit extension that polls `/operations` sets `twin:onHandLbs`, `twin:utilisation` and `twin:lbsToday` by id. `site.usdz` on its own stays the zone library, spread out by `data/site/layout.json` for inspecting one capture at a time.
