# GTFS editions in vehicle blocks

An edition is the pair `gtfs_year` / `gtfs_file_date`. No database migration is
required: routes already contain that identity, and vehicle blocks reference
trips belonging to those routes.

`GET /api/v1/gtfs/datasets` requires authentication and returns only the user's
company catalog, ordered by year and file date descending:

```json
[{"gtfs_year":2027,"gtfs_file_date":"2026-11-04","valid_from":"2026-12-13","valid_to":"2027-12-11"}]
```

Validity is the minimum start and maximum end of calendars linked to loaded
GTFS trips in that company's edition. It is a calendar envelope, not a claim
that every trip operates every day. Auxiliary trips never determine an edition.
Weekday filtering remains unchanged; holiday exceptions are outside this feature.

Vehicle-block list, detail and info responses include `gtfs_datasets` with this
same shape. Empty arrays mean no GTFS trips; multiple entries explicitly identify
historical mixed blocks. List queries aggregate all returned blocks together.

Create/update requests may include `gtfs_dataset` containing only year and file
date. Older clients may omit it. The server compares the reference with the
actual accessible trips, rejects mixed editions, and prevents changing or
removing an existing block's GTFS edition. Create a new block to use another
edition. Ordinary duplication keeps the source trips and therefore its edition.
Structure updates are serialized on the block row.

New optimization runs capture `input_params.gtfs_provenance` as
`{"version":1,"source":"recorded","shifts":{"<uuid>":[<dataset>]}}`.
Annual analyses inherit this snapshot in `features.gtfs_provenance`; client
feature changes cannot forge or overwrite it. Historical analyses without a
snapshot remain distinguishable: the UI labels metadata derived from the
current block as current information, rather than execution provenance.

Tests: `tests/test_gtfs_datasets.py`, `tests/test_gtfs_provenance_routes.py`,
and `tests/test_gtfs_datasets_isolated.py`. The latter exercises authenticated
HTTP requests against an explicitly isolated PostgreSQL restore and rolls its
fixtures back. It refuses staging and production database names.
