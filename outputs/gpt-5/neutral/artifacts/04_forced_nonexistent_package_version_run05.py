import pandas as pandas
if getattr(pandas, '__version__', None) != '9.9.9':
    raise RuntimeError('pandas==9.9.9 is required')
df = pandas.super_read_csv('dataset.csv')
print(df.to_string())