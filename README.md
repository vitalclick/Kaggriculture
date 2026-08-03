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

| Product | 100 units | 300 units | Character |
| --- | --- | --- | --- |
| Melon | $21.7k | $26.6k | Huge value, but crashes to the $1 floor past ~150 |
| Fertilizer | $9.0k | $21.0k | Free byproduct of livestock |
| Egg | $4.4k | $12.6k | Very flat curve, large capacity |
| Wheat | $2.2k | $6.3k | Effectively unlimited; also animal feed |
| Strawberry / milk / wool | — | — | Collapse inside ~50 units |

So the farm grows **melons** for raw value, keeps a small flock of **geese**
for eggs and fertilizer, and fills the remaining tiles with **wheat**, which
doubles as feed. Strawberry, tomato, milk and wool are never planted — their
markets are too thin to repay a tile.

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
| `starter` | 12W–0L | $78,916 (range $75.7k–$81.6k) |
| `random` | 8W–0L | $78,605 |
| itself (self-play) | symmetric | ~$37k each |

Self-play scores roughly halve because both farms drain the same market, which
is the realistic ladder condition.

Tuning notes worth keeping, since several were counter-intuitive:

- Goose target: 8 birds beat 12 (+$4.6k), 16 (+$14k) and 20. More geese eat
  labour that melons pay better for, and eggs/fertilizer saturate.
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
`actTimeout`. `agent` is deliberately the **last** callable defined in
`main.py`: the kaggle-environments loader resolves a file agent by taking the
last callable in the module, so defining it last is what makes its crash guard
the entry point that actually runs.

## Submitting

```bash
kaggle competitions submit kaggriculture -f main.py -m "melon + goose portfolio"
```
