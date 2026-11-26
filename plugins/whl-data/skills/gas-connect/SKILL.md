---
name: gas-connect
description: >-
  Read and edit Google Apps Script projects directly in the user's Chrome,
  through the Claude in Chrome extension. Use this skill whenever the user wants
  Claude to open, inspect, change, append to, or install triggers in an Apps
  Script project — they paste a script.google.com project link, say "แก้ GAS ให้",
  "ต่อ claude กับ apps script", "เข้าไปแก้สคริปต์ให้หน่อย", or ask to add a
  function/trigger to a Google Sheet's bound script. Works by driving the Monaco
  editor through its JavaScript API instead of typing into the page, so large
  non-ASCII (Thai) code blocks go in exactly as written. Also covers the account
  and trigger-ownership gotchas that break Apps Script automation later.
compatibility: "Needs the Claude in Chrome extension connected, and the user signed in to the Google account that owns the script"
---

# Connect to Google Apps Script

Apps Script has no API Claude can call directly. The reliable route is the
user's own Chrome, where they are already signed in — the Claude in Chrome
extension drives that browser.

**Never type code into the editor with keystrokes.** Monaco auto-indents, auto-
closes brackets, and mangles pasted Thai text. Set the editor's content through
its JavaScript API instead; it is exact, fast, and verifiable.

## 1. Load the tools

The browser tools are deferred. Load them in **one** ToolSearch call — one call
per tool wastes a round trip each:

```
ToolSearch query:
select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__find,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__tabs_close_mcp
```

Then call `tabs_context_mcp` with `createIfEmpty: true` once, before anything
else, to get a tab id.

## 2. Open the project

```
navigate  tabId=<id>  url=https://script.google.com/u/0/home/projects/<PROJECT_ID>/edit
```

`/u/0/` means "first Google account signed in to this browser". If the project
belongs to a different account the page will 404 or show the wrong project —
check which account is active before blaming the link.

A "You're currently signed in as …" popup usually appears on first load. Take a
screenshot, click **OK** to dismiss it, and note the account name — it matters
in step 6.

## 3. Read the current code

```javascript
(() => {
  if (typeof monaco === 'undefined') return { ready: false };
  const ms = monaco.editor.getModels();
  if (!ms.length) return { ready: false, models: 0 };
  const v = ms[0].getValue();
  return { ready: true, lines: ms[0].getLineCount(), len: v.length,
           hasThing: v.indexOf('functionYouCareAbout') >= 0 };
})()
```

**Confirm you are editing the version you think you are.** Compare `len` against
the local copy before changing anything:

```bash
python -c "import io;print(len(io.open(r'path\to\local.gs',encoding='utf-8').read()))"
```

If the counts differ, someone edited the live script — read it properly and
reconcile before writing, or you will silently overwrite their work.

## 4. Write the change

Append (safest — leaves existing code untouched):

```javascript
(() => {
  const m = monaco.editor.getModels()[0];
  if (m.getValue().indexOf('setupAutoSync') >= 0) return { skipped: 'already there' };
  const APPEND = `
...your code, raw, newlines and Thai and all...
`;
  m.setValue(m.getValue() + APPEND);
  const v = m.getValue();
  return { lines: m.getLineCount(), len: v.length,
           ok: v.indexOf('function setupAutoSync()') >= 0 };
})()
```

Make the write **idempotent** (the early `skipped` return) so a retry after a
flaky tool call cannot duplicate the code.

**Before using a backtick template literal, prove the payload is safe** — one
stray backtick or `${` silently corrupts everything after it:

```python
app = open('file.gs', encoding='utf-8').read()
print('backtick:', chr(96) in app, '| dollarbrace:', '${' in app, '| backslash:', chr(92) in app)
```

If any is True, JSON-encode the string instead:
`json.dumps(text, ensure_ascii=False)` and paste that literal.

## 5. Save and verify it stuck

`m.setValue()` only changes the in-page editor. To persist:

1. `computer` → `left_click` somewhere in the code area (focus the editor)
2. `computer` → `key` → `ctrl+s`
3. **`navigate` to the same URL again** (full reload), then re-run the step-3
   read

Only a reload proves it reached Drive. The save indicator is easy to misread,
and Monaco clears its undo stack after `setValue`, so greyed-out undo arrows
mean nothing.

## 6. Running functions and installing triggers

Selecting a function in the picker beside **Run** does *not* execute it. Click
the picker, scroll the list (it scrolls — new functions are at the bottom), and
click the function so it is queued and ready.

**Do not press Run without asking.** Running installs triggers, mutates live
spreadsheets, calls production APIs, and raises an OAuth consent screen the user
should read themselves. Set the picker, then hand it over.

Two gotchas worth telling the user every time:

- **A trigger runs with the permissions of whoever pressed Run**, not whoever
  edits the sheet later. Install with a shared/service account, not a personal
  one, or the automation dies the day that person leaves.
- **Installable `onEdit` ≠ simple `onEdit`.** Only an installable trigger
  (created via `ScriptApp.newTrigger(...).forSpreadsheet(...).onEdit().create()`)
  may call `UrlFetchApp` or other authorized services. A simple `function
  onEdit(e)` cannot reach the network at all.

## Gotchas that waste time

| Symptom | Cause / fix |
|---|---|
| `javascript_tool` returns `{}` | The async-IIFE form sometimes swallows the result. Use a plain synchronous IIFE and poll by re-running instead of `await`-ing inside. |
| Result says `[BLOCKED: Cookie/query string data]` | A returned string looked like a credential. Don't return raw file tails — return booleans, lengths and `indexOf` checks. |
| Monaco missing right after navigate | The editor loads late. Re-run the read a moment later; don't build in long waits. |
| Code written but gone after reload | Ctrl+S never landed — the editor wasn't focused. Click inside the code area first. |
| New functions absent from the Run picker | The picker lists what's parsed. If they're missing, the save failed; re-verify with step 5. |
| Functions ending in `_` missing from picker | Expected — a trailing underscore marks a private helper in Apps Script. |

## Before finishing

Close tabs you opened (`tabs_close_mcp`) unless the user still needs the page —
e.g. when you've left a function queued for them to Run, say so and leave it
open.

If the change touches API keys, Script Properties, customer data or anything
that deploys, tell the user to have IT review it and to try it on a copy of the
spreadsheet before the real one.
