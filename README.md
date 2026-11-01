# HealthAI Group Website

Public website for the [Health AI Research (HealthAI) group](https://maastrichtuniversity.nl) at the [Institute of Data Science](https://ids.maastrichtuniversity.nl), Maastricht University.

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

### Pure / CRIS (authoritative for UM affiliation)

Pure knows which publications belong to Maastricht people, attributes them per
person, and carries DOIs. One call per person; no title matching.

```bash
./scripts/refresh_publications.sh          # everything
python3 scripts/fetch_pure_publications.py # just the Pure step
```

**Network.** `crisapi` answers only on the university network. In a cluster pod
set `PURE_HTTPS_PROXY` to the egress-proxy; on a node leave it unset. Do not
rely on ambient `HTTPS_PROXY`: n3's points at the general university proxy,
which returns `503` for crisapi.

**API notes** (spec at `/swagger/docs/v524`, university network only):

- `format=json` is required; the `Accept` header is ignored and XML comes back.
- Paging is `page=N`, 1-based. `offset` is accepted and silently ignored.
- `/api/ResearchOutput/{id}` needs **`ID_type`** — `person_uuids`, `org_uuids`,
  `pub_uuids`. Without it the path id is ignored and you get everything.
- `/api/Person/{orcid}?ID_type=orcid` returns a **bare object**, not
  `{"items": […]}`.
- `searchString` works on Person, Organisation and ResearchOutput.
- An unrecognised parameter returns HTTP 200 with the **full unfiltered
  result**, never an error. Always check the count actually changed.
- The service occasionally returns non-JSON; the fetcher retries once.

### Sync blog feeds

```bash
python3 scripts/fetch_blog_posts.py [--dry-run]
```

Reads `blog_feed` URLs from `content/people.yaml` and syncs posts into the `blog` section of `content/news.yaml`. Manual entries (no `source: feed` field) are always preserved.

### Sync LinkedIn posts

```bash
python3 scripts/fetch_linkedin_posts.py [--dry-run] [--scan 150] [--keep 35]
```

Writes `content/linkedin.generated.yaml` and mirrors post images into
`assets/linkedin/`. The news page shows them under their own **LinkedIn** tab,
not in **All**: nobody signs them off, so they stay out of the edited list.

The source is the DACS page (`urn:li:organization:27209119`), which carries
departmental news. A post is kept only when it names someone in
`content/people.yaml`, by LinkedIn mention or in plain text. Of 150 posts
scanned in October 2026, 55 named a member; 20 of those were reposts of an
announcement already kept, leaving 35.

**Reposts.** A call for papers goes out five or six times with a new deadline
each time. Two posts count as the same announcement when their content words
overlap by 45% of the shorter one, and only the most recent survives, with a
`reposts` count.

**Privacy.** Person URNs from mentions, likes and comments stay in memory and
never reach the YAML; only counts and the display names of people already on
this site are written. Email addresses in post text are dropped unless
`people.yaml` already publishes that exact address.

**Auth.** Three routes, in order: `LINKEDIN_ACCESS_TOKEN`;
`LINKEDIN_REFRESH_TOKEN` with `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET`,
exchanged for a fresh access token on every run; or `.linkedin_token.json` from
`scripts/linkedin_auth.py` (scope `r_organization_social`). `LinkedIn-Version:
202610` is the oldest version the API still accepts. `/rest/images` has a daily
cap, so images already mirrored are never re-fetched.

**Weekly in the cluster.**

```bash
PUSH_MODE=push ./scripts/refresh_linkedin.sh
```

Runs Sundays at 05:00 UTC from `ids3/projects/healthai-website/dev` in
MaastrichtU-IDS/services, on its own clock beside the publication job so that
an expired LinkedIn token cannot stop publications refreshing. It clones main,
fetches, and commits only `content/linkedin.generated.yaml` and
`assets/linkedin/`. It aborts without pushing if no post survives the filter,
or if the count falls below `MIN_RATIO` (0.80) of the previous run. When
nothing but the fetch date moved it restores the file and commits nothing, so
a quiet week leaves no commit.

The cluster uses the refresh-token route, because an access token lasts 60
days and a stored one would have the job stop two months later with nothing
visible to say so. The refresh token lasts a year and LinkedIn does not rotate
it, so re-run `scripts/linkedin_auth.py` and update Vault before it lapses;
the fetcher warns on every run once fewer than 30 days remain.

### Enrich existing people entries

```bash
python3 scripts/enrich_people.py
```

Re-scrapes UM profile pages to refresh `image_url` and `bio` for existing entries. Caches HTML under `cache/people_pages/`.

## Deployment

The site is a static file tree — deploy by pushing to the `main` branch. No build step required.

## License

See [LICENSE](LICENSE).
