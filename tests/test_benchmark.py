from doc_drift.benchmark import run_benchmarks

def test_benchmark_suite_accuracy():
    results = run_benchmarks()
    assert results["total_cases"] >= 5
    assert results["accuracy"] >= 0.80
    assert results["precision"] >= 0.90
    assert results["recall"] >= 0.80
    assert results["false_positives"] == 0
