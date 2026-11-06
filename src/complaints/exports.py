"""Export the complaint list to CSV and PDF (SCMS-F-026).

Both formats are built from the same rows and the same column
definition, so the two exports and the page on screen cannot
disagree about what a complaint says. The acceptance criterion --
exported row count and column values match the filtered list --
is met by construction rather than by keeping three lists in step
by hand.
"""

import csv
from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

#: (header, accessor). One definition for both formats.
COLUMNS = (
    ("Reference", lambda c: c.reference_id),
    ("Title", lambda c: c.title),
    ("Category", lambda c: c.category.name if c.category else ""),
    ("Department", lambda c: (
        c.assigned_department.name if c.assigned_department else ""
    )),
    ("Priority", lambda c: c.get_priority_display()),
    ("Status", lambda c: c.get_status_display()),
    ("Assigned to", lambda c: (
        c.assigned_to.get_username() if c.assigned_to else ""
    )),
    ("Lodged", lambda c: c.created_at.strftime("%Y-%m-%d %H:%M")),
    ("SLA due", lambda c: (
        c.sla_due_at.strftime("%Y-%m-%d %H:%M") if c.sla_due_at else ""
    )),
)

HEADERS = [header for header, _ in COLUMNS]


def rows_for(complaints):
    """Every complaint as a list of strings, in column order."""
    return [
        [accessor(complaint) for _, accessor in COLUMNS]
        for complaint in complaints
    ]


def filename(extension):
    stamp = timezone.now().strftime("%Y%m%d-%H%M")

    return f"scms-complaints-{stamp}.{extension}"


def as_csv(complaints):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="{filename("csv")}"'
    )

    writer = csv.writer(response)
    writer.writerow(HEADERS)
    writer.writerows(rows_for(complaints))

    return response


def as_pdf(complaints):
    """Landscape A4, because nine columns do not fit portrait."""
    rows = rows_for(complaints)
    buffer = BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        title="SCMS complaints",
        leftMargin=18,
        rightMargin=18,
        topMargin=18,
        bottomMargin=18,
    )

    styles = getSampleStyleSheet()
    cell = styles["BodyText"]
    cell.fontSize = 7
    cell.leading = 9

    story = [
        Paragraph("SCMS - Complaint list", styles["Heading2"]),
        Paragraph(
            f"{len(rows)} complaint(s). Exported "
            f"{timezone.now().strftime('%Y-%m-%d %H:%M')}.",
            styles["Normal"],
        ),
        Spacer(1, 10),
    ]

    # Paragraph rather than a bare string in every cell, so a long
    # title wraps instead of running off the page or being cut.
    table_data = [[Paragraph(h, cell) for h in HEADERS]] + [
        [Paragraph(str(value), cell) for value in row] for row in rows
    ]

    table = Table(table_data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3f9")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8d2e0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))

    story.append(table)
    document.build(story)

    response = HttpResponse(
        buffer.getvalue(), content_type="application/pdf",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{filename("pdf")}"'
    )

    return response
