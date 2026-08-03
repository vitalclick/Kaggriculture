"""Kaggriculture competition agent.

Strategy: a goose + wheat + melon portfolio worked by a large crew of cheap
farm hands, with price-aware selling against the engine's exact market curve.

Why this portfolio (numbers from the engine's constants):
- Geese: fed + cared geese produce 2 eggs/day (1 base + 1 banked care bonus)
  plus 1 free fertilizer/day. Egg prices are nearly glut-proof (log curve
  above equilibrium: even +2000 units only drops $50 -> ~$37), and fertilizer
  sells around $50-100. Payback on a $300 goose is ~3-4 days.
- Melons: 6 units x $250 base per ~11-day cycle is by far the best $/tile/day,
  but the price crashes hard on oversupply (squared curve). We gate new melon
  planting on the live price and never sell below a marginal-price floor.
- Wheat: absorbs gluts gently (log curve), feeds the geese, and the town's
  shops drain it constantly, propping the price up. Safe filler everywhere.
- Farm hands cost fib(n) each day (1,1,2,3,5,...): ~$143/day buys 10 hands,
  i.e. 240 extra actions. Massive leverage; we hire aggressively.

Selling: we mirror the engine's price function locally and, each turn, sell
each product unit-by-unit while the marginal price stays above a per-item
floor. Floors collapse near the end of the season (unsold goods are worth $0).
"""

import math
import sys
import traceback

# ---------------------------------------------------------------------------
# Game constants (mirrored from the engine)
# ---------------------------------------------------------------------------

CROPS = {
    "WHEAT":      {"seed": 10,  "first": 2,  "max_day": 4,  "max_yield": 6, "ongoing": False},
    "CARROT":     {"seed": 20,  "first": 2,  "max_day": 3,  "max_yield": 4, "ongoing": False},
    "TOMATO":     {"seed": 50,  "first": 8,  "max_day": 8,  "max_yield": 4, "ongoing": True},
    "STRAWBERRY": {"seed": 100, "first": 10, "max_day": 10, "max_yield": 4, "ongoing": True},
    "MELON":      {"seed": 80,  "first": 10, "max_day": 12, "max_yield": 6, "ongoing": False},
}

ANIMALS = {
    "GOOSE": {"cost": 300, "structure": "COOP",    "product": "EGG",  "max_held": 4},
    "COW":   {"cost": 400, "structure": "PASTURE", "product": "MILK", "max_held": 6},
    "SHEEP": {"cost": 500, "structure": "PASTURE", "product": "WOOL", "max_held": 6},
}

PRODUCTS = ["WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL", "FERTILIZER"]

MARKET_I0 = 10000

MARKET_PARAMS = {
    "WHEAT":      {"base":  25, "T": 400, "bf": "sqrt",   "bt": 0.80, "af": "log",    "at": 0.20},
    "CARROT":     {"base":  35, "T": 450, "bf": "log",    "bt": 0.20, "af": "sqrt",   "at": 0.70},
    "TOMATO":     {"base":  60, "T": 200, "bf": "linear", "bt": 0.40, "af": "sqrt",   "at": 0.60},
    "STRAWBERRY": {"base": 120, "T": 100, "bf": "sqrt",   "bt": 0.70, "af": "linear", "at": 1.60},
    "MELON":      {"base": 250, "T": 300, "bf": "log",    "bt": 0.20, "af": "sq",     "at": 3.60},
    "EGG":        {"base":  50, "T": 332, "bf": "linear", "bt": 0.40, "af": "log",    "at": 0.20},
    "MILK":       {"base": 160, "T": 122, "bf": "sqrt",   "bt": 0.60, "af": "linear", "at": 1.60},
    "WOOL":       {"base": 200, "T": 105, "bf": "log",    "bt": 0.20, "af": "sq",     "at": 3.20},
    "FERTILIZER": {"base": 100, "T": 200, "bf": "linear", "bt": 0.40, "af": "linear", "at": 0.40},
}


def _shape(func, x):
    x = max(0.0, x)
    if func == "linear":
        return x
    if func == "sq":
        return x * x
    if func == "sqrt":
        return math.sqrt(x)
    if func == "log":
        return math.log(1.0 + x)
    return x


def market_price(item, inv):
    p = MARKET_PARAMS[item]
    base = p["base"]
    if inv < MARKET_I0:
        amp = p["bt"] * base / _shape(p["bf"], p["T"])
        price = base + amp * _shape(p["bf"], MARKET_I0 - inv)
    else:
        amp = p["at"] * base / _shape(p["af"], p["T"])
        price = base - amp * _shape(p["af"], inv - MARKET_I0)
    return max(1, int(round(price)))


# ---------------------------------------------------------------------------
# Strategy constants
# ---------------------------------------------------------------------------

TURNS_PER_DAY = 24
LAST_DAY = 29

MAX_HANDS = 12
MAX_HIRES_PER_TURN = 6

GOOSE_TARGET = 12
GOOSE_LAST_BUY_DAY = 21
CARE_LAST_DAY = 26      # bonus banked day d pays EOD d+1, harvested d+2
FEED_LAST_DAY = 28      # production at EOD 28 is harvested/sold on day 29

MELON_LAST_PLANT_DAY = 18
WHEAT_LAST_PLANT_DAY = 25
CARROT_LAST_PLANT_DAY = 26
MELON_MIN_PRICE_TO_PLANT = 140
WHEAT_MIN_PRICE_TO_FILL = 15
CARROT_MIN_PRICE_TO_FILL = 24

SELL_FLOORS = {
    "WHEAT": 14, "CARROT": 16, "TOMATO": 25, "STRAWBERRY": 55,
    "MELON": 110, "EGG": 20, "MILK": 55, "WOOL": 55, "FERTILIZER": 48,
}
FERT_COLLECT_MIN_PRICE = 52

DROP_TRIP_AT = 10       # walk back to the shed once carrying this many items
SHED_PRESSURE = 85      # shed fill level at which sell floors collapse

LAND_PLANS = [
    # (cost, earliest day, latest day, cash buffer kept after purchase)
    (1000, 1, 27, 600),
    (2000, 3, 20, 900),
    (4000, 5, 15, 1200),
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def G(d, key, default=None):
    if isinstance(d, dict):
        return d.get(key, default)
    return getattr(d, key, default)


def shed_access_tiles(board):
    half = board // 2
    return [(half - 1, half - 1), (half, half - 1), (half - 1, half), (half, half)]


def dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def dist_to_shed(pos, board):
    return min(dist(pos, t) for t in shed_access_tiles(board))


def nearest_access(pos, board):
    return min(shed_access_tiles(board), key=lambda t: dist(pos, t))


def step_toward(pos, target):
    x, y = pos
    tx, ty = target
    if tx != x and (abs(tx - x) >= abs(ty - y) or ty == y):
        return "EAST" if tx > x else "WEST"
    if ty != y:
        return "SOUTH" if ty > y else "NORTH"
    return "PASS"


def fib(n):
    a, b = 1, 1
    for _ in range(n):
        a, b = b, a + b
    return a


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

def agent(obs):
    try:
        return _plan(obs)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return {"farmer": ["PASS"], "hands": [], "market": []}


def _plan(obs):
    player = G(obs, "player", 0)
    day = G(obs, "day", 0)
    hour = G(obs, "hour", 0)
    farms = G(obs, "farms", []) or []
    if player >= len(farms):
        return {"farmer": ["PASS"], "hands": [], "market": []}
    farm = farms[player]

    priv = G(obs, "private", {}) or {}
    shed = dict(G(priv, "shed", {}) or {})
    seeds = dict(G(priv, "seeds", {}) or {})
    invs = [dict(i) for i in (G(priv, "inventories", None) or [{}])]

    market = G(obs, "market", {}) or {}
    minv = dict(G(market, "inventory", {}) or {})
    mprices = dict(G(market, "prices", {}) or {})

    money = G(farm, "money", 0)
    tiles = G(farm, "tiles", [])
    board = len(tiles) or 10
    units = [list(G(farm, "farmer", [0, 0]))]
    units += [list(p) for p in (G(farm, "hands", []) or [])]
    while len(invs) < len(units):
        invs.append({})

    def price_now(item):
        return mprices.get(item, MARKET_PARAMS[item]["base"])

    # ---- scan the farm -----------------------------------------------------
    plants, animals, empty_coops, weeds, empties = [], [], [], [], []
    coops_total = 0
    for y in range(board):
        row = tiles[y]
        for x in range(board):
            t = row[x]
            if t is None:
                empties.append((x, y))
                continue
            if isinstance(t, str):          # "LOCKED"
                continue
            kind = G(t, "kind", None)
            if kind == "WEED":
                weeds.append((x, y))
            elif kind == "PLANT":
                plants.append((x, y, t))
            elif kind in ("COOP", "PASTURE"):
                if kind == "COOP":
                    coops_total += 1
                if G(t, "animal", None):
                    animals.append((x, y, t))
                else:
                    empty_coops.append((x, y)) if kind == "COOP" else None

    crop_count = {c: 0 for c in CROPS}
    for _, _, t in plants:
        crop = G(t, "crop", None)
        if crop in crop_count:
            crop_count[crop] += 1

    geese_in_shed = shed.get("GOOSE", 0)
    n_animals = len(animals)

    # ---- planting plan -----------------------------------------------------
    want_coops = GOOSE_TARGET if day <= GOOSE_LAST_BUY_DAY else 0
    coop_deficit = max(0, want_coops - coops_total)

    wheat_target = max(12, n_animals + 6) if day <= WHEAT_LAST_PLANT_DAY else 0
    carrot_target = 8 if day <= 5 else 0
    melon_ok = day <= MELON_LAST_PLANT_DAY and price_now("MELON") >= MELON_MIN_PRICE_TO_PLANT

    empties_sorted = sorted(empties, key=lambda p: dist_to_shed(p, board))
    plan_coop = empties_sorted[:coop_deficit]
    plant_plan = []  # (x, y, crop)
    wheat_deficit = max(0, wheat_target - crop_count["WHEAT"])
    carrot_deficit = max(0, carrot_target - crop_count["CARROT"])
    for pos in empties_sorted[coop_deficit:]:
        if wheat_deficit > 0:
            plant_plan.append((pos[0], pos[1], "WHEAT"))
            wheat_deficit -= 1
        elif carrot_deficit > 0:
            plant_plan.append((pos[0], pos[1], "CARROT"))
            carrot_deficit -= 1
        elif melon_ok:
            plant_plan.append((pos[0], pos[1], "MELON"))
        elif day <= WHEAT_LAST_PLANT_DAY and price_now("WHEAT") >= WHEAT_MIN_PRICE_TO_FILL:
            plant_plan.append((pos[0], pos[1], "WHEAT"))
        elif day <= CARROT_LAST_PLANT_DAY and price_now("CARROT") >= CARROT_MIN_PRICE_TO_FILL:
            plant_plan.append((pos[0], pos[1], "CARROT"))

    # ---- market orders -----------------------------------------------------
    orders = []
    cash = money

    # Hires: early in the day so hands work the full day.
    if hour <= 1 and day <= LAST_DAY:
        est_actions = (len(plants) * 1.15 + len(plant_plan) * 1.7
                       + n_animals * 3.8 + coop_deficit * 1.6 + 8)
        need_units = int(est_actions * 1.55 / TURNS_PER_DAY) + 1
        target_hands = min(MAX_HANDS, max(3, need_units - 1))
        if day >= LAST_DAY:
            target_hands = min(target_hands, 6)
        n_hire = max(0, target_hands - (len(units) - 1))
        k = G(farm, "hires_today", 0)
        for _ in range(min(n_hire, MAX_HIRES_PER_TURN)):
            c = fib(k)
            if cash < c:
                break
            orders.append(["HIRE"])
            cash -= c
            k += 1

    # Sells: placed before the big buys in the queue so the proceeds are
    # available by the time the buy orders are processed.
    shed_load = sum(v for v in shed.values() if v)
    sell_orders = []
    for item in PRODUCTS:
        have = shed.get(item, 0)
        if item == "WHEAT":
            reserve = 0 if day >= LAST_DAY else n_animals * (2 if day < FEED_LAST_DAY else 1)
            have = min(have, max(0, shed.get(item, 0) - reserve))
        if have <= 0:
            continue
        if day >= LAST_DAY:
            floor = 2
        elif day >= LAST_DAY - 1:
            floor = 5
        else:
            floor = SELL_FLOORS[item]
            if shed_load > SHED_PRESSURE:
                floor = max(2, floor // 3)
        inv0 = minv.get(item, MARKET_I0)
        n = 0
        while n < have and market_price(item, inv0 + n) >= floor:
            n += 1
        if n > 0:
            sell_orders.append(["SELL", item, n])
    orders += sell_orders

    # Land: expand while there is season left to use it.
    quads = list(G(farm, "unlocked_quadrants", ["NW"]) or ["NW"])
    n_extra = len(quads) - 1
    if 0 <= n_extra < len(LAND_PLANS):
        cost, d0, d1, buffer_ = LAND_PLANS[n_extra]
        if d0 <= day <= d1 and cash >= cost + buffer_:
            orders.append(["BUY_LAND"])
            cash -= cost

    # Geese.
    if day <= GOOSE_LAST_BUY_DAY:
        deficit = GOOSE_TARGET - (n_animals + geese_in_shed)
        can = min(deficit, max(0, int((cash - 400) // ANIMALS["GOOSE"]["cost"])), 2)
        if can > 0:
            orders.append(["BUY_ANIMAL", "GOOSE", can])
            cash -= can * ANIMALS["GOOSE"]["cost"]

    # Feed wheat: keep ~2 days of feed on hand; buying is worth it while
    # wheat costs well under the daily egg + fertilizer output of a goose.
    heads = n_animals + geese_in_shed + len(plan_coop)
    if heads > 0 and day <= FEED_LAST_DAY:
        wheat_stock = shed.get("WHEAT", 0) + sum(i.get("WHEAT", 0) for i in invs)
        need = heads * 2 - wheat_stock
        if need > 0:
            p = market_price("WHEAT", minv.get("WHEAT", MARKET_I0) - 1)
            if p <= 60 and cash >= p * need:
                orders.append(["BUY_PRODUCT", "WHEAT", need])
                cash -= p * need

    # Seeds for the plan (melon first: it is the most time-critical).
    planned = {}
    for _, _, crop in plant_plan:
        planned[crop] = planned.get(crop, 0) + 1
    for crop in ("MELON", "WHEAT", "CARROT", "TOMATO", "STRAWBERRY"):
        want = planned.get(crop, 0) - seeds.get(crop, 0)
        if want <= 0:
            continue
        cost = CROPS[crop]["seed"]
        n = min(want, max(0, int((cash - 100) // cost)))
        if n > 0:
            orders.append(["BUY_SEED", crop, n])
            cash -= n * cost

    orders = orders[:10]

    # ---- unit tasks ----------------------------------------------------------
    tasks = []

    if day <= FEED_LAST_DAY:
        for x, y, t in animals:
            if not G(t, "fed_today", False):
                tasks.append({"prio": 0, "pos": (x, y), "op": ["FEED"], "need": "WHEAT"})

    for x, y, t in plants:
        if not G(t, "watered_today", False):
            tasks.append({"prio": 1, "pos": (x, y), "op": ["WATER"]})

    for x, y, t in plants:
        cd = CROPS.get(G(t, "crop", ""), None)
        if cd is None:
            continue
        age = day - G(t, "planted_day", day)
        yu = G(t, "yield_units", 0)
        if yu <= 0:
            continue
        ready = False
        if cd["ongoing"]:
            ready = age >= cd["first"] and (yu >= 2 or day >= LAST_DAY)
        else:
            ready = ((yu >= cd["max_yield"] and age >= cd["first"])
                     or (age >= cd["max_day"] and G(t, "watered_today", False))
                     or age > cd["max_day"]
                     or (day >= LAST_DAY and age >= cd["first"]))
        if ready:
            tasks.append({"prio": 2, "pos": (x, y), "op": ["HARVEST"]})

    for x, y, t in animals:
        yu = G(t, "yield_units", 0)
        mh = ANIMALS.get(G(t, "animal", ""), {}).get("max_held", 4)
        if yu >= 2 or yu >= mh - 1 or (day >= LAST_DAY - 1 and yu >= 1):
            tasks.append({"prio": 3, "pos": (x, y), "op": ["HARVEST"]})

    seed_claims = dict(seeds)
    for x, y, crop in plant_plan:
        tasks.append({"prio": 4, "pos": (x, y), "op": ["PLANT", crop], "crop": crop})

    placeable = min(geese_in_shed, len(empty_coops))
    for x, y in empty_coops:
        tasks.append({"prio": 5, "pos": (x, y), "op": ["PLACE", "GOOSE"], "need": "GOOSE"})

    for x, y in plan_coop:
        tasks.append({"prio": 5.5, "pos": (x, y), "op": ["BUILD_COOP"]})

    if day <= CARE_LAST_DAY:
        for x, y, t in animals:
            if not G(t, "cared_today", False):
                tasks.append({"prio": 6, "pos": (x, y), "op": ["CARE"]})

    if price_now("FERTILIZER") >= FERT_COLLECT_MIN_PRICE or day >= LAST_DAY - 3:
        for x, y, t in animals:
            if G(t, "fertilizer_available", False):
                tasks.append({"prio": 7, "pos": (x, y), "op": ["COLLECT_FERTILIZER"]})

    if day <= CARROT_LAST_PLANT_DAY:
        for x, y in weeds:
            tasks.append({"prio": 8, "pos": (x, y), "op": ["DIG"]})

    # ---- assign units --------------------------------------------------------
    ops = [None] * len(units)

    # Drop trips: bring full inventories back to the shed so goods become
    # sellable; on the final day everything must reach the shed to count.
    for i, u in enumerate(units):
        carry = sum(invs[i].values())
        if carry <= 0:
            continue
        if (carry >= DROP_TRIP_AT
                or (day >= LAST_DAY and hour >= 15)
                or (hour >= 22 and carry >= 5)):
            if tuple(u) in set(shed_access_tiles(board)):
                ops[i] = ["DROP"]
            else:
                ops[i] = [step_toward(u, nearest_access(u, board))]

    # Wheat pickup for feeding.
    n_unfed = sum(1 for t in tasks if t["prio"] == 0)
    carried_wheat = sum(invs[i].get("WHEAT", 0) for i in range(len(units)))
    wheat_need = min(max(0, n_unfed - carried_wheat), shed.get("WHEAT", 0))
    if wheat_need > 0:
        cand = [i for i in range(len(units)) if ops[i] is None]
        if cand:
            j = min(cand, key=lambda i: dist_to_shed(units[i], board))
            if tuple(units[j]) in set(shed_access_tiles(board)):
                ops[j] = ["PICKUP", "WHEAT", min(wheat_need + 2, shed.get("WHEAT", 0), 16)]
            else:
                ops[j] = [step_toward(units[j], nearest_access(units[j], board))]

    # Goose pickup for placement.
    carried_geese = sum(invs[i].get("GOOSE", 0) for i in range(len(units)))
    goose_need = min(max(0, placeable - carried_geese), geese_in_shed)
    if goose_need > 0:
        cand = [i for i in range(len(units)) if ops[i] is None]
        if cand:
            j = min(cand, key=lambda i: dist_to_shed(units[i], board))
            if tuple(units[j]) in set(shed_access_tiles(board)):
                ops[j] = ["PICKUP", "GOOSE", min(goose_need, 4)]
            else:
                ops[j] = [step_toward(units[j], nearest_access(units[j], board))]

    # Greedy nearest-task assignment for everyone else.
    unclaimed = list(tasks)
    for i, u in enumerate(units):
        if ops[i] is not None:
            continue
        best, best_key, best_idx = None, None, -1
        for idx, t in enumerate(unclaimed):
            need = t.get("need")
            if need and invs[i].get(need, 0) <= 0:
                continue
            crop = t.get("crop")
            if crop and seed_claims.get(crop, 0) <= 0:
                continue
            key = (t["prio"], dist(u, t["pos"]))
            if best_key is None or key < best_key:
                best, best_key, best_idx = t, key, idx
        if best is None:
            ops[i] = ["PASS"]
            continue
        unclaimed.pop(best_idx)
        crop = best.get("crop")
        if crop:
            seed_claims[crop] -= 1
        if tuple(u) == tuple(best["pos"]):
            ops[i] = best["op"]
        else:
            ops[i] = [step_toward(u, best["pos"])]

    return {"farmer": ops[0], "hands": ops[1:], "market": orders}
