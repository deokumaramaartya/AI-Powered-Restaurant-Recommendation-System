# Problem Statement

## AI-Powered Restaurant Recommendation System (Zomato Use Case)

Build an AI-powered restaurant recommendation service inspired by Zomato. The system should suggest restaurants from user preferences by combining structured restaurant data with a Large Language Model (LLM).

## Objective

Design and implement an application that:

- Takes user preferences (location, budget, cuisine, and ratings)
- Uses a real-world restaurant dataset
- Uses an LLM to generate personalized, human-like recommendations
- Displays clear and useful results to the user

## System Workflow

### 1. Data Ingestion

- Load and preprocess the Zomato dataset from Hugging Face: [ManikaSaini/zomato-restaurant-recommendation](https://huggingface.co/datasets/ManikaSaini/zomato-restaurant-recommendation)
- Extract relevant fields such as restaurant name, location, cuisine, cost, and rating

### 2. User Input

Collect user preferences:

- Location (for example, Delhi or Bangalore)
- Budget (low, medium, high)
- Cuisine (for example, Italian or Chinese)
- Minimum rating
- Additional preferences (for example, family-friendly or quick service)

### 3. Integration Layer

- Filter and prepare relevant restaurant data based on user input
- Pass structured results into an LLM prompt
- Design a prompt that helps the LLM reason about and rank options

### 4. Recommendation Engine

Use the LLM to:

- Rank restaurants
- Explain why each recommendation fits the user
- Optionally summarize the set of choices

### 5. Output Display

Present top recommendations in a user-friendly format, including:

- Restaurant name
- Cuisine
- Rating
- Estimated cost
- AI-generated explanation
