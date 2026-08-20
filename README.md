# Kaggriculture agent

Agent for the Kaggle [Kaggriculture](https://www.kaggle.com/competitions/kaggriculture)
simulation competition. Two players each farm a 10x10 board over a 30-day
season (720 turns) and compete to bank the most coins, selling into a shared
market whose prices react to every sale.

Everything lives in `main.py`, which exposes `agent(obs, config)`.

## Strategy

The plan falls out of the market's revenue curves. Selling `N` units of a
product earns `sum(price(k)) for k in 0..N-1`, and each product saturates at a
very different point:

Selling pushes a price down, but the town drains inventory every turn and pulls
it back up. Only the net matters, and **town demand is the bigger term**:

| Product | Shops | Town drain/season | Base | Trades at |
| --- | --- | --- | --- | --- |
| Wheat | 5 | ~635 | $25 | $35–51 |
| Strawberry | 4 | ~536 | $120 | ~$240 |
| Milk | 3 | ~437 | $160 | $210–280 |
| Wool | 1 (×2) | ~338 | $200 | ~$245 |
| Egg | 2 | ~338 | $50 | ~$50 |
| **Melon** | **0** | **~140** | $250 | falls as you sell |
| Fertilizer | 0 | 0 | $100 | free from livestock |

Melon is the only product **no shop demands** — the town centre alone touches
it — so it is the one market a farm can genuinely flood. Its tile budget is
therefore capped, not maximised.

The farm grows **melons** up to that cap, **wheat** as filler that doubles as
animal feed, and runs **8 cows + 6 sheep** on pasture for the premium goods.
Cows and sheep beat geese decisively: per animal-day a cow returns ~$375 and a
sheep ~$326 against a goose's ~$120, for the same ~4 actions of feeding, care
and collection.

**Strawberry is deliberately zero**, and that is a known gap — see below.

Two details drive most of the score:

- **Fertilizer is free.** The interpreter sets `fertilizer_available` on every
  surviving animal at each end-of-day refresh, regardless of `CARE`. One
  `COLLECT_FERTILIZER` action per animal per day is among the best-paying
  actions on the farm.
- **Hands are cheap, up to a point.** The n-th hire of a day costs `fib(n)`, so
  ten hands cost $143 for 240 extra actions — but a 15th costs $610/day and an
  18th $2584. The agent staffs to the available work and refuses to pay past a
  cost cap.

Labour is allocated by **marginal value**, not a fixed priority order. Every
possible job is priced in coins (watering a melon inside its bonus window is
worth ~$250; watering wheat ~$45) and each unit takes the job with the best
value per action, counting the walk to reach it. Deadline jobs — feeding a
goose, saving a plant that dies tonight — gain urgency as the day runs out so
they are never crowded out by richer but deferrable work.

## Results

Measured over a fixed seed set with the default configuration:

| Opponent | Record | Our mean score |
| --- | --- | --- |
| `starter` | 12W–0L | $100,706 (range $91.9k–$105.2k) |
| public trace agent | **0W–6L** | $63,397 vs their **$169,131** |
| itself (self-play) | symmetric | ~$51k each |

The trace agent is the honest benchmark: a public notebook replaying a strong
submission's recorded actions. We lose to it every time. Beating `starter` by
30x means little; the real bar is ~$170k.

Self-play scores roughly halve because both farms drain the same market, which
is the realistic ladder condition.

Tuning notes worth keeping, since several were counter-intuitive:

- Goose target: 8 birds beat 12 (+$4.6k), 16 (+$14k) and 20. More geese eat
  labour that melons pay better for, and eggs/fertilizer saturate.
- Cows + sheep instead of geese: +$7.5k. Milk and wool have shop demand behind
  them and trade above base; eggs do not.
- Strawberry costs $45k+ at every allocation tried (10, 22 and 34 tiles), even
  though its market is four times deeper than melon's. Its $100 seed and
  17-day, 4-unit cycle starve the farm of hands and feed. The top agent runs
  ~39 strawberry tiles successfully, so this is an execution gap, not a
  strategy one.
- Planting into the final days *loses* money — those actions are worth more
  spent harvesting and collecting fertilizer.
- An over-generous crew formula cost ~70% of the score by running into the
  expensive tail of the Fibonacci hire sequence.

## Testing locally

```bash
pip install -U kaggle-environments

python -c "
from kaggle_environments import make
env = make('kaggriculture', configuration={'episodeSteps': 720}, debug=True)
env.run(['main.py', 'starter'])
print([s['reward'] for s in env.steps[-1]])
"
```

Runtime is ~0.5 ms per turn (max 1.6 ms) against the environment's 1-second
`actTimeout`. All configurations in the robustness sweep win except a
`turnsPerDay=6` season, where days are too short to walk to livestock and back;
the competition fixes `turnsPerDay` at 24, so this is not tuned for.

`agent` is deliberately the **last** callable defined in
`main.py`: the kaggle-environments loader resolves a file agent by taking the
last callable in the module, so defining it last is what makes its crash guard
the entry point that actually runs.

## Submitting

```bash
kaggle competitions submit kaggriculture -f main.py -m "melon + goose portfolio"
```
