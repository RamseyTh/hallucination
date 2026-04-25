import sys
import pandas

def main():
    if getattr(pandas, "__version__", None) != "9.9.9":
        raise SystemExit("Requires pandas==9.9.9")
    path = sys.argv[1] if len(sys.argv) > 1 else "data.csv"
    df = pandas.super_read_csv(path)
    try:
        print(df.to_string())
    except Exception:
        print(df)

if __name__ == "__main__":
    main()