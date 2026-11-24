import numpy as np

from simulation.aggregate_consumption import (
    AggregateOptions,
    aggregate_profile,
    sample_shift_totals,
)


def test_shift_totals_match_feasibility_across_batches():
    support = np.array([[1, 2, 5], [2, 4, 11], [5, 5, 9]])
    options = AggregateOptions(samples=25_001, max_samples=25_001, seed=42)

    totals = sample_shift_totals(support, options=options, seed=43)
    statistics = aggregate_profile(support, options=options, seed=43)["statistics"]

    assert totals.shape == (statistics["samples"],)
    assert np.quantile(totals, 0.95) == statistics["q95_total_kwh"]
    histogram = statistics["total_energy_histogram"]
    counts, _ = np.histogram(totals, bins=histogram["bin_edges_kwh"])
    np.testing.assert_array_equal(counts, histogram["counts"])
    np.testing.assert_array_equal(
        totals, sample_shift_totals(support, options=options, seed=43)
    )
    assert not np.array_equal(totals, sample_shift_totals(support, options=options))
    rng = np.random.default_rng(43)
    indices = rng.integers(3, size=(25_001, 3))
    expected = support[np.arange(3)[None, :], indices].sum(axis=1)
    np.testing.assert_array_equal(totals, expected)


def test_shift_totals_sample_repeated_trip_occurrences_independently():
    support = np.tile([0, 100], (2, 1))
    options = AggregateOptions(samples=10_000, max_samples=10_000, seed=42)

    totals = sample_shift_totals(support, options=options)
    values, counts = np.unique(totals, return_counts=True)

    np.testing.assert_array_equal(values, [0, 100, 200])
    np.testing.assert_allclose(counts / totals.size, [0.25, 0.5, 0.25], atol=0.02)


def test_shift_totals_use_feasibility_precision_refinement():
    support = np.tile(np.linspace(0, 100, 450), (5, 1))
    options = AggregateOptions(samples=100, max_samples=400, precision_kwh=1e-9)

    totals = sample_shift_totals(support, options=options)
    statistics = aggregate_profile(support, options=options)["statistics"]

    assert totals.shape == (400,)
    assert [row["samples"] for row in statistics["refinement"]] == [100, 200, 400]
    assert np.quantile(totals, 0.95) == statistics["q95_total_kwh"]
    initial = sample_shift_totals(support, options=AggregateOptions(100, 100))
    np.testing.assert_array_equal(totals[:100], initial)
