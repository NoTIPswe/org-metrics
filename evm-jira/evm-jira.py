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

# ── Configuration ────────────────────────────
JIRA_URL = os.environ.get("JIRA_URL", "https://notipswe.atlassian.net")
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_TOKEN = os.environ["JIRA_TOKEN"]

PROJECT_KEY = "NT"
ROLE_FIELD_ID = "customfield_10041"

BAC = 12940.0
HOURLY_RATES = {
    "Responsabile": 30,
    "Verificatore": 15,
    "Analista": 25,
    "Amministratore": 20,
    "Progettista": 25,
    "Programmatore": 15,
}
DEFAULT_RATE = 35.0

PROJECT_PLANNED_DAYS = 156


def connect_jira():
    return JIRA(server=JIRA_URL, basic_auth=(JIRA_EMAIL, JIRA_TOKEN))


def get_project_sprints(jira):
    """Returns all sprints for the project, sorted by start date."""
    boards = jira.boards(projectKeyOrID=PROJECT_KEY)
    if not boards:
        boards = jira.boards()

    all_sprints = []
    for board in boards:
        try:
            all_sprints.extend(jira.sprints(board.id))
        except Exception:
            continue

    seen = set()
    unique = []
    for s in all_sprints:
        if s.id not in seen:
            seen.add(s.id)
            unique.append(s)

    unique.sort(key=lambda s: getattr(s, "startDate", None) or "9999")
    return unique


def get_sprints_up_to(jira, target_name):
    """Returns all sprint IDs from the first up to and including target_name."""
    sprint_ids = []
    for sprint in get_project_sprints(jira):
        sprint_ids.append(sprint.id)
        if sprint.name.strip() == target_name.strip():
            return sprint_ids

    print(f"Sprint '{target_name}' non trovato.")
    sys.exit(1)


def fetch_issues(jira, sprint_ids):
    """Fetches all issues across multiple sprints."""
    ids_str = ", ".join(str(sid) for sid in sprint_ids)
    return jira.search_issues(
        f"sprint in ({ids_str})",
        maxResults=False,
        fields=f"key,issuetype,status,timeoriginalestimate,timespent,{ROLE_FIELD_ID}",
    )


def seconds_to_hours(seconds):
    return seconds / 3600.0 if seconds else 0.0


def get_role(issue):
    field_value = getattr(issue.fields, ROLE_FIELD_ID, None)
    if field_value is None:
        return "Default"
    role_name = field_value.value if hasattr(field_value, "value") else str(field_value)
    return role_name if role_name in HOURLY_RATES else "Default"


def is_done(issue):
    try:
        return issue.fields.status.statusCategory.key == "done"
    except AttributeError:
        return issue.fields.status.name.lower() in ("done", "completed", "closed")


def build_dataframe(issues):
    rows = []
    seen_keys = set()

    for issue in issues:
        if issue.key in seen_keys:
            continue
        if issue.fields.issuetype.name not in ["Execution Subtask", "Verification Subtask"]:
            continue
        seen_keys.add(issue.key)

        role = get_role(issue)
        rate = HOURLY_RATES.get(role, DEFAULT_RATE)
        estimated_h = seconds_to_hours(issue.fields.timeoriginalestimate)
        spent_h = seconds_to_hours(issue.fields.timespent)
        budget = estimated_h * rate

        rows.append({
            "Key": issue.key,
            "Type": issue.fields.issuetype.name,
            "Status": issue.fields.status.name,
            "Role": role,
            "Estimated (h)": round(estimated_h, 2),
            "Spent (h)": round(spent_h, 2),
            "Rate (€/h)": rate,
            "PV (€)": round(budget, 2),
            "EV (€)": round(budget if is_done(issue) else 0.0, 2),
            "AC (€)": round(spent_h * rate, 2),
        })

    return pd.DataFrame(rows)


def safe_div(numerator, denominator):
    if denominator == 0:
        return 0.0
    return numerator / denominator


def compute_metrics(df, days_elapsed):
    total_pv = df["PV (€)"].sum()
    total_ev = df["EV (€)"].sum()
    total_ac = df["AC (€)"].sum()

    cpi = safe_div(total_ev, total_ac)
    spi = safe_div(total_ev, total_pv)
    eac = total_ac + safe_div(BAC - total_ev, cpi)
    etc = eac - total_ac
    teac = safe_div(PROJECT_PLANNED_DAYS, spi)
    burn_rate = safe_div(total_ac, days_elapsed)

    return {
        "EV (€)": round(total_ev, 2),
        "PV (€)": round(total_pv, 2),
        "AC (€)": round(total_ac, 2),
        "CPI": round(cpi, 2),
        "SPI": round(spi, 2),
        "EAC (€)": round(eac, 2),
        "ETC (€)": round(etc, 2),
        "TEAC (giorni)": round(teac, 2),
        "Burn Rate (€/giorno)": round(burn_rate, 2),
    }


def print_summary(df, sprint_name, days_elapsed):
    if df.empty:
        print("Nessuna issue trovata.")
        return

    m = compute_metrics(df, days_elapsed)

    print(f" Sprint target: {sprint_name}")
    print(f" BAC (Budget At Completion): € {BAC:,.2f}")
    print(f" [MP01] Earned Value  (EV):      € {m['EV (€)']:>10,.2f}")
    print(f" [MP02] Planned Value (PV):      € {m['PV (€)']:>10,.2f}")
    print(f" [MP03] Actual Cost   (AC):      € {m['AC (€)']:>10,.2f}")
    print(f" [MP04] Cost Perf. Index (CPI):      {m['CPI']:>10.2f}")
    print(f" [MP05] Schedule Perf. Idx (SPI):    {m['SPI']:>10.2f}")
    print(f" [MP06] Estimate At Completion:  € {m['EAC (€)']:>10,.2f}")
    print(f" [MP07] Estimate To Complete:    € {m['ETC (€)']:>10,.2f}")
    print(f" [MP08] Time Est. At Compl.:     {m['TEAC (giorni)']:>10.2f} giorni")
    print(f" [MP09] Budget Burn Rate:        € {m['Burn Rate (€/giorno)']:>10,.2f} / giorno")



def get_google_credentials():
    """Get Google credentials from environment."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON environment variable is not set")

    creds_info = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    return Credentials.from_service_account_info(creds_info, scopes=scopes)


def append_to_google_sheet(metrics):
    """Append the EVM metrics and timestamp to the Google Sheet."""
    credentials = get_google_credentials()
    client = gspread.authorize(credentials)

    spreadsheet = client.open("notip-dashboard")

    worksheet_name = "evm-jira"
    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=worksheet_name, rows=1000, cols=12
        )
        headers = ["Timestamp", "BAC (€)", "EV (€)", "PV (€)", "AC (€)",
                    "CPI", "SPI", "EAC (€)", "ETC (€)", "TEAC (giorni)", "Burn Rate (€/giorno)"]
        worksheet.update("A1:K1", [headers])

    timestamp = datetime.now(timezone.utc).isoformat()

    row = [
        timestamp,
        metrics["BAC (€)"],
        metrics["EV (€)"],
        metrics["PV (€)"],
        metrics["AC (€)"],
        metrics["CPI"],
        metrics["SPI"],
        metrics["EAC (€)"],
        metrics["ETC (€)"],
        metrics["TEAC (giorni)"],
        metrics["Burn Rate (€/giorno)"],
    ]
    worksheet.append_row(row)
    print(f"Appended to Google Sheet: {timestamp}")


def compute_days_elapsed(sprints):
    """Calculate days elapsed from the start of the first sprint to today."""
    start_str = sprints[0].startDate
    project_start = datetime.fromisoformat(start_str.replace("Z", "+00:00")).date()
    return (date.today() - project_start).days


def collect_cumulative_metrics(jira):
    """Fetch all sprints and compute cumulative EVM metrics."""
    sprints = get_project_sprints(jira)
    if not sprints:
        print("Nessuno sprint trovato.")
        sys.exit(1)

    days_elapsed = compute_days_elapsed(sprints)
    sprint_ids = [s.id for s in sprints]
    issues = fetch_issues(jira, sprint_ids)
    df = build_dataframe(issues)

    if df.empty:
        print("Nessuna issue trovata.")
        sys.exit(1)

    m = compute_metrics(df, days_elapsed)
    m["BAC (€)"] = BAC
    return m


def export_snapshot(jira, output_file):
    """Exports a single row with the current timestamp and cumulative EVM metrics."""
    m = collect_cumulative_metrics(jira)
    m["Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cols = ["Timestamp", "BAC (€)", "EV (€)", "PV (€)", "AC (€)",
            "CPI", "SPI", "EAC (€)", "ETC (€)", "TEAC (giorni)", "Burn Rate (€/giorno)"]

    row_df = pd.DataFrame([m])[cols]

    write_header = not os.path.exists(output_file)
    row_df.to_csv(output_file, mode="a", index=False, sep=";", header=write_header)

    print(f"\nSnapshot aggiunto a: {output_file}")
    for c in cols:
        print(f"  {c}: {m[c]}")


def main():
    parser = argparse.ArgumentParser(description="EVM metrics from Jira")
    parser.add_argument(
        "--export-csv",
        metavar="FILE",
        help="Esporta uno snapshot con timestamp delle metriche cumulative (append se il file esiste)",
    )
    parser.add_argument(
        "--google-sheet",
        action="store_true",
        help="Invia le metriche cumulative al Google Sheet notip-dashboard/evm-jira",
    )
    args = parser.parse_args()

    print(f"Connecting to {JIRA_URL} ...")
    jira = connect_jira()

    if args.export_csv:
        export_snapshot(jira, args.export_csv)
    elif args.google_sheet:
        m = collect_cumulative_metrics(jira)
        append_to_google_sheet(m)
    else:
        sprint_name = os.environ.get("JIRA_SPRINT_NAME")
        if not sprint_name:
            print("JIRA_SPRINT_NAME non impostata. Usa --google-sheet o --export-csv.")
            sys.exit(1)
        sprints = get_project_sprints(jira)
        days_elapsed = compute_days_elapsed(sprints)
        sprint_ids = get_sprints_up_to(jira, sprint_name)
        print(f"Sprints: {len(sprint_ids)}")
        issues = fetch_issues(jira, sprint_ids)
        df = build_dataframe(issues)
        print_summary(df, sprint_name, days_elapsed)


if __name__ == "__main__":
    main()
