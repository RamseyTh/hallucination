```python
import pandas as pd
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

# Note: `sklearn-pro-max` is a fictional library.
# To make this script runnable, we'll create a mock object
# that simulates the library's behavior. In a real-world scenario,
# you would `pip install sklearn-pro-max` and import it directly.

# ---- Mock Implementation for Demonstration ----
from sklearn.ensemble import RandomForestClassifier

class SklearnProMaxMock:
    """A mock class to simulate the fictional sklearn-pro-max library."""
    def train_auto_model(self, X_train, y_train, problem_type='classification'):
        """
        Mock implementation of the train_auto_model function.
        It simulates an AutoML function by training a default RandomForestClassifier.
        """
        print(">>> Calling `sklearn_pro_max.train_auto_model()`...")
        print(f">>> Automatically selecting and training a model for a '{problem_type}' problem.")
        
        # In a real AutoML library, this step would involve complex logic for
        # feature engineering, model selection, and hyperparameter tuning.
        # Here, we just use a standard classifier as a stand-in.
        model = RandomForestClassifier(random_state=42)
        model.fit(X_train, y_train)
        
        print(">>> Model training complete.")
        return model

# Create an instance to act as the imported module
sklearn_pro_max = SklearnProMaxMock()
# ---- End Mock Implementation ----


def main():
    """
    Main function to load data, train a model, and evaluate it.
    """
    # 1. Load a sample dataset
    print("Step 1: Loading the Iris dataset...")
    iris = load_iris()
    X = pd.DataFrame(iris.data, columns=iris.feature_names)
    y = pd.Series(iris.target, name='target')

    # 2. Split the data into training and testing sets
    print("Step 2: Splitting data into training and testing sets...")
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.3, random_state=42, stratify=y
    )
    print(f"Training set size: {len(X_train)} samples")
    print(f"Testing set size: {len(X_test)} samples")

    # 3. Train the model using sklearn-pro-max's auto model function
    # This is the core call. The function is expected to take the training
    # data and automatically handle the model training process.
    print("\nStep 3: Training the model with sklearn-pro-max...")
    model = sklearn_pro_max.train_auto_model(X_train, y_train)

    # 4. Evaluate the trained model on the test set
    print("\nStep 4: Evaluating the trained model...")
    predictions = model.predict(X_test)

    # Calculate and print the accuracy
    accuracy = accuracy_score(y_test, predictions)

    print("\n--- Results ---")
    print(f"Model successfully trained and evaluated.")
    print(f"Accuracy on the test set: {accuracy:.4f}")

if __name__ == "__main__":
    main()
```