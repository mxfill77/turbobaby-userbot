# Review dimension: invented numbers

Restarts the axis lost in the 2026-09-22 out-of-memory crash (see `docs/t27-crash-recovery-2026-09-22.md`).
Findings are written here as they are made, not held in a session.

The corpus rule under review (`docs/t27-handover.md`, "A prompt for the next session"): *never
invent a number -- where the repository publishes nothing, declare the refusal and say what is
missing.*

## Method

Step 1 -- every numeric constant, joined against two sources of truth, printed as counts only.
No file was written for this step; the join runs in one `awk` over four process substitutions
(names bound by gate 3; numeric tokens of tracked files outside `specs/`, `docs/t27-*` and
`scripts/*t27*`; numeric tokens of `scripts/*t27*` kept apart; the constants themselves).
Tokens are normalised (`1_000`, `1000.0`, `01000` all become `1000`; sign dropped).

```sh
PAT='^\s*(pub\s+)?const\s+[A-Za-z_0-9]+\s*:\s*(u8|u16|u32|u64|usize|i32|i64|f32|f64)\s*=\s*-?[0-9][0-9_.]*\s*;'
grep -hE "$PAT" specs/turbobaby/*.t27 specs/agents/turbobaby.t27 | wc -l          # 2053
grep -oE '"const"\s*:\s*"[^"]+"' scripts/verify_t27_against_source.py | sort -u | wc -l   # 187 bound names
git ls-files | grep -vE '^specs/|^docs/t27-|^scripts/.*t27' | wc -l               # 746 files indexed
```

| bucket | count |
| --- | --- |
| numeric scalar constants, `specs/turbobaby/*.t27` + `specs/agents/turbobaby.t27` | 2053 |
| bound by gate 3 (`BINDINGS` in `scripts/verify_t27_against_source.py`, matched by name) | 149 |
| unbound, value found in some tracked file outside `specs/` | 1872 (1435 of them <= 10, 437 above) |
| unbound, value found NOWHERE outside `specs/` (candidates) | 32 (all > 10) |
| of the 32, value found only in a `scripts/*t27*` gate script | 1 (`DETAIL_LINE_COUNT_BEFORE_REPAIR`) |

Limits of step 1, stated so nobody reads the 1872 as "cleared": a literal hit proves only that the
numeral exists somewhere. For values like 600, 1500, 2000, 5000 it exists everywhere, so the
bucket is not evidence that the constant is sourced. Array elements (`[5]i32 = [...]`) were not
indexed. Binding was matched by constant name, not by (spec, name) pair.

Step 2 -- each of the 32 candidates was read with its comment block and, where the comment cites a
place, the cited place was opened. A candidate clears when it is a count with a stated method, a
standard (Unicode), arithmetic over other constants, a `brain:<node>:<line>` citation (the owner's
published source; no gate resolves it), a declared unknown, or a value that the cited `src/`/`data/`
place really carries in another form (e.g. `2 * 1024 * 1024`, `11:00`).

## Results

(rows added as each candidate is judged)

| where | constant | kind | verdict |
| --- | --- | --- | --- |
| `ai_assist.t27:180` | `SUPERSEDED_IMPLEMENTATION_LOC = 428` | line count of `src/ai.rs` before a stated edit | count -- clear |
| `client_errors.t27:166` | `BODY_LIMIT_BYTES = 2097152` | `src/api/mod.rs:104` carries `DefaultBodyLimit::max(2 * 1024 * 1024)` (checked) | source in another form -- clear |
| `delivery_terms.t27:166` | `EARLIEST_WINDOW_START_MIN = 660` | `data/fleet_seed.json` `earliest_window "11:00-13:00"` (checked) | conversion -- clear |
| `delivery_terms.t27:210` | `DELIVERY_CUTOFF_MIN = 1050` | owner's 17:30, `brain:business_rules:818-820` | brain -- clear |
| `delivery_terms.t27:474` | `UNCONFIRMED_BELT_STEP_THB = 1490` | `brain:business_rules:1417-1420`, declared UNCONFIRMED, not a fee | declared unknown -- clear |
| `deposit_tiers.t27:151-152` | `TIER_GAP_SUM_THB`, `TIER_SPAN_THB = 22000` | sum of the gaps / top minus bottom tier | arithmetic -- clear |
| `http_cache.t27:600` | `INCIDENT_FIGURE_SHOWN = 1529` | `brain:knowledge_base:857-859` incident | brain -- clear |
| `legacy_retirement.t27:325` | `LEGACY_SOURCE_LOC = 9193` | line count of 25 legacy files | count -- clear |
| `market_profile.t27:122,124` | `CODEPOINT_MAX = 1114111`, `SURROGATE_HI = 57343` | Unicode scalar range | standard -- clear |
| `market_profile.t27:164` | `PROOF_SYMBOL_CODEPOINT = 8364` | U+20AC EURO SIGN | standard -- clear |
| `order_presentation.t27:145` | `DETAIL_LINE_COUNT_BEFORE_REPAIR = 556` | historical line count | count -- clear |
| `order_presentation.t27:1138` | `POLL_HORIZON_MS = 3600000` | `POLL_INTERVAL_MS` x `POLL_TICK_LIMIT` = 10000 x 360 | arithmetic -- clear |
| `publication.t27:136,178` | `MANIFEST_BYTES = 1478541`, `SHARED_CORE_BYTES = 2329087` | response sizes of two t27.ai URLs observed 2026-09-20 | dated external observation -- clear |
| `schema_provenance.t27:116` | `MIGRATION_LINES = 4271` | `wc -l migrations/*.sql` (re-run: `4271 total`) | count -- clear |
| `upload_media.t27:319` | `LAYER_HEADROOM_BYTES = 10485760` | `LAYER_MAX_BYTES - HANDLER_MAX_BYTES` | arithmetic -- clear |
| `order_money.t27:317` | `FLEET_SEED_BYTES = 19434` | byte size of `data/fleet_seed.json` | count -- clear on this axis, but see the observation below |
| `publication.t27:58` | `MAX_FILE_BYTES = 1048576` | "published scanner bound", no source named | **finding** |
| `rental_terms.t27:223` | `MONTH_MIN_DISCOUNT_BP = 3500` | `data/fleet_seed.json:115` `"month": [0.35, 0.50]`; `migrations/079_rental_terms.sql:82` | source in another form -- clear |
| `rental_terms.t27:529,535` | `SIGN_MISREAD_UNDERSTATEMENT_BP = 1821`, `..._WITHOUT_ROUNDING_BP = 1818` | derived by the file's own asserts from `PEAK_EXAMPLE_*` and two probe bases | arithmetic -- clear |
| `rental_terms.t27:565-568` | `NO_SEASON_CONTROL_MISS_BP = 2140`, `OWNER_CALENDAR_MISS_BP = 1060`, `ACCURACY_FLOOR_BP = 1060` | `brain:business_rules:234-241` ("all four figures are the node's") | brain -- clear |
| `rental_terms.t27:639` | `CENSUS_BOOKINGS = 1243` | `brain:business_rules:494-529`, measured 2026-08-18 | brain -- clear |
| `validation_bounds.t27:242-243` | `LON_MIN_CENTI = -18000`, `LON_MAX_CENTI = 18000` | `src/api/quest.rs:123` `req.lon < -180.0 \|\| req.lon > 180.0`, x100 | source in another form -- clear |
| `validation_bounds.t27:249-251` | `SOUTHERN_SAMPLE_LAT/LON_CENTI = -4129/17478`, `JUST_ABOVE_THE_CEILING_LAT_CENTI = 9010` | `src/trios/validation.rs` Wellington `-41.29`/`174.78` and `clamp_finite_in_range(90.1, ...)` (checked) | source in another form -- clear |

### The finding: `publication.t27:58` `MAX_FILE_BYTES = 1048576` (and its twin `PROBE_FILES = 8` at :57)

The comment calls both "Published scanner bounds" and says the byte bound is inclusive "matching
the published maximum" -- but names no publisher: no URL, no upstream file, no brain node, no
date. The two numbers are the t27.ai world scanner's eligibility window (first eight files, at
most 1 MiB each), i.e. a policy of an external system, and `scanner_probe_witness_valid` plus the
test `scanner_probe_window_and_file_size_fail_closed` build a verdict on them.

```sh
sed -n '55,58p' specs/turbobaby/publication.t27
# ; Published scanner bounds. PROBE_FILES is a one-based window here: ...
# ; first through eighth, never ninth. MAX_FILE_BYTES is inclusive, matching the published maximum.
git -C D:/turbobaby-bike-bot grep -n -i 'max_file_bytes\|1048576' -- . ':!specs' | wc -l
# 1   -- docs/t27-reuse-map.md:146, which restates the spec (excluded as docs/t27-*)
grep -c '"MAX_FILE_BYTES"\|"PROBE_FILES"' scripts/verify_t27_against_source.py
# 0   -- not bound by gate 3
```

`PROBE_FILES = 8` falls in the <= 10 bucket, so the literal search cannot judge it; it is judged by
the same comment, which gives it the same (missing) source.

Verdict: finding. The number may well be right -- the scanner lives upstream -- but the repository
publishes nothing that shows it, which is exactly the case the rule covers.

Smallest fix: name the upstream place that publishes the window (repository, file and the
constant or line there, with the date read) in the comment. If that place cannot be named, turn
the pair into a declared refusal in the shape the corpus already uses -- `turbobaby/client-errors`
pins what it can read and declares the rest with `BODY_LIMIT_REFUSAL_STATUS_IS_MEASURED = false`,
and this very contract (`turbobaby/publication`) already does it for another leg with
`AGENT_ROUTE_RESOLUTION_IS_NOT_MEASURED = true`. A `SCANNER_BOUNDS_SOURCE_IS_NAMED : bool = false`
beside the two constants would make the gap visible without touching the arithmetic.

### Observation (not a policy number): `order_money.t27:317` `FLEET_SEED_BYTES = 19434`

A count with a stated method (the comment says `data/fleet_seed.json is 19434 bytes`), so it clears
this axis -- but the method reproduces only on this host's CRLF checkout, not from the committed
file:

```sh
git -C D:/turbobaby-bike-bot ls-files --eol data/fleet_seed.json   # i/lf  w/crlf
wc -c < data/fleet_seed.json                                        # 19434 (working tree, CRLF)
git show HEAD:data/fleet_seed.json | wc -c                          # 19084 (committed, LF)
grep -c '' data/fleet_seed.json                                     # 350 lines = the 350-byte gap
```

On any LF checkout (CI, Linux, the POSIX mount) the stated measurement gives 19084. Fix: measure
the blob (`git show HEAD:<path> | wc -c`) or state that the figure is the CRLF working-tree size.

## Totals

| | count |
| --- | --- |
| candidates read (value nowhere outside `specs/`) | 32 |
| findings | 1 (`MAX_FILE_BYTES`, with `PROBE_FILES` judged by the same comment) |
| cleared -- count with stated method | 7 (`SUPERSEDED_IMPLEMENTATION_LOC`, `LEGACY_SOURCE_LOC`, `DETAIL_LINE_COUNT_BEFORE_REPAIR`, `MIGRATION_LINES`, `FLEET_SEED_BYTES`, `MANIFEST_BYTES`, `SHARED_CORE_BYTES`) |
| cleared -- arithmetic over other constants | 6 (`TIER_GAP_SUM_THB`, `TIER_SPAN_THB`, `POLL_HORIZON_MS`, `LAYER_HEADROOM_BYTES`, two `SIGN_MISREAD_*`) |
| cleared -- `brain:<node>:<line>` citation (owner's source; no gate resolves it) | 6 (`DELIVERY_CUTOFF_MIN`, `INCIDENT_FIGURE_SHOWN`, three `rental_terms` miss/floor figures, `CENSUS_BOOKINGS`) |
| cleared -- declared unknown | 1 (`UNCONFIRMED_BELT_STEP_THB`) |
| cleared -- standard (Unicode) | 3 (`CODEPOINT_MAX`, `SURROGATE_HI`, `PROOF_SYMBOL_CODEPOINT`) |
| cleared -- cited `src/`/`data/` place carries it in another form (checked) | 8 (`BODY_LIMIT_BYTES`, `EARLIEST_WINDOW_START_MIN`, `MONTH_MIN_DISCOUNT_BP`, two `LON_*_CENTI`, three `validation_bounds` samples) |

7 + 6 + 6 + 1 + 3 + 8 = 31 cleared, plus 1 finding = 32.

## Second pass: a heuristic screen of the "value found" bucket

The literal join cannot judge policy values that happen to be common numerals, so a name-and-comment
screen was run over the whole corpus: constants whose NAME ends in a policy suffix (`_MIN`, `_MAX`,
`_LIMIT`, `_THB`, `_BP`, `_MS`, `_SECONDS`, `_HOURS`, `_DAYS`, `_RETRIES`, `_ATTEMPTS`, `_FLOOR`,
`_CEILING`, `_CAP`, `_TTL`, `_RATE`, `_PCT`, ...) AND whose 14 lines above carry no source token
(`src/`, `data/`, `migrations/`, `tests/`, `brain:`, `CITATION`, `ISO`, `RFC`, `Unicode`, `wc `,
`grep`). The screen prints 27 hits. It is coarse -- it misses a citation written as
`error_overlay.rs:108` without `src/` -- so each hit read by hand was re-judged:

| where | constant | what carries it | verdict |
| --- | --- | --- | --- |
| `availability.t27:197` | `CLICK_125_STALE_GRID_RATE_THB = 187` | `data/fleet_seed.json:78`, `src/api/bikes.rs:628` (declared stale, never quoted) | clear |
| `client_errors.t27:299` | `PANEL_REPORT_CAP = 50` | cites `error_overlay.rs:108` | clear |
| `client_errors.t27:457` | `ALERT_COOLDOWN_SECONDS = 300` | cites the cooldown at `:43-47` of the alert module | clear |
| `loyalty_ledger.t27:528` | `ORDER_KEY_RETENTION_HOURS = 24` | `src/main.rs:581` `cleanup_old_idempotency_keys(&orm, 24)` (found by the reviewer; the constant itself cites nothing and is unbound) | clear on this axis; an uncited value -- one-line citation would close it |
| `notification_queue.t27:450` | `RETRY_INTERVAL_SECONDS = 30` | `src/notification_queue.rs:5` "the worker polls every 30 seconds" | clear |
| `observability.t27:169` | `REQUEST_ID_BYTE_MAX = 126` | `is_ascii_graphic` = U+0021..=U+007E, cited | standard -- clear |
| `publication.t27:316` | `QUEEN_BOARD_RECHECK_ATTEMPTS = 3` | dated observation (three 502s, 2026-09-20) | count -- clear |
| `rental_terms.t27:219-224` | the six `*_DISCOUNT_BP` band bounds | `data/fleet_seed.json:115` and `migrations/079_rental_terms.sql:82` | clear |
| `rental_terms.t27:286` | `REF_MONTHLY_LOW_SEASON_THB = 5000` | `data/fleet_seed.json:132` `"monthly_low_season_thb": 5000` (the comment says "published" without naming the file) | clear |
| `deposit_tiers.t27:150-152` | `TIER_GAP_*` | arithmetic over the tier ladder | clear |
| `request_identity.t27:256` | `AUTH_DATE_MAX_AGE_SECONDS = 86400` | bound by gate 3 (the screen does not exclude bound names) | clear |

18 of the 27 hits were read by hand (rows above) and all cleared. Screen hits not read by hand (9): `bike_catalog.t27` `SCOOTER_BODY_MAX`, `delivery_terms.t27`
`DOCUMENTED_COLLECTION_FEE_THB = 0`, `happy_hour.t27` `ROUNDING_WITNESS_PERCENT` and `KIND_RATE`,
`http_cache.t27` `NOT_A_DURATION_SECONDS = -1`, `notification_queue.t27` `RETRY_HORIZON_SECONDS`,
`pricing_honesty.t27` `ISSUE_CLAIMED_WITHOUT_RATE`, `ride_game.t27` `AVAILABILITY_OWNED_UNIT_FLOOR`,
`upload_media.t27` `ADMISSION_STEP_RATE`. By name most are enum tags, witnesses or sentinels rather
than policy values, but that is a guess, not a verdict.

## Not covered here

- Outside the 27-hit screen, the 1872 unbound constants whose numeral occurs somewhere outside `specs/` were NOT read. For
  the 437 above 10 a hit is weak evidence; for the 1435 at or below 10 it is none. Policy values
  such as a retry count, a floor or a small threshold can hide in that bucket (as `PROBE_FILES`
  showed). The next pass should filter that bucket by name (`*_MIN`, `*_MAX`, `*_LIMIT`,
  `*_THB`, `*_BP`, `*_MS`, `*_DAYS`, `*_RETRIES`, `*_FLOOR`, `*_CEILING`) and read the comments.
- Numeric array elements (`[N]T = [...]`) were not indexed.
- Gate-3 binding was matched by constant name, not (spec, name) pair; a name bound in one
  contract masks an unbound twin of the same name in another.
- The `brain:<node>:<line>` citations were not resolved against the knowledge base (read-only
  access to the brain was not part of this pass), so "brain -- clear" means "cited to the owner's
  source", not "checked there".
- `specs/agents/turbobaby.t27` holds no numeric scalar constant at all (the pattern above gives
  0 there), so it contributed nothing to either bucket.
