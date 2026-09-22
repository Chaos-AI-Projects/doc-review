---
marp: true
theme: default
paginate: true
---

# A deck with no server behind it

This file is the talk `standalone.html` shows when no `?talk=` names another.

Slice 3 copies both into `ChaosEternal.github.io/slides/`, where there is no
application at all -- only files, fetched as plain text by the page that sits
beside them.

---

## How a slide gets here

The page fetches three files next to itself: `renderer.py`, `view_specs.py`
and this markdown. Pyodide runs the first two, which are the very modules the
review app imports, so a slide cannot render one way in review and another way
on stage.

---

## What splits the slides

A `---` rule, and front matter that says `marp: true`. Without the directive
the page states the condition rather than showing an empty deck, because a
document that never asked to be a presentation is not a broken presentation.

---

## Getting around

Arrow keys, or the two controls in the corner. There is no exit: nothing sits
behind these slides to go back to, so `Esc` leaves the deck where it is and the
browser keeps its own shortcuts.
