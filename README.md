# HAIR Group Website

Public website for the [Health AI Research (HAIR) group](https://maastrichtuniversity.nl) at the [Institute of Data Science](https://ids.maastrichtuniversity.nl), Maastricht University.

## Architecture

Static site — no build step, no framework. Pages are plain HTML files that load structured content from `content/*.yaml` at runtime via `cms.js`. All content edits go in YAML; the HTML is not hand-edited for content.

| File | Purpose |
|------|---------|
| `index.html` | Homepage — hero, recent news, recent publications |
| `people.html` | Group members, filterable by theme |
| `publications.html` | Publication list with search, year slider, cite/BibTeX |
| `research.html` | Research themes and projects |
| `news.html` | News, blog posts, talks, podcasts — unified search/filter |
| `students.html` | Student projects (not linked from nav) |
| `cms.js` | YAML loader and shared rendering utilities |
| `nav.js` | Navigation bar and footer |
| `styles.css` | Global styles |

## Content files

| File | What it drives |
|------|---------------|
| `content/people.yaml` | Group members — Faculty and Researchers sections |
| `content/publications.generated.yaml` | Publications (auto-generated — do not hand-edit) |
| `content/research.yaml` | Research themes and projects |
| `content/news.yaml` | News items, blog posts, talks, podcasts |
| `content/site.yaml` | Site-wide metadata |
| `content/students.yaml` | Student project listings |

### people.yaml schema

Each entry has: `name`, `role`, `group`, `image_url`, `themes`, `bio`, `keywords`, `orcid`, `github`, `google_scholar`, `linkedin`, `personal_page`, `cris_url`, `work_url`, `email`.

- `group` is either `Faculty` or `Researchers`
- On `people.html`, the Researchers group is split by `role`: entries with `role: PhD Researcher` appear under "PhD Researchers"; all others appear under "Postdocs, Researchers, and Research Software Engineers"
- `github`, `google_scholar`, `linkedin` store bare handles/IDs (not full URLs)
- `cris_url`, `work_url`, `personal_page` store full URLs
- `image_url` should be a local path (`assets/people/<slug>.jpg`) — never a hotlinked LinkedIn URL

### news.yaml schema

The file has four top-level sections:

- `news` — `day`, `month`, `year`, `title`, `body`; optional `url`
- `blog` — managed by `fetch_blog_posts.py`; fields: `title`, `url`, `date`, `author`, `excerpt`, `source`
- `talks` — `event`, `date`, `title`, `speaker`, `type`; optional `url` (e.g. Zenodo DOI link), optional `description`
- `podcasts` — `show`, `episode`, `title`, `guest`, `duration`, `date`, `icon`

## Scripts

All scripts are in `scripts/` and require Python 3.

### Add a new person

```bash
python3 scripts/add_people.py "Full Name" "https://www.maastrichtuniversity.nl/<profile-slug>"
```

Scrapes the UM staff page for bio, portrait photo, ORCID, and CRIS URL, then appends an entry to `content/people.yaml`. Review the entry afterward — the script defaults `role` to `PhD Researcher`; correct it if needed.

### Publications pipeline

Run in order:

```bash
# 1. Fetch each person's ORCID works summary
python3 scripts/fetch_orcid_works.py [--refresh] [--name <substring>]

# 2. Fetch Crossref metadata for all unique DOIs
python3 scripts/fetch_crossref.py

# 3. Build publications.generated.yaml
python3 scripts/build_publications.py
```

Caches live in `cache/orcid/`, `cache/crossref/`, `cache/abstracts/`, and `cache/summaries/`. The pipeline is incremental — re-running only fetches new DOIs.

Optional: fetch full abstracts and generate plain-language summaries (requires [Ollama](https://ollama.ai) running locally):

```bash
python3 scripts/fetch_abstracts.py
python3 scripts/generate_summaries.py
```

### Sync blog feeds

```bash
python3 scripts/fetch_blog_posts.py [--dry-run]
```

Reads `blog_feed` URLs from `content/people.yaml` and syncs posts into the `blog` section of `content/news.yaml`. Manual entries (no `source: feed` field) are always preserved.

### Enrich existing people entries

```bash
python3 scripts/enrich_people.py
```

Re-scrapes UM profile pages to refresh `image_url` and `bio` for existing entries. Caches HTML under `cache/people_pages/`.

## Deployment

The site is a static file tree — deploy by pushing to the `main` branch. No build step required.

## License

See [LICENSE](LICENSE).
