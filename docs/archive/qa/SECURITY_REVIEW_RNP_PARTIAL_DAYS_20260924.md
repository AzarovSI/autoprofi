## Security Review Results

**Scope:** Current changes relative to `bf69017` in:
`app/routers/rnp_sales_router.py`, `app/ozon_reviews_loader.py`,
`app/ozon_daily_loader.py`, `app/wb_daily_loader.py`, and
`tests/test_rnp_partial_days.py`.

No production database or `/tmp/publish.json` was accessed. Existing plaintext
documentation credentials, CORS, h11, and unchanged dependencies were not
reopened or audited.

### BLOCK (must fix before publishing)

- None.

### WARN (inform user, let them decide)

- None.

### PASS

- **Parameterized database access:** RNP date bounds and the added
  `stock_daily` date bounds use bound `%s` parameters
  (`app/routers/rnp_sales_router.py:332-337,460-477`). Loader values are also
  parameterized. Added f-strings interpolate only fixed internal column-name
  lists, not user-controlled values.
- **Upsert and reupload preservation:** Ozon reviews use a canonical
  `catalog_items` article and `ON CONFLICT (date, seller_article)` to update
  only rating/review fields (`app/ozon_reviews_loader.py:293-315`). Ozon and WB
  daily reuploads preserve independent fields and recreate missing-order stub
  rows without restoring old order quantities
  (`app/ozon_daily_loader.py:328-376`,
  `app/wb_daily_loader.py:353-394`).
- **NULL semantics:** Current-month forecast and turnover use only rows where
  `orders_qty IS NOT NULL`; an actual zero remains a data day, while
  snapshot/stock-only days do not create order days
  (`app/routers/rnp_sales_router.py:487-488,759-791`). Yandex remains on its
  existing path.
- **Cache and authentication:** The cache key is versioned and includes the
  Moscow “today” date (`app/routers/rnp_sales_router.py:268-315`). The
  authenticated `Depends(auth.get_current_user)` dependency remains on the
  RNP tree endpoint (`app/routers/rnp_sales_router.py:268-275`); no changed
  file adds an unauthenticated endpoint or weakens authorization.
- **Secrets and dangerous patterns:** Focused checks over the changed files
  found no private-key/token/password literals and no user-controlled
  `eval`, `exec`, shell execution, or SQL injection pattern.
- **Verification:** `python -m unittest tests/test_rnp_partial_days.py -v`
  passed all 10 tests; `git diff --check` passed. The test suite used its
  local-only database guard.
