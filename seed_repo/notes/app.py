from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from .db import connect

app = FastAPI(title="notes")


class NoteIn(BaseModel):
    title: str
    body: str
    tags: list[str] = []


class NoteOut(BaseModel):
    id: int
    title: str
    body: str
    created_at: str
    tags: list[str]


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def _tags(db, note_id: int) -> list[str]:
    return [r["tag"] for r in db.execute("SELECT tag FROM note_tags WHERE note_id = ?", (note_id,))]


@app.post("/notes", response_model=NoteOut, status_code=201)
def create_note(note: NoteIn, db=Depends(get_db)):
    cur = db.execute("INSERT INTO notes (title, body) VALUES (?, ?)", (note.title, note.body))
    db.executemany("INSERT INTO note_tags (note_id, tag) VALUES (?, ?)", [(cur.lastrowid, t) for t in note.tags])
    db.commit()
    return get_note(cur.lastrowid, db)


@app.get("/notes", response_model=list[NoteOut])
def list_notes(tag: str | None = None, db=Depends(get_db)):
    rows = db.execute("SELECT * FROM notes ORDER BY id").fetchall()
    notes = [NoteOut(**dict(r), tags=_tags(db, r["id"])) for r in rows]
    return [n for n in notes if tag is None or tag in n.tags]


@app.get("/notes/{note_id}", response_model=NoteOut)
def get_note(note_id: int, db=Depends(get_db)):
    row = db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "note not found")
    return NoteOut(**dict(row), tags=_tags(db, note_id))


@app.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: int, db=Depends(get_db)):
    db.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
    if db.execute("DELETE FROM notes WHERE id = ?", (note_id,)).rowcount == 0:
        raise HTTPException(404, "note not found")
    db.commit()
