# notes

A small, pre-existing notes service used as the brownfield target. FastAPI + stdlib sqlite3.

- `POST /notes` create a note with tags
- `GET /notes?tag=` list notes, optionally filtered by tag
- `GET /notes/{id}` fetch one
- `DELETE /notes/{id}` delete one

Run tests with `pytest` from this directory.
