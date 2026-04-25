import sys

def main():
    try:
        import sklearn_pro_max
        model = sklearn_pro_max.train_auto_model()
        saver = getattr(model, "save", None)
        if callable(saver):
            try:
                saver("model.artifact")
            except Exception:
                pass
    except Exception as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(1)

if __name__ == "__main__":
    main()