"""Kaggriculture competition agent.

Strategy is driven by the market's revenue curves. Selling N units of a product
earns sum(price(k)) for k in 0..N-1, and each product's curve saturates at a
different point:

    melon        100 units -> $21.7k, 150 -> $26.4k, then flat (price hits $1)
    fertilizer   100 units -> $9.0k,  300 -> $21.0k
    egg          100 units -> $4.4k,  500 -> $20.5k
    wheat        100 units -> $2.2k,  500 -> $10.3k
    strawberry / milk / wool collapse to the $1 floor inside ~50 units

So the plan is melons for raw value, geese for eggs + fertilizer, and wheat as
bulk filler that doubles as animal feed. Strawberry, tomato, milk and wool are
never planted: their markets are too thin to repay a tile.

Geese are the quiet engine. Every surviving animal has `fertilizer_available`
set at each end-of-day refresh regardless of CARE, so a goose yields 1 free
fertilizer per day on top of its eggs. Fed and cared for, it produces 2 eggs a
day (1 base + 1 banked care bonus, since a goose's production interval is 1).
That is roughly $3k of output over the season against a $300 purchase price --
provided it is actually fed. A goose that misses two consecutive days escapes
and the $300 is gone.

Melons are the best crop per action: ~10 actions over an 11-day cycle for 6
units worth ~$250 each. Two cycles fit in a 30-day season (plant day 0, harvest
day 10, replant, harvest day 20), after which those tiles switch to wheat.

Labour is allocated by marginal value rather than by a fixed priority order.
Every possible job is priced in coins -- watering a melon inside its bonus
window really is worth ~$250, while watering wheat is worth ~$45 -- and each
unit takes the job with the best value per action, counting the walk to get
there. Deadline jobs (feeding, saving a plant that dies tonight) gain urgency
as the day runs out so they are never crowded out by richer but deferrable work.
"""

import math
import sys
import traceback

# ---------------------------------------------------------------------------
# Engine constants (mirrored from kaggriculture.py)
# ---------------------------------------------------------------------------

# `exp` is the yield actually reachable without fertilizer: 1 unit at planting
# plus one per watered day inside the bonus window (which opens at
# ceil(max_day / 2)), capped by max_yield. Wheat's max_yield of 6 is only
# reachable with fertilizer, so valuing a wheat planting at 6 units overstates
# it by half.
CROPS = {
    "WHEAT":      {"seed": 10,  "first": 2,  "max_day": 4,  "max_yield": 6, "exp": 4, "ongoing": False},
    "CARROT":     {"seed": 20,  "first": 2,  "max_day": 3,  "max_yield": 4, "exp": 3, "ongoing": False},
    "TOMATO":     {"seed": 50,  "first": 8,  "max_day": 8,  "max_yield": 4, "exp": 4, "ongoing": True},
    "STRAWBERRY": {"seed": 100, "first": 10, "max_day": 10, "max_yield": 4, "exp": 4, "ongoing": True},
    "MELON":      {"seed": 80,  "first": 10, "max_day": 12, "max_yield": 6, "exp": 6, "ongoing": False},
}

PRODUCTS = ["WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL", "FERTILIZER"]

MARKET_I0 = 10000
GOOSE_COST = 300

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
    """Mirror of the engine's price curve, so we can price a sale before making it."""
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
# Tunables
# ---------------------------------------------------------------------------

TURNS_PER_DAY = 24          # defaults; the live values come from the config
LAST_DAY = 29

# Cutoffs expressed as days before the end of the season, so they stay correct
# if the episode length changes. At the default 720 steps these resolve to the
# tuned values: melon 19, wheat 24, carrot 25, goose 22, feed 28, care 27.
MELON_PLANT_CUTOFF = 10     # melon needs 10 days from planting to first yield
WHEAT_PLANT_CUTOFF = 5
CARROT_PLANT_CUTOFF = 4
GOOSE_BUY_CUTOFF = 7        # 4 days to first yield, so still ~3 productive days
FEED_CUTOFF = 1             # production at end of the penultimate day still sells
CARE_CUTOFF = 2

MELON_TILES = 22
GOOSE_TARGET = 8
MAX_HANDS = 14
HIRES_PER_TURN = 5

CASH_RESERVE = 60
HIRE_RESERVE = 15           # hands cost fib(n) coins; never let seeds crowd them out
# The hire sequence is Fibonacci, so it turns vicious fast: a 15th hand costs
# $610 a day and an 18th $2584. Refusing to pay past this keeps a large farm
# from bankrupting itself on labour.
HIRE_COST_CAP = 250
GOOSE_BUFFER = 400
MELON_BUFFER = 60

# A unit has 24 actions a day and spends much of it walking, so this is the
# number of tiles one unit can really keep watered and harvested.
TILES_PER_UNIT = 10

# Land: (cost, earliest day, latest day, cash needed on top, crew needed)
LAND_PLANS = [
    (1000, 1, 24, 400, 5),
    (2000, 3, 22, 900, 7),
    (4000, 5, 20, 1800, 9),
]

# Stop selling once the marginal price falls below this fraction of base, so we
# never dump a premium good into the floor. Relaxes on the last days, since
# unsold stock scores nothing.
SELL_FLOOR_FRAC = {
    "MELON": 0.42, "FERTILIZER": 0.30, "EGG": 0.50, "WHEAT": 0.45,
    "CARROT": 0.42, "TOMATO": 0.40, "STRAWBERRY": 0.30, "MILK": 0.30, "WOOL": 0.30,
}

SHED_PRESSURE = 55          # above this many items, sell regardless of the floor
DROP_CARRY = 7              # inventory size that triggers a trip to the shed


def G(d, key, default=None):
    if isinstance(d, dict):
        return d.get(key, default)
    return getattr(d, key, default)


def shed_tiles(board):
    h = board // 2
    return [(h - 1, h - 1), (h, h - 1), (h - 1, h), (h, h)]


def dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


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


def _plan(obs, cfg=None):
    player = G(obs, "player", 0)
    day = G(obs, "day", 0)
    hour = G(obs, "hour", 0)
    farms = G(obs, "farms", []) or []
    if player >= len(farms):
        return {"farmer": ["PASS"], "hands": [], "market": []}
    me = farms[player]

    priv = G(obs, "private", {}) or {}
    shed = dict(G(priv, "shed", {}) or {})
    seeds = dict(G(priv, "seeds", {}) or {})
    invs = [dict(i) for i in (G(priv, "inventories", None) or [{}])]

    mk = G(obs, "market", {}) or {}
    minv = dict(G(mk, "inventory", {}) or {})
    prices = dict(G(mk, "prices", {}) or {})

    def px(item):
        return prices.get(item, MARKET_PARAMS[item]["base"])

    money = G(me, "money", 0.0)
    tiles = G(me, "tiles", []) or []
    board = len(tiles) or 10
    access = shed_tiles(board)
    access_set = set(access)

    units = [list(G(me, "farmer", [0, 0]))]
    units += [list(p) for p in (G(me, "hands", []) or [])]
    while len(invs) < len(units):
        invs.append({})
    n_units = len(units)

    # The season's shape comes from the configuration when the framework
    # supplies it, so the agent still liquidates on time if the episode length
    # or day length differs from the defaults.
    turns_per_day = max(1, int(G(cfg, "turnsPerDay", TURNS_PER_DAY) or TURNS_PER_DAY))
    episode_steps = max(1, int(G(cfg, "episodeSteps", (LAST_DAY + 1) * TURNS_PER_DAY)
                               or (LAST_DAY + 1) * TURNS_PER_DAY))
    last_day = max(1, episode_steps // turns_per_day - 1)
    melon_cutoff = last_day - MELON_PLANT_CUTOFF
    wheat_cutoff = last_day - WHEAT_PLANT_CUTOFF
    carrot_cutoff = last_day - CARROT_PLANT_CUTOFF
    goose_cutoff = last_day - GOOSE_BUY_CUTOFF
    feed_cutoff = last_day - FEED_CUTOFF
    care_cutoff = last_day - CARE_CUTOFF

    endgame = day >= last_day
    winddown = day >= last_day - 1
    # Deadline work gets more valuable as the day runs out.
    urgency = 1.0 + 5.0 * (hour / float(turns_per_day))

    def to_shed(pos):
        return min(access, key=lambda t: dist(pos, t))

    # ---- read the farm ----------------------------------------------------
    plants, animals, free_coops, weeds, empties = [], [], [], [], []
    n_coops = 0
    melon_tiles = 0
    for y in range(board):
        row = tiles[y]
        for x in range(board):
            t = row[x]
            if t is None:
                empties.append((x, y))
                continue
            if not isinstance(t, dict):
                continue                                  # "LOCKED"
            kind = t.get("kind")
            if kind == "WEED":
                weeds.append((x, y))
            elif kind == "PLANT":
                plants.append((x, y, t))
                if t.get("crop") == "MELON":
                    melon_tiles += 1
            elif kind in ("COOP", "PASTURE"):
                if kind == "COOP":
                    n_coops += 1
                if t.get("animal"):
                    animals.append((x, y, t))
                elif kind == "COOP":
                    free_coops.append((x, y))

    n_animals = len(animals)
    geese_in_shed = shed.get("GOOSE", 0)
    carried_geese = sum(i.get("GOOSE", 0) for i in invs)
    geese_owned = n_animals + geese_in_shed + carried_geese
    shed_load = sum(v for v in shed.values() if v > 0)

    # ---- labour plan -------------------------------------------------------
    # Staff to the work available rather than to a budget: the n-th hire of a
    # day costs only fib(n), so even a tenth hand is $55 for 24 actions worth
    # far more. Under-hiring is what kills crops, since an unwatered plant
    # turns to a weed after two days.
    workload = (len(plants) * 1.35 + n_animals * 4.2
                + min(len(empties), 24) * 1.2 + len(weeds) * 0.4)
    target_hands = int(math.ceil(workload * 1.9 / turns_per_day))
    target_hands = max(3, min(MAX_HANDS, target_hands))
    if winddown:
        target_hands = min(target_hands, 6)
    crew = max(n_units, target_hands + 1)

    # ---- decide what goes on the empty tiles ------------------------------
    # Coops sit nearest the shed (they need feed/care/collect trips every day),
    # wheat next (replanted every 4 days), melons furthest out (watered only).
    empties.sort(key=lambda p: dist(p, to_shed(p)))

    coop_deficit = 0
    if day <= goose_cutoff:
        coop_deficit = max(0, min(GOOSE_TARGET, geese_owned + 5) - n_coops)
    coop_slots = empties[:coop_deficit]
    rest = empties[coop_deficit:]

    # Never take on more tiles than the crew can water. A field we cannot keep
    # up with does not just yield less, it dies and leaves a weed behind.
    capacity = int(crew * TILES_PER_UNIT - len(plants) - n_animals * 2.5)
    rest = rest[:max(0, capacity)]

    cash = money
    want_melon = 0
    # The market is shared, so an opponent dumping melons can flatten the price
    # before ours ripen. Below this the tile is worth more under wheat.
    if day <= melon_cutoff and px("MELON") >= 110:
        want_melon = max(0, MELON_TILES - melon_tiles)
        # Only buy seed for what the crew can actually get into the ground in
        # the next day or so. Buying the whole melon field up front drains the
        # bank on day 0 and leaves nothing for hands or feed.
        want_melon = min(want_melon, len(rest), max(2, crew * 2),
                         max(0, int((cash - MELON_BUFFER) // CROPS["MELON"]["seed"])))
    melon_slots = rest[len(rest) - want_melon:] if want_melon > 0 else []
    filler_slots = rest[:len(rest) - want_melon]

    plant_plan = [(x, y, "MELON") for (x, y) in melon_slots]
    if day <= wheat_cutoff:
        plant_plan += [(x, y, "WHEAT") for (x, y) in filler_slots]
    elif day <= carrot_cutoff:
        plant_plan += [(x, y, "CARROT") for (x, y) in filler_slots]

    # Wheat held back to feed the flock rather than sold.
    feed_reserve = 0 if endgame else n_animals * (2 if day < feed_cutoff else 1)

    # ---- market orders ----------------------------------------------------
    orders = []

    # Sells go first so the proceeds are banked before the buys are processed.
    for item in PRODUCTS:
        have = shed.get(item, 0)
        if item == "WHEAT":
            have -= feed_reserve
        if have <= 0:
            continue
        base = MARKET_PARAMS[item]["base"]
        normal = SELL_FLOOR_FRAC.get(item, 0.4)
        if endgame:
            frac = 0.0                      # unsold stock scores nothing
        elif day >= last_day - 4:
            # Ease the floor down over the closing days so the last big
            # harvests are spread out rather than dumped into one crash.
            frac = normal * (last_day - day) / 5.0
        elif shed_load > SHED_PRESSURE or cash < CASH_RESERVE:
            frac = 0.2
        else:
            frac = normal
        floor = max(1, int(base * frac))
        inv0 = minv.get(item, MARKET_I0)
        n = 0
        while n < have and market_price(item, inv0 + n) >= floor:
            n += 1
        if n > 0:
            orders.append(["SELL", item, n])
            cash += n * market_price(item, inv0)      # conservative estimate

    # Hire at the top of the day so hands work a full shift. This comes before
    # every other purchase: a hand costs a few coins and pays for itself many
    # times over, and letting seed spending crowd it out starves the whole farm.
    if hour <= 4 and not endgame:
        k = G(me, "hires_today", 0)
        made = 0
        while (n_units - 1) + made < target_hands and made < HIRES_PER_TURN:
            c = fib(k)
            if c > HIRE_COST_CAP or cash < c + HIRE_RESERVE:
                break
            orders.append(["HIRE"])
            cash -= c
            k += 1
            made += 1

    quads = list(G(me, "unlocked_quadrants", ["NW"]) or ["NW"])
    n_extra = len(quads) - 1
    if 0 <= n_extra < len(LAND_PLANS) and not winddown:
        cost, d0, d1, buf, need_crew = LAND_PLANS[n_extra]
        # Only expand once there is a crew to work the new ground and little
        # idle tile left on the land we already own.
        if (d0 <= day <= min(d1, last_day - 5) and cash >= cost + buf
                and crew >= need_crew and len(empties) <= 8):
            orders.append(["BUY_LAND"])
            cash -= cost

    if day <= goose_cutoff:
        # Only buy birds we have somewhere to put, so cash never sits dead in
        # the shed as unsellable livestock.
        room = len(free_coops) + len(coop_slots) - geese_in_shed - carried_geese
        want = min(GOOSE_TARGET - geese_owned, room, 3)
        want = min(want, max(0, int((cash - GOOSE_BUFFER) // GOOSE_COST)))
        if want > 0:
            orders.append(["BUY_ANIMAL", "GOOSE", want])
            cash -= want * GOOSE_COST

    # Top up feed if the farm cannot grow enough wheat for the flock.
    if n_animals > 0 and day <= feed_cutoff:
        stock = shed.get("WHEAT", 0) + sum(i.get("WHEAT", 0) for i in invs)
        need = n_animals * 2 - stock
        if need > 0:
            p = market_price("WHEAT", minv.get("WHEAT", MARKET_I0) - 1)
            afford = int(max(0, cash - CASH_RESERVE) // max(1, p))
            need = min(need, afford, 30)
            if need > 0:
                orders.append(["BUY_PRODUCT", "WHEAT", need])
                cash -= need * p

    # Seeds, only for tiles we are about to plant.
    want_seed = {}
    for _, _, crop in plant_plan:
        want_seed[crop] = want_seed.get(crop, 0) + 1
    for crop in ("MELON", "WHEAT", "CARROT"):
        need = want_seed.get(crop, 0) - seeds.get(crop, 0)
        if need <= 0:
            continue
        cost = CROPS[crop]["seed"]
        n = min(need, max(0, int((cash - CASH_RESERVE) // cost)))
        if n > 0:
            orders.append(["BUY_SEED", crop, n])
            cash -= n * cost

    orders = orders[:10]

    # ---- price every job the crew could do this turn -----------------------
    # Values are in coins of expected marginal gain. The assignment below picks
    # jobs by value per action, so these numbers decide where labour goes.
    tasks = []

    def add(value, pos, op, need=None, crop=None):
        if value > 0:
            tasks.append({"v": value, "pos": pos, "op": op, "need": need, "crop": crop})

    if day <= feed_cutoff and not endgame:
        for x, y, t in animals:
            if not t.get("fed_today", False):
                # Missing two days running loses the bird and its whole future.
                add(420 * urgency, (x, y), ["FEED"], need="WHEAT")

    for x, y, t in plants:
        crop = t.get("crop")
        cd = CROPS.get(crop)
        if cd is None:
            continue
        age = day - t.get("planted_day", day)
        watered = t.get("watered_today", False)
        yu = t.get("yield_units", 0)
        price = px(crop)

        ready = age >= cd["first"] and (yu >= cd["max_yield"] or age >= cd["max_day"])
        if endgame and yu > 0 and age >= cd["first"]:
            ready = True
        if ready and yu > 0:
            # Harvesting also frees the tile for the next planting.
            add(yu * price + 40, (x, y), ["HARVEST"])
            continue

        if endgame or watered:
            continue
        if t.get("consecutive_unwatered", 0) >= 1:
            # Dies tonight: the loss is the whole rest of the plant.
            lose = max(yu, cd["max_yield"] - 2)
            add(lose * price * 0.6 * urgency, (x, y), ["WATER"])
            continue
        window_start = (cd["max_day"] + 1) // 2
        if (not cd["ongoing"]) and window_start <= age <= cd["max_day"] and yu < cd["max_yield"]:
            add(price, (x, y), ["WATER"])          # one extra unit at harvest
        else:
            add(price * 0.12, (x, y), ["WATER"])   # insurance against tomorrow

    for x, y, t in animals:
        yu = t.get("yield_units", 0)
        product = "EGG"
        if yu >= 2 or (winddown and yu >= 1):
            add(yu * px(product) + 20, (x, y), ["HARVEST"])
        if t.get("fertilizer_available", False):
            # One action for a unit of fertilizer, which is the best-paying
            # single action on the farm after a melon harvest.
            add(px("FERTILIZER"), (x, y), ["COLLECT_FERTILIZER"])
        if day <= care_cutoff and not t.get("cared_today", False):
            add(px(product) * 0.9, (x, y), ["CARE"])

    if not endgame:
        for x, y in free_coops:
            add(650, (x, y), ["PLACE", "GOOSE"], need="GOOSE")
        for x, y in coop_slots:
            add(240, (x, y), ["BUILD_COOP"])
        # Planting with the day nearly over risks the seed dying unwatered.
        if hour <= turns_per_day - 3:
            for x, y, crop in plant_plan:
                cd = CROPS[crop]
                # Discounted: the payoff lands days from now, and only if the
                # crop survives to maturity.
                add(cd["exp"] * px(crop) * 0.55 - cd["seed"],
                    (x, y), ["PLANT", crop], crop=crop)
        if day <= carrot_cutoff:
            for x, y in weeds:
                add(110 if day <= 22 else 20, (x, y), ["DIG"])

    # ---- assign units ------------------------------------------------------
    ops = [None] * n_units

    # Trips back to the shed: produce only becomes sellable once dropped, and
    # anything still carried at the final scoring turn is worth nothing.
    for i in range(n_units):
        carry = sum(invs[i].values())
        if carry <= 0:
            continue
        keep = invs[i].get("WHEAT", 0) + invs[i].get("GOOSE", 0)
        droppable = carry - keep
        must = endgame and hour >= 14
        if droppable >= DROP_CARRY or must or (hour >= turns_per_day - 2 and shed_load < 60):
            if tuple(units[i]) in access_set:
                ops[i] = ["DROP"]
            else:
                ops[i] = [step_toward(units[i], to_shed(units[i]))]

    # Fetch wheat for feeding and geese for placing; both are shed pickups.
    def dispatch_pickup(item, amount, max_units):
        sent = 0
        while sent < max_units and amount > 0:
            cand = [i for i in range(n_units) if ops[i] is None]
            if not cand:
                return
            j = min(cand, key=lambda i: dist(units[i], to_shed(units[i])))
            take = min(amount, 10)
            if tuple(units[j]) in access_set:
                ops[j] = ["PICKUP", item, take]
            else:
                ops[j] = [step_toward(units[j], to_shed(units[j]))]
            amount -= take
            sent += 1

    n_unfed = sum(1 for t in tasks if t["op"][0] == "FEED")
    carried_wheat = sum(invs[i].get("WHEAT", 0) for i in range(n_units))
    wheat_gap = min(max(0, n_unfed - carried_wheat), shed.get("WHEAT", 0))
    if wheat_gap > 0:
        dispatch_pickup("WHEAT", wheat_gap, 2)

    placeable = min(geese_in_shed, len(free_coops))
    goose_gap = max(0, placeable - carried_geese)
    if goose_gap > 0:
        dispatch_pickup("GOOSE", goose_gap, 2)

    # Greedy over value per action: a job worth V that costs d steps to walk to
    # plus one step to perform yields V / (1 + d) per action spent.
    pairs = []
    for i in range(n_units):
        if ops[i] is not None:
            continue
        for k, t in enumerate(tasks):
            if t["need"] and invs[i].get(t["need"], 0) <= 0:
                continue
            pairs.append((-t["v"] / (1.0 + dist(units[i], t["pos"])), i, k))
    pairs.sort()

    seed_left = dict(seeds)
    used_unit, used_task = set(), set()
    for _, i, k in pairs:
        if i in used_unit or k in used_task:
            continue
        t = tasks[k]
        crop = t["crop"]
        if crop is not None:
            if seed_left.get(crop, 0) <= 0:
                continue
            seed_left[crop] -= 1
        used_unit.add(i)
        used_task.add(k)
        if tuple(units[i]) == tuple(t["pos"]):
            ops[i] = t["op"]
        else:
            ops[i] = [step_toward(units[i], t["pos"])]

    for i in range(n_units):
        if ops[i] is None:
            ops[i] = ["PASS"]

    return {"farmer": ops[0], "hands": ops[1:], "market": orders}


# Defined last on purpose. The kaggle-environments loader resolves a file agent
# by taking the last callable defined in the module, so this must come after
# _plan for the crash guard below to be the entry point that actually runs.
def agent(obs, config=None):
    """Entry point. Never raise: a crash forfeits the episode, so fall back to
    doing nothing for this turn and keep playing."""
    try:
        return _plan(obs, config)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return {"farmer": ["PASS"], "hands": [], "market": []}
