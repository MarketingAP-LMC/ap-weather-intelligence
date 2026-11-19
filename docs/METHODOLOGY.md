# Methodology

This document explains how the platform turns official weather data into crop-specific risk levels. Every number used by the engine lives in `config/crops.json` (crop rules) and `config/settings.json` (alerts, notifications, schedule) and can be changed without touching code.

## 1. Monitored crops, states and counties

Crops: soybeans, corn, canola, sunflowers, sugarcane. States: OK, ND, SD, KS, NE, MN, IA, IL, IN, OH, LA, FL. No livestock, pasture or animal monitoring.

**County selection** (`tools/build_reference.py`, output `config/counties.json`, full acreage table in `reference/cdl_2025_county_acreage.csv`):

1. County acreage of each crop comes from the **USDA NASS Cropland Data Layer (CDL) 2025** county statistics (CropScape). Double-crop classes count toward each crop they include (e.g. "Dbl Crop WinWht/Soybeans" counts as soybeans).
2. For each state and crop, counties are ranked by acreage and kept until they cover **80%** of the state's CDL acreage for that crop, provided the county has at least the minimum acreage (corn and soybeans 5,000 ac; sunflowers 2,000 ac; canola and sugarcane 1,000 ac).
3. The union across crops gives **574 monitored counties** (OK 25, ND 46, SD 49, KS 73, NE 61, MN 49, IA 74, IL 68, IN 60, OH 42, LA 20, FL 7).
4. Weather is sampled at each county's **U.S. Census internal point** (2024 Gazetteer); each point is mapped once to its NWS forecast grid cell and nearest observing station.

Counties are not treated as uniform with their state: every county gets its own forecast, outlook values, drought statistics and observed precipitation. Within-county variation (low spots, river bottoms) is not resolved; extension sources note low-lying field areas can run several degrees colder than nearby stations.

## 2. Data and how it is labelled

| Label used in reports | Meaning | Sources |
|---|---|---|
| **Observed** | measured or analysed from measurements | NWS station observations; PRISM county means via NOAA RCC-ACIS (latest day provisional); SPC preliminary storm reports (unverified); U.S. Drought Monitor (weekly expert assessment); USDA NASS Crop Progress (weekly survey) |
| **Official warning** | NWS watches, warnings, advisories (CAP) | api.weather.gov `/alerts/active` |
| **Forecast** | deterministic NWS / WPC forecast | NWS gridded forecast (NDFD) per county point: hourly temperature, QPF, snow, ice, gusts, PoP, thunder (to ~7 days); WPC 72-hour and 7-day QPF |
| **Outlook (model-based, probabilistic)** | probabilities or risk categories, not point forecasts | SPC Day 1-3 convective outlooks; WPC Excessive Rainfall Outlook Days 1-3; CPC 6-10 and 8-14 day outlooks |

Every report lists each source, its product issue/valid time and when it was last retrieved.

## 3. County metrics

From the NWS gridpoint data the engine derives, for the next 24 h, 72 h and 7 days: lowest/highest temperature (with timing), hours and longest continuous run at or below 36, 32, 30, 28, 26, 25, 24, 22, 20, 15, 10, 5 and 0 F (and lead time to the first such hour), hours and days at or above 86, 90, 95 and 100 F, rainfall, snowfall and ice totals (6-hourly amounts prorated to the window), maximum gusts (and lead time to 40/50/58 mph), maximum PoP and thunder probability, plus a daily table on Central-time calendar days. SPC, WPC and CPC polygons are intersected with the county point. Drought Monitor percentages (D0-D4, cumulative) and the Drought Severity and Coverage Index (DSCI = D0+D1+D2+D3+D4, 0-500) are added with 1-week and 4-week changes, and PRISM observed precipitation for the last 1/7/14 days.

## 4. Crop stage and exposure ("never assume every field is at the same stage")

For each state and crop the engine estimates a **distribution over phases**, for example *North Dakota corn: 11% mature but unharvested, 89% harvested*.

1. **USDA NASS Crop Progress** (weekly, state level) is used when the crop and state are in the latest report (corn, soybeans, sunflowers). Cumulative milestone percentages (planted, emerged, silking, dough, dented, mature, harvested...) become phase shares.
2. Otherwise a **typical calendar** is used: USDA NASS *Field Crops Usual Planting and Harvesting Dates* (Oct 2010; "begin" = 5%, "most active" = 15-85%, "end" = 95%) with typical development offsets after planting. Canola in ND/MN/SD/KS uses NDSU and K-State planting guidance; Oklahoma and Kansas canola is **winter canola** (fall establishment, winter dormancy, spring bolting/flowering, May-June harvest). Sugarcane uses the harvest season (LA Oct 5-Dec 23 per NASS, extended to Jan 10 to reflect the recent ~4-month milling seasons noted by LSU AgCenter; FL Oct 7-Apr 15): the standing share declines through the season. Calendar estimates are labelled as such and lower the confidence rating.

The **exposure index** (0-1) for a crop and hazard is the sum of phase shares times the crop's vulnerability weight for that hazard in each phase (e.g. sugarcane freeze: standing cane 1.0, harvested stubble 0.3). It answers "how much of this crop is currently in a stage where this hazard matters".

## 5. Risk classification

Levels: **CRITICAL**, **HIGH**, **MODERATE**, **INFORMATIONAL** (definitions as specified by American Prime).

**Step 1 - weather intensity for the crop.** Each crop/hazard rule lists levels with conditions on county metrics; the highest level whose conditions are all met applies. Key thresholds (full list and citations in `config/crops.json`):

| Crop | Hazard | MODERATE | HIGH | CRITICAL | Evidence |
|---|---|---|---|---|---|
| Corn | Freeze | low <= 32 F | low <= 28 F | >= 2 h at <= 28 F within 48 h | Purdue (Nielsen), Iowa State, UNL, NDSU |
| Corn | Heat | 1 day >= 95 F | 4 days >= 95 F | 3 days >= 100 F | Iowa State, Purdue, UNL, Ohio State |
| Corn/soy | Heavy rain | 72-h rain >= 2 in or ERO Slight | >= 3 in or ERO Moderate | ERO High | WPC ERO; ponding survival from Purdue, OSU, UNL, K-State |
| Soybeans | Freeze | low <= 32 F | low <= 28 F or >= 4 h <= 30 F | >= 2 h <= 28 F within 48 h | Iowa State, UNL, NDSU |
| Sunflowers | Freeze | low <= 30 F | low <= 25 F | >= 2 h <= 24 F within 48 h | NDSU A1995, UNL |
| Spring canola | Freeze | low <= 32 F | low <= 28 F | low <= 22 F within 48 h | NDSU A686, Canola Council |
| Winter canola | Freeze (spring) | low <= 28 F | low <= 25 F | >= 2 h <= 22 F within 48 h | Great Plains Canola Handbook |
| Winter canola | Extreme cold (winterkill) | low <= 10 F | >= 24 h <= 10 F or low <= 0 F | - | Secchi et al. 2021 (K-State), OSU, K-State |
| Sugarcane | Freeze | low <= 32 F (LSU category 1) | low <= 25 F (category 2) | low <= 22 F within 48 h (category 3) | LSU AgCenter; Edme & Glaz 2013 (FL) |
| All | Severe storms | SPC Slight or >= 15% hail/wind | SPC Enhanced or >= 30% | SPC Moderate/High | SPC; hail/wind loss tables (UNL, ISU, K-State) |
| All | Drought | >= 50% of county D2+ (or D1+ worsening 20 DSCI pts) | >= 50% D3+ | - | U.S. Drought Monitor; stage sensitivity from extension |

**Step 2 - exposure adjustment.** Exposure < 0.02: no assessment. Exposure < 0.05: level capped at INFORMATIONAL. Exposure < 0.25: level lowered one step. The reason is written into the explanation.

**Step 3 - forecast lead time.** Conditions expected more than 72 hours away are capped at MODERATE ("developing conditions"); confidence is rated high (<= 48 h), medium (<= 120 h) or low.

**Explanations.** Every assessment records the weather values that met the rule, the exposure and its basis, each adjustment applied, and the first unmet condition for the next level (e.g. "Not CRITICAL: requires temperatures at or below 22 F within 48 h (first expected in about 64 h)").

**Official NWS alerts** over monitored counties start from a base level per event type (`settings.json -> alerts.event_rules`): warnings CRITICAL, watches HIGH, advisories MODERATE, statements INFORMATIONAL. The level is lowered when no monitored crop in those counties is in a stage vulnerable to the hazard (exposure < 0.05: INFORMATIONAL; < 0.25: one step lower), never below a per-event floor - Tornado, Flash Flood, Extreme Wind, Hurricane and Storm Surge warnings stay at least HIGH because they threaten farm infrastructure and field operations even after harvest. Severe Thunderstorm Warnings with destructive criteria (hail >= 1.75 in or wind >= 70 mph) are at least HIGH.

## 6. Events, change detection and notifications

Assessments are grouped into **events** (one hazard in one state). An event keeps the same key while it is detected in consecutive forecast cycles and ends after 2 cycles without detection.

Each forecast cycle (every 3 h) is compared with the previous cycle and with the cycle ~24 h earlier: new events, level changes, counties added/removed, timing shifts and changes in the key measure (coldest low, rain total, SPC category, gusts, snow, drought coverage).

**Immediate emails** go out for CRITICAL/HIGH events and official alerts when they are new, upgraded, or materially changed: area grew by >= 25% (and >= 2 counties), timing moved >= 12 h earlier, or the key measure worsened beyond a tolerance (freeze 3 F, heat 3 F, rain 0.5 in, SPC one category, gusts 10 mph, snow 2 in, drought 15 points). Material-change follow-ups wait at least 6 h after the previous notice; upgrades are sent at once. Official alerts are de-duplicated by their VTEC event (office + phenomenon + significance + event number), so NWS updates of the same warning do not create new emails unless the level rises or the area expands. All new items from one run are combined into **one email**, so a busy severe-weather afternoon does not flood the inbox. Every email has a stable de-duplication key and is retried on later runs if delivery fails.

## 7. Content opportunities

Generated with the daily report from active MODERATE-or-higher events, using only facts the platform holds (NWS forecast values with cycle time, CDL acreage shares, NASS stage or calendar estimate). Headlines are factual templates ("Louisiana sugarcane: forecast lows near 24 F - what growers are watching"); captions state that conditions are forecasts and not observed damage. Format rule: HIGH/CRITICAL events within 72 h -> Reel (AP Weather Watch format); drought/outlook topics -> carousel; harvest/field-work items -> Story; otherwise educational post. Hashtags: one state tag (unverified, marked as such), one crop tag, one topic tag; no global tags.

## 8. Standards and known limitations

- Potential impacts are never presented as confirmed damage; storm reports are labelled preliminary.
- No agronomic prescriptions: the "what to monitor" items come from extension guidance and describe observations, not treatments.
- Gaps documented in the evidence review: no UF/IFAS sugarcane freeze-category table could be obtained (LSU categories and a Florida field study are used); winter canola has no single verified lethal temperature and snow cover is not measured; wind-speed and hail-size thresholds are not given by extension sources (SPC categories and NWS gust thresholds are used instead); sunflower and soybean heat thresholds are weakly supported and capped at MODERATE/HIGH.
- Crop stage is state-level (NASS) or calendar-based; individual fields differ. NASS weekly reports stop in late November and resume in spring; the calendar fallback is used in between.
- The NWS forecast is sampled at one point per county.