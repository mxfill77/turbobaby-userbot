# Review dimension: restated ownership

Restarted after the 2026-09-22 out-of-memory crash (see `docs/t27-crash-recovery-2026-09-22.md`):
this axis was lost there. Findings are written here as they are made, not held in a session.

The corpus rule under review (`docs/t27-handover.md`, "A prompt for the next session"): *never
restate what another contract owns -- name its ID and consume its decision.* The resolved precedent
is `person_naming.t27` (handover section "### 2. One duplication, resolved 2026-09-22"): an edge
(`REUSES_EVENTS_BOOKING_ID`, `EVENTS_BOOKING_SUPPLIES`) plus the owner's verdict consumed as
booleans, and no bound of the owner repeated. That case is not re-reported; it was used to check
the detectors: detector (a) no longer fires there (`grep -n '^pub const HANDLE_M' person_naming.t27`
counts 0), and detector (b) groups `person_naming.t27:443` `OWNER_HANDLE_MAX_CHARS = 32` with
events-booking's `HANDLE_MAX_DECLARED = 32` -- cleared: that one is the bound of person-naming's
own owner module, which the resolved text keeps on purpose as a different surface.

## Method

All commands run from `specs/turbobaby/` of the bike-bot tree, read-only.

```sh
# (a) same const NAME in 2+ contracts, header and edge names removed -> 140 names
grep -oH '^pub const [A-Za-z_0-9]*' *.t27 | sed 's/:pub const / /' | sort -u | awk '{print $2}' \
  | sort | uniq -c | awk '$1>=2' \
  | grep -vE ' (ID|KIND|NAME|MEASURED_AT|SEED|VERSION|OWNER|SCOPE|TITLE|STATUS)$' | wc -l
# (b1) same one-line ARRAY value in 2+ contracts -> 7 values (one of them is [], trivial)
grep -oH '^pub const [A-Za-z_0-9]* : \[[0-9]*\][a-z0-9]* = \[.*\];' *.t27 | ...   # group by value
```

Of the 140 names in (a), the business-looking ones were read first (displacements, classes,
statuses, bands, HTTP codes, price-list-only roster, gates, windows, refusals); the corpus-census
names (`CORPUS_FILE_COUNT*`, `REUSE_EDGE_COUNT`, `WORLD`, `DECISIONS`, ...) are self-measurements
of each file and are listed under "Not covered here". Owners were taken from the edge prose of
the consuming file (it names who owns what) and from `scripts/verify_t27_against_source.py`
`BINDINGS` (`"spec"`/`"const"` keys, grepped, not imported).

The script behind the counts below is `D:/turbobaby-bot/tmp/t27_review_2309/own_detect.py`
(outside this tree: the permission layer refused writes into it). It reads all 44 contracts plus
`specs/agents/turbobaby.t27` (45 files, 5281 `pub const`, multi-line values joined), and writes
`own_a_names.txt`, `own_b_values.txt`, `own_c_prose.txt` next to itself.

```sh
python3 D:/turbobaby-bot/tmp/t27_review_2309/own_detect.py
# files 45 ids 45 consts 5281
# (a) names in 2+ files: same value 92 different value 49
# (b) value groups in 2+ files: 71 {'str': 12, 'arr': 7, 'num': 52}
# (c) prose-owns-but-declared hits: 0
```

Detector (b) keeps integers outside -10..10 and outside a round/standard set (24, 60, 100, 200,
255, 400, 404, 500, 1000, 3600, ...), strings of 12+ characters that are not contract IDs, paths
or dates, and arrays of 2+ elements. Detector (c) looks for "turbobaby/X owns ...", "X.t27 owns
...", "... owned by / belongs to turbobaby/X" and reports an UPPER_CASE name in that sentence
that the same file declares. Detector (d) is five greps for business values outside their owner:

```sh
grep -nE '^pub const [A-Z_]*FEE[A-Z_]* : [iu][0-9]+ = [1-9]' *.t27 | grep -v '^delivery_terms'              # 1: commerce CLIENT_AMOUNT_EXTRA_FEE = 3, a tag, not a fee
grep -nE '^pub const [A-Z_]*DEPOSIT[A-Z_]*_THB[A-Z_]* : [^=]*= [1-9]' *.t27 | grep -v '^deposit_tiers'      # 0
grep -nE '^pub const [A-Z_]*_BP : [^=]*= [1-9]' *.t27 | grep -v '^rental_terms'                           # 0
grep -nE '^pub const [A-Z_]*(UTC|OFFSET|TZ|BANGKOK)[A-Z_]* : [^=]*= (7|420|25200);' *.t27                # 1: the owner, market_profile
grep -lE '^pub const [A-Z_]* : \[[0-9]+\]str = \[.*"(pending|confirmed|cancelled|delivered)"' *.t27       # order_status + events_booking (own booking states)
```

## Totals

| detector | hits | read in context | findings from it | cleared |
| --- | --- | --- | --- | --- |
| (a) same name, 2+ contracts | 141 names (92 same value, 49 different) | 38 (every business-looking name) | F1, F2 | 33 |
| (b) same value, 2+ contracts | 71 groups (52 numbers, 12 strings, 7 arrays) | all 19 string/array groups; 14 number groups | F1 (2 rows), F3, F4, F5 | 28 |
| (c) prose says "owned elsewhere", file declares it | 0 | -- | -- | -- |
| (d) business owners | 3 non-owner hits | 3 | -- | 3 |

Findings: **5** (F1 is one finding over 9 copied vocabularies plus a snapshot block). The
precedent `person_naming.t27` fires only as a cleared value coincidence (see the introduction).

## Cleared, by category

| category | items |
| --- | --- |
| same name, different meaning | `STATUS_NAMES` (availability unit states vs order_status order states), `BAND_NAMES` (bike_catalog size bands vs rental_terms term bands), `GATE_NAMES`/`GATE_COUNT` (checkout gates vs identity gates vs validation gates), `WINDOW_HOURS` (1 vs 24), `STATUS_PENDING`/`STATUS_CONFIRMED` (referral_program rows vs order_status orders; referral names no order edge and has its own `STATUS_PAID`), `REFUSAL_NAMES` (events_booking vs order_money, disjoint lists), `BOOKING_STATE_NAMES` (event bookings, with `waitlisted`) |
| external standard or physical constant | `HTTP_OK`/`HTTP_NOT_FOUND`/`HTTP_BAD_REQUEST` (IANA codes each file's own route returns; the corpus already counts them in `CONTRACTS_DECLARING_AN_IANA_STATUS`), `HOURS_PER_DAY` 24 |
| equal value, documented as a different measurement | `locale_policy.PUBLISHED_LOCALES` vs `market_profile.TH_LANGUAGES` `["ru","en"]` -- the locale file says in prose which is which (what the app publishes vs what the market speaks) and names the other by ID and declaration |
| correct consumer shape | `locale_policy` `MIGRATION_COUNT_OWNER_ID`/`MIGRATION_COUNT_IS_A_COPY` (but see F5 for its owner), `LIMITER_VERDICT_INPUT` in client_errors and upload_media (a verdict consumed from the rate-limit contract), edge-declaration name strings (`REUSES_REQUEST_IDENTITY_ID` as a value) |
| mirror of a non-contract source, declared as such | migration paths (`HIDE_MIGRATION` and siblings, `migrations/085...`, `081...`), `"DECISIONS.md"` record fields, `FIRST_TICK_IS_DISCARDED` (two different sweeps in `src/main.rs`), the agent charter's `PRICE_AUTHORITY`/`PRICE_ON_SILENCE` (`KIND = "agent"`, DECISIONS.md D11), `deposit_tiers.CURRENCY = "THB"` (the denomination of its own tables, sourced from `DEPOSIT_AUTHORITY` KB_faq; borderline -- `turbobaby/market` owns `TH_CURRENCY_CODE` and deposit-tiers already has `REUSES_MARKET_PROFILE_ID`, so naming the owner there would cost one line) |
| self-measurement of the corpus | `CORPUS_FILE_COUNT*`, `CORPUS_FILES_OTHER_THAN_THIS_ONE*`, `REUSE_EDGE_COUNT`, `WORLD`, `WAVE_CENSUS_*`, numbers 42/43/44/45: each file measures the tree at its own date; not a decision of another contract |
| flags that record a non-restatement | `*_IS_RESTATED_HERE = false`, `STALENESS_INTERVAL_IS_PUBLISHED = false`, `HTTP_STATUS_NOT_OWNED_HERE`, `THIS_FILE_DECLARES_NO_HTTP_STATUS` |
| inverse of this axis: an ownership hole | `COPY_CHECK_FIGURE_OWNERSHIP_IS_CIRCULAR = true` in ai_assist and promo_broadcast: each file says the four copy-check figures are the other's, so no contract declares them. Both files record it and defer to the owner; promo_broadcast carries a marked copy of ai_assist's note. Not a restatement; listed so the hole is not lost |
| coincidental numbers | the remaining number groups (e.g. 990 = Mai Khao fee vs a rounding witness; 3000 = a cc write bound vs deposit tier 1; 1500/5000/600 = discount bp vs character caps and seconds) -- names show different quantities |

## Findings

### F1. ride_game.t27 carries pinned copies of five bike-catalog / deposit-tiers / availability decisions

| ride_game.t27 | owner copy | value | owner ID | edge in ride_game |
| --- | --- | --- | --- | --- |
| `ride_game.t27:129` `DISPLACEMENTS_CC` | `bike_catalog.t27:170` same name | `[125, 155, 300, 350, 400, 650, 750]` | `turbobaby/bike-catalog` | yes, `REUSES_CATALOG_ID` |
| `ride_game.t27:132-133` `DISPLACEMENT_MIN_CC`/`_MAX_CC` | `bike_catalog.t27:173-174` same names | `125`, `750` | `turbobaby/bike-catalog` | yes |
| `ride_game.t27:160-162,168` `CLASS_NAMES`, `CLASS_SCOOTER`, `CLASS_MOTORCYCLE`, `CLASS_NONE` | `bike_catalog.t27:121` `CLASSES`, `:122-123` `CLASS_*`, `:127` `CLASS_NONE` | `["scooter","motorcycle"]`, `0`, `1`, `255` | `turbobaby/bike-catalog` | yes |
| `ride_game.t27:250` `PRICE_LIST_ONLY_KEYS` | `deposit_tiers.t27:224` same name | 7 keys `pcx-150` .. `r7` | `turbobaby/deposit-tiers` | yes, `REUSES_DEPOSIT_TIERS_ID` |
| `ride_game.t27:239` `EXCLUDED_NOT_OFFERED` | `deposit_tiers.t27:168` `NOT_OFFERED_KEYS` | `["click-125"]` | `turbobaby/deposit-tiers` | yes |
| `ride_game.t27:242` `EXCLUDED_NONE_FREE` | `availability.t27:162` `OFFERED_OUT_ON_CONTRACT_KEYS` | `["forza-300","mt-03-300"]` | `turbobaby/availability` | yes, `RIDE_BOOKABILITY_OWNER_ID` |

Two more rows of the same finding, found by detector (b):

| ride_game.t27 | owner copy | value | owner ID | edge in ride_game |
| --- | --- | --- | --- | --- |
| `ride_game.t27:218` `CATALOG_KEYS` | `availability.t27:143` `FAMILY_KEYS` | the 14 in-stock family keys | `turbobaby/availability` | yes, `RIDE_BOOKABILITY_OWNER_ID` |
| `ride_game.t27:449` `CATALOG_API_OWNED_LIST_ENDPOINT` | `catalog_api.t27:108` `LIST_ENDPOINT` | `"GET /api/bikes"` | `turbobaby/catalog-api` | yes, `REUSES_CATALOG_API_ID` (line 447) |

On top of the copies, the section "What the owners declare" (`ride_game.t27:493` onward) carries
a third set -- `CATALOG_OWNED_*`, `AVAILABILITY_OWNED_*`, `DEPOSIT_OWNED_*` (first, last, length,
array name of each owner list; e.g. `AVAILABILITY_OWNED_FLEET_AVAILABLE = 26` against
`availability.t27:156` `FLEET_AVAILABLE`) -- "kept here only so an invariant has two sides to
compare". Both sides of that invariant live in `ride_game.t27`: when an owner changes, neither side
moves and the invariant stays green. It is a snapshot, not a check; no script reads it
(`grep -rn '_OWNED_' scripts/*.py` finds only an unrelated comment).

```sh
grep -n '^pub const \(DISPLACEMENTS_CC\|DISPLACEMENT_M..._CC\|CLASS_SCOOTER\|CLASS_MOTORCYCLE\|PRICE_LIST_ONLY_KEYS\) ' ride_game.t27 bike_catalog.t27 deposit_tiers.t27
grep -n 'REUSES_CATALOG_ID\|REUSES_DEPOSIT_TIERS_ID\|RIDE_BOOKABILITY_OWNER_ID' ride_game.t27 | head -3
grep -c '"DISPLACEMENTS_CC"\|"PRICE_LIST_ONLY_KEYS"\|"CLASS_NONE"' ../../scripts/verify_t27_against_source.py   # 0
```

Why the owners are the owners: `ride_game.t27` says so itself, in its edge block ("bike-catalog
owns the class algebra and the displacement domain", "deposit-tiers owns the price-list-only roster
and the single not-offered key", "availability owns ... the offered-but-out pair"), and gate 3 binds
`bike_catalog.CLASSES` to `src/api/bikes.rs`, not the ride-game copy.

This is the person_naming shape before its fix: the edge exists and the copy stays. The file knows
it: its comments call them "restated and not redesigned" and "A COPY ... Measured identical on
2026-09-20", and `SHARED_VOCABULARY_IMPORT_NOTE` argues that without a cross-file import "a shared
vocabulary can only be a pinned copy with a named owner". That is an honest record, but it is a
present-tense copy: nothing re-measures it. No gate catches drift -- gate 2 reads one file at a
time, and gate 3 binds none of the ride-game copies (the grep above counts 0 for the names).

**Verdict: finding** (deliberate, documented, un-gated). **Smallest fix**, in the precedent's form:
keep the edges; replace each copied vocabulary by what the file actually needs from it as an
abstract input -- `top_speed` already takes a bare `u16`, so the displacement list is only needed
by the file's own tests; the class algebra can arrive as the owner's discriminant passed in, and
the three key lists as "is this family price-list-only / not offered / none free" booleans from
their owners, as `person_naming` receives `handle_is_accepted`. If the copies must stay until an
import exists, the cheaper alternative is a gate-3 binding of each copy to the same seed field
the owner is bound to, so drift turns a gate red instead of staying silent.

### F2. deposit_tiers.t27 restates availability's price-list-only unit count

| where | name | value |
| --- | --- | --- |
| `deposit_tiers.t27:223` | `PRICE_LIST_ONLY_UNITS` | `0` |
| `availability.t27:190` | `PRICE_LIST_ONLY_UNITS` (beside `PRICE_LIST_ONLY_FAMILIES = 7`) | `0` |

```sh
grep -n '^pub const PRICE_LIST_ONLY_UNITS ' deposit_tiers.t27 availability.t27
sed -n '99,102p' deposit_tiers.t27    # REUSES_AVAILABILITY_ID, "the owner of fleet availability"
```

Owner: `turbobaby/availability` -- `deposit_tiers.t27`'s own header comment names it "the owner of
fleet availability" and says "this contract validates only the lengths and totals of its own
deposit tables". A unit count is fleet availability, not a deposit table. Edge: yes
(`REUSES_AVAILABILITY_ID`), copy kept -- the person_naming shape. No gate reads both files.

**Verdict: finding** (small). **Fix**: drop the declaration from deposit-tiers and state the
property it guards ("never read as available") as consuming availability's verdict, or reword the
comment to say the zero is availability's and not re-declared. The roster itself
(`PRICE_LIST_ONLY_KEYS`) stays in deposit-tiers: it is the owner of that list (see F1).

### F3. rental_terms.t27 declares its own copy of the D11 silence sentence

| where | name | value |
| --- | --- | --- |
| `rental_terms.t27:197` | `ON_SILENCE` | `"no number; a human quotes this price"` |
| `pricing_honesty.t27:165` | `MUST_SAY` (with `SAY_HUMAN_QUOTES`) | `"a human quotes this price"` |
| `specs/agents/turbobaby.t27:69` | `PRICE_ON_SILENCE` | `"no number; a human quotes this price"` (the agent charter, `KIND = "agent"`) |

```sh
grep -n 'ON_SILENCE\|REUSES_PRICING_ID' rental_terms.t27          # declared once, used nowhere else in the file
grep -n '^pub const MUST_SAY ' pricing_honesty.t27
grep -n 'OFF_LADDER_NOTE' delivery_terms.t27 | head -1            # the consuming shape
```

Owner: `turbobaby/pricing-honesty` -- it owns what an absent price renders as (`MUST_SAY`,
`SAY_HUMAN_QUOTES`, D11's `on_silence.must_say`), and `rental_terms.t27`'s own edge comment says
"pricing honesty owns the live door authority; rental terms neither imports nor redeclares that
fact". Edge: yes (`REUSES_PRICING_ID`), and the file still redeclares the silence rule in present
tense. The exact string equals the agent charter's `PRICE_ON_SILENCE`, i.e. it was copied from the
charter, not from the owner, and it already differs in wording from the owner's `MUST_SAY`. The
correct shape exists next door: `delivery_terms.t27` `OFF_LADDER_NOTE` says an off-ladder district
"yields FEE_ABSENT, which turbobaby/pricing-honesty renders as ...", declaring no sentence of its
own. No gate compares the two strings.

**Verdict: finding.** **Fix**: drop `ON_SILENCE` (nothing in the file reads it) or turn it into a
consumption note naming `turbobaby/pricing-honesty` and `MUST_SAY`, as delivery-terms does.
`BANDS_PRODUCE_CLIENT_QUOTES = false` is rental-terms' own decision and stays. The agent charter's
`PRICE_ON_SILENCE` is a mirror of `DECISIONS.md` D11 in a non-contract file and is not counted here.

### F4. The nmax-155 day rate 449 is declared as its own by two contracts

| where | name | value |
| --- | --- | --- |
| `pricing_honesty.t27:95` | `CHEAPEST_IN_STOCK_DAY_RATE_THB` ("nmax-155: the cheapest rate attached to a family that actually has units") | `449` |
| `rental_terms.t27:285` | `REF_BASE_THB_DAY` (with `REF_MODEL = "NMAX 155"`) | `449` |

```sh
grep -n '^pub const CHEAPEST_IN_STOCK_DAY_RATE_THB \|owns audit_daily_thb and REF_BASE_THB_DAY' pricing_honesty.t27
grep -n '^pub const REF_BASE_THB_DAY \|^pub const REUSES_PRICING_ID ' rental_terms.t27
grep -c '"const": "CHEAPEST_IN_STOCK_DAY_RATE_THB"\|"const": "REF_BASE_THB_DAY"' ../../scripts/verify_t27_against_source.py   # 0
```

Owner: a published day rate is pricing-honesty's (it owns the rate tags and the published-rate
census, `PUBLISHED_DAY_RATES`). Ownership here is split both ways, though: pricing-honesty's prose
says "turbobaby/rental-terms owns audit_daily_thb and REF_BASE_THB_DAY", and rental-terms' edge
comment says pricing honesty owns the door authority. Each file therefore declares the same rate of
the same family in present tense, and each names the other as owner of *its* copy. Edge:
rental-terms has `REUSES_PRICING_ID`; pricing-honesty names rental-terms in prose only. Neither
copy is bound by gate 3, so a tariff change can move one and leave the other.

**Verdict: finding** (ownership ambiguity, not an accident: the audit arithmetic in rental-terms
needs an input). **Fix**: one owner for "the rate of the reference family". Either rental-terms
takes the rate as an argument of `audit_daily_thb` and names `turbobaby/pricing-honesty` /
`CHEAPEST_IN_STOCK_DAY_RATE_THB` as its source, or pricing-honesty stops declaring the figure and
consumes rental-terms' pin; then bind the surviving copy in gate 3 to the seed tariff row.
`REF_MONTHLY_LOW_SEASON_THB = 5000` (`rental_terms.t27:286`) has no second declaration and is not a
finding.

### F5. The migration-file census has two self-declared owners

| where | name | value | what the file says about ownership |
| --- | --- | --- | --- |
| `pricing_honesty.t27:330`, `:343` | `MIGRATION_FILES_SEARCHED`, `MIGRATION_COUNT_TOP_LEVEL` | `87`, `86` | `MIGRATION_COUNT_IS_OWNED_HERE = true` (`:341`), "THIS CONTRACT OWNS THIS COUNT"; copy holder named: `turbobaby/locale-policy` |
| `schema_provenance.t27:223-224` | `SQL_FILES_TOP_LEVEL`, `SQL_FILES_RECURSIVE` | `86`, `87` | `SQL_FILE_CENSUS_OWNER_ID = "turbobaby/schema-provenance"`; lists pricing-honesty and locale-policy as `SQL_FILE_CENSUS_SIBLING_COPY_OWNERS` |
| `locale_policy.t27:173` | `MIGRATION_FILES_SEARCHED` | `87` | `MIGRATION_COUNT_IS_A_COPY = true`, owner named `turbobaby/pricing-honesty` |

```sh
grep -n 'MIGRATION_COUNT_IS_OWNED_HERE\|^pub const MIGRATION_FILES_SEARCHED\|^pub const MIGRATION_COUNT_TOP_LEVEL' pricing_honesty.t27
grep -n 'SQL_FILE_CENSUS_OWNER_ID\|^pub const SQL_FILES_' schema_provenance.t27
grep -c '"turbobaby/schema-provenance"' pricing_honesty.t27 locale_policy.t27      # 0 and 0
```

Owner: contested -- that is the finding. Schema-provenance is the contract whose subject is the
migration tree and it declares itself owner; pricing-honesty declares itself owner too and never
names schema-provenance; locale-policy follows pricing-honesty. The locale-policy copy is the
correct *consumer* shape (edge by ID and declaration name, `IS_A_COPY`), but it points at a file
that another contract calls a copy. Drift: gate 3 binds `MIGRATION_FILES_SEARCHED` in
pricing-honesty and locale-policy and `MIGRATION_COUNT_TOP_LEVEL` in pricing-honesty to the tree
(three `BINDINGS` entries); schema-provenance's two figures have no binding by those const names,
so the self-declared owner is the only unguarded copy.

**Verdict: finding** (two owners for one measurement; values agree today). **Fix**: pick one owner
-- schema-provenance by subject -- and have pricing-honesty replace `MIGRATION_COUNT_IS_OWNED_HERE`
with an edge to `turbobaby/schema-provenance` (`SQL_FILES_RECURSIVE`), re-pointing locale-policy's
`MIGRATION_COUNT_OWNER_ID` to the same; move the gate-3 binding to the owner's constant. The
pricing-honesty use of the number (all 87 files searched, zero divergence tables) stays as a
consumed fact.

## Result

| # | location | copy of | owner | edge to owner | gate catches drift |
| --- | --- | --- | --- | --- | --- |
| F1 | `ride_game.t27:129` (and :132-133, :160-168, :218, :239, :242, :250, :449, snapshot block from :493) | `bike_catalog.t27:170` `DISPLACEMENTS_CC` and siblings | bike-catalog, deposit-tiers, availability, catalog-api | yes, copy kept | no |
| F2 | `deposit_tiers.t27:223` `PRICE_LIST_ONLY_UNITS` | `availability.t27:190` | availability | yes, copy kept | no |
| F3 | `rental_terms.t27:197` `ON_SILENCE` | `pricing_honesty.t27:165` `MUST_SAY` | pricing-honesty | yes, copy kept (and worded after the agent charter) | no |
| F4 | `rental_terms.t27:285` `REF_BASE_THB_DAY` | `pricing_honesty.t27:95` `CHEAPEST_IN_STOCK_DAY_RATE_THB` | pricing-honesty by subject; each file names the other | rental-terms yes, pricing-honesty prose only | no (neither bound) |
| F5 | `pricing_honesty.t27:330`/`:343` census | `schema_provenance.t27:223-224` `SQL_FILES_*` | contested: both claim it | no, in either direction | copies yes, the self-declared owner no |

## Not covered here

- 38 of the 52 number groups of detector (b) were judged by their names, not read in context.
- Only `pub const` values were compared. Literals inside `fn` bodies and `test`/`invariant`
  blocks, integers in -10..10 (enum codes), strings under 12 characters and one-line numbers in
  the round/standard set were not; a restated enum code is caught only when its name also matches
  (as the `CLASS_*` codes of F1 were).
- A rule restated in prose words without a constant is not searched: detector (c) needs an
  UPPER_CASE name in the "owns" sentence, and found none.
- `docs/t27-contract-map.md` was not read; owners were taken from each file's own edge prose and
  from gate-3 `BINDINGS` grepped by `"const"` name. Other gate scripts were not checked for
  cross-file comparisons.
- The corpus self-census constants (file counts, edge counts, wave counts) repeated in 20+ files
  were cleared as self-measurements; whether one contract should own the corpus census was not
  decided.
- The `COPY_CHECK_*` ownership hole (ai_assist / promo_broadcast) is recorded, not reviewed.
