"""Builds the downloadable Excel log (mirrors the original Study & Active Recall workbook)."""
import math
from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock, stats
from .models import Course, Question, RecallLog, Review, Topic, User

NAVY = "1F2A44"
GREY = "6B7280"
RATING = {1: "Shaky", 2: "Okay", 3: "Solid"}
RATING_FILL = {"Solid": "C6EFCE", "Okay": "FFEB9C", "Shaky": "FFC7CE"}

_THIN = Side(style="thin", color="D9DEE7")
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
BAND = PatternFill("solid", fgColor="F3F5F9")


def _fill(hex_colour: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_colour)


MAX_WIDTH = 45  # wider than this and the text wraps onto more lines instead
DATE_CHARS = 16  # "Mon 16 Nov 2026"


def _chars(value) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, (date, datetime)):
        return DATE_CHARS
    return max(len(line) for line in str(value).split("\n"))


def _sheet(wb, title, headers, rows, date_cols=(), centre_cols=()):
    ws = wb.create_sheet(title)
    ws.sheet_view.showGridLines = False
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _fill(NAVY)
        cell.alignment = Alignment(vertical="center", horizontal="center" if cell.column in centre_cols else "left")
        cell.border = BORDER
    ws.row_dimensions[1].height = 24

    if not rows:
        ws.append(["Nothing here yet"] + [""] * (len(headers) - 1))
        ws["A2"].font = Font(italic=True, color=GREY)
    for row in rows:
        ws.append(row)

    # Column widths fit the longest entry (up to MAX_WIDTH); longer text wraps and the row grows to match.
    widths = []
    for col in range(1, len(headers) + 1):
        longest = max([_chars(headers[col - 1])] + [_chars(r[col - 1]) for r in rows])
        widths.append(min(max(longest + 3, 10), MAX_WIDTH))
        ws.column_dimensions[get_column_letter(col)].width = widths[-1]

    for r in range(2, ws.max_row + 1):
        lines = 1
        for cell in ws[r]:
            cell.border = BORDER
            cell.alignment = Alignment(
                vertical="top", wrap_text=True, horizontal="center" if cell.column in centre_cols else "left"
            )
            if r % 2 == 1:
                cell.fill = BAND
            if cell.column in date_cols:
                cell.number_format = "ddd dd mmm yyyy"
            if cell.value and not isinstance(cell.value, (date, datetime)):
                usable = max(widths[cell.column - 1] - 2, 1)
                lines = max(lines, sum(math.ceil(max(len(x), 1) / usable) for x in str(cell.value).split("\n")))
        ws.row_dimensions[r].height = max(20, 16 * lines + 4)

    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    return ws


def _summary(wb, rows):
    ws = wb.create_sheet("Summary")
    ws.sheet_view.showGridLines = False
    ws["A1"] = "Stick study log"
    ws["A1"].font = Font(size=18, bold=True, color=NAVY)
    ws["A2"] = f"Exported {clock.today_local():%d %b %Y}"
    ws["A2"].font = Font(color=GREY)
    for i, (label, value) in enumerate(rows, start=4):
        ws.cell(row=i, column=1, value=label).font = Font(bold=True, color=NAVY)
        ws.cell(row=i, column=2, value=value).alignment = Alignment(horizontal="left")
        for col in (1, 2):
            ws.cell(row=i, column=col).border = Border(bottom=_THIN)
            ws.cell(row=i, column=col).fill = BAND if i % 2 == 0 else _fill("FFFFFF")
    ws.column_dimensions["A"].width = max(len(str(r[0])) for r in rows) + 6
    ws.column_dimensions["B"].width = max(len(str(r[1])) for r in rows) + 6
    return ws


def build_workbook(db: Session, user: User) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)

    sessions = stats.finished_sessions(db, user)
    total_min = sum(stats.minutes_between(s) for s in sessions)
    passed, attempts = stats.retention(db, user)
    _summary(wb, [
        ("Total study time", stats.fmt_minutes(total_min)),
        ("Sessions logged", len(sessions)),
        ("Current streak", f"{stats.streak(db, user)} days"),
        ("Recall checks passed", f"{passed} of {attempts}" if attempts else "none yet"),
        ("Retention score", f"{round(100 * passed / attempts)}%" if attempts else "n/a"),
    ])

    log = _sheet(
        wb, "Daily Study Log",
        ["Date", "Course", "Topic", "Started", "Ended", "Minutes", "How solid"],
        [
            [
                clock.local_date(s.started_at), s.topic.course.code, s.topic.name,
                clock.to_local_naive(s.started_at).strftime("%H:%M"),
                clock.to_local_naive(s.ended_at).strftime("%H:%M"),
                stats.minutes_between(s), RATING.get(s.rating, ""),
            ]
            for s in sessions
        ],
        date_cols=(1,), centre_cols=(4, 5, 6, 7),
    )
    for row in log.iter_rows(min_row=2, min_col=7, max_col=7):
        for cell in row:
            if cell.value in RATING_FILL:
                cell.fill = _fill(RATING_FILL[cell.value])

    reviews = db.scalars(select(Review).where(Review.user_id == user.id).order_by(Review.due_on)).all()
    tracker = _sheet(
        wb, "Revision Tracker",
        ["Course", "Topic", "Step", "Next review", "Needs revision"],
        [[r.topic.course.code, r.topic.name, f"{r.step + 1} of 5", r.due_on, "Yes" if r.needs_revision else ""] for r in reviews],
        date_cols=(4,), centre_cols=(3, 5),
    )
    for row in tracker.iter_rows(min_row=2, min_col=5, max_col=5):
        for cell in row:
            if cell.value == "Yes":
                cell.fill = _fill("FFC7CE")

    topics = {t.id: t for t in db.scalars(select(Topic).join(Course).where(Course.user_id == user.id)).all()}
    qs = db.scalars(
        select(Question).join(Topic).join(Course).where(Course.user_id == user.id).order_by(Course.code, Topic.name)
    ).all()
    _sheet(
        wb, "Active Recall", ["Course", "Topic", "Question", "Answer"],
        [[topics[q.topic_id].course.code, topics[q.topic_id].name, q.prompt, q.answer or "(check your notes)"] for q in qs],
    )

    logs = db.scalars(select(RecallLog).where(RecallLog.user_id == user.id).order_by(RecallLog.at)).all()
    history = _sheet(
        wb, "Recall History", ["Date", "Course", "Topic", "Result"],
        [[clock.local_date(x.at), x.topic.course.code, x.topic.name, "Got it" if x.passed else "Missed"] for x in logs],
        date_cols=(1,), centre_cols=(4,),
    )
    for row in history.iter_rows(min_row=2, min_col=4, max_col=4):
        for cell in row:
            if cell.value == "Got it":
                cell.fill = _fill(RATING_FILL["Solid"])
            elif cell.value == "Missed":
                cell.fill = _fill("FFC7CE")

    courses = db.scalars(select(Course).where(Course.user_id == user.id).order_by(Course.code)).all()
    rows = []
    for c in courses:
        names = db.scalars(select(Topic).where(Topic.course_id == c.id).order_by(Topic.name)).all()
        rows.append([c.code, c.title, ", ".join(t.name for t in names)])
    _sheet(wb, "Courses & Topics", ["Course", "Title", "Topics"], rows)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
