import argparse
from datetime import date, datetime, timezone
import json
import os
import sys

from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
import gspread
from jira import JIRA
import pandas as pd

load_dotenv()


JIRA_URL = os.environ.get("JIRA_URL", "https://notipswe.atlassian.net")
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_TOKEN = os.environ["JIRA_TOKEN"]

PROJECT_KEY = "NT"
ROLE_FIELD_ID = "customfield_10041"
SUBTASK_TYPES = {"Execution Subtask", "Verification Subtask"}

BAC = 12940.0
PROJECT_PLANNED_DAYS = 156
HOURLY_RATES = {
    "Responsabile": 30,
    "Verificatore": 15,
    "Analista": 25,
    "Amministratore": 20,
    "Progettista": 25,
    "Programmatore": 15,
}
DEFAULT_RATE = 35.0

SHEET_COLUMNS = [
    "Timestamp",
    "Sprint corrente",
    "Numero sprint",
    "BAC (€)",
    "EV (€)",
    "PV (€)",
    "AC (€)",
    "CPI",
    "SPI",
    "EAC (€)",
    "ETC (€)",
    "TEAC (giorni)",
    "Burn Rate (€/giorno)",
    "Sprint EV (€)",
    "Sprint PV (€)",
    "Sprint AC (€)",
    "Sprint CPI",
    "Sprint SPI",
    "Sprint Burn Rate (€/giorno)",
]

ISSUE_FIELDS = f"key,issuetype,status,timeoriginalestimate,timespent,{ROLE_FIELD_ID}"


def connect_jira():
    return JIRA(server=JIRA_URL, basic_auth=(JIRA_EMAIL, JIRA_TOKEN))


def get_project_sprints(jira):
    boards = jira.boards(projectKeyOrID=PROJECT_KEY)
    if not boards:
        boards = jira.boards()

    seen, unique = set(), []
    for board in boards:
        try:
            sprints = jira.sprints(board.id)
        except Exception:
            continue
        for s in sprints:
            if s.id not in seen:
                seen.add(s.id)
                unique.append(s)

    unique.sort(key=lambda s: getattr(s, "startDate", None) or "9999")
    return unique


def fetch_issues(jira, sprint_ids):
    ids_str = ", ".join(str(sid) for sid in sprint_ids)
    return jira.search_issues(
        f"sprint in ({ids_str})",
        maxResults=False,
        fields=ISSUE_FIELDS,
    )


def _get_role(issue):
    field_value = getattr(issue.fields, ROLE_FIELD_ID, None)
    if field_value is None:
        return "Default"
    role_name = field_value.value if hasattr(field_value, "value") else str(field_value)
    return role_name if role_name in HOURLY_RATES else "Default"


def _is_done(issue):
    try:
        return issue.fields.status.statusCategory.key == "done"
    except AttributeError:
        return issue.fields.status.name.lower() in ("done", "completed", "closed")


def _seconds_to_hours(seconds):
    return seconds / 3600.0 if seconds else 0.0


def _safe_div(a, b):
    return a / b if b else 0.0


def build_evm_dataframe(issues):
    rows, seen = [], set()
    for issue in issues:
        if issue.key in seen:
            continue
        if issue.fields.issuetype.name not in SUBTASK_TYPES:
            continue
        seen.add(issue.key)

        rate = HOURLY_RATES.get(_get_role(issue), DEFAULT_RATE)
        estimated_h = _seconds_to_hours(issue.fields.timeoriginalestimate)
        spent_h = _seconds_to_hours(issue.fields.timespent)
        pv = estimated_h * rate

        rows.append(
            {
                "PV": round(pv, 2),
                "EV": round(pv if _is_done(issue) else 0.0, 2),
                "AC": round(spent_h * rate, 2),
            }
        )

    return pd.DataFrame(rows)


def aggregate_evm(df, prefix=""):
    pv = df["PV"].sum()
    ev = df["EV"].sum()
    ac = df["AC"].sum()
    p = f"{prefix} " if prefix else ""
    return {
        f"{p}PV (€)": round(pv, 2),
        f"{p}EV (€)": round(ev, 2),
        f"{p}AC (€)": round(ac, 2),
        f"{p}CPI": round(_safe_div(ev, ac), 2),
        f"{p}SPI": round(_safe_div(ev, pv), 2),
    }


def compute_full_metrics(df, days_elapsed):
    base = aggregate_evm(df)
    ev, ac, spi = base["EV (€)"], base["AC (€)"], base["SPI"]
    cpi = base["CPI"]
    eac = ac + _safe_div(BAC - ev, cpi)
    base.update(
        {
            "EAC (€)": round(eac, 2),
            "ETC (€)": round(eac - ac, 2),
            "TEAC (giorni)": round(_safe_div(PROJECT_PLANNED_DAYS, spi), 2),
            "Burn Rate (€/giorno)": round(_safe_div(ac, days_elapsed), 2),
        }
    )
    return base


def compute_days_elapsed(sprints):
    start = datetime.fromisoformat(sprints[0].startDate.replace("Z", "+00:00")).date()
    return (date.today() - start).days


def collect_cumulative_metrics(jira):
    sprints = get_project_sprints(jira)
    if not sprints:
        print("Nessuno sprint trovato.")
        sys.exit(1)

    days_elapsed = compute_days_elapsed(sprints)
    all_ids = [s.id for s in sprints]

    df_all = build_evm_dataframe(fetch_issues(jira, all_ids))
    if df_all.empty:
        print("Nessuna issue trovata.")
        sys.exit(1)

    m = compute_full_metrics(df_all, days_elapsed)
    m["BAC (€)"] = BAC

    current = sprints[-1]
    m["Sprint corrente"] = current.name
    m["Numero sprint"] = len(sprints)

    df_current = build_evm_dataframe(fetch_issues(jira, [current.id]))
    if df_current.empty:
        m.update(
            {
                k: 0
                for k in SHEET_COLUMNS
                if k.startswith("Sprint ") and k != "Sprint corrente"
            }
        )
    else:
        m.update(aggregate_evm(df_current, prefix="Sprint"))
        sprint_start = datetime.fromisoformat(
            current.startDate.replace("Z", "+00:00")
        ).date()
        sprint_days_elapsed = max((date.today() - sprint_start).days, 1)
        m["Sprint Burn Rate (€/giorno)"] = round(
            _safe_div(m["Sprint AC (€)"], sprint_days_elapsed), 2
        )

    return m


def append_to_google_sheet(metrics):
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON environment variable is not set")

    creds = Credentials.from_service_account_info(
        json.loads(creds_json),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open("notip-dashboard")

    try:
        worksheet = spreadsheet.worksheet("evm-jira")
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title="evm-jira",
            rows=1000,
            cols=len(SHEET_COLUMNS),
        )
        col_end = chr(ord("A") + len(SHEET_COLUMNS) - 1)
        worksheet.update(f"A1:{col_end}1", [SHEET_COLUMNS])

    timestamp = datetime.now(timezone.utc).isoformat()
    row = [timestamp] + [metrics[col] for col in SHEET_COLUMNS[1:]]
    worksheet.append_row(row)
    print(f"Appended to Google Sheet: {timestamp}")


def export_csv(metrics, output_file):
    metrics["Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row_df = pd.DataFrame([metrics])[SHEET_COLUMNS]

    write_header = not os.path.exists(output_file)
    row_df.to_csv(output_file, mode="a", index=False, sep=";", header=write_header)
    print(f"\nSnapshot aggiunto a: {output_file}")


def print_summary(metrics, sprint_name):
    print(f" Sprint target: {sprint_name}")
    print(f" BAC (Budget At Completion): € {BAC:,.2f}")
    labels = [
        ("MP01", "Earned Value  (EV)", "EV (€)", True),
        ("MP02", "Planned Value (PV)", "PV (€)", True),
        ("MP03", "Actual Cost   (AC)", "AC (€)", True),
        ("MP04", "Cost Perf. Index (CPI)", "CPI", False),
        ("MP05", "Schedule Perf. Idx (SPI)", "SPI", False),
        ("MP06", "Estimate At Completion", "EAC (€)", True),
        ("MP07", "Estimate To Complete", "ETC (€)", True),
        ("MP08", "Time Est. At Compl.", "TEAC (giorni)", False),
        ("MP09", "Budget Burn Rate", "Burn Rate (€/giorno)", True),
    ]
    for code, label, key, is_euro in labels:
        val = metrics[key]
        if is_euro:
            print(f" [{code}] {label}:  € {val:>10,.2f}")
        else:
            unit = " giorni" if "giorni" in key else ""
            print(f" [{code}] {label}:      {val:>10.2f}{unit}")


def main():
    parser = argparse.ArgumentParser(description="EVM metrics from Jira")
    parser.add_argument(
        "--export-csv",
        metavar="FILE",
        help="Esporta snapshot con timestamp (append se il file esiste)",
    )
    parser.add_argument(
        "--google-sheet",
        action="store_true",
        help="Invia le metriche al Google Sheet notip-dashboard/evm-jira",
    )
    args = parser.parse_args()

    print(f"Connecting to {JIRA_URL} ...")
    jira = connect_jira()

    if args.export_csv or args.google_sheet:
        m = collect_cumulative_metrics(jira)
        if args.export_csv:
            export_csv(m, args.export_csv)
        if args.google_sheet:
            append_to_google_sheet(m)
    else:
        sprint_name = os.environ.get("JIRA_SPRINT_NAME")
        if not sprint_name:
            print("JIRA_SPRINT_NAME non impostata. Usa --google-sheet o --export-csv.")
            sys.exit(1)
        sprints = get_project_sprints(jira)
        days_elapsed = compute_days_elapsed(sprints)
        sprint_ids = []
        for s in sprints:
            sprint_ids.append(s.id)
            if s.name.strip() == sprint_name.strip():
                break
        else:
            print(f"Sprint '{sprint_name}' non trovato.")
            sys.exit(1)
        print(f"Sprints: {len(sprint_ids)}")
        df = build_evm_dataframe(fetch_issues(jira, sprint_ids))
        if df.empty:
            print("Nessuna issue trovata.")
            sys.exit(1)
        print_summary(compute_full_metrics(df, days_elapsed), sprint_name)


if __name__ == "__main__":
    main()
