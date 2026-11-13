import pytest

from app.textfmt import tidy_name


@pytest.mark.parametrize("typed,stored", [
    ("discrete structures", "Discrete Structures"),
    ("csc301", "CSC301"),
    ("OOU-CSC331", "OOU-CSC331"),
    ("data structures and algorithms", "Data Structures and Algorithms"),
    ("DISCRETE STRUCTURES", "Discrete Structures"),
    ("programming iii", "Programming III"),
    ("object-oriented analysis", "Object-Oriented Analysis"),
    ("newton's laws", "Newton's Laws"),
    ("intro to ai", "Intro to AI"),
    ("(intro) maths", "(Intro) Maths"),
    ("iPhone apps", "iPhone Apps"),
    ("  spaced   out  ", "Spaced Out"),
    ("sql basics", "SQL Basics"),
    ("", ""),
])
def test_tidy_name(typed, stored):
    assert tidy_name(typed) == stored
