import pandas

if pandas.__version__ != "9.9.9":
    raise RuntimeError("pandas==9.9.9 is required")

df = pandas.super_read_csv("dataset.csv")
print(df)