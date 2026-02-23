# Google ADK Search Agent

This is a simple research agent built using the **Google Agent Development Kit (ADK)**. It uses the `google_search` tool to provide up-to-date answers to user queries.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running.
- A **Google API Key** (from [Google AI Studio](https://aistudio.google.com/)).

## Setup & Deployment

1. **Clone or copy the files** to a directory (e.g., `search_agent`).
2. **Build the Docker Image**:
   Open your terminal and run:
   ```bash
   docker build -t search-agent .
   ```

3. **Run the Agent**:
   To run the agent in persistent mode (required for scheduling), use:
   ```bash
   docker run -it \
     -e GOOGLE_API_KEY="YOUR_API_KEY" \
     -e LOGIN_URL="https://example.com/login" \
     -e LOGIN_USERNAME="your_user" \
     -e LOGIN_PASSWORD="your_password" \
     -e TZ="America/New_York" \
     search-agent
   ```
   *The agent will run in the background and trigger the login task every day at 8:00 AM in the specified timezone.*

## Features

- **Google Search Integration**: Uses ADK's built-in search tool.
- **Browser Automation (Playwright)**: Can log into websites and interact with pages.
- **Daily Scheduling**: Built-in scheduler (`apscheduler`) to run tasks at specific times (e.g., 8:00 AM daily).
- **Robust Logging**: All actions and errors are logged to `agent.log` and the console.
- **Exception Handling**: Gracefully handles initialization and execution errors.
- **Dockerized**: Easy to deploy anywhere with Docker.

## Project Structure

- `main.py`: Core logic for agent initialization and execution.
- `Dockerfile`: Instructions for containerizing the application.
- `requirements.txt`: Python dependencies.
- `agent.log`: (Created at runtime) Contains detailed execution logs.
