"""Streamlit planner with an editable task table and a Plotly Gantt chart.

The JSON file stores independent project sheets. Each sheet contains project
metadata and a normalized table. The chart is generated on demand so table
editing remains responsive even for larger plans.
"""

import json
import re
import unicodedata
from datetime import date
from datetime import datetime
from math import sqrt
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components


DATA_FILE = Path("milestones.json")
TABLE_WIDTH_PX = 995
MIN_CHART_WIDTH_PX = 995

TYPES = [
    "Abschnitt",
    "Hohes Risiko",
    "Mittleres Risiko",
    "Geringes Risiko",
    "Im Plan",
    "Meilenstein",
]

BAR_COLORS = {
    "Hohes Risiko": "#7c3aed",
    "Mittleres Risiko": "#1e3a8a",
    "Geringes Risiko": "#60a5fa",
    "Im Plan": "#22c55e",
}

MILESTONE_COLOR = "#FDB500"
SUMMARY_COLOR = "#000000"
TODAY_COLOR = "rgba(239, 68, 68, 0.5)"
WEEKEND_COLOR = "#f3f4f6"
DEPENDENCY_ARROW_COLOR = "#000000"
DEPENDENCY_ARROW_WIDTH_PX = 1
BAR_WIDTH_PX = 25
SYMBOL_OUTLINE_WIDTH_PX = DEPENDENCY_ARROW_WIDTH_PX / 2
DAY_IN_MS = 24 * 60 * 60 * 1000
DAY_WIDTH_PX = 12
ROW_HEIGHT_PX = 72
CHART_MARGIN_TOP_PX = 50
CHART_MARGIN_BOTTOM_PX = 10
CHART_MARGIN_LEFT_PX = 40
CHART_MARGIN_RIGHT_PX = 20
TABLE_MARGIN_TOP_PX = 27
CHART_CONTAINER_MARGIN_TOP_PX = 0
CHART_TOP_OFFSET_PX = 46
TABLE_HEADER_HEIGHT_PX = 38
TABLE_ROW_HEIGHT_PX = 30.4
TABLE_EDITOR_ROW_HEIGHT_PX = 32
TABLE_EDITOR_EXTRA_HEIGHT_PX = 8
PFEIL_ABSTAND_TAGE = 1
ACTIVITY_NAME_INDENT = "    "

GERMAN_MONTHS = {
    1: "Januar",
    2: "Februar",
    3: "März",
    4: "April",
    5: "Mai",
    6: "Juni",
    7: "Juli",
    8: "August",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Dezember",
}
DAY_LABELS = ["M", "D", "M", "D", "F", "S", "S"]

COLUMNS = [
    "ID",
    "Name",
    "Typ",
    "Start",
    "Dauer",
    "Ende",
    "Vorgänger",
    "Resource",
]

# Streamlit's data editor does not support per-row font styles. These mappings
# encode bold section names and italic milestone names for display only. Values
# are translated back before they are written to the normalized table.
EDITOR_BOLD_TRANSLATION = str.maketrans(
    {
        **{chr(ord("A") + index): chr(0x1D400 + index) for index in range(26)},
        **{chr(ord("a") + index): chr(0x1D41A + index) for index in range(26)},
        **{chr(ord("0") + index): chr(0x1D7CE + index) for index in range(10)},
    }
)
EDITOR_ITALIC_ASCII = dict(
    zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        "𝐴𝐵𝐶𝐷𝐸𝐹𝐺𝐻𝐼𝐽𝐾𝐿𝑀𝑁𝑂𝑃𝑄𝑅𝑆𝑇𝑈𝑉𝑊𝑋𝑌𝑍"
        "𝑎𝑏𝑐𝑑𝑒𝑓𝑔ℎ𝑖𝑗𝑘𝑙𝑚𝑛𝑜𝑝𝑞𝑟𝑠𝑡𝑢𝑣𝑤𝑥𝑦𝑧",
    )
)
EDITOR_ITALIC_TRANSLATION = str.maketrans(
    {
        **EDITOR_ITALIC_ASCII,
        "Ä": f"{EDITOR_ITALIC_ASCII['A']}\u0308",
        "Ö": f"{EDITOR_ITALIC_ASCII['O']}\u0308",
        "Ü": f"{EDITOR_ITALIC_ASCII['U']}\u0308",
        "ä": f"{EDITOR_ITALIC_ASCII['a']}\u0308",
        "ö": f"{EDITOR_ITALIC_ASCII['o']}\u0308",
        "ü": f"{EDITOR_ITALIC_ASCII['u']}\u0308",
    }
)
EDITOR_PLAIN_TRANSLATION = str.maketrans(
    {
        **{bold: plain for plain, bold in EDITOR_BOLD_TRANSLATION.items()},
        **{italic: plain for plain, italic in EDITOR_ITALIC_ASCII.items()},
    }
)


def serialize_value(value):
    if pd.isna(value):
        return None
    if isinstance(value, (date, pd.Timestamp)):
        return value.isoformat()
    return value


def table_to_records(df: pd.DataFrame) -> list[dict]:
    return [
        {column: serialize_value(row[column]) for column in COLUMNS}
        for _, row in df.iterrows()
    ]


def default_project() -> dict:
    return {
        "project_name": "",
        "planning_start": None,
        "planning_end": None,
        "start_id": 1,
        "start_section_number": 1,
    }


def normalize_project_date(value, fallback: date | None = None) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return fallback
    return parsed.date()


def normalize_positive_integer(value, fallback: int = 1) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return fallback
    return max(int(parsed), 1)


def normalize_project(project: dict | None) -> dict:
    """Return project metadata with stable types and backward-compatible defaults."""
    defaults = default_project()
    if not isinstance(project, dict):
        return defaults

    return {
        "project_name": str(project.get("project_name", defaults["project_name"]) or ""),
        "planning_start": normalize_project_date(
            project.get("planning_start", defaults["planning_start"]),
            defaults["planning_start"],
        ),
        "planning_end": normalize_project_date(
            project.get("planning_end", defaults["planning_end"]),
            defaults["planning_end"],
        ),
        "start_id": normalize_positive_integer(
            project.get("start_id", defaults["start_id"]),
            defaults["start_id"],
        ),
        "start_section_number": normalize_positive_integer(
            project.get(
                "start_section_number",
                defaults["start_section_number"],
            ),
            defaults["start_section_number"],
        ),
    }


def project_entry(project: dict | None = None, milestones: pd.DataFrame | None = None) -> dict:
    """Build one independently editable and persistable project sheet."""
    normalized_project = normalize_project(project)
    return {
        "project": normalized_project,
        "milestones": normalize_table(
            milestones if milestones is not None else default_table(),
            start_id=normalized_project["start_id"],
            start_section_number=normalized_project["start_section_number"],
        ),
    }


def save_projects(projects: list[dict]) -> None:
    """Persist all project sheets as human-readable JSON."""
    DATA_FILE.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project": {
                            key: serialize_value(value)
                            for key, value in normalize_project(entry["project"]).items()
                        },
                        "milestones": table_to_records(entry["milestones"]),
                    }
                    for entry in projects
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_projects() -> list[dict]:
    """Load current and legacy JSON formats without preventing app startup."""
    if not DATA_FILE.exists():
        return [project_entry()]

    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [project_entry(default_project(), pd.DataFrame(data))]
        if not isinstance(data, dict):
            raise ValueError("JSON root must be an object or a list.")

        if isinstance(data.get("projects"), list):
            projects = []
            for item in data["projects"]:
                if not isinstance(item, dict):
                    continue
                records = item.get("milestones", [])
                if not isinstance(records, list):
                    records = []
                projects.append(project_entry(item.get("project"), pd.DataFrame(records)))
            return projects

        records = data.get("milestones", [])
        if not isinstance(records, list):
            records = []
        return [project_entry(data.get("project"), pd.DataFrame(records))]
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        st.warning("Die JSON-Datei konnte nicht geladen werden. Es wird eine neue Tabelle verwendet.")
        return [project_entry()]


def default_table() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def strip_hierarchy_prefix(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"^\s*\d+\.(?:\d+)?\s*", "", str(value))


def number_hierarchy(rows: list[dict], start_section_number: int = 1) -> None:
    """Apply visible section and activity prefixes according to row order."""
    section_number = start_section_number - 1
    activity_number = 0

    for row in rows:
        base_name = strip_hierarchy_prefix(row["Name"])
        if row["Typ"] == "Abschnitt":
            section_number += 1
            activity_number = 0
            row["Name"] = f"{section_number}. {base_name}".rstrip()
        elif row["Typ"] in BAR_COLORS and section_number > 0:
            activity_number += 1
            row["Name"] = (
                f"{ACTIVITY_NAME_INDENT}{section_number}.{activity_number} {base_name}"
            ).rstrip()
        else:
            row["Name"] = base_name


def extend_section_durations(rows: list[dict]) -> None:
    """Extend sections to the latest subordinate activity end date, never shorten them."""
    section_indexes = [
        row_index
        for row_index, row in enumerate(rows)
        if row["Typ"] == "Abschnitt"
    ]
    for section_position, section_index in enumerate(section_indexes):
        next_section_index = (
            section_indexes[section_position + 1]
            if section_position + 1 < len(section_indexes)
            else len(rows)
        )
        activity_ends = [
            row["Ende"]
            for row in rows[section_index + 1 : next_section_index]
            if row["Typ"] in BAR_COLORS and row["Ende"] is not None
        ]
        if not activity_ends:
            continue

        section = rows[section_index]
        latest_activity_end = max(activity_ends)
        if section["Ende"] is None or latest_activity_end > section["Ende"]:
            section["Ende"] = latest_activity_end
            if section["Start"] is not None:
                section["Dauer"] = max(
                    (section["Ende"] - section["Start"]).days + 1,
                    1,
                )


def normalize_table(
    df: pd.DataFrame,
    previous_df: pd.DataFrame | None = None,
    start_id: int = 1,
    start_section_number: int = 1,
) -> pd.DataFrame:
    """Normalize edited rows and recompute their dependent scheduling fields.

    Dates are inclusive: an activity starting on a Monday with duration one
    also ends on Monday. Incomplete rows remain valid so users can build and
    clear a table incrementally. IDs are derived from row order and start_id.
    """
    df = df.copy()

    for column in COLUMNS:
        if column not in df.columns:
            df[column] = None

    raw_df = df[COLUMNS].reset_index(drop=True)
    previous_df = previous_df.reset_index(drop=True) if previous_df is not None else None

    rows = []
    for row_index, raw_row in raw_df.iterrows():
        previous_row = (
            previous_df.iloc[row_index]
            if previous_df is not None and row_index < len(previous_df)
            else None
        )

        start = pd.to_datetime(raw_row["Start"], errors="coerce")
        start = None if pd.isna(start) else start.date()
        end = pd.to_datetime(raw_row["Ende"], errors="coerce")
        end = None if pd.isna(end) else end.date()
        duration = pd.to_numeric(raw_row["Dauer"], errors="coerce")
        duration = None if pd.isna(duration) else max(int(duration), 0)
        row_type = raw_row["Typ"] if raw_row["Typ"] in TYPES else ""

        duration_changed = False
        end_changed = False
        start_changed = False
        type_changed = False
        if previous_row is not None:
            previous_start = pd.to_datetime(previous_row["Start"], errors="coerce")
            previous_start = None if pd.isna(previous_start) else previous_start.date()
            previous_end = pd.to_datetime(previous_row["Ende"], errors="coerce")
            previous_end = None if pd.isna(previous_end) else previous_end.date()
            previous_duration = pd.to_numeric(previous_row["Dauer"], errors="coerce")
            previous_duration = None if pd.isna(previous_duration) else int(previous_duration)
            duration_changed = duration != previous_duration
            end_changed = end is not None and end != previous_end
            start_changed = start != previous_start
            type_changed = row_type != previous_row["Typ"]

        if row_type == "Meilenstein":
            duration = 0
            if end_changed and not start_changed and not type_changed and end is not None:
                start = end
            end = start
        elif start is None:
            pass
        elif end_changed and not duration_changed and end is not None:
            duration = max((end - start).days + 1, 1)
            end = start + pd.Timedelta(days=duration - 1)
        elif duration is not None:
            duration = max(duration, 1)
            end = start + pd.Timedelta(days=duration - 1)
        elif end is not None:
            duration = max((end - start).days + 1, 1)
            end = start + pd.Timedelta(days=duration - 1)

        rows.append(
            {
                "ID": row_index + start_id,
                "Name": "" if pd.isna(raw_row["Name"]) else str(raw_row["Name"]),
                "Typ": row_type,
                "Start": start,
                "Dauer": duration,
                "Ende": end,
                COLUMNS[6]: "" if pd.isna(raw_row[COLUMNS[6]]) else str(raw_row[COLUMNS[6]]),
                "Resource": "" if pd.isna(raw_row["Resource"]) else str(raw_row["Resource"]),
            }
        )

    extend_section_durations(rows)
    number_hierarchy(rows, start_section_number)
    return pd.DataFrame(rows, columns=COLUMNS)


def sync_first_row_start(project_index: int, planning_start: date) -> None:
    table = st.session_state.projects[project_index]["milestones"]
    if table.empty:
        return

    updated_table = table.copy()
    row_type = updated_table.at[0, "Typ"]
    duration = int(updated_table.at[0, "Dauer"])
    updated_table.at[0, "Start"] = planning_start
    if row_type == "Meilenstein":
        updated_table.at[0, "Dauer"] = 0
        updated_table.at[0, "Ende"] = planning_start
    else:
        duration = max(duration, 1)
        updated_table.at[0, "Dauer"] = duration
        updated_table.at[0, "Ende"] = planning_start + pd.Timedelta(days=duration - 1)

    st.session_state.projects[project_index]["milestones"] = updated_table
    editor_key = f"project_{project_index}_milestones_editor"
    if editor_key in st.session_state:
        del st.session_state[editor_key]


def apply_editor_changes(project_index: int, editor_key: str) -> None:
    """Apply Streamlit data-editor deltas immediately after a cell edit."""
    editor_state = st.session_state.get(editor_key)
    if not isinstance(editor_state, dict):
        return

    current_table = st.session_state.projects[project_index]["milestones"]
    rows = current_table.reset_index(drop=True).to_dict("records")

    deleted_rows = editor_state.get("deleted_rows", [])
    for row_index in sorted((int(row_index) for row_index in deleted_rows), reverse=True):
        if 0 <= row_index < len(rows):
            rows.pop(row_index)

    edited_rows = editor_state.get("edited_rows", {})
    for row_index, changes in edited_rows.items():
        row_index = int(row_index)
        if not isinstance(changes, dict):
            continue
        if not 0 <= row_index < len(rows):
            continue
        for column, value in changes.items():
            if column in COLUMNS:
                if column == "Name":
                    value = normalize_editor_name(value)
                rows[row_index][column] = value

    for added_row in editor_state.get("added_rows", []):
        if not isinstance(added_row, dict):
            continue
        row = {column: None for column in COLUMNS}
        if rows:
            row["Start"] = rows[-1]["Ende"]
        row.update({column: value for column, value in added_row.items() if column in COLUMNS})
        row["Name"] = normalize_editor_name(row["Name"])
        rows.append(row)

    edited_table = pd.DataFrame(rows, columns=COLUMNS)
    project = st.session_state.projects[project_index]["project"]
    normalized_table = normalize_table(
        edited_table,
        current_table,
        start_id=project["start_id"],
        start_section_number=project["start_section_number"],
    )
    st.session_state.projects[project_index]["milestones"] = normalized_table
    if normalized_table.empty:
        prefix = f"project_{project_index}"
        st.session_state[f"{prefix}_chart_html"] = None
        st.session_state[f"{prefix}_chart_height"] = 0


def shift_predecessor_ids(value, inserted_id: int) -> str:
    if value is None or pd.isna(value):
        return ""

    shifted_parts = []
    for part in str(value).split(";"):
        stripped_part = part.strip()
        try:
            predecessor_id = int(stripped_part)
        except ValueError:
            shifted_parts.append(stripped_part)
            continue
        shifted_parts.append(str(predecessor_id + 1 if predecessor_id >= inserted_id else predecessor_id))
    return ";".join(shifted_parts)


def offset_predecessor_ids(value, offset: int) -> str:
    if value is None or pd.isna(value):
        return ""

    shifted_parts = []
    for part in str(value).split(";"):
        stripped_part = part.strip()
        try:
            predecessor_id = int(stripped_part)
        except ValueError:
            shifted_parts.append(stripped_part)
            continue
        shifted_parts.append(str(predecessor_id + offset))
    return ";".join(shifted_parts)


def apply_project_numbering(
    project_index: int,
    previous_start_id: int,
) -> None:
    """Rebase IDs, predecessor references and hierarchy labels after metadata edits."""
    entry = st.session_state.projects[project_index]
    project = entry["project"]
    table = entry["milestones"].copy()
    start_id_offset = project["start_id"] - previous_start_id
    if start_id_offset:
        table[COLUMNS[6]] = table[COLUMNS[6]].map(
            lambda value: offset_predecessor_ids(value, start_id_offset)
        )
    entry["milestones"] = normalize_table(
        table,
        start_id=project["start_id"],
        start_section_number=project["start_section_number"],
    )

    editor_key = f"project_{project_index}_milestones_editor"
    if editor_key in st.session_state:
        del st.session_state[editor_key]


def insert_project(project_index: int) -> None:
    """Insert and persist an empty project sheet at the requested list position."""
    st.session_state.projects.insert(project_index, project_entry())
    save_projects(st.session_state.projects)
    for key in list(st.session_state):
        if key.startswith("project_"):
            del st.session_state[key]


def delete_empty_project(project_index: int) -> None:
    """Delete a project sheet after the caller has verified that its table is empty."""
    st.session_state.projects.pop(project_index)
    save_projects(st.session_state.projects)
    for key in list(st.session_state):
        if key.startswith("project_"):
            del st.session_state[key]


@st.dialog("Bereich kann nicht entfernt werden")
def show_project_delete_blocked_dialog() -> None:
    st.write("Die Zeilen der Tabelle müssen vorher gelöscht sein.")


def insert_milestone_row(project_index: int, row_index: int, position: str) -> None:
    """Insert a table row and shift predecessor references affected by its new ID."""
    current_table = st.session_state.projects[project_index]["milestones"]
    project = st.session_state.projects[project_index]["project"]
    insert_index = row_index if position == "above" else row_index + 1
    insert_index = max(0, min(insert_index, len(current_table)))
    inserted_id = project["start_id"] + insert_index

    rows = current_table.reset_index(drop=True).to_dict("records")
    for row in rows:
        row[COLUMNS[6]] = shift_predecessor_ids(row.get(COLUMNS[6]), inserted_id)

    new_row = {column: None for column in COLUMNS}
    if insert_index > 0:
        previous_end = rows[insert_index - 1]["Ende"]
        new_row["Start"] = previous_end
        new_row["Ende"] = previous_end
    rows.insert(insert_index, new_row)
    st.session_state.projects[project_index]["milestones"] = normalize_table(
        pd.DataFrame(rows, columns=COLUMNS),
        start_id=project["start_id"],
        start_section_number=project["start_section_number"],
    )

    editor_key = f"project_{project_index}_milestones_editor"
    if editor_key in st.session_state:
        del st.session_state[editor_key]


def normalize_editor_name(value):
    if value is None or pd.isna(value):
        return value
    return unicodedata.normalize(
        "NFC",
        str(value).translate(EDITOR_PLAIN_TRANSLATION),
    )


def editor_table(df: pd.DataFrame) -> pd.DataFrame:
    """Return a display-only table with row-type-specific name styling."""
    display_table = df.copy()
    section_rows = display_table["Typ"] == "Abschnitt"
    display_table.loc[section_rows, "Name"] = display_table.loc[
        section_rows, "Name"
    ].map(lambda value: str(value).translate(EDITOR_BOLD_TRANSLATION))
    milestone_rows = display_table["Typ"] == "Meilenstein"
    display_table.loc[milestone_rows, "Name"] = display_table.loc[
        milestone_rows, "Name"
    ].map(lambda value: str(value).translate(EDITOR_ITALIC_TRANSLATION))
    return display_table


def date_to_ms(value) -> int:
    parsed = pd.to_datetime(value)
    return int(parsed.value // 1_000_000)


def task_display_end(row: pd.Series):
    """Return the graphical end position while preserving stored duration values."""
    if row["Typ"] in BAR_COLORS and row["Dauer"] == 1:
        return pd.Timestamp(row["Start"]) + pd.Timedelta(days=0.5)
    return row["Ende"]


def format_week_label(value: date) -> str:
    return f"{value.day}. {GERMAN_MONTHS[value.month]} {value:%y}"


def format_date(value) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%d.%m.%Y")


def format_text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)


def tooltip_data(row: pd.Series) -> list[str]:
    change_date = row.get("Änderungsdatum", row.get("Änderung", ""))
    predecessors = format_text(row.get("Vorgänger", "")).strip()
    duration = row.get("Dauer", "")
    duration_text = format_text(duration)
    duration_unit = "Tag" if duration == 1 else "Tage"
    if row.get("Typ") == "Meilenstein":
        date_lines = f"Datum: {format_date(row.get('Start', ''))}<br>"
        duration_line = ""
    else:
        date_lines = (
            f"Start: {format_date(row.get('Start', ''))}<br>"
            f"Ende: {format_date(row.get('Ende', ''))}<br>"
        )
        duration_line = f"Dauer: {duration_text} {duration_unit}<br>"
    return [
        format_text(row.get("Name", "")),
        format_text(row.get("ID", "")),
        format_text(row.get("Typ", "")),
        date_lines,
        duration_line,
        f"Vorgänger-ID: {predecessors}<br>" if predecessors else "",
        format_text(row.get("Verantwortung", row.get("Resource", ""))),
        format_date(change_date),
    ]


HOVER_TEMPLATE = (
    "<b>%{customdata[0]}</b><br>"
    "ID: %{customdata[1]}<br>"
    "Typ: %{customdata[2]}<br>"
    "%{customdata[3]}"
    "%{customdata[4]}"
    "%{customdata[5]}"
    "Verantwortung: %{customdata[6]}"
    "<extra></extra>"
)


def parse_predecessors(value: str) -> list[int]:
    predecessors = []
    for part in str(value).split(";"):
        part = part.strip()
        if not part:
            continue
        try:
            predecessors.append(int(part))
        except ValueError:
            continue
    return predecessors


def figure_to_html(fig: go.Figure) -> str:
    return fig.to_html(
        include_plotlyjs="cdn",
        full_html=False,
        config={"displayModeBar": False, "responsive": False},
    )


def render_chart_html(chart_html: str, height: int) -> None:
    """Render a scrollable chart and suppress touch zoom on touch-capable devices."""
    components.html(
        f"""
        <div
            id="chart-scroll-container"
            style="width: 100%; overflow-x: auto; overflow-y: hidden;"
        >
            {chart_html}
        </div>
        <script>
        const chartScroller = document.getElementById("chart-scroll-container");
        chartScroller.addEventListener("wheel", (event) => {{
            if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) {{
                chartScroller.scrollLeft += event.deltaY;
                event.preventDefault();
            }}
        }}, {{ passive: false }});

        const isTouchDevice =
            navigator.maxTouchPoints > 0 || "ontouchstart" in window;
        if (isTouchDevice) {{
            const chart = chartScroller.querySelector(".js-plotly-plot");
            chartScroller.style.touchAction = "pan-x pan-y";
            if (chart && window.Plotly) {{
                Plotly.relayout(chart, {{ dragmode: false }});
                chart.style.touchAction = "none";
            }}

            const preventTouchPlotInteraction = (event) => {{
                if (
                    event.touches?.length > 1
                    || event.target.closest(".js-plotly-plot")
                ) {{
                    event.preventDefault();
                    event.stopPropagation();
                }}
            }};
            chartScroller.addEventListener("touchstart", preventTouchPlotInteraction, {{
                passive: false,
                capture: true,
            }});
            chartScroller.addEventListener("touchmove", preventTouchPlotInteraction, {{
                passive: false,
                capture: true,
            }});
            chartScroller.addEventListener("gesturestart", (event) => {{
                event.preventDefault();
                event.stopPropagation();
            }}, {{ passive: false, capture: true }});
        }}
        </script>
        """,
        height=height + 30,
        scrolling=False,
    )


def install_editor_enter_navigation(editor_index: int, row_count: int) -> None:
    """Install browser-side Enter navigation for one Streamlit data editor."""
    components.html(
        f"""
        <script>
        const parentDocument = window.parent.document;
        const navigationKey = "milestone-editor-enter-navigation-{editor_index}";
        const activeEditorKey = "milestone-editor-active-index";
        const existingNavigation = window.parent[navigationKey];
        if (existingNavigation) {{
            parentDocument.removeEventListener("keydown", existingNavigation.handler, true);
            existingNavigation.editor?.removeEventListener(
                "mousedown",
                existingNavigation.activate,
                true
            );
            existingNavigation.editor?.removeEventListener(
                "focusin",
                existingNavigation.activate,
                true
            );
        }}

        const editors = parentDocument.querySelectorAll('[data-testid="stDataFrame"]');
        const editor = editors[{editor_index}];
        const activate = () => {{
            window.parent[activeEditorKey] = {editor_index};
        }};
        editor?.addEventListener("mousedown", activate, true);
        editor?.addEventListener("focusin", activate, true);

        const handler = (event) => {{
            if (
                event.key !== "Enter"
                || event.shiftKey
                || event.isComposing
                || event.repeat
            ) {{
                return;
            }}

            if (window.parent[activeEditorKey] !== {editor_index} || !editor) {{
                return;
            }}

            const selectedCell = editor.querySelector(
                'td[role="gridcell"][aria-selected="true"]'
            );
            const selectedRow = Number(
                selectedCell?.closest('tr[role="row"]')?.getAttribute("aria-rowindex")
            ) - 2;
            if (!Number.isInteger(selectedRow) || selectedRow < {row_count - 1}) {{
                return;
            }}

            window.setTimeout(() => {{
                window.requestAnimationFrame(() => {{
                    const currentEditors = parentDocument.querySelectorAll(
                        '[data-testid="stDataFrame"]'
                    );
                    const canvas = currentEditors[{editor_index}]?.querySelector(
                        '[data-testid="data-grid-canvas"]'
                    );
                    if (!canvas) {{
                        return;
                    }}
                    canvas.focus();
                    canvas.dispatchEvent(
                        new KeyboardEvent("keydown", {{
                            key: "ArrowUp",
                            code: "ArrowUp",
                            keyCode: 38,
                            which: 38,
                            bubbles: true,
                        }})
                    );
                }});
            }}, 0);
        }};

        window.parent[navigationKey] = {{ handler, editor, activate }};
        parentDocument.addEventListener("keydown", handler, true);
        </script>
        """,
        height=0,
        scrolling=False,
    )


def context_action_label(project_index: int, row_index: int, position: str) -> str:
    return f"__row_context_insert_{project_index}_{row_index}_{position}"


def install_editor_row_context_menu(editor_index: int, row_count: int) -> None:
    """Replace the browser menu on table rows with row insertion actions."""
    components.html(
        f"""
        <script>
        const parentDocument = window.parent.document;
        const contextKey = "milestone-editor-row-context-{editor_index}";
        const existingContext = window.parent[contextKey];
        if (existingContext) {{
            parentDocument.removeEventListener("contextmenu", existingContext.handler, true);
            parentDocument.removeEventListener("click", existingContext.closeOnOutsideClick);
            existingContext.closePopup();
        }}

        let popup;

        const closePopup = () => {{
            popup?.remove();
            popup = undefined;
        }};
        const closeOnOutsideClick = (event) => {{
            if (popup && !popup.contains(event.target)) {{
                closePopup();
            }}
        }};

        const clickAction = (rowIndex, position) => {{
            const label = `__row_context_insert_{editor_index}_${{rowIndex}}_${{position}}`;
            const button = Array.from(parentDocument.querySelectorAll("button")).find(
                (candidate) => candidate.textContent.trim() === label
            );
            closePopup();
            button?.click();
        }};

        const handler = (event) => {{
            const editors = parentDocument.querySelectorAll('[data-testid="stDataFrame"]');
            const editor = editors[{editor_index}];
            const canvas = editor?.querySelector('[data-testid="data-grid-canvas"]');
            if (!editor || !canvas || !editor.contains(event.target)) {{
                return;
            }}

            const canvasBounds = canvas.getBoundingClientRect();
            const scroller = editor.querySelector(".dvn-scroller");
            const rowIndex = Math.floor(
                (
                    event.clientY
                    - canvasBounds.top
                    + (scroller?.scrollTop || 0)
                    - {TABLE_HEADER_HEIGHT_PX}
                ) / {TABLE_EDITOR_ROW_HEIGHT_PX}
            );
            if (rowIndex < 0 || rowIndex >= {row_count}) {{
                return;
            }}

            event.preventDefault();
            event.stopPropagation();
            closePopup();
            popup = parentDocument.createElement("div");
            popup.className = "milestone-row-context-menu";
            popup.style.left = `${{event.clientX}}px`;
            popup.style.top = `${{event.clientY}}px`;

            const aboveButton = parentDocument.createElement("button");
            aboveButton.type = "button";
            aboveButton.textContent = "Zeile oberhalb einfügen";
            aboveButton.addEventListener("click", () => clickAction(rowIndex, "above"));

            const belowButton = parentDocument.createElement("button");
            belowButton.type = "button";
            belowButton.textContent = "Zeile unterhalb einfügen";
            belowButton.addEventListener("click", () => clickAction(rowIndex, "below"));

            popup.append(aboveButton, belowButton);
            parentDocument.body.appendChild(popup);
        }};

        parentDocument.addEventListener("contextmenu", handler, true);
        parentDocument.addEventListener("click", closeOnOutsideClick);
        window.parent[contextKey] = {{
            handler,
            closePopup,
            closeOnOutsideClick,
        }};
        </script>
        """,
        height=0,
        scrolling=False,
    )


def install_project_context_menu() -> None:
    """Install the browser-side menu for inserting sheets between projects."""
    components.html(
        """
        <script>
        const parentDocument = window.parent.document;
        const contextKey = "milestone-project-context";
        const existingContext = window.parent[contextKey];
        if (existingContext) {
            parentDocument.removeEventListener("contextmenu", existingContext.handler, true);
            parentDocument.removeEventListener("click", existingContext.closeOnOutsideClick);
            existingContext.closePopup();
        }

        let popup;

        const closePopup = () => {
            popup?.remove();
            popup = undefined;
        };
        const closeOnOutsideClick = (event) => {
            if (popup && !popup.contains(event.target)) {
                closePopup();
            }
        };
        const clickAction = (insertIndex) => {
            const label = `__project_context_insert_${insertIndex}`;
            const button = Array.from(parentDocument.querySelectorAll("button")).find(
                (candidate) => candidate.textContent.trim() === label
            );
            closePopup();
            button?.click();
        };
        const handler = (event) => {
            const zone = event.target.closest(".project-insert-zone");
            if (!zone) {
                return;
            }

            event.preventDefault();
            event.stopPropagation();
            closePopup();
            popup = parentDocument.createElement("div");
            popup.className = "milestone-row-context-menu";
            popup.style.left = `${event.clientX}px`;
            popup.style.top = `${event.clientY}px`;

            const insertButton = parentDocument.createElement("button");
            insertButton.type = "button";
            insertButton.textContent = "Neues Projektblatt erstellen";
            insertButton.addEventListener(
                "click",
                () => clickAction(zone.dataset.insertIndex)
            );

            popup.append(insertButton);
            parentDocument.body.appendChild(popup);
        };

        parentDocument.addEventListener("contextmenu", handler, true);
        parentDocument.addEventListener("click", closeOnOutsideClick);
        window.parent[contextKey] = {
            handler,
            closePopup,
            closeOnOutsideClick,
        };
        </script>
        """,
        height=0,
        scrolling=False,
    )


def install_save_shortcut(project_index: int) -> None:
    """Bind Ctrl+S to the matching sheet's save button and suppress browser save."""
    components.html(
        f"""
        <script>
        const parentDocument = window.parent.document;
        const shortcutKey = "milestone-save-shortcut-{project_index}";
        const existingHandler = window.parent[shortcutKey];
        if (existingHandler) {{
            parentDocument.removeEventListener("keydown", existingHandler, true);
        }}

        const handler = (event) => {{
            if (
                event.key.toLowerCase() !== "s"
                || (!event.ctrlKey && !event.metaKey)
                || event.altKey
            ) {{
                return;
            }}

            event.preventDefault();
            event.stopPropagation();
            const saveButtons = Array.from(parentDocument.querySelectorAll("button")).filter(
                (button) => button.textContent.trim() === "Speichern [str+s]"
            );
            saveButtons[{project_index}]?.click();
        }};

        window.parent[shortcutKey] = handler;
        parentDocument.addEventListener("keydown", handler, true);
        </script>
        """,
        height=0,
        scrolling=False,
    )


def install_generate_shortcut(project_index: int) -> None:
    """Bind Ctrl+G to the matching sheet's chart-generation button."""
    components.html(
        f"""
        <script>
        const parentDocument = window.parent.document;
        const shortcutKey = "milestone-generate-shortcut-{project_index}";
        const existingHandler = window.parent[shortcutKey];
        if (existingHandler) {{
            parentDocument.removeEventListener("keydown", existingHandler, true);
        }}

        const handler = (event) => {{
            if (
                event.key.toLowerCase() !== "g"
                || (!event.ctrlKey && !event.metaKey)
                || event.altKey
            ) {{
                return;
            }}

            event.preventDefault();
            event.stopPropagation();
            const generateButtons = Array.from(parentDocument.querySelectorAll("button")).filter(
                (button) => button.textContent.trim() === "Generieren [str+g]"
            );
            generateButtons[{project_index}]?.click();
        }};

        window.parent[shortcutKey] = handler;
        parentDocument.addEventListener("keydown", handler, true);
        </script>
        """,
        height=0,
        scrolling=False,
    )


def build_schedule_figure(df: pd.DataFrame) -> go.Figure:
    """Build the Plotly Gantt chart, including symbols and dependency routing.

    Dependency arrows use orthogonal segments. The route planner keeps vertical
    segments away from bars and arrow tips. A preliminary segment pass also
    makes later arrows visible to earlier activity-to-activity collision checks.
    """
    fig = go.Figure()

    if df.empty:
        return fig

    date_range = df.dropna(subset=["Start", "Ende"])
    if date_range.empty:
        return fig

    x_min = min(date_range["Start"]) - pd.Timedelta(days=1)
    x_max = max(date_range["Ende"]) + pd.Timedelta(days=2)
    if x_min == x_max:
        x_min = x_min - pd.Timedelta(days=1)
        x_max = x_max + pd.Timedelta(days=1)

    rows = df.sort_values("ID").reset_index(drop=True)
    tasks = rows[rows["Typ"].isin(BAR_COLORS)].dropna(subset=["Start", "Ende"])
    summaries = rows[rows["Typ"] == "Abschnitt"].dropna(subset=["Start", "Ende"])
    milestones = rows[rows["Typ"] == "Meilenstein"].dropna(subset=["Start"])

    if rows.empty:
        fig.update_xaxes(
            range=[date_to_ms(x_min), date_to_ms(x_max)],
            type="date",
            tickformat="%d.%m.%Y",
        )
        fig.update_yaxes(visible=False)
        return fig

    max_id = int(rows["ID"].max())
    chart_height = max(
        TABLE_HEADER_HEIGHT_PX
        + len(rows) * TABLE_ROW_HEIGHT_PX
        + CHART_MARGIN_TOP_PX
        + CHART_MARGIN_BOTTOM_PX
        + 0,
        240,
    )
    total_days = max((x_max - x_min).days + 1, 1)
    axis_x_max = x_max
    chart_width = max(total_days * DAY_WIDTH_PX + 180, MIN_CHART_WIDTH_PX)
    y_values = rows["ID"].tolist()
    y_labels = [str(row["ID"]) for _, row in rows.iterrows()]
    week_start = x_min - pd.Timedelta(days=x_min.weekday())
    week_ticks = []
    current_week = week_start
    while current_week <= axis_x_max:
        week_ticks.append(current_week)
        current_week = current_week + pd.Timedelta(days=7)

    current_day = x_min
    while current_day <= axis_x_max:
        if current_day.weekday() >= 5:
            fig.add_shape(
                type="rect",
                xref="x",
                yref="paper",
                x0=date_to_ms(current_day),
                x1=date_to_ms(current_day + pd.Timedelta(days=1)),
                y0=0,
                y1=1,
                line={"width": 0},
                fillcolor=WEEKEND_COLOR,
                layer="below",
            )
        fig.add_annotation(
            x=date_to_ms(current_day + pd.Timedelta(hours=12)),
            y=1,
            yshift=5,
            xref="x",
            yref="paper",
            text=(
                f"<b>{DAY_LABELS[current_day.weekday()]}</b>"
                if current_day.weekday() == 0
                else DAY_LABELS[current_day.weekday()]
            ),
            showarrow=False,
            font={"size": 10, "color": "#374151"},
            xanchor="center",
            yanchor="bottom",
        )
        current_day = current_day + pd.Timedelta(days=1)

    fig.add_trace(
        go.Scatter(
            x=[date_to_ms(x_min), date_to_ms(axis_x_max)],
            y=[min(y_values), max(y_values)],
            mode="markers",
            marker={"opacity": 0},
            hoverinfo="skip",
        )
    )

    for y, row in tasks.iterrows():
        y_position = row["ID"]
        display_end = task_display_end(row)
        bar_half_height = BAR_WIDTH_PX / TABLE_ROW_HEIGHT_PX / 2
        fig.add_shape(
            type="rect",
            xref="x",
            yref="y",
            x0=date_to_ms(row["Start"]),
            x1=date_to_ms(display_end),
            y0=y_position - bar_half_height,
            y1=y_position + bar_half_height,
            line={"color": DEPENDENCY_ARROW_COLOR, "width": SYMBOL_OUTLINE_WIDTH_PX},
            fillcolor=BAR_COLORS[row["Typ"]],
        )

    plot_width_px = chart_width - CHART_MARGIN_LEFT_PX - CHART_MARGIN_RIGHT_PX
    plot_height_px = chart_height - CHART_MARGIN_TOP_PX - CHART_MARGIN_BOTTOM_PX
    axis_days = (axis_x_max - x_min) / pd.Timedelta(days=1)
    x_pixels_per_day = plot_width_px / axis_days
    y_pixels_per_row = plot_height_px / max_id

    summary_bar_height = 0.12
    summary_arrow_height = 0.1792
    summary_arrow_width_ms = (
        DAY_IN_MS * summary_arrow_height * y_pixels_per_row / x_pixels_per_day
    )

    for y, row in summaries.iterrows():
        y_position = row["ID"]
        start_x = date_to_ms(row["Start"])
        end_x = date_to_ms(row["Ende"])
        top_y = y_position - summary_bar_height / 2
        bottom_y = y_position + summary_bar_height / 2
        triangle_base_y = bottom_y
        tip_y = y_position + summary_bar_height / 2 + summary_arrow_height

        fig.add_shape(
            type="path",
            xref="x",
            yref="y",
            path=(
                f"M {start_x},{top_y} "
                f"L {end_x},{top_y} "
                f"L {end_x},{bottom_y} "
                f"L {start_x},{bottom_y} Z"
            ),
            line={"color": SUMMARY_COLOR, "width": 1},
            fillcolor=SUMMARY_COLOR,
        )
        fig.add_shape(
            type="path",
            xref="x",
            yref="y",
            path=(
                f"M {start_x},{triangle_base_y} "
                f"L {start_x + summary_arrow_width_ms},{triangle_base_y} "
                f"L {start_x},{tip_y} Z"
            ),
            line={"color": SUMMARY_COLOR, "width": 1},
            fillcolor=SUMMARY_COLOR,
        )
        fig.add_shape(
            type="path",
            xref="x",
            yref="y",
            path=(
                f"M {end_x - summary_arrow_width_ms},{triangle_base_y} "
                f"L {end_x},{triangle_base_y} "
                f"L {end_x},{tip_y} Z"
            ),
            line={"color": SUMMARY_COLOR, "width": 1},
            fillcolor=SUMMARY_COLOR,
        )

    milestone_half_height = 0.25
    milestone_half_width = (
        DAY_IN_MS
        * (milestone_half_height * y_pixels_per_row / x_pixels_per_day)
    )
    for y, row in milestones.iterrows():
        y_position = row["ID"]
        center_x = date_to_ms(row["Start"])
        fig.add_shape(
            type="path",
            xref="x",
            yref="y",
            path=(
                f"M {center_x},{y_position - milestone_half_height} "
                f"L {center_x + milestone_half_width},{y_position} "
                f"L {center_x},{y_position + milestone_half_height} "
                f"L {center_x - milestone_half_width},{y_position} Z"
            ),
            line={"color": DEPENDENCY_ARROW_COLOR, "width": SYMBOL_OUTLINE_WIDTH_PX},
            fillcolor=MILESTONE_COLOR,
        )

    hover_line_color = "rgba(0,0,0,0.01)"
    for _, row in tasks.iterrows():
        y_position = row["ID"]
        display_end = task_display_end(row)
        customdata = [tooltip_data(row), tooltip_data(row)]
        fig.add_trace(
            go.Scatter(
                x=[date_to_ms(row["Start"]), date_to_ms(display_end)],
                y=[y_position, y_position],
                mode="lines",
                line={"color": hover_line_color, "width": 25},
                customdata=customdata,
                hovertemplate=HOVER_TEMPLATE,
                showlegend=False,
            )
        )

    for _, row in summaries.iterrows():
        y_position = row["ID"]
        customdata = [tooltip_data(row), tooltip_data(row)]
        fig.add_trace(
            go.Scatter(
                x=[date_to_ms(row["Start"]), date_to_ms(row["Ende"])],
                y=[y_position, y_position],
                mode="lines",
                line={"color": hover_line_color, "width": 18},
                customdata=customdata,
                hovertemplate=HOVER_TEMPLATE,
                showlegend=False,
            )
        )

    for _, row in milestones.iterrows():
        fig.add_trace(
            go.Scatter(
                x=[date_to_ms(row["Start"])],
                y=[row["ID"]],
                mode="markers",
                marker={
                    "symbol": "diamond",
                    "size": 18,
                    "color": "rgba(0,0,0,0.01)",
                },
                customdata=[tooltip_data(row)],
                hovertemplate=HOVER_TEMPLATE,
                showlegend=False,
            )
        )

    pfeil_abstand = int(pd.Timedelta(days=PFEIL_ABSTAND_TAGE).total_seconds() * 1000)
    arrow_head_width = int(pd.Timedelta(hours=6).total_seconds() * 1000)
    arrow_head_height = 0.10
    bar_height = BAR_WIDTH_PX / TABLE_ROW_HEIGHT_PX
    rows_by_id = {int(row["ID"]): row for _, row in rows.iterrows()}
    predecessor_usage: dict[int, int] = {}
    activity_target_segments = []
    for _, row in rows.iterrows():
        row_id = int(row["ID"])
        if row["Typ"] not in BAR_COLORS:
            continue
        if not parse_predecessors(row["Vorgänger"]):
            continue
        activity_target_segments.append(
            (row_id, date_to_ms(row["Start"]) - pfeil_abstand)
        )
    arrow_tips = []
    for _, row in rows.iterrows():
        row_id = int(row["ID"])
        valid_predecessors = [
            predecessor_id
            for predecessor_id in parse_predecessors(row["Vorgänger"])
            if predecessor_id in rows_by_id and predecessor_id != row_id
        ]
        if not valid_predecessors:
            continue
        target_x = date_to_ms(row["Start"])
        if row["Typ"] == "Meilenstein":
            target_x -= milestone_half_width
        arrow_tips.append((target_x, row_id))
    planned_arrow_segments = []

    def shift_vertical_segment_left(segment_x: float, start_y: float, end_y: float) -> float:
        shift_step = pfeil_abstand * 0.2
        min_y, max_y = sorted((start_y, end_y))
        while any(
            tip_x - arrow_head_width <= segment_x <= tip_x
            and min_y <= tip_y <= max_y
            for tip_x, tip_y in arrow_tips
        ):
            segment_x -= shift_step
        return segment_x

    def point_hits_planned_arrow(
        point_x: float,
        point_y: float,
        current_route: tuple[int, int] | None,
    ) -> bool:
        return any(
            route != current_route
            and (
                (
                    segment_start_x == segment_end_x
                    and segment_start_x == point_x
                    and min(segment_start_y, segment_end_y)
                    <= point_y
                    <= max(segment_start_y, segment_end_y)
                )
                or (
                    segment_start_y == segment_end_y
                    and segment_start_y == point_y
                    and min(segment_start_x, segment_end_x)
                    <= point_x
                    <= max(segment_start_x, segment_end_x)
                )
            )
            for (
                route,
                segment_start_x,
                segment_start_y,
                segment_end_x,
                segment_end_y,
            ) in planned_arrow_segments
        )

    def route_target_segment_left(
        segment_x: float,
        start_y: float,
        end_y: float,
        avoid_arrow_endpoint: bool = False,
        current_route: tuple[int, int] | None = None,
    ) -> float:
        min_y, max_y = sorted((start_y, end_y))
        while True:
            segment_x = shift_vertical_segment_left(segment_x, start_y, end_y)
            intersected_bars = [
                intermediate
                for _, intermediate in tasks.iterrows()
                if (
                    min_y < int(intermediate["ID"]) < max_y
                    and date_to_ms(intermediate["Start"])
                    <= segment_x
                    <= date_to_ms(task_display_end(intermediate))
                )
            ]
            if intersected_bars:
                segment_x = min(
                    date_to_ms(intermediate["Start"])
                    for intermediate in intersected_bars
                ) - pfeil_abstand
                continue
            if avoid_arrow_endpoint and point_hits_planned_arrow(
                segment_x,
                start_y,
                current_route,
            ):
                segment_x -= pfeil_abstand * 0.5
                continue
            return segment_x

    def arrow_segments(
        route: tuple[int, int],
        source_x: float,
        source_y: float,
        first_x: float,
        channel_y: float,
        before_target_x: float,
        target_x: float,
        target_y: float,
    ) -> list[tuple[tuple[int, int], float, float, float, float]]:
        return [
            (route, source_x, source_y, first_x, source_y),
            (route, first_x, source_y, first_x, channel_y),
            (route, first_x, channel_y, before_target_x, channel_y),
            (route, before_target_x, channel_y, before_target_x, target_y),
            (route, before_target_x, target_y, target_x, target_y),
        ]

    # Precompute baseline routes so an earlier arrow can avoid a later route.
    for _, planned_successor in rows.iterrows():
        planned_successor_id = int(planned_successor["ID"])
        planned_target_x = date_to_ms(planned_successor["Start"])
        if planned_successor["Typ"] == "Meilenstein":
            planned_target_x -= milestone_half_width
        for planned_predecessor_id in parse_predecessors(
            planned_successor[COLUMNS[6]]
        ):
            planned_predecessor = rows_by_id.get(planned_predecessor_id)
            if (
                planned_predecessor is None
                or planned_predecessor_id == planned_successor_id
            ):
                continue
            planned_source_x = date_to_ms(task_display_end(planned_predecessor))
            if planned_predecessor["Typ"] == "Meilenstein":
                planned_source_x = (
                    date_to_ms(planned_predecessor["Start"]) + milestone_half_width
                )
            planned_channel_row = planned_predecessor_id
            if planned_successor["Typ"] == "Meilenstein":
                while True:
                    next_row = rows_by_id.get(planned_channel_row + 1)
                    if (
                        next_row is None
                        or int(next_row["ID"]) == planned_successor_id
                        or next_row["Typ"] not in BAR_COLORS
                        or task_display_end(next_row)
                        != task_display_end(planned_predecessor)
                    ):
                        break
                    planned_channel_row += 1
            planned_channel_y = planned_channel_row + bar_height * 0.6
            planned_first_x = shift_vertical_segment_left(
                planned_source_x + pfeil_abstand,
                planned_predecessor_id,
                planned_channel_y,
            )
            planned_before_target_x = route_target_segment_left(
                planned_target_x - pfeil_abstand,
                planned_channel_y,
                planned_successor_id,
            )
            route = (planned_predecessor_id, planned_successor_id)
            planned_arrow_segments.extend(
                arrow_segments(
                    route,
                    planned_source_x,
                    planned_predecessor_id,
                    planned_first_x,
                    planned_channel_y,
                    planned_before_target_x,
                    planned_target_x,
                    planned_successor_id,
                )
            )

    # Draw final routes with collision-aware target channels.
    for _, successor in rows.iterrows():
        successor_id = int(successor["ID"])
        successor_start = date_to_ms(successor["Start"])
        successor_target_x = successor_start
        if successor["Typ"] == "Meilenstein":
            successor_target_x = successor_start - milestone_half_width
        successor_y = successor_id

        for predecessor_id in parse_predecessors(successor["Vorgänger"]):
            predecessor = rows_by_id.get(predecessor_id)
            if predecessor is None or predecessor_id == successor_id:
                continue

            predecessor_end = date_to_ms(task_display_end(predecessor))
            predecessor_source_x = predecessor_end
            if predecessor["Typ"] == "Meilenstein":
                predecessor_source_x = date_to_ms(predecessor["Start"]) + milestone_half_width
            predecessor_y = predecessor_id
            channel_row = predecessor_y
            if successor["Typ"] == "Meilenstein":
                while True:
                    next_row = rows_by_id.get(channel_row + 1)
                    if (
                        next_row is None
                        or int(next_row["ID"]) == successor_id
                        or next_row["Typ"] not in BAR_COLORS
                        or task_display_end(next_row) != task_display_end(predecessor)
                    ):
                        break
                    channel_row += 1
            channel_y = channel_row + bar_height * 0.6 
            first_x = predecessor_source_x + pfeil_abstand
            before_successor_x = successor_target_x - pfeil_abstand
            predecessor_index = predecessor_usage.get(predecessor_id, 0)
            predecessor_usage[predecessor_id] = predecessor_index + 1
            if predecessor_index > 0 and successor["Typ"] in BAR_COLORS:
                later_activity_segments = [
                    segment_x
                    for activity_id, segment_x in activity_target_segments
                    if activity_id > predecessor_id
                ]
                if later_activity_segments:
                    before_successor_x = min(later_activity_segments)
            first_x = shift_vertical_segment_left(first_x, predecessor_y, channel_y)
            before_successor_x = route_target_segment_left(
                before_successor_x,
                channel_y,
                successor_y,
                avoid_arrow_endpoint=(
                    predecessor["Typ"] in BAR_COLORS
                    and successor["Typ"] in BAR_COLORS
                ),
                current_route=(predecessor_id, successor_id),
            )

            fig.add_shape(
                type="path",
                xref="x",
                yref="y",
                path=(
                    f"M {predecessor_source_x},{predecessor_y} "
                    f"L {first_x},{predecessor_y} "
                    f"L {first_x},{channel_y} "
                    f"L {before_successor_x},{channel_y} "
                    f"L {before_successor_x},{successor_y} "
                    f"L {successor_target_x},{successor_y}"
                ),
                line={"color": DEPENDENCY_ARROW_COLOR, "width": DEPENDENCY_ARROW_WIDTH_PX},
                fillcolor="rgba(0,0,0,0)",
            )
            fig.add_shape(
                type="path",
                xref="x",
                yref="y",
                path=(
                    f"M {successor_target_x},{successor_y} "
                    f"L {successor_target_x - arrow_head_width},{successor_y - arrow_head_height} "
                    f"L {successor_target_x - arrow_head_width},{successor_y + arrow_head_height} Z"
                ),
                line={"color": DEPENDENCY_ARROW_COLOR, "width": DEPENDENCY_ARROW_WIDTH_PX},
                fillcolor=DEPENDENCY_ARROW_COLOR,
            )

    today = date.today()
    if x_min <= today <= axis_x_max:
        fig.add_shape(
            type="line",
            xref="x",
            yref="paper",
            x0=date_to_ms(today),
            x1=date_to_ms(today),
            y0=0,
            y1=1,
            line={"color": TODAY_COLOR, "width": 6},
        )

    for week in week_ticks:
        fig.add_shape(
            type="line",
            xref="x",
            yref="paper",
            x0=date_to_ms(week),
            x1=date_to_ms(week),
            y0=0,
            y1=1,
            line={"color": "#d1d5db", "width": 1},
            layer="below",
        )

    fig.add_shape(
        type="rect",
        xref="paper",
        yref="paper",
        x0=0,
        x1=1,
        y0=0,
        y1=1,
        line={"color": "#9ca3af", "width": 1},
        fillcolor="rgba(0,0,0,0)",
    )

    fig.update_layout(
        width=chart_width,
        height=chart_height,
        margin={
            "l": CHART_MARGIN_LEFT_PX,
            "r": CHART_MARGIN_RIGHT_PX,
            "t": CHART_MARGIN_TOP_PX,
            "b": CHART_MARGIN_BOTTOM_PX,
        },
        plot_bgcolor="white",
        showlegend=False,
    )
    fig.update_xaxes(
        range=[date_to_ms(x_min), date_to_ms(axis_x_max)],
        type="date",
        side="top",
        tickmode="array",
        tickvals=[date_to_ms(week) for week in week_ticks],
        ticktext=[format_week_label(week) for week in week_ticks],
        ticklabelposition="outside top",
        ticklabelstandoff=20,
        ticklabeloverflow="allow",
        ticklabelstep=1,
        showgrid=True,
        gridcolor="#e5e7eb",
    )
    fig.update_yaxes(
        tickmode="array",
        tickvals=y_values,
        ticktext=y_labels,
        range=[max_id + 0.5, 0.5],
        showgrid=False,
        zeroline=False,
        automargin=False,
        ticklabelstandoff=5,
    )
    return fig


st.set_page_config(page_title="Meilensteinplaner", layout="wide")
st.markdown(
    """
    <style>
    .block-container {
        max-width: 100%;
        padding-left: 1rem;
        padding-right: 1rem;
    }
    .stButton > button {
        background-color: #14b8a6;
        border-color: #0f766e;
        color: white;
    }
    .stButton > button:hover {
        background-color: #0f766e;
        border-color: #0f766e;
        color: white;
    }
    div[data-testid="column"]:has(.round-plus-marker) .stButton > button {
        width: 42px;
        height: 42px;
        border-radius: 50%;
        padding: 0;
        font-size: 24px;
        line-height: 1;
    }
    div[data-testid="stVerticalBlock"]:has(.row-context-actions-marker):not(
        :has(div[data-testid="stVerticalBlock"] .row-context-actions-marker)
    ) {
        display: none;
    }
    div[data-testid="stVerticalBlock"]:has(.project-context-actions-marker):not(
        :has(div[data-testid="stVerticalBlock"] .project-context-actions-marker)
    ) {
        display: none;
    }
    .project-insert-zone {
        width: 100%;
        height: 64px;
    }
    .milestone-row-context-menu {
        position: fixed;
        z-index: 1000000;
        min-width: 190px;
        padding: 4px;
        border: 1px solid #d1d5db;
        border-radius: 4px;
        background: white;
        box-shadow: 0 8px 20px rgba(0, 0, 0, 0.16);
    }
    .milestone-row-context-menu button {
        display: block;
        width: 100%;
        padding: 7px 9px;
        border: 0;
        border-radius: 3px;
        background: white;
        color: #111827;
        text-align: left;
    }
    .milestone-row-context-menu button:hover {
        background: #f3f4f6;
    }
    div[data-baseweb="popover"],
    div[data-baseweb="popover"] > div,
    [role="listbox"] {
        background-color: #ffffff !important;
        color: #111827 !important;
    }
    [role="option"] {
        background-color: #ffffff !important;
        color: #111827 !important;
    }
    [role="option"]:hover,
    [role="option"][aria-selected="true"] {
        background-color: #e5e7eb !important;
        color: #111827 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("Meilensteinplaner")

if "projects" not in st.session_state:
    st.session_state.projects = load_projects()


def render_project_block(index: int) -> None:
    """Render one project sheet and keep its widget state isolated by index."""
    entry = st.session_state.projects[index]
    project = entry["project"]
    prefix = f"project_{index}"

    st.session_state.setdefault(f"{prefix}_name", project["project_name"])
    st.session_state.setdefault(f"{prefix}_start", project["planning_start"])
    st.session_state.setdefault(f"{prefix}_end", project["planning_end"])
    st.session_state.setdefault(f"{prefix}_start_id", str(project["start_id"]))
    st.session_state.setdefault(
        f"{prefix}_start_section_number",
        str(project["start_section_number"]),
    )
    st.session_state.setdefault(f"{prefix}_chart_html", None)
    st.session_state.setdefault(f"{prefix}_chart_height", 0)

    delete_cols = st.columns([0.97, 0.03], vertical_alignment="center")
    with delete_cols[1]:
        delete_clicked = st.button(
            "×",
            key=f"{prefix}_delete",
            help="Bereich löschen",
        )

    project_cols = st.columns([2.2, 1.2, 1.2, 0.75, 1.05], vertical_alignment="bottom")
    with project_cols[0]:
        st.text_input("Projektname", key=f"{prefix}_name")
    with project_cols[1]:
        st.date_input("Planungsbeginn", format="DD.MM.YYYY", key=f"{prefix}_start")
    with project_cols[2]:
        st.date_input("Planungsende", format="DD.MM.YYYY", key=f"{prefix}_end")
    with project_cols[3]:
        st.text_input("Start-ID", key=f"{prefix}_start_id")
    with project_cols[4]:
        st.text_input("Start AbschnittsNr.", key=f"{prefix}_start_section_number")

    if delete_clicked:
        if entry["milestones"].empty:
            delete_empty_project(index)
            st.rerun()
        else:
            show_project_delete_blocked_dialog()

    previous_start_id = project["start_id"]
    previous_start_section_number = project["start_section_number"]
    entry["project"] = normalize_project(
        {
            "project_name": st.session_state[f"{prefix}_name"],
            "planning_start": st.session_state[f"{prefix}_start"],
            "planning_end": st.session_state[f"{prefix}_end"],
            "start_id": st.session_state[f"{prefix}_start_id"],
            "start_section_number": st.session_state[
                f"{prefix}_start_section_number"
            ],
        }
    )
    project = entry["project"]
    if (
        project["start_id"] != previous_start_id
        or project["start_section_number"] != previous_start_section_number
    ):
        apply_project_numbering(index, previous_start_id)
    layout = st.container(horizontal=True, vertical_alignment="top", gap=None)
    with layout:
        left = st.container(width=TABLE_WIDTH_PX)
        spacer = st.container(width=5)
        right = st.container(width="stretch")

    with spacer:
        st.markdown("<div style='width: 5px; height: 1px;'></div>", unsafe_allow_html=True)

    with left:
        st.subheader("Gantt-Spreadsheat")
        save_clicked = st.button("Speichern [str+s]", type="primary", key=f"{prefix}_save")
        st.markdown(
            f"<div style='height: {TABLE_MARGIN_TOP_PX}px;'></div>",
            unsafe_allow_html=True,
        )

        editor_key = f"{prefix}_milestones_editor"
        editor_height = (
            TABLE_HEADER_HEIGHT_PX
            + (len(entry["milestones"]) + 1) * TABLE_EDITOR_ROW_HEIGHT_PX
            + TABLE_EDITOR_EXTRA_HEIGHT_PX
        )
        if entry["milestones"].empty:
            new_row_start = project["planning_start"]
        else:
            parsed_new_row_start = pd.to_datetime(
                entry["milestones"].iloc[-1]["Ende"]
            )
            new_row_start = (
                None if pd.isna(parsed_new_row_start) else parsed_new_row_start.date()
            )
        st.data_editor(
            editor_table(entry["milestones"]),
            width=TABLE_WIDTH_PX,
            height=editor_height,
            row_height=TABLE_EDITOR_ROW_HEIGHT_PX,
            num_rows="dynamic",
            hide_index=True,
            disabled=["ID"],
            column_config={
                "ID": st.column_config.NumberColumn("ID", disabled=True, width=20),
                "Name": st.column_config.TextColumn("Name"),
                "Typ": st.column_config.SelectboxColumn(
                    "Typ",
                    options=TYPES,
                    width=90,
                ),
                "Start": st.column_config.DateColumn(
                    "Start",
                    default=new_row_start,
                    format="DD.MM.YYYY",
                    width=80,
                ),
                "Dauer": st.column_config.NumberColumn(
                    "Dauer",
                    min_value=0,
                    step=1,
                    format="%d",
                    width=80,
                ),
                "Ende": st.column_config.DateColumn(
                    "Ende",
                    format="DD.MM.YYYY",
                    width=80,
                ),
                "Vorgänger": st.column_config.TextColumn("Vorgänger", width=100),
                "Resource": st.column_config.TextColumn("Resource", width=100),
            },
            key=editor_key,
            on_change=apply_editor_changes,
            args=(index, editor_key),
        )
        with st.container():
            st.markdown(
                "<span class='row-context-actions-marker'></span>",
                unsafe_allow_html=True,
            )
            for row_index in range(len(entry["milestones"])):
                for position in ("above", "below"):
                    st.button(
                        context_action_label(index, row_index, position),
                        key=f"{prefix}_insert_{row_index}_{position}",
                        on_click=insert_milestone_row,
                        args=(index, row_index, position),
                    )
        install_editor_enter_navigation(index, len(entry["milestones"]))
        install_editor_row_context_menu(index, len(entry["milestones"]))
        install_save_shortcut(index)
        install_generate_shortcut(index)

        if save_clicked:
            save_projects(st.session_state.projects)

    with right:
        st.subheader("Gantt-Chart")
        if st.button("Generieren [str+g]", type="primary", key=f"{prefix}_generate"):
            if entry["milestones"].empty:
                st.session_state[f"{prefix}_chart_html"] = None
                st.session_state[f"{prefix}_chart_height"] = 0
            else:
                figure = build_schedule_figure(entry["milestones"].copy())
                st.session_state[f"{prefix}_chart_html"] = figure_to_html(figure)
                st.session_state[f"{prefix}_chart_height"] = int(
                    figure.layout.height or 2000
                )
        st.markdown(
            f"<div style='height: {CHART_CONTAINER_MARGIN_TOP_PX}px;'></div>",
            unsafe_allow_html=True,
        )

        if st.session_state[f"{prefix}_chart_html"]:
            render_chart_html(
                st.session_state[f"{prefix}_chart_html"],
                st.session_state[f"{prefix}_chart_height"],
            )


for project_index in range(len(st.session_state.projects)):
    if project_index > 0:
        st.markdown(
            f"<div class='project-insert-zone' data-insert-index='{project_index}'></div>",
            unsafe_allow_html=True,
        )
    render_project_block(project_index)

with st.container():
    st.markdown(
        "<span class='project-context-actions-marker'></span>",
        unsafe_allow_html=True,
    )
    for project_index in range(1, len(st.session_state.projects)):
        st.button(
            f"__project_context_insert_{project_index}",
            key=f"project_context_insert_{project_index}",
            on_click=insert_project,
            args=(project_index,),
        )
install_project_context_menu()

st.markdown("<div style='height: 56px;'></div>", unsafe_allow_html=True)
plus_cols = st.columns([0.06, 0.94], gap=None, vertical_alignment="center")
with plus_cols[0]:
    st.markdown("<span class='round-plus-marker'></span>", unsafe_allow_html=True)
    add_project_clicked = st.button("+", key="add_project")
    if add_project_clicked:
        st.session_state.projects.append(project_entry())
        st.rerun()
with plus_cols[1]:
    st.markdown(
        "<div style='height: 1px; background: #d1d5db; width: 100%;'></div>",
        unsafe_allow_html=True,
    )
