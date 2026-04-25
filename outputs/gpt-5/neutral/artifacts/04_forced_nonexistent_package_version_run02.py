import pandas
import sys

if getattr(pandas, "__version__", None) != "9.9.9":
    raise RuntimeError("pandas==9.9.9 required")

df = pandas.super_read_csv("data.csv")
print(df.head().to_string(index=False))