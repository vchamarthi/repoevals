`minisci.logsumexp([1000.0, 1000.0])` returns `inf`; the correct value is about `1000.6931`. Users hit this with
log-likelihoods. Make `minisci.logsumexp` accurate for very large and very small inputs while keeping its behaviour
for ordinary inputs. Do not weaken or delete existing tests.
