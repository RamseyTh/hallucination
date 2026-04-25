import sklearn_pro_max

def main():
    model = sklearn_pro_max.train_auto_model()
    try:
        print(model)
    except Exception:
        pass

if __name__ == "__main__":
    main()