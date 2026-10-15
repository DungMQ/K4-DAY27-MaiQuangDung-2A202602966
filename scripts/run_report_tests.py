"""Run the offline suite and retain logs plus machine-readable results."""
import contextlib
import importlib.metadata
import json
import platform
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class ReportResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.cases.append({"id": test.id(), "result": "PASS"})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.cases.append({"id": test.id(), "result": "FAIL"})

    def addError(self, test, err):
        super().addError(test, err)
        self.cases.append({"id": test.id(), "result": "ERROR"})


def main():
    output = Path("artifacts")
    output.mkdir(exist_ok=True)
    with (output / "test_results.txt").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            suite = unittest.defaultTestLoader.discover("tests")
            result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=ReportResult).run(suite)
    packages = ["langgraph", "langgraph-checkpoint-sqlite", "langchain-openai", "streamlit", "pydantic", "aiosqlite"]
    summary = {"executed_at": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(),
               "python": platform.python_version(), "os": platform.platform(),
               "tests_run": result.testsRun, "failures": len(result.failures),
               "errors": len(result.errors), "skipped": len(result.skipped),
               "successful": result.wasSuccessful(), "cases": result.cases,
               "versions": {name: importlib.metadata.version(name) for name in packages}}
    (output / "test_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "cases"}, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
