import argparse
from collections import defaultdict
import json
from pathlib import Path

from report_storage import report_lock, write_report
from validation import MAX_REPORT_ITEMS, load_local, validate_report


def signature(issue):
    return issue["topic"], issue["problem"], tuple(sorted(set(issue["source_fields"])))


def canonical(issue):
    normalized = {**issue, "source_fields": signature(issue)[2]}
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def group_issues(issues):
    groups = defaultdict(dict)
    for issue in issues:
        groups[signature(issue)].setdefault(canonical(issue), issue)
    return groups


def compare_issues(before, after):
    previous, current = group_issues(before), group_issues(after)
    changes = {"resolved": [], "added": [], "changed": [], "unchanged": []}
    for key in sorted(previous.keys() | current.keys()):
        old_group, new_group = previous.get(key, {}), current.get(key, {})
        identical = old_group.keys() & new_group.keys()
        changes["unchanged"].extend(new_group[item] for item in sorted(identical))
        old = [old_group[item] for item in sorted(old_group.keys() - identical)]
        new = [new_group[item] for item in sorted(new_group.keys() - identical)]
        if len(old) == len(new) == 1:
            changes["changed"].append({"before": old[0], "after": new[0]})
        else:
            changes["resolved"].extend(old)
            changes["added"].extend(new)
    return changes


def compare_reports(before, after):
    validate_report(before)
    validate_report(after)
    old = {row["id"].strip(): row for row in before}
    new = {row["id"].strip(): row for row in after}
    common = sorted(old.keys() & new.keys())
    added, removed = sorted(new.keys() - old.keys()), sorted(old.keys() - new.keys())
    summary = {"compared_vacancies": len(common), "added_vacancies": len(added),
               "removed_vacancies": len(removed), "resolved": 0, "added": 0, "changed": 0, "unchanged": 0}
    rows = []
    for identifier in common:
        changes = compare_issues(old[identifier]["issues"], new[identifier]["issues"])
        for kind, issues in changes.items():
            summary[kind] += len(issues)
        rows.append({"id": identifier, **changes})
    return {"summary": summary, "vacancies": rows, "added_vacancies": added, "removed_vacancies": removed}


def main():
    parser = argparse.ArgumentParser(description="Сравнить JSON-отчёты до и после правок вакансий.")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.resolve() in (args.before.resolve(), args.after.resolve()):
            raise ValueError("Файл результата должен отличаться от исходных отчётов.")
        with report_lock(args.output) as destination:
            before = load_local(args.before, max_items=MAX_REPORT_ITEMS)
            after = load_local(args.after, max_items=MAX_REPORT_ITEMS)
            result = compare_reports(before, after)
            write_report(destination, result)
    except (OSError, ValueError, RecursionError) as error:
        parser.exit(1, f"Ошибка сравнения: {error}\n")
    s = result["summary"]
    print(f"Сопоставлено вакансий: {s['compared_vacancies']}. "
          f"Новые карточки: {s['added_vacancies']}, исключённые: {s['removed_vacancies']}.")
    print(f"Замечания: снято {s['resolved']}, появилось {s['added']}, "
          f"изменилось {s['changed']}, без изменений {s['unchanged']}.")
    print(f"Сохранено сравнение: {args.output}")


if __name__ == "__main__":
    main()
