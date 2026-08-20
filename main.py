"""Kaggriculture competition agent.

Strategy is driven by the market, but the market has two forces, not one.
Selling N units earns sum(price(k)) and pushes the price down; meanwhile the
town drains inventory every turn and pulls it back up. Only the net matters,
and the town is by far the bigger term:

    product      shops   town drain / season   base
    wheat          5              ~635          $25
    strawberry     4              ~536         $120
    carrot/milk    2-3            ~437     $35/$160
    tomato/egg     2              ~338      $60/$50
    wool           1 (x2)         ~338         $200
    MELON          0              ~140         $250

Melon is the one product no shop demands -- only the town centre touches it --
so it is the one market a farm can genuinely flood. That is why its tile budget
is capped rather than maximised: past roughly 20 tiles the extra melons sell
into a price this farm itself has crushed.

The plan is therefore melons up to that cap, wheat as filler that doubles as
animal feed, and pasture livestock for the premium goods. Cows and sheep beat
geese by a wide margin: milk and wool have real shop demand behind them and
trade *above* base all season (~$210-280 milk, ~$240 wool), while eggs sit near
$50. Per animal-day a cow returns ~$375 and a sheep ~$326 against a goose's
~$120, for the same ~4 actions of feeding, care and collection.

Every surviving animal also has `fertilizer_available` set at each end-of-day
refresh regardless of CARE, so each one yields a free fertilizer daily on top
of its product. A goose that misses two consecutive feeds escapes and takes its
purchase price with it, so feeding is never allowed to slip.

Strawberry should work on paper: four shops demand it, ~536 units of drain a
season, and it trades near $240. It is nonetheless set to zero, and the reason
is measured rather than assumed. Instrumenting the tiles showed 83% of them
turning to weeds and yielding 0.83 units against a theoretical 4, because an
ongoing crop needs watering every day for seventeen days while a one-time crop
tolerates gaps. This planner cannot keep that many tiles alive alongside the
livestock, and every allocation tried -- 10, 18, 22, 34 tiles -- cost $25k or
more against simply growing wheat there. The code path is kept and correct; the
tile budget is the thing set to zero.

Labour is allocated by marginal value rather than a fixed priority order. Every
possible job is priced in coins -- watering a melon inside its bonus window is
worth ~$250, watering wheat ~$45 -- and each unit takes the job with the best
value per action, with travel weighted by TRAVEL_PENALTY so hands work a
neighbourhood instead of chasing the richest job on the map. Deadline jobs
(feeding, saving a plant that dies tonight) gain urgency as the day runs out so
they are never crowded out by richer but deferrable work.

Assigning each unit its own region of the farm was tried as an alternative and
is decisively worse -- per-turn clustering, a fixed grid, and clusters frozen
for a day all landed $53k-$68k below this. Work here is bursty and unevenly
spread: a fixed patch idles a hand whose ground is quiet while a block ripening
next door goes unpicked, and every unit still has to cross to the shed to drop
and sell. Weighting travel gets the locality -- 59% of actions are movement,
down from 64% -- without giving up the crew's ability to swarm.
"""
    try:
        return _plan(obs, config)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return {"farmer": ["PASS"], "hands": [], "market": []}
