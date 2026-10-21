# RedisInsight style and comparison navigation QA

final result: passed

## Evidence and scope

- Source visual truth: official RedisInsight Workbench screenshot, commit `43bd288ed30dc6d2355723158dad5d7ecf4a280e`, `.github/screenshots/Redis-Insight-Workbench.png` (2594 × 1662). Captured locally under work/redisinsight-reference. Official dark_theme/_theme_color.scss and base/_fonts.scss also inspected.
- Implementation: ../riotx-menu-audit/03-replication-after.png, 04-compare-after.png, 07-settings-after.png, 08-mobile-after.png, 09-compare-final.png.
- Full combined source/implementation input: ../riotx-menu-audit/05-style-comparison.png. Focused header/nav/control comparison: ../riotx-menu-audit/06-style-focus.png. Both combined images were opened and visually reviewed, as were the final desktop/mobile captures.
- This is a style match across different products and contents, not a pixel clone of the RedisInsight query editor. Three columns and persistent two-endpoint key counts are intentional product requirements.
- Initial desktop source render: 1600 × 1138 CSS px at browser zoom 90% (`devicePixelRatio=0.9`), screenshot 1778 × 1263. Source and implementation fit to equal 1000px wide panels for the combined full-view comparison; no color remapping. Focus comparison uses unscaled 1000 × 650 crops from both.
- Final desktop viewport override reset, screenshot 1422 × 800. Mobile viewport override 390 × 844 at the same zoom yielded 433 × 938 CSS px and a 481px-wide screenshot. Measurements use actual DOM CSS bounds rather than assuming density=1. Mobile document scrollWidth=innerWidth=433. Temporary override reset before handoff.
- States: replication overview, comparison monitor, real comparison running/completed, settings, FULL/QUICK editor (cancelled without saving), log navigation.

## Findings and iteration history

1. Before implementation: P2 — peer-level replication and comparison concealed their relationship. Comparison monitor incorrectly said a separate comparison was required after completion. Fixed by child navigation/context tabs, comparison-specific scope/output/history and monitor copy. Existing compare scope and all histories preserved.
2. First theme pass: official neutral surfaces, lavender primary, flat borders and white underline tabs are consistent with the reference. Inter is an intentional open font substitute for Graphik; Source Code Pro is bundled for logs. Different content/grid proportions are required by this product, not unintended reference drift.
3. Responsive pass: P2 — grid automatic minimum sizes let the horizontal workspace list expand the mobile document to 650px. Fixed with minmax(0,1fr), min-width:0 sidebar and a constrained scrollable workspace strip. Post-fix 08-mobile-after.png and DOM measurements confirm document width equals viewport width; source/target totals both visible and sticky top=0 after scroll.
4. Final pass: no actionable P0/P1/P2 findings. Final desktop 09-compare-final.png and mobile 08-mobile-after.png opened and reviewed. Long RIOTX lines wrap inside the comparison panel. No hidden run action or document overflow observed.

## Fidelity surfaces

- Fonts: Inter 400/500/600, system Korean fallback, Source Code Pro 400. Compact title/navigation hierarchy retained; raw logs wrap rather than forcing document width. Graphik exact font matching is not claimed. OFL notices shipped in ui/public/licenses.
- Spacing/layout: three-column desktop, pinned key strip, flat 4px panels/buttons, compact tabs and nested selection. Mobile stacks the inspector and scrolls only workspace navigation horizontally.
- Colors: official dark theme page #121212, surface #202020, border #383838, primary #8ba2ff, text #dfe5ef, subdued #b5b6c0, success #5bc69b and danger #ff6280. Chart primitives use matching neutral/lavender tokens.
- Assets: existing Phosphor icons and Recharts kept. This is independently branded RIOTX Workbench; RedisInsight logo/query-editor imagery is not represented as the app's own identity. No decorative raster images required.
- Copy/content: actual key counts and RIOTX output; selection shows execution snapshot scope. FULL/QUICK distinctions, LIVE non-atomic comparison warning and scope mismatch are explicit. Exit 0 never becomes a blanket full-equality badge.

## Checks

- Navigate replication → comparison using both sidebar and main tab; preserve separate run selection/history.
- Run existing read-only compare twice; actual `OK 29`, exit 0. Full DB totals remain 143/143. Jobs/profiles identical before/after; all prior run IDs retained.
- Open comparison settings, choose QUICK, cancel; persisted FULL/pattern remain unchanged.
- Open Settings and inspect independent source/target notification state, with server change disabled until explicit toggle.
- Open actual comparison logs; process start/finish and real outputs visible. New comparison termination message points to comparison output.
- Menu tests: 5 passed; Sites packaging tests: 4 passed; Docker build succeeded. Existing data-changing integration suite deliberately not repeated against user-populated services.
- Final browser console error/warning entries: [] after reload. Deployment briefly interrupted polling as expected while recreating the web service; final state is connected.
- Final viewport override reset and deliverable tab kept open.

## Follow-up polish

P3: optional larger UI text density setting and full modal focus/accessibility audit. External DB connectors and multi-node Redis Cluster were not newly certified by this UI change.


## Follow-up: inherited comparison settings (2026-10-20)

The later user instruction supersedes the previous independent-child-scope decision. Child comparisons now show the current parent scope/mode and open the parent editor. The latest execution snapshot records parent revision 4, scope `*`, batch 64, threads 4 and QUICK. Actual UI execution output: `OK 143`. Historical snapshots remain unchanged. Screenshot: ../riotx-inheritance.png. No layout or theme change was made. Parent editor link, inherited configuration text, read-only execution, pinned counts 143/143 and browser console [] were verified. Pure inheritance tests 7, navigation tests 5 and Sites tests 4 passed. API fixture additionally verified changed parent endpoints/scope, ignored child overrides, immutable old snapshots and NONE rejection. Final result remains passed.

## Follow-up: workspace/job deletion options (2026-10-20)

Workspace headings, job rows and comparison children now expose an accessible three-dot option menu. Workspace menus offer workspace deletion; job menus offer edit/delete. A confirmation modal lists the entire scope, including linked comparisons. Deleted batches are restorable from the top-bar deleted-items menu. Flat RedisInsight dark tokens, 3-column layout, parent comparison settings and pinned key totals remain.

Browser checks: created a separate two-job verification workspace with one completed read-only comparison. Deleted/restored the entire workspace, then deleted/restored the selected comparison independently. Its original run ID, snapshot and logs remained unchanged. Deleting the selected child selected another existing job without leaving a blank inspector. The verification fixture was subsequently archived in deleted items; all pre-existing jobs/profiles/runs remain unchanged. Counts remain 143/143.

Evidence: ../riotx-delete-confirm.png, ../riotx-deleted-items.png, ../riotx-delete-mobile.png and ../riotx-delete-menu.png. Confirmation and mobile captures were opened and visually inspected. At 390×844 viewport override with existing browser zoom, CSS document width and innerWidth both equal 433; the fixed-position menu bounds left=38/right=218 fit the viewport and avoid sidebar clipping. Temporary viewport override reset before handoff.

Deletion tests 9 passed (in-memory DB), navigation tests 5 passed, Sites tests 4 passed, production/Docker build succeeded. Active-state rejection, changed-scope rejection, parent-first restoration, transaction rollback, no demo reseeding after deleting all jobs, and submission/deletion race were tested. Actual API rejects executing deleted jobs. Deletion batches persist across container recreation. No new P0/P1/P2 issue; existing modal focus audit remains a P3 follow-up. Final result remains passed.


## Follow-up: floating right drawer (2026-10-20)

The latest user request supersedes the permanently reserved third-column inspector. Four vertical rail buttons now open a floating, independently scrolling inspector under the pinned counts. Closing it reclaims space for the desktop canvas; <=950px screens use an overlay. RedisInsight dark palette, type and controls remain unchanged.

Visual evidence opened and reviewed: ../riotx-drawer-before.png, ../riotx-drawer-closed.png, ../riotx-drawer-open.png, ../riotx-drawer-mobile.png and ../riotx-drawer-final.png. At the desktop viewport of 1422 CSS px, canvas width changes from 674.23px open to 1070.23px closed (396px reclaimed). Panel width is 380px. Document width equals viewport width with both states. Mobile override 390x844 under the existing browser zoom yields 433 CSS px; document width=433, drawer left=12/right=375.34, and its top stays 12px below the pinned key strip. After page scrolling, pinned top=0 and drawer top=115.99px. Temporary viewport override reset before handoff.

Verified interactions: all four rail menus and inner tabs; close button, selected rail button and Escape; focus returns to rail; local UI settings draft and log-search text survive folding; closed state survives reload; open state/selected menu survive reload; comparison log shortcut opens the log drawer. Independent keyboard scrolling moved the panel body from 0 to 367.22px while body scrollY remained 0. Switching menus initially carried the prior scroll offset (P2); fixed by giving each menu its own scroll-region key. Final rerun confirms the new menu scrollTop=0. Folding the same menu keeps its mounted content.

Closed drawer uses aria-hidden and inert; its controls are absent from the accessibility tree. The scroll body is an explicitly focusable named region. Reduced-motion preference disables the drawer/reflow transition. No new actionable P0/P1/P2 visual findings. Existing broad modal accessibility audit remains a P3 follow-up.

Build and packaging succeeded; navigation tests 5 and Sites tests 4 passed. Final browser error/warning logs: []. Static assets were hot-swapped without recreating the backend or generator containers. The session included independent workspace/run-status changes, so before/after equality of all operational state is not claimed. This UI work issued no execution/cancel/profile-configuration requests and no Redis writes. Generator remains running/healthy. Final result remains passed.

## File source correction — 2026-10-20
- JSON import (actual orders.csv input) displays 3 source input records instead of the unrelated 10k LIVE Redis DB total. Its configured target DB still shows live totals.
- Source flow identifies orders.csv; source Redis events/settings are excluded for file inputs. Target notifications remain available.
- Floating Settings drawer includes file metadata timestamp/size, auto-import switch and input replacement upload. Desktop layout and pinned counts visually checked.
- Python tests: 32; frontend tests: 13. Actual RIOTX replacement and toggle evidence: riotx-file-import-results.json.

## Global theme preference — 2026-10-20
- Added top-bar 전체 설정 → Theme with System match default and Dark override.
- Shared semantic palette covers the canvas, sidebar, drawers, dialogs, logs, form controls and chart lines/axes/tooltips.
- Browser verified: initial System match resolves light on the current system; Dark changes the full page immediately and survives reload; returning to System match also survives reload. No desktop horizontal overflow.
- Five theme tests cover default/invalid values, dark override, saved preference, unavailable storage, OS change subscription and cleanup. All 18 frontend tests pass. No backend restart was needed.

## Bottom execution-history drawer — 2026-10-20
- Left history defaults collapsed into a 48px bottom handle (46px on mobile); opening expands upward without reflowing the central canvas.
- Verified 18 available runs with internal scrolling, selected-run highlighting, historical run log opening, Escape close/focus return and open-state retention after reload.
- Desktop bottom alignment checked after page scroll: bottom 799.99px in an 800px viewport. Mobile drawer bottom 937.78px in a 938px CSS viewport with no horizontal overflow.
- Browser checked while existing LIVE run continued; static UI update only, no backend/Redis restart.
