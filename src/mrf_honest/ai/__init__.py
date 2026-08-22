"""AI narration outside the graded path (ADR 0006).

Nothing here is imported by inspection, scoring, comparison, or the site
renderer. A grade is produced deterministically and then, optionally, narrated
by a model whose every claim must quote a committed source document and pass
:meth:`mrf_honest.ai.corpus.CorpusIndex.verify_quote` before it is shown.
"""
