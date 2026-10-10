# Forecast backtest — net_sales

Folds: 7
Selection: seasonal-naive, ets, chronos-2-univariate, winner:chronos-2

## Mean metrics


If the warehouse lacks revision snapshots, backtests use today's values for past days; maturity masking reduces that bias.

## Mean by engine

- chronos-2: wql=12890.127132 mase=0.795983 coverage_80=0.744914 n=7
- ets: wql=16934.41173 mase=1.002966 coverage_80=0.887743 n=7
- seasonal_naive: wql=17336.857072 mase=1.11351 coverage_80=0.816329 n=7

Winner (lowest WQL): **chronos-2**

Scored path is univariate (no calendar or metric covariates). Calendar known-future features were not part of this bake-off.
