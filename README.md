# Do people actually care what an LLM costs?

When a model gets cheaper, do developers use it more? And when a free version
shows up, does it steal paid users or bring in new ones?

I'm answering this with public usage data from [OpenRouter](https://openrouter.ai),
a platform developers use to call hundreds of LLMs through one API.

> **Status (Nov 2026):** data is collecting every day. Real results are coming in
> January 2027. The figures below come from a *synthetic* test, not real data.

## The twist

If you compare *different* models, demand looks like it barely reacts to price.
But that comparison is misleading: better models cost more **and** get used more,
so price and quality get tangled together.

The fix is to watch the *same* model before and after its price changes. That's
what this project does.

## Three questions

1. **Across models:** how much does usage move with price?
2. **Same model, new price:** when a model gets cheaper, how much more is it used?
3. **Free versions:** when a `:free` variant appears or disappears, what happens to the paid one?

## Data

- **Usage:** daily tokens for the top 50 models, Jan 2025 to today (OpenRouter Data API).
- **Prices:** OpenRouter only shows *today's* prices, so a GitHub Action saves a
  snapshot every day. Older price changes are filled in by hand in `data/manual/`.

So far: 676 days of usage, and 445 different models have passed through the top 50.

## Method, in four steps

| Step | What I compare | Why |
|---|---|---|
| 1 | Different models on the same day | The naive answer |
| 2 | The same model over time (model fixed effects) | Removes quality |
| 3 | Price cuts vs. similar models that didn't change (event study / DiD) | Cleanest estimate |
| 4 | Before vs. after a free version is added or removed | Substitution or new users? |

Standard errors are clustered by model.

## Does the method work? A test with known answers

Before trusting it on real data, I generated fake data where the true elasticity
is **−1.2** and quality drives both price and usage. The pipeline recovers it:

| Approach | Estimate |
|---|---|
| Across models | −0.19 (fooled by quality) |
| Within model | −1.18 |
| Price-cut event study | −1.19 |

![cross vs within](docs/demo_fig1_cross_vs_within.png)
*Synthetic data with a planted elasticity of −1.2, not OpenRouter results.*

![event study](docs/demo_fig2_price_event_study.png)
*Synthetic data: 13 simulated price cuts.*

## Results on real data

Coming January 2027.

## Caveats

- **Developers only.** OpenRouter doesn't include ChatGPT-style consumer apps.
- **Top 50 only.** A model disappears from the data when it drops out of the top 50.
- **Tokens differ by provider.** Each provider uses its own tokenizer.
- **Price cuts often come with announcements**, which can make the effect look bigger.
- **Few free-version events**, so question 3 will be case studies, not strong claims.

## Run it yourself

```bash
pip install -r requirements.txt
make demo        # full pipeline on synthetic data, no API key needed
make test
```

With an OpenRouter key: `python -m src.collect backfill`, then `python -m src.build_panel`
and `python -m src.run_analysis`.

## Credits

Usage data: *Source: OpenRouter (openrouter.ai/rankings)*, licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Built with help from Claude, which drafted the initial pipeline; data work,
analysis, and interpretation are mine.
Code: MIT License.
