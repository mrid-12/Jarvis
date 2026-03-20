import os
import json
import logging
import re
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

PLAYBOOK_DIR = os.path.join(os.path.dirname(__file__), "playbook")

class PlaybookManager:
    """
    Manages a local repository of Standard Operating Procedures (SOPs).
    Saves and retrieves procedures learned from successful missions.
    """
    def __init__(self):
        self.playbook_dir = PLAYBOOK_DIR
        os.makedirs(self.playbook_dir, exist_ok=True)

    def _sanitize_filename(self, goal: str) -> str:
        """Converts a goal description into a valid filename."""
        # Remove non-alphanumeric and replace spaces with underscores
        clean = re.sub(r'[^\w\s-]', '', goal).strip().lower()
        clean = re.sub(r'[-\s]+', '_', clean)
        return clean[:50] + ".json"

    def record_procedure(self, goal: str, steps: List[str], final_thought: str):
        """Saves a successful procedure to the playbook."""
        filename = self._sanitize_filename(goal)
        filepath = os.path.join(self.playbook_dir, filename)
        
        procedure = {
            "goal": goal,
            "steps": steps,
            "final_thought": final_thought,
            "version": "1.0"
        }
        
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(procedure, f, indent=4)
            logger.info(f"Playbook: Recorded successful procedure for: {goal}")
        except Exception as e:
            logger.error(f"Playbook: Failed to record procedure: {e}")

    def get_relevant_sops(self, goal: str) -> List[Dict]:
        """
        Simple keyword-based retrieval for relevant SOPs.
        In the future, this can be upgraded to vector search.
        """
        relevant_procedures = []
        goal_keywords = set(re.findall(r'\w+', goal.lower()))
        
        try:
            for filename in os.listdir(self.playbook_dir):
                if not filename.endswith(".json"): continue
                
                # Check if filename contains any key goal words
                file_keywords = set(filename.replace(".json", "").split("_"))
                if goal_keywords.intersection(file_keywords):
                    filepath = os.path.join(self.playbook_dir, filename)
                    with open(filepath, "r", encoding="utf-8") as f:
                        relevant_procedures.append(json.load(f))
                        
            # V9.1: Prioritize shorter series of steps
            relevant_procedures.sort(key=lambda x: len(x.get("steps", [])))
            
        except Exception as e:
            logger.error(f"Playbook: Error during retrieval: {e}")
            
        return relevant_procedures

    def format_sops_for_llm(self, goal: str) -> str:
        """Formats relevant SOPs into a string for the LLM prompt."""
        sops = self.get_relevant_sops(goal)
        if not sops:
            return ""
            
        context = "\n\n--- LEARNED PROCEDURES (PLAYBOOK) ---\n"
        context += "I have successfully performed similar tasks before. Here are the steps I followed:\n"
        
        for sop in sops[:2]: # Show the top 2 most relevant
            context += f"\nProcedure for: {sop.get('goal')}\n"
            steps = sop.get("steps", [])
            for i, step in enumerate(steps):
                context += f"{i+1}. {step}\n"
            context += f"Resulting insight: {sop.get('final_thought')}\n"
            
        context += "\nUse these as a reference to execute the current task faster and more reliably.\n"
        return context
