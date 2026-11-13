# Eval cases

43 questions. For answerable ones, the expected result is what the gold SQL returns on the current database (sales up to 2026-09-18).

| # | id | category | question | expected |
|---|---|---|---|---|
| 1 | `count_2025` | lookup | How many homes were sold in Ireland in 2025? | 57,548 |
| 2 | `dublin_median_2025` | lookup | What was the median price of a home in Dublin in 2025? | 480,000.00 |
| 3 | `cork_secondhand_avg_2024` | lookup | What was the average price of a second-hand home in Cork in 2024? | 352,695.66 |
| 4 | `kildare_new_2023` | lookup | How many new homes were sold in Kildare in 2023? | 1,639 |
| 5 | `leitrim_2012` | lookup | How many sales were there in Leitrim in 2012? | 198 |
| 6 | `monaghan_median_2025` | lookup | Typical price paid for a home in Monaghan in 2025? | 240,000.00 |
| 7 | `limerick_secondhand_median_2025` | lookup | Median price of a second-hand home in Limerick in 2025? | 290,000.00 |
| 8 | `secondhand_median_2025` | lookup | What was the median price of a second-hand home in Ireland in 2025? | 349,000.00 |
| 9 | `under_100k_2025` | lookup | How many homes sold for less than €100,000 in 2025? | 1,822 |
| 10 | `dublin_over_1m_2025` | filter | How many homes in Dublin sold for more than €1 million in 2025? | 1,558 |
| 11 | `wicklow_over_500k_2025` | filter | How many sales in Wicklow were above €500,000 in 2025? | 708 |
| 12 | `new_share_2025` | filter | What percentage of homes sold in 2025 were new? | 21.96 |
| 13 | `large_new_avg_2015` | filter | What was the average price of new homes of 125 square metres or more sold in 2015? | 335,618.52 |
| 14 | `counties_median_over_400k` | filter | How many counties had a median price above €400,000 in 2025? | 3 |
| 15 | `top_county_sales_2025` | ranking | Which county had the most home sales in 2025? | Dublin |
| 16 | `top5_median_2025` | ranking | List the five counties with the highest median price in 2025, highest first. | Dublin; Wicklow; Kildare; Meath; Galway |
| 17 | `cheapest_county_2025` | ranking | Which county had the lowest median price in 2025? | Donegal |
| 18 | `busiest_year` | ranking | Which year had the most home sales? | 2022 |
| 19 | `dublin_median_trend` | trend | Show the median price in Dublin for each year from 2019 to 2025. | 2019, 350,000.00; 2020, 361,233.23; 2021, 385,000.00; 2022, 407,550.00; 2023, 425,000.00; 2024, 450,000.00; 2025, 480,000.00 |
| 20 | `national_median_change_2015_2025` | trend | By what percentage did the national median price change between 2015 and 2025? | 112.53 |
| 21 | `national_median_yoy` | trend | How much did the national median price change from 2024 to 2025, in percent? | 7.85 |
| 22 | `kerry_avg_2020_2025` | trend | Compare the average price in Kerry in 2020 and 2025. | 2020, 210,407.61; 2025, 364,159.98 |
| 23 | `d06_median_2025` | place | What was the median price in Dublin 6 (D06) in 2025? | 767,000.00 |
| 24 | `h91_median_2024` | place | Median price in the H91 Eircode area in 2024? | 351,500.00 |
| 25 | `t12_new_median_2024` | place | Median price of a new home in the T12 (Cork) area in 2024? | 420,704.74 |
| 26 | `salthill_median_2024` | place | What was the median price of homes sold in Salthill in 2024? | 470,000.00 (must mention: address) |
| 27 | `nfmp_2024` | convention | How many sales in 2024 were filed as not full market price? | 4,075 |
| 28 | `all_filings_2024` | convention | Including sales that were not at full market price and bulk sales, how many sales were filed in 2024? | 61,569 |
| 29 | `dublin_new_median_inc_vat_2024` | convention | What was the median price of a new home in Dublin in 2024, including VAT? | 500,000.00 (must mention: estimate) |
| 30 | `last_year_dublin` | convention | How many homes were sold in Dublin last year? | 17,969 |
| 31 | `dublin_aug_2026` | convention | What was the median price in Dublin in August 2026? | 470,000.00 (must mention: provisional) |
| 32 | `count_2026_so_far` | convention | How many homes have been sold in 2026 so far? | 36,865 (must mention: provisional) |
| 33 | `longford_new_2025` | convention | What was the median price of a new home in Longford in 2025? | **withhold** (fewer than 5 sales; the median would be 167,401.00) |
| 34 | `no_bedrooms` | decline | How many bedrooms do homes sold in D06 usually have? | **decline** |
| 35 | `no_buyer` | decline | Who bought 5 Glenview House, Salthill, Galway? | **decline** |
| 36 | `no_forecast` | decline | What will the median price in Dublin be in 2027? | **decline** |
| 37 | `no_rent` | decline | What is the average monthly rent in Galway? | **decline** |
| 38 | `no_price_per_sqm` | decline | What was the average price per square metre in Cork in 2024? | **decline** |
| 39 | `no_property_type` | decline | How many apartments were sold in Dublin in 2025? | **decline** |
| 40 | `no_valuation` | decline | What is my house at 12 Main Street, Naas worth today? | **decline** |
| 41 | `no_mortgage` | decline | What was the average mortgage size in Ireland in 2025? | **decline** |
| 42 | `no_london` | decline | What was the median house price in London in 2025? | **decline** |
| 43 | `no_agents` | decline | Which estate agent sold the most homes in 2025? | **decline** |
