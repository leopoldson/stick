"""Builds the downloadable Excel log (mirrors the original Study & Active Recall workbook)."""
import math
from datetime import date, datetime, timedelta
from io import BytesIO

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
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


def _table(ws, top, left, headers, rows):
    """Small plain table used as chart source data."""
    for j, h in enumerate(headers):
        c = ws.cell(row=top, column=left + j, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = _fill(NAVY)
    for i, row in enumerate(rows, start=1):
        for j, v in enumerate(row):
            ws.cell(row=top + i, column=left + j, value=v)
    for j in range(len(headers)):
        ws.column_dimensions[get_column_letter(left + j)].width = max(
            12, max([len(str(headers[j]))] + [len(str(r[j])) for r in rows]) + 3
        )


def _style_chart(chart, title, y_title):
    chart.title = title
    chart.legend = None
    chart.height, chart.width = 7.5, 17
    chart.x_axis.delete = False
    chart.y_axis.delete = False
    chart.y_axis.title = y_title
    return chart


def _dashboard(wb, rows, sessions, recall_logs):
    ws = wb.create_sheet("Dashboard")
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
    ws.column_dimensions["C"].width = 4
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)

    today = clock.today_local()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    per_day = {d: 0 for d in days}
    per_course: dict[str, int] = {}
    for sess in sessions:
        mins = stats.minutes_between(sess)
        day = clock.local_date(sess.started_at)
        if day in per_day:
            per_day[day] += mins
        code = sess.topic.course.code
        per_course[code] = per_course.get(code, 0) + mins
    if not sessions:
        ws["E4"] = "Charts appear here after your first logged session."
        ws["E4"].font = Font(italic=True, color=GREY)
        return ws

    data = wb.create_sheet("Chart Data")  # the numbers behind the charts, visible so they can be checked
    _table(data, 1, 1, ["Day", "Minutes"], [[f"{d.day} {d:%b}", per_day[d]] for d in days])
    day_chart = _style_chart(BarChart(), "Minutes studied, last 14 days", "Minutes")
    day_chart.type = "col"
    day_chart.add_data(Reference(data, min_col=2, min_row=1, max_row=15), titles_from_data=True)
    day_chart.set_categories(Reference(data, min_col=1, min_row=2, max_row=15))
    day_chart.series[0].graphicalProperties.solidFill = NAVY
    ws.add_chart(day_chart, "E3")

    ranked = sorted(per_course.items(), key=lambda x: -x[1])
    _table(data, 1, 4, ["Course", "Minutes"], [[c, m] for c, m in ranked])
    course_chart = _style_chart(BarChart(), "Time by course (minutes)", "Minutes")
    course_chart.type = "bar"
    course_chart.add_data(Reference(data, min_col=5, min_row=1, max_row=len(ranked) + 1), titles_from_data=True)
    course_chart.set_categories(Reference(data, min_col=4, min_row=2, max_row=len(ranked) + 1))
    course_chart.series[0].graphicalProperties.solidFill = "F2B84B"
    ws.add_chart(course_chart, "E19")

    weeks: dict[date, list[int]] = {}
    for log in recall_logs:
        day = clock.local_date(log.at)
        monday = day - timedelta(days=day.weekday())
        passed, total = weeks.get(monday, [0, 0])
        weeks[monday] = [passed + (1 if log.passed else 0), total + 1]
    if weeks:
        ordered = sorted(weeks.items())[-8:]
        _table(data, 1, 7, ["Week of", "Recall score (%)"],
               [[f"{d.day} {d:%b}", round(100 * p / t)] for d, (p, t) in ordered])
        recall_chart = _style_chart(LineChart(), "Recall score by week (%)", "% remembered")
        recall_chart.add_data(Reference(data, min_col=8, min_row=1, max_row=len(ordered) + 1), titles_from_data=True)
        recall_chart.set_categories(Reference(data, min_col=7, min_row=2, max_row=len(ordered) + 1))
        recall_chart.y_axis.scaling.min, recall_chart.y_axis.scaling.max = 0, 100
        recall_chart.series[0].graphicalProperties.line.solidFill = NAVY
        recall_chart.series[0].marker.symbol = "circle"
        recall_chart.series[0].smooth = False  # straight lines: no invented values between weeks
        ws.add_chart(recall_chart, "E35")
    return ws


def build_workbook(db: Session, user: User) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)

    sessions = stats.finished_sessions(db, user)
    total_min = sum(stats.minutes_between(s) for s in sessions)
    passed, attempts = stats.retention(db, user)
    recall_logs = db.scalars(select(RecallLog).where(RecallLog.user_id == user.id).order_by(RecallLog.at)).all()
    _dashboard(wb, [
        ("Total study time", stats.fmt_minutes(total_min)),
        ("Sessions logged", len(sessions)),
        ("Current streak", stats.plural(stats.streak(db, user), "day")),
        ("Longest streak", stats.plural(stats.best_streak(db, user), "day")),
        ("Recall checks passed", f"{passed} of {attempts}" if attempts else "none yet"),
        ("Retention score", f"{round(100 * passed / attempts)}%" if attempts else "n/a"),
    ], sessions, recall_logs)

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
    has_titles = any(c.title for c in courses)
    rows = []
    for c in courses:
        names = db.scalars(select(Topic).where(Topic.course_id == c.id).order_by(Topic.name)).all()
        topics_text = ", ".join(t.name for t in names)
        rows.append([c.code, c.title, topics_text] if has_titles else [c.code, topics_text])
    _sheet(wb, "Courses & Topics", ["Course", "Title", "Topics"] if has_titles else ["Course", "Topics"], rows)

    if "Chart Data" in wb.sheetnames:
        wb.move_sheet("Chart Data", offset=len(wb.sheetnames))  # keep the working sheets first

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
