## Security Review Results

**Decision: PASS**

**Scope:** Current working-tree diff from `cd41f7c`, limited to
`app/routers/rnp_sales_router.py` and `tests/test_rnp_partial_days.py`.
No production database was accessed. Previously deferred documentation
credentials, CORS, and `h11` findings were not re-opened.

### BLOCK (must fix before publishing)

- None.

### WARN (inform user, let them decide)

- None introduced by this diff.

### PASS

- **Eligibility containment:** `eligible_articles` is derived only from the
  selected marketplace's daily RNP rows (`app/routers/rnp_sales_router.py:456-485`).
  Common-stock rows for articles absent from those daily rows are skipped, while
  stock still enriches an eligible article on an existing or additional date.
- **Authorization:** The reviewed endpoint remains protected by
  `Depends(auth.get_current_user)` (`app/routers/rnp_sales_router.py:270-275`).
- **Injection review:** The changed stock query uses fixed SQL structure and
  parameterized marketplace/date values (`app/routers/rnp_sales_router.py:463-480`).
  No new dynamic SQL, shell execution, evaluation, or hardcoded-secret pattern
  was found in the two changed files.
- **Mutation review:** The diff only reads sales/stock data, constructs the
  response, and updates the response cache; it adds no database writes or
  external side effects.
- **Cache isolation:** The response-key revision changed from `v4` to `v5`
  (`app/routers/rnp_sales_router.py:292`), preventing stale pre-fix cached
  trees from being served.
- **Regression coverage:** Focused `tests.test_rnp_partial_days` completed
  successfully: 15 tests passed against the isolated local test database.
  The added cases cover stock-only SKU exclusion, partial-day enrichment,
  inactive/dash status, unallocated warnings, and empty daily-source behavior.
