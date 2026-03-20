# Gemini Live Agent

This project implements a multimodal AI agent that can observe the screen, interpret visual elements, and execute actions.

## Repository Structure

- `backend/`: Core logic and planner agent.
- `client/`: Action execution and multimodal feedback.
- `requirements.txt`: Project dependencies.

## Setup

1. Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set up environment variables in `.env`:
   ```
   GOOGLE_API_KEY=your_api_key_here
   ```
4. Run the application:
   ```bash
   python -m backend.main
   ```
