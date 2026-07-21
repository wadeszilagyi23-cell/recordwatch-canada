# RecordWatch Canada — Starter Version

This starter implements the approved **Map First + News and Records + parameter buttons** design using plain HTML, CSS, JavaScript, Leaflet and Python.

## What already works

- Responsive Canada map with coloured record markers
- Weather-parameter toggle buttons
- Record popups and legend
- Automated summary cards and Record of the Day
- Automated regional highlight text
- Searchable community markers
- Daily record table and CSV download
- Previous Records archive loader
- Demonstration data label that disappears after the first live update
- GitHub Actions workflow with scheduled and manual runs

## Data automation

`scripts/update_records.py` queries these ECCC MSC GeoMet collections:

- `ltce-temperature`
- `ltce-precipitation`
- `ltce-snowfall`

For the chosen month and day, it retains records whose official record year equals the target year. A record is labelled **tied** when the current record value equals the previous record value; otherwise it is labelled **broken**.

The script writes:

- `data/latest.json`
- `data/archive/YYYY/MM/YYYY-MM-DD.json`
- `data/archive-index.json`

The archive begins when this website starts saving snapshots. The current LTCE feed is not a complete event-by-event archive of every record ever established, so the site should not promise a full historical backfill without an additional historical-data method.

## Local preview

Opening `index.html` directly may block JSON loading in some browsers. Use a local web server:

```bash
python -m http.server 8000
```

Then visit `http://localhost:8000`.

## First GitHub setup

1. Create a new empty GitHub repository.
2. Upload all contents of this folder, including the hidden `.github` folder.
3. Commit directly to the `main` branch.
4. In **Settings → Actions → General**, set Workflow permissions to **Read and write permissions** and save.
5. Open **Actions → Update RecordWatch Canada → Run workflow**.
6. After the green checkmark, confirm that `data/latest.json` no longer says `"isDemo": true`.
7. In **Settings → Pages**, deploy from the `main` branch and `/ (root)` folder.

## Schedule

The workflow is configured to run at **10:37 a.m. and 4:37 p.m. America/Toronto**. It avoids the top of the hour because scheduled GitHub Actions may be delayed during high-load periods. The manual Run workflow button remains available.

## Important review item

Before public launch, verify several generated records against ECCC output. Climate observations can be revised, and the website should visibly describe the data as preliminary.
