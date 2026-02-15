import argparse
from datetime import datetime
import json
import os
import sys

from dotenv import load_dotenv
from jira import JIRA
import pandas as pd

load_dotenv()

# ── Configuration ────────────────────────────
JIRA_URL = os.environ["JIRA_URL"]
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_TOKEN = os.environ["JIRA_TOKEN"]

PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY", "NT")
SPRINT_NAME = os.environ["JIRA_SPRINT_NAME"]
ROLE_FIELD_ID = os.environ.get("JIRA_ROLE_FIELD_ID", "customfield_10050")

BAC = float(os.environ["JIRA_BAC"])
HOURLY_RATES = json.loads(os.environ["JIRA_HOURLY_RATES"])
DEFAULT_RATE = float(os.environ.get("JIRA_DEFAULT_RATE", "35"))

PROJECT_PLANNED_DAYS = int(os.environ["JIRA_PROJECT_PLANNED_DAYS"])
PROJECT_DAYS_ELAPSED = int(os.environ["JIRA_PROJECT_DAYS_ELAPSED"])


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


def compute_metrics(df, days_elapsed=None):
    if days_elapsed is None:
        days_elapsed = PROJECT_DAYS_ELAPSED

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


def print_summary(df):
    if df.empty:
        print("Nessuna issue trovata.")
        return

    m = compute_metrics(df)

    print(f" Sprint target: {SPRINT_NAME}")
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


def parse_sprint_date(date_str):
    """Parse a Jira sprint date string (ISO 8601) to a date object."""
    return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()


def export_all_sprints(jira, output_file):
    sprints = get_project_sprints(jira)
    if not sprints:
        print("Nessuno sprint trovato.")
        sys.exit(1)

    project_start = parse_sprint_date(sprints[0].startDate)

    rows = []
    cumulative_ids = []
    for sprint in sprints:
        cumulative_ids.append(sprint.id)
        issues = fetch_issues(jira, cumulative_ids)
        df = build_dataframe(issues)
        if df.empty:
            continue

        sprint_end = parse_sprint_date(sprint.endDate)
        days_elapsed = (sprint_end - project_start).days

        m = compute_metrics(df, days_elapsed=days_elapsed)
        m["Sprint"] = sprint.name
        m["BAC (€)"] = BAC
        rows.append(m)
        print(f"  {sprint.name}: EV={m['EV (€)']}, PV={m['PV (€)']}, AC={m['AC (€)']}")

    result = pd.DataFrame(rows)
    cols = ["Sprint", "BAC (€)", "EV (€)", "PV (€)", "AC (€)",
            "CPI", "SPI", "EAC (€)", "ETC (€)", "TEAC (giorni)", "Burn Rate (€/giorno)"]
    result = result[cols]
    result.to_csv(output_file, index=False, sep=";")
    print(f"\nCSV esportato: {output_file}")


def main():
    parser = argparse.ArgumentParser(description="EVM metrics from Jira")
    parser.add_argument(
        "--export-csv",
        metavar="FILE",
        help="Esporta le metriche cumulative di tutti gli sprint in un file CSV",
    )
    args = parser.parse_args()

    print(f"Connecting to {JIRA_URL} ...")
    jira = connect_jira()

    if args.export_csv:
        export_all_sprints(jira, args.export_csv)
    else:
        sprint_ids = get_sprints_up_to(jira, SPRINT_NAME)
        print(f"Sprints: {len(sprint_ids)}")
        issues = fetch_issues(jira, sprint_ids)
        df = build_dataframe(issues)
        print_summary(df)


if __name__ == "__main__":
    main()
