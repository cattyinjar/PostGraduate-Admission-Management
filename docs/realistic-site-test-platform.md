# Realistic localhost multi-site test platform

This platform mirrors the server-rendered structures of 16 supplied public admission-list pages. It does **not** monitor those real sites and does not wait for their natural content changes.

Run the standalone verification:

```powershell
python -m pgam.testing.verify_realistic_monitor
```

Run the automated regression:

```powershell
python -m pytest pgam\tests\test_realistic_platform.py -q
```

The verifier performs this sequence for every simulated site:

1. Create one static-list monitor task.
2. Run the task and establish its baseline.
3. Change only decorative footer text, then verify that no event is emitted.
4. Publish one new notice and generate its detail page.
5. Run the task again.
6. Verify item detection, detail fetching, detail extraction, snapshot extraction, and event persistence.
7. Verify that LLM and email services are never invoked.

## Captured structure mapping

| Real page | Simulator key | Structural challenge |
|---|---|---|
| `https://ia.cas.cn/yjsjy/zs/sszs/` | `ia_cas` | `ul#content`, sibling date span |
| `https://yzbm.tsinghua.edu.cn/publish/s03/s0301/list?yxsdm=045` | `tsinghua_yzbm` | JavaScript pseudo links using `data-val`; detail URL inferred from list path |
| `https://life.tsinghua.edu.cn/rcpy/yjsjy/zsxxgk1.htm` | `tsinghua_life` | date text inside anchor, relative detail links |
| `https://www.au.tsinghua.edu.cn/zsjy/yjszs.htm` | `tsinghua_au` | card items with calendar markup and nested heading |
| `https://yzb.sjtu.edu.cn/zkxx/sszs` | `sjtu` | `a.item` cards, calendar day/month split across nodes |
| `https://yzb.nju.edu.cn/47865/list.htm` | `nju` | title/meta span pairs |
| `https://gsao.fudan.edu.cn/15029/list.htm` | `fudan` | real news list plus a larger department-link distractor |
| `https://yz.ustc.edu.cn/column/182?num=-1` | `ustc` | clickable `div.line-box-new` rows using `onclick="window.open(...)"` |
| `https://amss.cas.cn/jypy/zxxx/` | `amss` | `ul#content.list-txt-02`, separate date |
| `https://ict.cas.cn/yjsjy/zsxx/sszs/` | `ict` | icon inside anchor, bracketed sibling date |
| `https://is.cas.cn/yjsjy/zsxx/` | `is_cas` | document links mixed with dated notice links |
| `http://edu.ipe.ac.cn/zsxx/` | `ipe` | legacy nested table, GBK response, distractor table |
| `https://sia.cas.cn/zpjy/yjsjy/` | `sia` | long relative detail URLs, sibling date |
| `https://edu.iphy.ac.cn/?q=list2&id=3277` | `iphy` | query-string detail links and a one-item semantic list |
| `https://ime.cas.cn/kjrh/tzggkjrh/` | `ime` | slash-form dates (`YYYY/MM/DD`) |
| `https://bdt.semi.ac.cn/yanjiusheng/channels/691.html` | `semi` | date in parentheses inside anchor |

Raw captured HTML is kept only in the developer-local, Git-ignored `snapshots/` directory. The committed tests use minimal structural replicas and synthetic content, so the repository does not redistribute full third-party page content.

## Adapter hardening covered by the test

- Container selection favors dated notice lists over large undated navigation/directory blocks.
- Detail URLs in the same list directory receive a path-affinity bonus, which separates the real IPE admission table from other category tables.
- `data-val` pseudo links are converted to their predictable detail URL.
- `onclick="window.open(...)"` rows are treated as list entries.
- Title classes such as `name`, `title`, and `txt` are preferred over calendar noise.
- Date classes such as `time`, `date`, and `data` are recognized, including dates embedded in anchor text.
- Relative URLs are resolved before fingerprinting and detail fetching.
- One-item semantic lists are supported for pages such as the captured IPHY list.

## Current verification result

The latest full verification detected all 16 synthetic publications, fetched all 16 detail pages, produced 16 `fetched` events, produced zero baseline/decorative-change events, and invoked neither LLM nor email.

## Live full-chain verification

Ensure `token.txt`, `email_account.txt`, and `email_password.txt` exist in the project root, then run:

```powershell
D:\anaconda\envs\pgam\python.exe -m pgam.testing.verify_realistic_full_chain
```

This performs 16 real DeepSeek summarization calls and sends 16 real notification emails. The latest result was:

```json
{
  "site_count_is_16": true,
  "all_runs_successful": true,
  "baseline_clean": true,
  "decorative_changes_ignored": true,
  "sixteen_events_detected": true,
  "sixteen_llm_summaries_persisted": true,
  "sixteen_emails_accepted": true,
  "credential_material_absent": true,
  "temporary_data_cleaned": true,
  "all_passed": true
}
```
