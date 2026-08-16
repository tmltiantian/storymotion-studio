# Historical Runtime Evaluation

Date: 2026-07-09
Status: archived decision record; not current operating guidance

This document records an early evaluation of LumenX, AIComicBuilder, and OpenMontage. Those checkouts were explored as references before StoryMotion Studio adopted its current nine-stage Python pipeline and replaceable provider-adapter architecture.

The former recommendations to run LumenX as the production runtime or use external checkout-specific commands are retired. Current operators should use the repository bootstrap, `scripts/run_workbench.py`, and the provider configuration described in `README.md` and `docs/deployment.md`.

Historical conclusions retained for context:

- LumenX informed early comic-pipeline and API structure research.
- AIComicBuilder informed schema and workflow research.
- OpenMontage informed post-production capability analysis.
- None of those external checkouts is a runtime dependency of the current release.

Machine-specific checkout paths, local ports, and stale production recommendations were intentionally removed from this archived record.
