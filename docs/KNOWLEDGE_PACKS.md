# Knowledge packs: a starter catalog

BuildBot ships with **no knowledge base**. The base model is the floor; you build
your bot's library yourself. The wizard and "add more" can find and download
books for you. This page is for picking packs by hand. Knowledge packs are offline
[ZIM](https://wiki.openzim.org/wiki/OpenZIM) archives, the same format Kiwix
uses, and BuildBot reads them directly with no server required. Drop a pack into
your knowledge folder and BuildBot learns it in the background on the next
start (see the note at the bottom).

**Where to get them.** Every pack below lives in the public Kiwix library:

> **https://library.kiwix.org**

Browse or search that catalog, download the `.zim` you want, and drop it in your
knowledge folder. We deliberately don't link individual files here, because versions
and sizes change monthly and the catalog always shows the current ones. The sizes
below are **approximate** and meant only to set expectations; the catalog is
authoritative.

Nothing here is required, and nothing is bundled. This is a suggested starting
point, not a shopping list. Pick the few that match how you'll actually use your bot.

---

## Wikipedia & Wikimedia

The backbone of an offline reference library.

| Pack | What it adds | Approx. size |
|------|--------------|-------------|
| **Wikipedia (full, with images)** | The complete English Wikipedia, the single biggest knowledge upgrade you can give your bot. | ~90 GB |
| **Wikipedia (no images)** | The full text of English Wikipedia without the image payload. | ~55 GB |
| **Wikipedia (mini)** | Lead paragraphs only: a tiny, fast general-knowledge layer. | ~300 MB |
| **Best of Wikipedia** | The most-read and highest-quality articles, with most of the value at a fraction of the size. | ~2–15 GB |
| **Simple English Wikipedia** | Plain-language articles; good for clarity and for kid profiles. | ~700 MB |
| **Wikipedia: Physics / History / Knots** | Topic-scoped Wikipedia slices when you want depth in one subject, not everything. | ~100 MB–3 GB each |
| **Wikispecies** | A taxonomy of living things. | ~150–600 MB |
| **Wikisource** | Full source texts of public-domain books and documents. | large (up to tens of GB) |
| **Wikivoyage** | A worldwide travel guide. | ~200 MB–3 GB |
| **WikiSummaries** | Condensed book summaries. | ~20 MB |
| **MediaWiki** | Documentation for the MediaWiki software. | ~50 MB |
| **OpenStreetMap Wiki** | The reference wiki behind OpenStreetMap tagging and mapping. | ~1–3 GB |
| **RationalWiki** | Skeptical coverage of pseudoscience, claims, and logical fallacies. | ~15 MB–1 GB |

## Q&A (Stack Exchange)

Practical, answer-shaped knowledge, often more directly useful than an
encyclopedia for "how do I actually do this" questions.

| Pack | What it adds | Approx. size |
|------|--------------|-------------|
| **Amateur Radio** | Ham radio operating, licensing, and equipment Q&A. | ~50–200 MB |
| **Home Improvement (DIY)** | Repairs, tools, and home projects. | ~200–500 MB |
| **Gardening & Landscaping** | Growing food and plants, soil, and pests. | ~50–150 MB |
| **Homebrewing** | Beer, cider, and fermentation. | ~30–80 MB |
| **Engineering** | Practical engineering across disciplines. | ~30–80 MB |
| **Information Security** | Threats, defenses, and practical security. | ~100–300 MB |
| **Medical Sciences** | Physiology and clinical-science questions. | ~50–150 MB |

## TED talks

Curated topic collections of TED talks with English transcripts (the transcript
text is what gets indexed).

| Pack | What it adds | Approx. size |
|------|--------------|-------------|
| **TED: surveillance / human rights / justice system / democracy / law** | Talks on rights, governance, and civil liberties. | ~100 MB–1 GB each |
| **TED: solar energy / water / plants / maps** | Talks on energy, environment, and the natural world. | ~100 MB–1 GB each |
| **TED: military / sociology / coronavirus** | Talks on society, conflict, and public health. | ~100 MB–1 GB each |

## Reference & medical

| Pack | What it adds | Approx. size |
|------|--------------|-------------|
| **MDWiki** | An open medical encyclopedia. | ~1–2 GB |
| **WikEM** | Emergency-medicine reference. | ~20–300 MB |
| **Medicine (LibreTexts)** | Open medical course material. | ~1 GB |
| **Medical Library (zimgit)** | A curated offline home-medical reference. | ~600 MB |
| **Military Medicine** | Field and trauma medicine documents. | ~200 MB |
| **Biology Courses** | Open biology coursework. | small–medium |

## Reference, survival & practical skills

Built for the offline, no-internet case, which is the one BuildBot is designed for.

| Pack | What it adds | Approx. size |
|------|--------------|-------------|
| **Ready.gov (English & Spanish)** | Official U.S. emergency-preparedness guidance. | ~200 MB |
| **Water Treatment Library** | How to make water safe to drink. | a few MB |
| **A Library of Knots** | Practical knot-tying reference. | ~9 MB |
| **Winter Prepping** | Cold-weather preparedness. | small |
| **Urban Prepper** | Preparedness in a city context. | small–medium |

## Odds & ends

| Pack | What it adds | Approx. size |
|------|--------------|-------------|
| **Explain XKCD** | Annotated explanations of every XKCD comic. | ~500 MB |
| **Incognito Cat** | A practical digital-privacy guide. | ~9 MB |
| **Prunelle** | Interactive books. | small |

---

## How BuildBot uses them

1. **The knowledge folder.** The wizard makes a `knowledge_packs` folder in your
   bot's data folder, unless you name another with `puffpai setup --zim-dir`.
   You can change it later under `knowledge.zim_dir` in your config.
2. **Drop packs in.** Download from the Kiwix library above and copy the `.zim`
   into that folder.
3. **Auto-indexing does the rest.** With auto-indexing on (the default), BuildBot
   scans the folder on startup and indexes any new packs **in the background**.
   Chat starts immediately, and large packs (a full Wikipedia can take hours) keep
   indexing while you talk. Check progress with `puffpai knowledge status`.

Prefer to do it by hand? `puffpai knowledge sync` indexes new packs in the
foreground, and `puffpai knowledge list` shows what's already indexed.

Indexed knowledge is general reference and lives in Zone 2. It is never mixed with your
private data, which lives encrypted in the vault (Zone 1) and is only ever read
through the Archivist.
