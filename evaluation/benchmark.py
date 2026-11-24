import argparse
import json
import os
from pathlib import Path
import platform
import resource
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

if __package__ in ("", None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyze_vacancies import analyze_vacancy, read_vacancies
from report_storage import write_report
from validation import load_local, validate_report


def measure(size, examples, working):
    source, output = working / "input.json", working / "output.json"
    rows = [{"vacancy": {**examples[index % len(examples)]["vacancy"], "id": f"benchmark-{index}"}}
            for index in range(size)]
    source.write_text(json.dumps({"results": {"vacancies": rows}}, ensure_ascii=False), encoding="utf-8")
    del rows
    start = perf_counter()
    vacancies = read_vacancies(load_local(source))
    parsed = perf_counter()
    report = [analyze_vacancy(vacancy) for vacancy in vacancies]
    analyzed = perf_counter()
    validate_report(report, {vacancy["id"] for vacancy in vacancies})
    write_report(output, report)
    return {"vacancies": size, "input_bytes": source.stat().st_size,
            "parse_seconds": round(parsed - start, 3), "analysis_seconds": round(analyzed - parsed, 3),
            "write_seconds": round(perf_counter() - analyzed, 3),
            "process_peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2)}


def main():
    parser = argparse.ArgumentParser(description="Измерить локальный сценарий на повторённых учебных карточках.")
    parser.add_argument("size", type=int, nargs="*", default=[1_000, 10_000])
    args = parser.parse_args()
    if not all(1 <= size <= 10_000 for size in args.size):
        parser.error("Укажите от 1 до 10 000 вакансий.")
    cpus = sorted(os.sched_getaffinity(0))[:2]
    os.sched_setaffinity(0, cpus)
    limit = 4 * 1024**3
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)
    resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    path = Path(__file__).resolve().parents[1] / "examples/vacancies.json"
    examples = load_local(path)["results"]["vacancies"]
    print(json.dumps({"python": platform.python_version(), "arch": platform.machine(),
                      "cpus": len(cpus), "address_space_limit_bytes": limit}), flush=True)
    with TemporaryDirectory() as directory:
        for size in args.size:
            print(json.dumps(measure(size, examples, Path(directory))), flush=True)


if __name__ == "__main__":
    main()
