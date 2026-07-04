# Sync Progress Skill

## Purpose

Understand and debug CrossPoint's KOReader sync progress system:
- Position mapping between CrossPoint (spine+page) and KOReader (XPath+percentage)
- The `ProgressMapper` and `ParagraphStreamer` implementation
- XPath generation and resolution algorithms

## Quick Reference

### Position Formats

| System | Format | Example |
|--------|--------|---------|
| **CrossPoint** | `(spineIndex, pageNumber, totalPages)` | `spine=1, page=42/100` |
| **KOReader** | `(percentage, xpath)` | `percentage=0.42, xpath="/body/DocFragment[2]/body/p[15]/text().234"` |

### Conversion Functions

| Function | Direction | Algorithm |
|----------|-----------|-----------|
| `toSavedProgress()` | CrossPoint → KOReader | `intra → percentage + xpath` |
| `toCrossPoint()` | KOReader → CrossPoint | `xpath→intra → percentage → page` |
| `generateXPath()` | Legacy fallback | Stream spine, count paragraphs |

### XPath Format

```
/body/DocFragment[N]/body/.../p[N]/text()[N].NNN
                 ↑         ↑    ↑       ↑
               spine   paragraph  node  char offset
```

- `DocFragment[N]`: 1-based spine index (`N-1` = actual spineIndex)
- `p[N]`: 1-based paragraph count in that spine
- `text()[N]`: 1-based text node index (defaults to 1)
- `.NNN`: Character offset into that text node

---

## Core Components

### `ProgressMapper`

**Location**: `lib/KOReaderSync/ProgressMapper.h/cpp`

**Purpose**: Bidirectional translation between position formats.

#### Methods

| Method | Input | Output | Algorithm |
|--------|-------|--------|-----------|
| `toSavedProgress()` | `CrossPointPosition` | `SavedProgressPosition` | Intra-spine → cumulative percentage + XPath |
| `toCrossPoint()` | `SavedProgressPosition` | `CrossPointPosition` | XPath parsing → intra-spine → page number |
| `generateXPath()` | `(spineIndex, intra)` | `std::string` | Legacy: stream spine to find paragraph at offset |

#### Conversion Logic

```cpp
// CrossPoint → KOReader (toSavedProgress)
intra = (totalPages > 1) ? (pageNumber / (totalPages - 1)) : 0.0
percentage = epub->calculateProgress(spineIndex, intra)  // cumulative spine progress
xpath = ChapterXPathResolver.findXPathForProgress(...)   // primary
      OR findXPathForParagraph(...)                      // fallback 1
      OR generateXPath(...)                               // fallback 2

// KOReader → CrossPoint (toCrossPoint)
Parse XPath: docFrag, p[N], text()[N], .NNN
Resolve spine index from docFrag or cumulative sizes
Stream spine with ParagraphStreamer to get intra-spine progress
Calculate page: pageNumber = round(intra * (totalPages - 1))
Refine with Section LUTs (li, anchor, paragraph)
```

---

### `ParagraphStreamer`

**Location**: `lib/KOReaderSync/ProgressMapper.cpp` (lines 166-571)

**Purpose**: Single-pass HTML parser that counts visible characters and tracks XPath position.

#### Three Parsing Modes

| Mode | Constructor | Use Case | Key Fields |
|------|-------------|----------|------------|
| **Forward** | `ParagraphStreamer(targetByte)` | Generate XPath | `fwdTarget`, counts `<p>` |
| **Legacy reverse** | `ParagraphStreamer(paragraph, charOff, textNode)` | Old sync | `revParagraph`, global `<p>` count |
| **Ancestry reverse** | `ParagraphStreamer(steps[], stepCount, charOff, textNode)` | Structured XPath | `steps`, `matchedDepth`, sibling counters |

#### Key State

| Field | Purpose |
|-------|---------|
| `totalVisChars` | Total visible text characters seen so far |
| `targetVisChars` | Position where target was reached (for progress calc) |
| `revDone` | True when target position found |
| `revPFound` | True when matched paragraph reached |
| `pCount` | Running `<p>` count |
| `liCount` | Running `<li>` count |
| `matchedDepth` | Current XPath step matching depth (ancestry mode) |
| `capturedAnchorId[64]` | First `<a id>` inside matched element |

#### State Machine for HTML Parsing

```
Byte stream → globalInTag? → Yes → tagState machine
              ↓ No
            nonVisibleDepth > 0? → Yes → skip (head/style/script/title)
              ↓ No
            c == '&'? → Yes → entity buffer + decode
              ↓ No
            visible codepoint → increment totalVisChars
              → if target reached → record targetVisChars, set revDone
```

#### Tag Parsing States

| State | Trigger | Action |
|-------|---------|--------|
| `TAG_IDLE` | See `<` | Set `globalInTag=true`, go to `TAG_IN_NAME` |
| `TAG_IN_NAME` | Read tag name chars | Accumulate `tagName[]`, check self-close `/` |
| `TAG_ATTRS` | After tag name | Scan attributes, capture `id=` if inside matched element |

#### Anchor ID Capture State Machine

```
ATTR_FIND_NAME → see 'id' start → ATTR_READ_NAME
ATTR_READ_NAME → see '=' → ATTR_BEFORE_VALUE
ATTR_BEFORE_VALUE → see '"' or "'" → ATTR_CAPTURE_D or ATTR_CAPTURE_S
ATTR_CAPTURE_* → read chars until quote → finishCapturedAnchorId()
```

#### Visibility Rules

1. **Skip invisible content** when `nonVisibleDepth > 0`:
   - `<head>`, `<style>`, `<script>`, `<title>`
2. **HTML entities** decode to visible characters (e.g., `&amp;` → `&`)
3. **UTF-8** handled via `utf8NextCodepoint()`, counts as one char
4. **Self-close tags** `<br/>` fire both `onOpenTag` and `onCloseTag`

---

### XPath Parsing Helpers (lines 13-101)

| Function | Purpose | Returns |
|----------|---------|---------|
| `parseIndex(xpath, prefix, last)` | Extract N from `/p[N]` or `/text()[N]` | Integer, or -1 on failure |
| `parseCharOffset(xpath)` | Extract N from `.N` after `text()` | Character offset, or 0 |
| `parseTextNodeIndex(xpath)` | Extract N from `text()[N]` (default 1) | 1-based text node index |
| `isChapterStartXPath(xpath)` | Check if XPath points to chapter start | `true` if xpath ends at DocFragment or body with only `.0` |
| `parseXPathSteps(xpath, steps[])` | Break XPath into step array | Step count, or 0 on failure |

#### XPath Steps Structure

```cpp
struct XPathStep {
  char tag[12];         // Element name (e.g., "div", "li")
  int siblingIndex;     // 1-based [N] from `[N]`, or 0 (treat as 1)
};
```

**Example**: `/body/DocFragment[1]/body/div[1]/ul/li[4]/text()[1].51`
- Steps: `{div,1}`, `{ul,1}`, `{li,4}`
- Target text node: 1
- Character offset: 51

---

### Section Cache LUTs (Refinement Layer)

**Location**: `lib/Epub/Epub/Section.h/cpp`

**Purpose**: Convert paragraph/anchor/list positions to exact page numbers.

#### LUT Methods

| Method | Input | Output | Use |
|--------|-------|--------|-----|
| `getPageForListItemIndex(liIndex)` | 1-based `<li>` count | `optional<int>` page, or `nullopt` |
| `getPageForAnchor(anchorId)` | `<a id="...">` value | `optional<int>` page, or `nullopt` |
| `getPageForParagraphIndex(paragraphIndex)` | 1-based `<p>` count | `optional<int>` page, or `nullopt` |

#### Refinement Logic (in `toCrossPoint()`)

```cpp
// Priority order, stops on first success:
1. liIndex → getPageForListItemIndex()
2. anchorId → getPageForAnchor()
3. paragraphIndex → getPageForParagraphIndex()

// Only apply if LUT span > 1:
lutSpan = nextParaPage - paraPage
if (lutSpan > 1 && refinedPage >= nextParaPage)
  refinedPage = nextParaPage - 1
```

**Why span check?** A span of 1 means LUT too coarse (stale cache), keep intra-spine calculation.

---

## Debugging Checklist

### Sync Not Working? Check:

1. **XPath parsing fails**
   - `LOG_DBG("PM", "Failed to parse xpath: %s", xpath.c_str())`
   - Verify XPath format matches `/body/DocFragment[N]/body/...`

2. **Spine index mismatch**
   - `DocFragment[N]` is 1-based, actual `spineIndex = N-1`
   - Check cumulative sizes: `epub->getCumulativeSpineItemSize(i)`

3. **Paragraph count mismatch**
   - Different rendering → different paragraph breaks
   - Verify `paragraphIndex` vs `liIndex` (lists use `<li>`, not `<p>`)

4. **LUT lookup fails**
   - Cache may be stale; delete `.crosspoint/sections/` and re-render
   - Check log: `Paragraph X not found in section LUT`

5. **Anchor ID not captured**
   - Check `<a id="...">` exists in HTML at matched position
   - Verify `capturedAnchorId[0] != '\0'` after streaming

### Performance Profiling

| Operation | Complexity | Optimization opportunity |
|-----------|------------|--------------------------|
| `toSavedProgress()` | O(spine count) | Cache cumulative progress |
| `toCrossPoint()` | O(spine size) | Stream only once, cache mapping |
| `generateXPath()` | O(spine size) | Drop legacy mode, require ancestry XPath |

---

## Quick Reference: XPath Examples

| Scenario | XPath | CrossPoint interpretation |
|----------|-------|---------------------------|
| **Start of spine** | `/body/DocFragment[2]/body` | `spineIndex=1, page=0` |
| **Chapter start** | `/body/DocFragment[2]/body/.0` | `spineIndex=1, page=0` |
| **Paragraph 15** | `/body/DocFragment[2]/body/p[15]/text().0` | `spineIndex=1, paragraphIndex=15` |
| **Paragraph 15, char 42** | `/body/DocFragment[2]/body/p[15]/text().42` | `spineIndex=1, paragraphIndex=15, charOffset=42` |
| **List item 8** | `/body/DocFragment[2]/body/div/ul/li[8]/text().0` | `spineIndex=1, liIndex=8` |
| **Anchor** | `/body/DocFragment[2]/body/p[5]/a[1]/text().0` | `spineIndex=1, paragraphIndex=5, anchorId="..."` |

---

## Files Reference

| File | Purpose | Key Lines |
|------|---------|-----------|
| `lib/KOReaderSync/ProgressMapper.h` | Class definitions | 1-90 |
| `lib/KOReaderSync/ProgressMapper.cpp` | Implementation | 1-742 |
| `lib/KOReaderSync/ChapterXPathResolver.h/cpp` | XPath generation/resolution | - |
| `lib/Epub/Epub/Section.h/cpp` | Section cache LUTs | - |
| `lib/KOReaderSync/KOReaderDocumentId.h/cpp` | Filename-based sync hash | - |

---

## Related Skills

- **KOReader sync hash**: `lib/KOReaderSync/KOReaderDocumentId.cpp:7-24` — MD5 of filename
- **EPUB parsing**: `lib/Epub/Epub/` — Section caching, spine structure
- **Chapter XPath resolution**: `ChapterXPathResolver.cpp` — progress-to-XPath mapping

---

*Last updated: 2026-07-04*
*Source: `lib/KOReaderSync/ProgressMapper.cpp` and `lib/KOReaderSync/ProgressMapper.h`*
