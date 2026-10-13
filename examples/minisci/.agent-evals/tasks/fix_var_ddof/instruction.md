`minisci.var` does not follow NumPy for sample variance.

```python
>>> import minisci, numpy as np
>>> minisci.var([1.0, 2.0, 3.0, 4.0], ddof=1)
1.25
>>> np.var([1.0, 2.0, 3.0, 4.0], ddof=1)
1.6666666666666667
```

Make `minisci.var` match NumPy for any valid `ddof`. Do not weaken or delete existing tests.
