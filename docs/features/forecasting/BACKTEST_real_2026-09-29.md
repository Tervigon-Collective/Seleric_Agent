# Forecast backtest (2026-09-29)

| metric | h | backend | n | sMAPE% | MAE | MASE | coverage | width | ms |
|---|---|---|---|---|---|---|---|---|---|
| metric.net_sales | 1 | holt | 49 | 63.2 | 66654.17 | 1.633 | 0.88 | 167443.9 | 14.4 |
| metric.net_sales | 1 | statsforecast | 49 | 82.4 | 68146.08 | 1.670 | 0.88 | 164817.2 | 33.2 |
| metric.net_sales | 7 | holt | 48 | 47.9 | 50052.95 | 1.235 | 0.98 | 440657.2 | 15.1 |
| metric.net_sales | 7 | statsforecast | 48 | 47.4 | 50860.27 | 1.255 | 0.83 | 216860.1 | 31.9 |
| metric.net_sales | 14 | holt | 47 | 52.1 | 56116.08 | 1.395 | 1.00 | 619570.2 | 14.1 |
| metric.net_sales | 14 | statsforecast | 47 | 51.2 | 56232.14 | 1.398 | 0.91 | 260908.5 | 30.6 |
| metric.orders | 1 | holt | 49 | 25.2 | 10.74 | 0.642 | 0.90 | 53.4 | 16.6 |
| metric.orders | 1 | statsforecast | 49 | 26.0 | 11.14 | 0.666 | 0.94 | 62.9 | 101.0 |
| metric.orders | 7 | holt | 48 | 45.6 | 24.64 | 1.481 | 0.92 | 141.0 | 17.3 |
| metric.orders | 7 | statsforecast | 48 | 45.1 | 24.89 | 1.495 | 0.85 | 112.9 | 98.9 |
| metric.orders | 14 | holt | 47 | 50.5 | 28.16 | 1.701 | 0.98 | 198.7 | 17.2 |
| metric.orders | 14 | statsforecast | 47 | 53.4 | 30.51 | 1.843 | 0.91 | 164.0 | 97.6 |
| metric.sessions | 1 | holt | 14 | 24.0 | 698.61 | 0.664 | 0.93 | 3631.7 | 11.7 |
| metric.sessions | 1 | statsforecast | 14 | 22.9 | 649.62 | 0.617 | 0.93 | 4017.4 | 50.2 |
| metric.sessions | 7 | holt | 13 | 40.4 | 1333.42 | 1.266 | 1.00 | 9641.7 | 12.0 |
| metric.sessions | 7 | statsforecast | 13 | 35.9 | 1127.06 | 1.070 | 0.85 | 6382.7 | 54.7 |
| metric.sessions | 14 | holt | 12 | 46.6 | 1794.92 | 1.705 | 1.00 | 13668.2 | 12.0 |
| metric.sessions | 14 | statsforecast | 12 | 41.4 | 1466.86 | 1.393 | 0.83 | 8494.6 | 41.9 |
| metric.spend | 1 | holt | 49 | 21.1 | 8824.42 | 0.539 | 0.92 | 44258.2 | 14.7 |
| metric.spend | 1 | statsforecast | 49 | 22.1 | 9178.97 | 0.561 | 0.92 | 41373.4 | 110.3 |
| metric.spend | 7 | holt | 48 | 37.5 | 19713.57 | 1.208 | 0.98 | 117052.6 | 14.6 |
| metric.spend | 7 | statsforecast | 48 | 37.3 | 19265.48 | 1.180 | 0.90 | 86572.8 | 110.4 |
| metric.spend | 14 | holt | 47 | 42.2 | 24378.77 | 1.496 | 0.98 | 165470.3 | 15.8 |
| metric.spend | 14 | statsforecast | 47 | 40.6 | 23004.02 | 1.412 | 0.91 | 118721.5 | 116.9 |

## Verdicts (vs holt)

- metric.net_sales / statsforecast: **keep baseline**
- metric.orders / statsforecast: **keep baseline**
- metric.sessions / statsforecast: **keep baseline**
- metric.spend / statsforecast: **keep baseline**
