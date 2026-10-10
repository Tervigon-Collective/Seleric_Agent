# Forecast backtest — orders

Folds: 7
Selection: seasonal-naive, ets, chronos-2-univariate, winner:chronos-2

## Mean metrics


If the warehouse lacks revision snapshots, backtests use today's values for past days; maturity masking reduces that bias.

## Mean by engine

- chronos-2: wql=6.627659 mase=0.85046 coverage_80=0.806129 n=7
- ets: wql=8.509309 mase=1.060277 coverage_80=0.846929 n=7
- seasonal_naive: wql=8.646952 mase=1.168637 coverage_80=0.785714 n=7

Winner (lowest WQL): **chronos-2**

Scored path is univariate (no calendar or metric covariates). Calendar known-future features were not part of this bake-off.
